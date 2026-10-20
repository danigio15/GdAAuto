"""Il filo con la NIO Open Telematics API, l'API ufficiale di NIO per l'Europa.

NIO apre i dati dell'auto ai proprietari con una «Personal Application»:
chi la crea nella console (``open-eu.nio.com/console``) riceve un client ID e
un client secret, e sceglie l'indirizzo di ritorno del login. Da lì è un
OAuth normale, con PKCE:

- il login si fa nel browser dell'utente (``/oauth2/authorize``), sulla
  pagina di NIO; NIO torna all'indirizzo scelto con ``?code=…&state=…``, che
  si incolla in GdAAuto. GdAAuto non vede mai la password;
- ``POST /api/2/oauth/token`` (form, autenticazione HTTP Basic col client)
  scambia il codice, e poi rinnova i token col refresh token. NIO chiude la
  risposta in una busta: ``{"result_code": "success", "data": {token}}``;
- le letture: ``GET https://open-api-eu.nio.com/api/1/telematics/vehicles/{vin}/vehicle_status/latest``
  (lo stato dell'auto), ``…/<risorsa>/changes`` (quello che è cambiato negli
  ultimi dieci minuti: un campo che non cambia non torna, e qui si tiene quello
  di prima) e ``…/aftersales/vehicles/{vin}/odometer_reports`` per i km.

Le regole del cloud, come le ha trovate chi lo usa da mesi:

- una richiesta ogni 15 secondi per Personal Application (anche con più
  auto): qui si aspetta il proprio turno, e dopo «troppe richieste» si sta
  zitti quanto dice ``Retry-After`` (o cinque minuti);
- ``soc_status/changes`` va chiesto senza date: con ``start_time`` e
  ``end_time`` NIO oggi risponde ``invalid_param``. Siccome dà solo i cambi
  degli ultimi dieci minuti, un'auto ferma non ridice la sua carica: l'ultima
  buona si tiene, e si salva nella voce perché sopravviva a un riavvio;
- NIO dice ``access_denied`` per le risorse che quell'auto o quell'account non
  concedono, e ``resource_not_found`` quando non ha record recenti.

L'API è in sola lettura: niente comandi. E non elenca le auto: la VIN la
scrive chi configura.

Indirizzi, ambiti, buste e ritmi vengono da nicolasvangeluwe/nio-home-assistant
(licenza MIT), che segue la documentazione di NIO: vedi RINGRAZIAMENTI.md.
Questo modulo non importa Home Assistant.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
import hashlib
import logging
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import aiohttp

from ...vettura import StatoVettura, Vettura
from ..errori import ErroreApi, ErroreCredenziali, ErroreRete
from .dati import interpreta, numero, unisci

_LOGGER = logging.getLogger(__name__)

ID = "nio"
NOME = "NIO"

AUTORIZZA = "https://open-eu.nio.com/oauth2/authorize"
URL_TOKEN = "https://open-eu.nio.com/api/2/oauth/token"
API = "https://open-api-eu.nio.com"
#: l'indirizzo di ritorno da scrivere nella Personal Application
RITORNO = "https://my.home-assistant.io/redirect/oauth"
#: gli ambiti in sola lettura della Personal Application (un ambito che manca dà «access_denied»)
AMBITI = (
    "vehicle:read",
    "vehicle:dynamics:read",
    "vehicle:location:read",
    "vehicle:energy:read",
    "vehicle:body:read",
    "vehicle:cabin:read",
    "vehicle:powertrain:read",
    "vehicle:diagnostics:read",
    "vehicle:adas:read",
    "vehicle:nomi:read",
)

_TIMEOUT = aiohttp.ClientTimeout(total=30)
_MARGINE_TOKEN = 120
_DA_RIENTRARE = {"invalid_grant", "invalid_token", "access_denied", "unauthorized", "invalid_client"}
_PASSEGGERI = {"server_error", "temporarily_unavailable", "internal_error", "rate_limited"}
_TROPPE = ("rate_limit", "rate limit", "too_many", "too many", "throttl", "request_frequen")

#: la distanza minima fra due richieste della stessa Personal Application
PAUSA_S = 15.0
#: dopo «troppe richieste», quanto si sta zitti se NIO non dice quanto
FERMO_QUOTA_S = 5 * 60
#: una risorsa negata si riprova ogni sei ore (i permessi si possono cambiare nella console)
RIPROVA_NEGATE_S = 6 * 3600

#: lette a ogni giro
SEMPRE = ("soc_status", "vehicle_status")
#: una per giro, a turno (le lente solo ogni tanto)
A_TURNO = ("position_status", "door_status", "position_status", "door_status", "*")
LENTE = ("window_status", "hvac_status", "extremum_data", "odometer_report")
#: le risorse che si salvano nella voce, per ripartire dopo un riavvio con quello che si sapeva
DA_RICORDARE = ("soc_status", "position_status", "door_status")

#: il turno di ogni Personal Application: quando può partire la prossima richiesta, e fino a quando si sta zitti
_TURNI: dict[str, float] = {}
_FERMI: dict[str, float] = {}
_SERRATURE: dict[str, asyncio.Lock] = {}


def comandi_di(_v: Vettura) -> set[str]:
    return set()


# --- il login nel browser ---------------------------------------------------------------


def pkce() -> tuple[str, str]:
    """(verifier, challenge S256) per il login."""
    verifier = secrets.token_urlsafe(96)[:128]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def link_di_accesso(
    client_id: str, challenge: str, stato: str, *, ritorno: str = RITORNO, autorizza: str = AUTORIZZA
) -> str:
    parametri = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": ritorno,
        "state": stato,
        "scope": " ".join(AMBITI),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{autorizza}?{urlencode(parametri)}"


def leggi_codice(incollato: str) -> tuple[str, str]:
    """(code, state) dall'indirizzo finale incollato, o dal solo codice."""
    testo = incollato.strip()
    if "code=" not in testo:
        return testo, ""
    parti = urlsplit(testo)
    parametri = parse_qs(parti.query or parti.fragment or testo.split("?", 1)[-1])
    return (parametri.get("code") or [""])[0], (parametri.get("state") or [""])[0]


async def chiedi_token(
    http: aiohttp.ClientSession, client_id: str, segreto: str, modulo: dict[str, str], *, url_token: str = URL_TOKEN
) -> dict[str, Any]:
    """Una richiesta a ``/api/2/oauth/token``: il token, o l'errore giusto."""
    try:
        async with http.post(
            url_token,
            data=modulo,
            auth=aiohttp.BasicAuth(client_id, segreto),
            headers={"Accept": "application/json"},
            timeout=_TIMEOUT,
        ) as r:
            status = r.status
            try:
                corpo = await r.json(content_type=None)
            except ValueError:
                corpo = None
    except (aiohttp.ClientError, TimeoutError, OSError) as err:
        raise ErroreRete(f"token: NIO non risponde: {err}") from err
    busta = corpo if isinstance(corpo, dict) else {}
    esito = str(busta.get("result_code") or busta.get("error") or "")
    if esito in _DA_RIENTRARE:
        raise ErroreCredenziali(f"NIO rifiuta l'autorizzazione ({esito}): serve rientrare")
    if esito in _PASSEGGERI or status == 429 or status >= 500:
        raise ErroreRete(f"token: NIO ha un guasto (HTTP {status}, {esito or 'senza codice'})")
    if 400 <= status < 500:
        raise ErroreCredenziali(f"NIO rifiuta la richiesta del token (HTTP {status}): serve rientrare")
    dati = busta.get("data")
    if esito != "success" or not isinstance(dati, dict) or not dati.get("access_token"):
        raise ErroreRete("token: NIO risponde una cosa che non si capisce")
    durata = numero(dati.get("expires_in")) or 3600.0
    return {
        "access_token": str(dati["access_token"]),
        "refresh_token": str(dati.get("refresh_token") or modulo.get("refresh_token") or ""),
        "scade": time.time() + durata,
    }


async def scambia_codice(
    http: aiohttp.ClientSession,
    client_id: str,
    segreto: str,
    codice: str,
    verifier: str,
    *,
    ritorno: str = RITORNO,
    url_token: str = URL_TOKEN,
) -> dict[str, Any]:
    modulo = {"grant_type": "authorization_code", "code": codice, "redirect_uri": ritorno, "code_verifier": verifier}
    return await chiedi_token(http, client_id, segreto, modulo, url_token=url_token)


# --- il cliente ---------------------------------------------------------------------


class _Vuoto(Exception):
    """NIO non ha record recenti per quella risorsa (``resource_not_found``, o una lista vuota)."""


class _Negato(Exception):
    """NIO non concede quella risorsa a questa auto o a questo account (``access_denied``)."""


class _Rallenta(ErroreApi):
    """NIO ha chiesto di rallentare."""

    def __init__(self, cosa: str, http: int = 0) -> None:
        super().__init__(cosa, "rate_limited", "NIO ha chiesto di rallentare: si riprova fra qualche minuto", http)


class ClienteNio:
    def __init__(
        self,
        sessione_http: aiohttp.ClientSession,
        client_id: str,
        segreto: str,
        vin: str,
        token: dict[str, Any],
        salva: Callable[[dict[str, Any]], None] | None = None,
        *,
        memoria: dict[str, Any] | None = None,
        api: str = API,
        url_token: str = URL_TOKEN,
    ) -> None:
        self._http = sessione_http
        self._client_id = client_id
        self._segreto = segreto
        self.vin = vin
        self.token: dict[str, Any] = dict(token)
        #: riceve quello da rimettere nella voce: ``{"token": …}`` o ``{"memoria": …}``
        self._salva = salva
        self._api_base = api.rstrip("/")
        self._url_token = url_token
        self._serratura_token = asyncio.Lock()
        #: l'ultimo record (unito) di ogni risorsa; quello salvato riparte dopo un riavvio
        self._parti: dict[str, dict[str, Any]] = {
            k: dict(v) for k, v in (memoria or {}).items() if k in DA_RICORDARE and isinstance(v, dict)
        }
        self._ricordato = self._da_ricordare()
        self._negate: dict[str, float] = {}
        self._turno = 0
        self._lenta = 0
        self._primo_giro = True
        self._ultimo: StatoVettura | None = None

    # --- i token ------------------------------------------------------------

    async def _token_valido(self, *, nuovo: bool = False) -> str:
        async with self._serratura_token:
            scade = float(self.token.get("scade") or 0)
            if not nuovo and self.token.get("access_token") and time.time() < scade - _MARGINE_TOKEN:
                return str(self.token["access_token"])
            rinnovo = str(self.token.get("refresh_token") or "")
            if not rinnovo:
                raise ErroreCredenziali("Manca il refresh token di NIO: serve rientrare")
            self.token = await chiedi_token(
                self._http,
                self._client_id,
                self._segreto,
                {"grant_type": "refresh_token", "refresh_token": rinnovo},
                url_token=self._url_token,
            )
            if self._salva:
                self._salva({"token": dict(self.token)})
            return str(self.token["access_token"])

    # --- la rete ------------------------------------------------------------

    async def _aspetta_il_turno(self) -> None:
        """Una richiesta ogni ``PAUSA_S`` per Personal Application, anche fra voci diverse."""
        serratura = _SERRATURE.setdefault(self._client_id, asyncio.Lock())
        while True:
            async with serratura:
                attesa = _TURNI.get(self._client_id, 0.0) - time.monotonic()
                if attesa <= 0:
                    _TURNI[self._client_id] = time.monotonic() + PAUSA_S
                    return
            # Si aspetta senza tenere la serratura: un «rallenta» può allungare il turno intanto.
            await asyncio.sleep(attesa)

    @property
    def _fermo(self) -> bool:
        """NIO ha chiesto di rallentare a questa Personal Application, e non è ancora ora."""
        return time.monotonic() < _FERMI.get(self._client_id, 0.0)

    def _fermati(self, secondi: float) -> None:
        _FERMI[self._client_id] = time.monotonic() + secondi

    async def _leggi(self, cosa: str, percorso: str) -> Any:
        """Una lettura: la busta di NIO aperta, o l'errore giusto."""
        if self._fermo:
            raise _Rallenta(cosa)
        for tentativo in (1, 2):
            token = await self._token_valido(nuovo=tentativo == 2)
            await self._aspetta_il_turno()
            try:
                async with self._http.get(
                    f"{self._api_base}{percorso}",
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                    timeout=_TIMEOUT,
                ) as r:
                    status = r.status
                    dopo = r.headers.get("Retry-After")
                    try:
                        corpo = await r.json(content_type=None)
                    except ValueError:
                        corpo = None
            except (aiohttp.ClientError, TimeoutError, OSError) as err:
                raise ErroreRete(f"{cosa}: NIO non risponde: {err}") from err
            if status == 401 and tentativo == 1:
                continue
            break
        busta = corpo if isinstance(corpo, dict) else {}
        esito = str(busta.get("result_code") or "")
        messaggi = " ".join(str(busta.get(k) or "") for k in ("result_code", "display_msg", "debug_msg")).lower()
        if status == 401:
            raise ErroreCredenziali(f"{cosa}: NIO rifiuta anche il token appena rinnovato")
        if status == 429 or any(p in messaggi for p in _TROPPE):
            self._fermati(max(numero(dopo) or 0.0, 0.0) or FERMO_QUOTA_S)
            raise _Rallenta(cosa, status)
        if esito == "access_denied" or (status == 403 and not esito):
            raise _Negato(cosa)
        if esito == "resource_not_found" or status == 404:
            raise _Vuoto(cosa)
        if status >= 500 or esito in _PASSEGGERI:
            raise ErroreRete(f"{cosa}: NIO ha un guasto (HTTP {status})")
        if not isinstance(corpo, dict):
            raise ErroreRete(f"{cosa}: NIO risponde una cosa che non si capisce (HTTP {status})")
        if esito != "success" or status >= 400:
            raise ErroreApi(cosa, esito or None, str(busta.get("display_msg") or busta.get("debug_msg") or ""), status)
        return busta.get("data")

    def _percorso(self, risorsa: str) -> str:
        if risorsa == "odometer_report":
            return f"/api/1/telematics/aftersales/vehicles/{self.vin}/odometer_reports"
        coda = "latest" if risorsa == "vehicle_status" else "changes"
        return f"/api/1/telematics/vehicles/{self.vin}/{risorsa}/{coda}"

    async def _risorsa(self, risorsa: str) -> dict[str, Any]:
        """Una risorsa letta e unita a quello che si sapeva."""
        vecchio = self._parti.get(risorsa)
        dati = await self._leggi(risorsa, self._percorso(risorsa))
        if risorsa == "vehicle_status":
            if not isinstance(dati, dict):
                raise ErroreRete(f"{risorsa}: NIO risponde una cosa che non si capisce")
            if numero(dati.get("mileage"), non_so=(0xFFFFFFFE, 0xFFFFFFFF)) is None and "mileage" in (vecchio or {}):
                # Uno stato senza chilometri non cancella quelli di prima.
                dati = {**dati, "mileage": (vecchio or {})["mileage"]}
            return dati
        voci = [d for d in dati if isinstance(d, dict)] if isinstance(dati, list) else []
        if not voci:
            raise _Vuoto(risorsa)
        if risorsa == "odometer_report":
            return max(voci, key=lambda d: str(d.get("recorded_at") or ""))
        return unisci(vecchio, voci)

    # --- quello che si ricorda ---------------------------------------------------

    def _da_ricordare(self) -> dict[str, Any]:
        ricordo: dict[str, Any] = {}
        for risorsa in DA_RICORDARE:
            if record := self._parti.get(risorsa):
                ricordo[risorsa] = {k: v for k, v in record.items() if not isinstance(v, (list, dict))}
        return ricordo

    def _ricorda(self) -> None:
        """Le ultime letture buone nella voce, solo quando cambiano."""
        ricordo = self._da_ricordare()
        if ricordo != self._ricordato and self._salva:
            self._ricordato = ricordo
            self._salva({"memoria": ricordo})

    # --- l'auto ----------------------------------------------------------------

    async def accedi(self) -> None:
        await self._token_valido()

    async def vetture(self) -> list[Vettura]:
        """L'API non elenca le auto: si prova a leggere quella della VIN scritta."""
        try:
            self._parti["vehicle_status"] = await self._risorsa("vehicle_status")
        except _Negato:
            raise ErroreApi(
                "auto", "access_denied", f"NIO non dà i dati della {self.vin} a questa Personal Application"
            ) from None
        except _Vuoto:
            raise ErroreApi("auto", "resource_not_found", f"NIO non conosce la {self.vin} su questo account") from None
        except _Rallenta:
            if "vehicle_status" not in self._parti:
                raise
        return [Vettura(vin=self.vin, marca=ID, modello=NOME, nome=f"{NOME} {self.vin[-4:]}")]

    def _da_leggere(self) -> list[str]:
        """Le risorse di questo giro: le sempre, e una a turno (al primo giro tutte le frequenti)."""
        adesso = time.monotonic()
        aperte = [r for r in (*SEMPRE, *A_TURNO, *LENTE) if adesso >= self._negate.get(r, 0.0)]
        elenco = [r for r in SEMPRE if r in aperte]
        if self._primo_giro:
            self._primo_giro = False
            return [*elenco, *(r for r in dict.fromkeys(A_TURNO) if r in aperte and r != "*")]
        for _ in range(len(A_TURNO)):
            scelta = A_TURNO[self._turno % len(A_TURNO)]
            self._turno += 1
            if scelta == "*":
                # La prossima lenta, nell'ordine, saltando le negate.
                for _ in range(len(LENTE)):
                    scelta = LENTE[self._lenta % len(LENTE)]
                    self._lenta += 1
                    if scelta in aperte:
                        break
            if scelta in aperte:
                elenco.append(scelta)
                break
        return elenco

    async def stato(self, v: Vettura) -> StatoVettura:
        if self._fermo and (ultimo := self._ultimo or v.stato) is not None:
            # NIO ha chiesto di rallentare: si tiene l'ultima lettura.
            return ultimo
        for risorsa in self._da_leggere():
            try:
                self._parti[risorsa] = await self._risorsa(risorsa)
            except _Negato:
                _LOGGER.debug("NIO non concede %s per %s", risorsa, v.titolo)
                self._negate[risorsa] = time.monotonic() + RIPROVA_NEGATE_S
            except _Vuoto:
                # Niente di nuovo negli ultimi dieci minuti: vale quello di prima.
                continue
            except _Rallenta:
                if (ultimo := self._ultimo or v.stato) is not None:
                    return ultimo
                if self._parti:
                    break
                raise
            except ErroreApi as err:
                if risorsa in SEMPRE:
                    raise
                _LOGGER.debug("%s di %s non letta: %s", risorsa, v.titolo, err)
        if not self._parti:
            raise ErroreApi("stato dell'auto", "access_denied", f"NIO non dà nessuna lettura per {v.titolo}")
        s = interpreta(self._parti)
        self._ultimo = s
        self._ricorda()
        return s
