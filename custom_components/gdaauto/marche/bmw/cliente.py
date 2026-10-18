"""Il filo con BMW CarData, l'API ufficiale e gratuita di BMW per i clienti.

Vale per BMW e MINI in Europa (il cloud è lo stesso). Chi configura crea il
proprio «Client ID» nel portale MyBMW / MINI, nella sezione CarData, e lo
abbona a «CarData API» e «CarData Stream». Poi si entra col «device code»:

1. GdAAuto chiede un codice a ``customer.bmwgroup.com`` (con PKCE: un segreto
   casuale, ``code_verifier``, di cui si manda solo l'impronta SHA-256);
2. chi configura apre il link, entra col suo account BMW e conferma il codice;
3. GdAAuto scambia il ``device_code`` (e il segreto) con i token: access
   (un'ora), refresh (due settimane, cambia a ogni rinnovo), id_token.

Le auto si leggono da ``api-cardata.bmwgroup.com`` (REST, intestazione
``x-version: v1``): ``/customers/vehicles/mappings`` dice quali auto sono
dell'account (si leggono solo quelle di cui si è utente principale),
``/basicData`` il modello, e ``/telematicData?containerId=…`` lo stato. Un
«container» è l'elenco dei descrittori che si vogliono: si crea una volta sola,
al primo accesso, e se ne tiene l'id.

BMW concede 50 chiamate REST al giorno per account. Per questo:

- l'elenco delle auto e il container si salvano nella configurazione, e un
  riavvio di Home Assistant non costa chiamate;
- il cliente tiene il conto delle chiamate delle ultime 24 ore e distanzia da
  solo le letture di ogni auto: se le opzioni chiedono più spesso di quanto
  BMW permetta, si restituisce l'ultimo stato letto invece di sprecare la
  quota.

Si legge soltanto: CarData non ha comandi. Lo streaming MQTT di CarData
(dati in tempo reale, senza quota) è una miglioria futura: servirebbe un client
MQTT, che oggi GdAAuto non ha.
"""

from __future__ import annotations

import asyncio
import base64
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import hashlib
import json
import logging
import secrets
import time
from typing import Any

import aiohttp

from ...vettura import StatoVettura, Vettura
from ..base import InAttesa
from ..errori import ErroreApi, ErroreCredenziali, ErroreRete
from .dati import DESCRITTORI, DESCRITTORI_RIDOTTI, interpreta

_LOGGER = logging.getLogger(__name__)

ID = "bmw"
NOME = "BMW CarData"

AUTH = "https://customer.bmwgroup.com"
API = "https://api-cardata.bmwgroup.com"
SCOPE = "authenticate_user openid cardata:api:read cardata:streaming:read"
_GRANT_DISPOSITIVO = "urn:ietf:params:oauth:grant-type:device_code"

_TIMEOUT = aiohttp.ClientTimeout(total=30)
_MARGINE = timedelta(minutes=2)

#: Le chiamate REST che BMW concede in 24 ore, e quante se ne tengono da parte
#: per un riavvio o per rifare il container.
QUOTA_GIORNALIERA = 50
RISERVA = 4
_GIORNO = 24 * 3600
#: Una lettura un po' prima del suo turno va bene: il coordinatore non è un orologio.
_TOLLERANZA = 5 * 60
#: Dopo un «quota esaurita» di BMW non si riprova per un'ora.
_PAUSA_QUOTA = 3600
#: Dopo la conferma BMW dice «access_denied» per un minuto o due, finché il
#: permesso non arriva dappertutto: fino a qui è ancora un'attesa.
_GRAZIA_NEGATO = 180

CONTAINER_NOME = "GdAAuto"
CONTAINER_NOME_RIDOTTO = "GdAAuto ridotto"

#: Le parole degli errori di CarData (``exveErrorId``) che vogliono un nuovo login.
_ERRORI_LOGIN = {"CU-100", "CU-101", "CU-102", "CU-103"}
_ERRORI_CONTAINER = {"CU-105", "CU-121"}
_ERRORI_DESCRITTORI = {"CU-400", "CU-401", "CU-402"}

_MARCHI = {"BMW": "BMW", "BMWI": "BMW", "MINI": "MINI", "ROLLS-ROYCE": "Rolls-Royce", "TOYOTA-SUPRA": "Toyota"}


def comandi_di(_v: Vettura) -> set[str]:
    return set()


def firma(descrittori: tuple[str, ...]) -> str:
    """Un'impronta corta dell'elenco: un container con la stessa si riusa."""
    return hashlib.sha1("|".join(sorted(descrittori)).encode()).hexdigest()[:10]


def _scopo(ridotto: bool) -> str:
    return f"GdAAuto {firma(DESCRITTORI_RIDOTTI if ridotto else DESCRITTORI)}"


# --- il login col codice su un altro schermo -----------------------------------------


def nuovo_verifier() -> tuple[str, str]:
    """Il segreto PKCE e la sua impronta S256."""
    verifier = secrets.token_urlsafe(64)[:96]
    impronta = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(impronta).rstrip(b"=").decode("ascii")


async def _post_auth(http: aiohttp.ClientSession, url: str, modulo: dict[str, str]) -> tuple[int, Any]:
    intestazioni = {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}
    try:
        async with http.post(url, data=modulo, headers=intestazioni, timeout=_TIMEOUT) as r:
            testo = await r.text()
            http_ = r.status
    except (aiohttp.ClientError, TimeoutError, OSError) as err:
        raise ErroreRete(f"Il login di BMW non risponde: {err}") from err
    try:
        corpo = json.loads(testo) if testo else {}
    except ValueError:
        corpo = {}
    return http_, corpo


def _motivo(corpo: Any, http: int) -> str:
    if isinstance(corpo, dict):
        return str(corpo.get("error_description") or corpo.get("error") or http)
    return str(http)


async def chiedi_codice(http: aiohttp.ClientSession, client_id: str, *, auth: str = AUTH) -> dict[str, Any]:
    """Il primo passo: un link e un codice da confermare sul sito di BMW."""
    verifier, sfida = nuovo_verifier()
    stato, corpo = await _post_auth(
        http,
        f"{auth}/gcdm/oauth/device/code",
        {
            "client_id": client_id,
            "response_type": "device_code",
            "scope": SCOPE,
            "code_challenge": sfida,
            "code_challenge_method": "S256",
        },
    )
    if stato >= 500 or stato == 429:
        raise ErroreRete(f"Il login di BMW ha un guasto: {stato}")
    if stato != 200 or not isinstance(corpo, dict) or not corpo.get("device_code"):
        # Un Client ID sbagliato, o non abbonato a CarData.
        raise ErroreCredenziali(f"BMW non accetta il Client ID: {_motivo(corpo, stato)}")
    return {
        "link": str(corpo.get("verification_uri_complete") or corpo.get("verification_uri") or ""),
        "codice": str(corpo.get("user_code") or ""),
        "device_code": str(corpo["device_code"]),
        "code_verifier": verifier,
        "intervallo": int(corpo.get("interval") or 5),
        "scade": time.time() + int(corpo.get("expires_in") or 300),
    }


def _token_da(corpo: dict[str, Any], vecchio: dict[str, Any] | None = None) -> dict[str, Any]:
    vecchio = vecchio or {}
    scade = datetime.now(UTC) + timedelta(seconds=int(corpo.get("expires_in") or 3600))
    return {
        "access_token": corpo["access_token"],
        "refresh_token": corpo.get("refresh_token") or vecchio.get("refresh_token"),
        "id_token": corpo.get("id_token") or vecchio.get("id_token"),
        "gcid": corpo.get("gcid") or vecchio.get("gcid"),
        "scade": scade.isoformat(),
    }


async def scambia_codice(
    http: aiohttp.ClientSession,
    client_id: str,
    stato: dict[str, Any],
    *,
    auth: str = AUTH,
    adesso: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Il passo finale, provato una volta: i token, o ``InAttesa`` se non è ancora confermato.

    ``stato`` è quello di ``chiedi_codice``; ci si annota quando BMW ha detto
    «negato» la prima volta, per la grazia dopo la conferma.
    """
    http_, corpo = await _post_auth(
        http,
        f"{auth}/gcdm/oauth/token",
        {
            "client_id": client_id,
            "grant_type": _GRANT_DISPOSITIVO,
            "device_code": stato["device_code"],
            "code_verifier": stato["code_verifier"],
        },
    )
    if http_ == 200 and isinstance(corpo, dict) and corpo.get("access_token"):
        return _token_da(corpo)
    errore = corpo.get("error") if isinstance(corpo, dict) else None
    if errore in ("authorization_pending", "slow_down"):
        raise InAttesa("Il codice non è ancora confermato sul sito di BMW")
    if errore == "access_denied":
        prima = stato.setdefault("negato_da", adesso())
        if adesso() - prima <= _GRAZIA_NEGATO:
            raise InAttesa("BMW non ha ancora registrato la conferma")
        raise ErroreCredenziali("BMW dice che l'accesso è stato negato")
    if http_ >= 500 or http_ == 429:
        # Appena dopo la conferma il login di BMW a volte inciampa: si riprova.
        raise ErroreRete(f"Il login di BMW ha un guasto: {http_}")
    raise ErroreCredenziali(f"BMW rifiuta il codice: {_motivo(corpo, http_)}")


def chi_e(token: dict[str, Any]) -> dict[str, str]:
    """L'email e l'id dell'account, dall'id_token (senza verificarlo: serve solo per il titolo)."""
    chi: dict[str, str] = {}
    if token.get("gcid"):
        chi["gcid"] = str(token["gcid"])
    parti = str(token.get("id_token") or "").split(".")
    if len(parti) == 3:
        try:
            corpo = json.loads(base64.urlsafe_b64decode(parti[1] + "=" * (-len(parti[1]) % 4)))
        except (ValueError, TypeError):
            corpo = {}
        if isinstance(corpo, dict):
            for chiave in ("email", "sub"):
                valore = corpo.get(chiave)
                if isinstance(valore, str) and "@" in valore:
                    chi["email"] = valore.lower()
                    break
            if "gcid" not in chi and isinstance(corpo.get("gcid"), str):
                chi["gcid"] = corpo["gcid"]
    return chi


# --- il conto delle chiamate -----------------------------------------------------------


class Quota:
    """Le chiamate REST delle ultime 24 ore: BMW ne concede 50."""

    def __init__(self, adesso: Callable[[], float] = time.time, limite: int = QUOTA_GIORNALIERA) -> None:
        self._adesso = adesso
        self.limite = limite
        self._chiamate: deque[float] = deque()
        self._ferma_fino = 0.0

    def _pulisci(self) -> float:
        ora = self._adesso()
        while self._chiamate and ora - self._chiamate[0] >= _GIORNO:
            self._chiamate.popleft()
        return ora

    @property
    def usate(self) -> int:
        self._pulisci()
        return len(self._chiamate)

    def disponibile(self) -> bool:
        ora = self._pulisci()
        return ora >= self._ferma_fino and len(self._chiamate) < self.limite

    def prendi(self, cosa: str) -> None:
        if not self.disponibile():
            raise ErroreRete(f"{cosa}: la quota giornaliera di BMW CarData è finita, si aspetta")
        self._chiamate.append(self._adesso())

    def esaurita(self) -> None:
        """BMW ha detto basta prima del conto: ci si ferma un po'."""
        self._ferma_fino = self._adesso() + _PAUSA_QUOTA


# --- il cliente ------------------------------------------------------------------------


class ClienteBmw:
    def __init__(
        self,
        sessione_http: aiohttp.ClientSession,
        client_id: str,
        token: dict[str, Any],
        salva: Callable[[dict[str, Any]], None] | None = None,
        *,
        container_id: str | None = None,
        ridotto: bool = False,
        vetture_note: list[dict[str, Any]] | None = None,
        auth: str = AUTH,
        api: str = API,
        adesso: Callable[[], float] = time.time,
    ) -> None:
        self._http = sessione_http
        self.client_id = client_id
        self.token = dict(token)
        self._salva = salva
        self.container_id = container_id
        self.ridotto = ridotto
        self._vetture_note = list(vetture_note or [])
        self.auth = auth
        self.api = api
        self._adesso = adesso
        self.quota = Quota(adesso)
        self._serratura = asyncio.Lock()
        self._serratura_container = asyncio.Lock()
        self._ultimi: dict[str, tuple[float, StatoVettura]] = {}
        self._quante = max(1, len(self._vetture_note))

    # --- i token ------------------------------------------------------------------

    async def _token_valido(self) -> str:
        async with self._serratura:
            scade = self.token.get("scade")
            if not scade or datetime.fromisoformat(scade) - _MARGINE < datetime.now(UTC):
                await self._rinnova()
            return str(self.token["access_token"])

    async def _rinnova(self) -> None:
        if not self.token.get("refresh_token"):
            raise ErroreCredenziali("Manca il refresh token di BMW")
        http_, corpo = await _post_auth(
            self._http,
            f"{self.auth}/gcdm/oauth/token",
            {"grant_type": "refresh_token", "refresh_token": self.token["refresh_token"], "client_id": self.client_id},
        )
        if http_ >= 500 or http_ == 429:
            raise ErroreRete(f"Il login di BMW ha un guasto: {http_}")
        if http_ != 200 or not isinstance(corpo, dict) or not corpo.get("access_token"):
            # Refresh scaduto (due settimane), Client ID tolto da CarData…: si rientra.
            raise ErroreCredenziali(f"BMW non rinnova l'accesso: {_motivo(corpo, http_)}")
        self.token = _token_da(corpo, self.token)
        self._salva_cambi({"token": dict(self.token)})

    def _salva_cambi(self, cambi: dict[str, Any]) -> None:
        if self._salva:
            self._salva(cambi)

    # --- la rete -------------------------------------------------------------------

    async def _chiama(
        self, cosa: str, metodo: str, percorso: str, *, query: dict[str, str] | None = None, corpo: Any = None
    ) -> Any:
        """Una chiamata a CarData, contata nella quota; un 401 rinnova il token e riprova una volta."""
        for tentativo in (1, 2):
            self.quota.prendi(cosa)
            intestazioni = {
                "Authorization": f"Bearer {await self._token_valido()}",
                "x-version": "v1",
                "Accept": "application/json",
            }
            try:
                async with self._http.request(
                    metodo, f"{self.api}{percorso}", params=query, json=corpo, headers=intestazioni, timeout=_TIMEOUT
                ) as r:
                    http_ = r.status
                    testo = await r.text()
            except (aiohttp.ClientError, TimeoutError, OSError) as err:
                raise ErroreRete(f"{cosa}: BMW CarData non risponde: {err}") from err
            try:
                risposta = json.loads(testo) if testo else {}
            except ValueError:
                risposta = {"exveErrorMsg": testo[:200]}
            if http_ in (200, 201, 204):
                return risposta
            codice = _codice(risposta)
            if http_ == 401 and tentativo == 1 and codice != "CU-103":
                async with self._serratura:
                    await self._rinnova()
                continue
            raise self._errore(cosa, http_, codice, risposta)
        raise ErroreRete(f"{cosa}: BMW CarData non risponde")  # pragma: no cover

    def _errore(self, cosa: str, http_: int, codice: str | None, risposta: Any) -> Exception:
        messaggio = ""
        if isinstance(risposta, dict):
            messaggio = str(risposta.get("exveErrorMsg") or risposta.get("exveNote") or risposta.get("message") or "")
        if http_ == 429 or codice == "CU-429":
            self.quota.esaurita()
            return ErroreRete(f"{cosa}: BMW dice che la quota giornaliera è finita")
        if http_ == 401 or codice in _ERRORI_LOGIN:
            return ErroreCredenziali(f"{cosa}: BMW rifiuta l'accesso ({codice or http_}: {messaggio})")
        if http_ >= 500 or codice in ("CU-500", "CU-503"):
            return ErroreRete(f"{cosa}: BMW CarData ha un guasto: {http_}")
        return ErroreApi(cosa, codice, messaggio, http_)

    # --- le auto -------------------------------------------------------------------

    async def accedi(self) -> None:
        await self._token_valido()

    async def vetture(self) -> list[Vettura]:
        """Le auto salvate al primo accesso: all'avvio non si spende la quota."""
        if self._vetture_note:
            return [_da_nota(n) for n in self._vetture_note]
        return await self.leggi_vetture()

    async def leggi_vetture(self) -> list[Vettura]:
        """Le auto dell'account, chieste a BMW: una chiamata, più una per auto."""
        risposta = await self._chiama("elenco delle auto", "GET", "/customers/vehicles/mappings")
        if isinstance(risposta, dict):
            risposta = risposta.get("mappings") or risposta.get("vehicles") or [risposta]
        elenco: list[Vettura] = []
        for voce in risposta if isinstance(risposta, list) else []:
            if not isinstance(voce, dict) or not isinstance(voce.get("vin"), str):
                continue
            # Le auto condivise (SECONDARY) CarData non le fa leggere.
            if str(voce.get("mappingType") or "PRIMARY").upper() != "PRIMARY":
                continue
            vin = voce["vin"]
            try:
                base = await self._chiama("dati dell'auto", "GET", f"/customers/vehicles/{vin}/basicData")
            except ErroreApi as err:
                _LOGGER.debug("BMW non dà i dati di base di un'auto: %s", err)
                base = {}
            if isinstance(base, dict) and base.get("isTelematicsCapable") is False:
                continue
            elenco.append(_da_base(vin, base if isinstance(base, dict) else {}))
        self._vetture_note = [nota_di(v) for v in elenco]
        self._quante = max(1, len(elenco))
        return elenco

    # --- il container --------------------------------------------------------------

    async def prepara_container(self) -> str:
        """L'id del container di GdAAuto: quello salvato, uno uguale già sul cloud, o uno nuovo."""
        async with self._serratura_container:
            if self.container_id:
                return self.container_id
            nome = CONTAINER_NOME_RIDOTTO if self.ridotto else CONTAINER_NOME
            scopo = _scopo(self.ridotto)
            elenco = await self._chiama("elenco dei container", "GET", "/customers/containers")
            if isinstance(elenco, dict):
                elenco = elenco.get("containers") or []
            for c in elenco if isinstance(elenco, list) else []:
                if (
                    isinstance(c, dict)
                    and c.get("purpose") == scopo
                    and c.get("name") == nome
                    and str(c.get("state") or "ACTIVE").upper() == "ACTIVE"
                    and c.get("containerId")
                ):
                    self.container_id = str(c["containerId"])
                    break
            else:
                self.container_id = await self._crea_container(nome, scopo)
            self._salva_cambi({"container_id": self.container_id, "container_ridotto": self.ridotto})
            return self.container_id

    async def _crea_container(self, nome: str, scopo: str) -> str:
        descrittori = DESCRITTORI_RIDOTTI if self.ridotto else DESCRITTORI
        try:
            nuovo = await self._chiama(
                "nuovo container",
                "POST",
                "/customers/containers",
                corpo={"name": nome, "purpose": scopo, "technicalDescriptors": list(descrittori)},
            )
        except ErroreApi as err:
            if err.codice == "CU-124":
                raise ErroreApi(
                    "nuovo container",
                    err.codice,
                    "l'account ha già 10 container: cancellane uno nel portale CarData",
                    err.http,
                ) from err
            if err.codice in _ERRORI_DESCRITTORI and not self.ridotto:
                # BMW non accetta un descrittore: si riparte con quelli sicuri.
                self.ridotto = True
                return await self._crea_container(CONTAINER_NOME_RIDOTTO, _scopo(True))
            raise
        if not isinstance(nuovo, dict) or not nuovo.get("containerId"):
            raise ErroreRete("nuovo container: BMW non ha dato l'id")
        return str(nuovo["containerId"])

    # --- lo stato --------------------------------------------------------------------

    def _spaziatura(self) -> float:
        """Ogni quanto si può leggere un'auto perché la quota basti a tutte per un giorno."""
        return max(0.0, _GIORNO * self._quante / (QUOTA_GIORNALIERA - RISERVA) - _TOLLERANZA)

    async def stato(self, v: Vettura) -> StatoVettura:
        ultimo = self._ultimi.get(v.vin)
        if ultimo:
            quando, stato = ultimo
            if self._adesso() - quando < self._spaziatura() or not self.quota.disponibile():
                # Troppo presto per la quota di BMW: vale l'ultima lettura.
                return stato
        try:
            risposta = await self._telematica(v.vin)
        except ErroreRete:
            if ultimo and not self.quota.disponibile():
                return ultimo[1]
            raise
        stato = interpreta(risposta)
        self._ultimi[v.vin] = (self._adesso(), stato)
        return stato

    async def _telematica(self, vin: str) -> Any:
        for tentativo in (1, 2):
            container = await self.prepara_container()
            try:
                return await self._chiama(
                    "stato dell'auto",
                    "GET",
                    f"/customers/vehicles/{vin}/telematicData",
                    query={"containerId": container},
                )
            except ErroreApi as err:
                if tentativo == 2:
                    raise
                if err.codice in _ERRORI_CONTAINER or (err.http == 404 and err.codice is None):
                    # Il container non c'è più (cancellato dal portale): se ne fa un altro.
                    self.container_id = None
                    continue
                if err.codice in _ERRORI_DESCRITTORI and not self.ridotto:
                    # BMW rifiuta il container intero per un descrittore: quello ridotto.
                    self.container_id = None
                    self.ridotto = True
                    continue
                raise
        raise ErroreRete("stato dell'auto: BMW CarData non risponde")  # pragma: no cover


def _codice(risposta: Any) -> str | None:
    if isinstance(risposta, dict):
        for valore in (risposta.get("exveErrorId"), *risposta.values()):
            if isinstance(valore, str) and valore.startswith("CU-"):
                return valore
    return None


def _marchio(base: dict[str, Any]) -> str:
    grezzo = str(base.get("brand") or "").strip()
    return _MARCHI.get(grezzo.upper().replace(" ", "-"), grezzo or "BMW")


def _da_base(vin: str, base: dict[str, Any]) -> Vettura:
    marchio = _marchio(base)
    modello = str(base.get("modelName") or base.get("series") or base.get("modelRange") or "").strip()
    anno = None
    data = str(base.get("constructionDate") or "")
    if data[:4].isdigit():
        anno = int(data[:4])
    return Vettura(
        vin=vin,
        marca=ID,
        modello=modello or marchio,
        nome=f"{marchio} {modello}".strip() if modello else marchio,
        anno=anno,
        colore=str(base.get("colourCode") or ""),
        extra={"marchio": marchio, "propulsione": base.get("propulsionType")},
    )


def nota_di(v: Vettura) -> dict[str, Any]:
    """Quello che si salva di un'auto per non chiederlo a ogni avvio."""
    return {"vin": v.vin, "modello": v.modello, "nome": v.nome, "anno": v.anno, "colore": v.colore, **v.extra}


def _da_nota(n: dict[str, Any]) -> Vettura:
    extra = {k: val for k, val in n.items() if k not in ("vin", "modello", "nome", "anno", "colore")}
    return Vettura(
        vin=str(n["vin"]),
        marca=ID,
        modello=str(n.get("modello") or ""),
        nome=str(n.get("nome") or ""),
        anno=n.get("anno"),
        colore=str(n.get("colore") or ""),
        extra=extra,
    )
