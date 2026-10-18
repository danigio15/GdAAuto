"""Il filo con le API ufficiali di Volvo Cars (``api.volvocars.com``).

Volvo apre ai proprietari, gratis, tre API del suo portale per sviluppatori
(developer.volvocars.com):

- **Connected Vehicle API v2**: l'elenco delle auto, porte, serratura,
  finestrini, motore, contachilometri, statistiche di viaggio, gomme,
  avvisi, tagliando, e i comandi (chiudere, aprire, clima, clacson e luci);
- **Energy API v2** (e la v1, in dismissione): batteria, autonomia, cavo,
  stato, tipo, potenza e tempo della ricarica, limite di carica;
- **Location API v1**: dov'è l'auto (non in tutti i paesi).

Ogni chiamata vuole due cose: la **chiave VCC** dell'applicazione creata da
chi configura nel portale (intestazione ``vcc-api-key``) e un token OAuth2
del suo **Volvo ID**. Il token si ottiene col login nel browser
(``volvoid.eu.volvocars.com``, PKCE), con il client id e il client secret
che il portale dà quando l'applicazione si «pubblica»: la password non passa
mai da GdAAuto. Il refresh token ruota: chi crea il cliente gli dà una
funzione ``salva`` che lo conserva nella configurazione.

Il cloud tiene quello che l'auto gli manda da sola: leggerlo non la sveglia.
Volvo concede circa 10 000 chiamate al giorno per applicazione: una lettura
completa ne costa sette o otto, e le cose che cambiano piano (gomme,
avvisi, tagliando) si rileggono ogni mezz'ora. ``Quota`` tiene il conto.

Gli indirizzi, gli scope e i nomi dei campi sono quelli della documentazione
pubblica di Volvo, come li usa l'integrazione ufficiale ``volvo`` di Home
Assistant (Apache 2.0). La libreria ``volvocarsapi`` che quella integrazione
usa è GPL-3.0: se ne sono guardati solo i fatti del protocollo, il codice è
scritto da capo. Vedi RINGRAZIAMENTI.md.
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
from urllib.parse import parse_qs, urlencode, urlparse

import aiohttp

from ...vettura import StatoVettura, Vettura
from ..errori import CodiceSbagliato, ErroreApi, ErroreComando, ErroreCredenziali, ErroreMarca, ErroreRete
from .dati import comandi_da, ha_batteria, ha_serbatoio, interpreta, vettura_da

_LOGGER = logging.getLogger(__name__)

ID = "volvo"
NOME = "Volvo"

#: Il login del Volvo ID (PingFederate) e le API. Si leggono al momento della chiamata: le prove li cambiano.
AUTH = "https://volvoid.eu.volvocars.com"
API = "https://api.volvocars.com"
#: L'indirizzo di ritorno che le istruzioni di Home Assistant fanno registrare nel portale Volvo.
REDIRECT_URI = "https://my.home-assistant.io/redirect/oauth"

#: Tutti gli scope che l'integrazione di Home Assistant chiede: letture, comandi e posizione.
SCOPE = (
    "openid",
    "conve:battery_charge_level",
    "conve:brake_status",
    "conve:climatization_start_stop",
    "conve:command_accessibility",
    "conve:commands",
    "conve:diagnostics_engine_status",
    "conve:diagnostics_workshop",
    "conve:doors_status",
    "conve:engine_status",
    "conve:fuel_status",
    "conve:lock_status",
    "conve:odometer_status",
    "conve:trip_statistics",
    "conve:tyre_status",
    "conve:vehicle_relation",
    "conve:warnings",
    "conve:windows_status",
    "energy:capability:read",
    "energy:state:read",
    "conve:lock",
    "conve:unlock",
    "conve:engine_start_stop",
    "conve:honk_flash",
    "location:read",
)

#: Lo scope che ogni comando vuole: senza, il comando non si offre.
SCOPE_DEI_COMANDI = {
    "LOCK": "conve:lock",
    "LOCK_REDUCED_GUARD": "conve:lock",
    "UNLOCK": "conve:unlock",
    "HONK": "conve:honk_flash",
    "FLASH": "conve:honk_flash",
    "HONK_AND_FLASH": "conve:honk_flash",
    "CLIMATIZATION_START": "conve:climatization_start_stop",
    "CLIMATIZATION_STOP": "conve:climatization_start_stop",
    "ENGINE_START": "conve:engine_start_stop",
    "ENGINE_STOP": "conve:engine_start_stop",
}

#: I comandi di GdAAuto e quelli di Volvo che li fanno, nell'ordine in cui si preferiscono.
COMANDI_NEUTRI: dict[str, tuple[str, ...]] = {
    "chiudi": ("LOCK",),
    "apri": ("UNLOCK",),
    "trova": ("HONK_AND_FLASH", "FLASH", "HONK"),
    "clima": ("CLIMATIZATION_START",),
}

#: Gli esiti dei comandi che vogliono dire «fatto».
_RIUSCITI = ("COMPLETED", "DELIVERED", "SUCCESS")
#: E quelli che no, detti per bene.
_MOTIVI = {
    "TIMEOUT": "l'auto non ha risposto in tempo",
    "CONNECTION_FAILURE": "l'auto non è raggiungibile",
    "VEHICLE_IN_SLEEP": "l'auto dorme e non riceve comandi",
    "NOT_ALLOWED": "adesso il comando non è permesso",
    "CAR_ERROR": "l'auto ha rifiutato il comando",
    "UNKNOWN_CAR_ERROR": "l'auto ha rifiutato il comando",
    "REJECTED": "il comando è stato rifiutato",
    "CAR_IN_USE": "l'auto è in uso",
    "UNKNOWN": "l'esito non si sa",
}

_TIMEOUT = aiohttp.ClientTimeout(total=30)
_MARGINE = timedelta(minutes=1)
#: Le cose che cambiano piano si rileggono ogni mezz'ora.
_LENTE_OGNI_S = 30 * 60
#: Una risorsa che il cloud nega (scope mancante, paese senza posizione) si riprova fra sei ore.
_PAUSA_VIETATA_S = 6 * 3600

#: Le risorse della Connected Vehicle API lette a ogni giro, e quelle lette ogni mezz'ora.
VELOCI = ("doors", "windows", "engine-status", "odometer", "statistics", "command-accessibility")
LENTE = ("tyres", "diagnostics", "warnings", "brakes", "engine")

#: Il tetto di Volvo per applicazione e quanto se ne usa per leggere: il resto è per i comandi.
QUOTA_GIORNALIERA = 10_000
QUOTA_LETTURE = 9_500


def comandi_di(v: Vettura) -> set[str]:
    """I comandi che quell'auto ha detto di accettare (e che il login ha permesso)."""
    comandi = v.extra.get("comandi") or {}
    return {neutro for neutro, nomi in COMANDI_NEUTRI.items() if any(n in comandi for n in nomi)}


# --- il login nel browser ------------------------------------------------------------


def nuovo_verifier() -> tuple[str, str]:
    """Il segreto PKCE e la sua impronta S256."""
    verifier = secrets.token_urlsafe(64)[:96]
    impronta = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(impronta).rstrip(b"=").decode("ascii")


def indirizzo_login(client_id: str, redirect_uri: str, sfida: str, stato: str) -> str:
    """La pagina del Volvo ID da aprire nel browser."""
    q = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(SCOPE),
        "code_challenge": sfida,
        "code_challenge_method": "S256",
        "state": stato,
    }
    return f"{AUTH}/as/authorization.oauth2?{urlencode(q)}"


def leggi_incollato(testo: str) -> tuple[str, str | None]:
    """Il codice (e lo ``state``, se c'è) da quello che si incolla: l'indirizzo finale o solo il codice.

    Un indirizzo con ``error=`` (login annullato, scope non concessi) è un rifiuto.
    """
    testo = testo.strip().strip('"').strip("'")
    if not testo:
        raise CodiceSbagliato("Non è stato incollato niente")
    if "=" in testo and ("code" in testo or "error" in testo):
        parte = urlparse(testo).query or testo.split("?", 1)[-1]
        q = parse_qs(parte)
        if q.get("error"):
            descrizione = (q.get("error_description") or q["error"])[0]
            raise ErroreCredenziali(f"Il Volvo ID ha rifiutato il login: {descrizione}")
        if q.get("code"):
            return q["code"][0], (q.get("state") or [None])[0]
    if any(c in testo for c in " /?&"):
        raise CodiceSbagliato("Nell'indirizzo incollato non c'è il codice del login")
    return testo, None


def chi_e(token: dict[str, Any]) -> dict[str, str]:
    """L'id dell'account (e l'email, se c'è) dall'id_token o dall'access token, senza verificarli.

    Servono solo a riconoscere l'account e a dargli un titolo.
    """
    chi: dict[str, str] = {}
    for chiave in ("id_token", "access_token"):
        parti = str(token.get(chiave) or "").split(".")
        if len(parti) != 3:
            continue
        try:
            corpo = json.loads(base64.urlsafe_b64decode(parti[1] + "=" * (-len(parti[1]) % 4)))
        except (ValueError, TypeError):
            continue
        if not isinstance(corpo, dict):
            continue
        if "sub" not in chi and isinstance(corpo.get("sub"), str) and corpo["sub"]:
            chi["sub"] = corpo["sub"]
        if "email" not in chi and isinstance(corpo.get("email"), str) and "@" in corpo["email"]:
            chi["email"] = corpo["email"].strip().lower()
    return chi


def _basic(client_id: str, client_secret: str) -> str:
    return "Basic " + base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()


def _token_da(corpo: dict[str, Any], vecchio: dict[str, Any] | None = None) -> dict[str, Any]:
    vecchio = vecchio or {}
    try:
        durata = int(corpo.get("expires_in") or 1800)
    except (TypeError, ValueError):
        durata = 1800
    token = {
        "access_token": str(corpo["access_token"]),
        "refresh_token": corpo.get("refresh_token") or vecchio.get("refresh_token"),
        "scade": (datetime.now(UTC) + timedelta(seconds=durata)).isoformat(),
        "scope": corpo.get("scope") or vecchio.get("scope") or "",
    }
    if corpo.get("id_token"):
        token["id_token"] = corpo["id_token"]
    return token


async def chiedi_token(
    http: aiohttp.ClientSession,
    client_id: str,
    client_secret: str,
    parametri: dict[str, str],
    vecchio: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Una chiamata al token endpoint del Volvo ID: codice o refresh, è lo stesso."""
    intestazioni = {
        "Authorization": _basic(client_id, client_secret),
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    try:
        async with http.post(f"{AUTH}/as/token.oauth2", data=parametri, headers=intestazioni, timeout=_TIMEOUT) as r:
            stato = r.status
            testo = await r.text()
    except (aiohttp.ClientError, TimeoutError, OSError) as err:
        raise ErroreRete(f"Il login del Volvo ID non risponde: {err}") from err
    corpo = _json(testo)
    if stato >= 500 or stato == 429:
        raise ErroreRete(f"Il login del Volvo ID ha un guasto: HTTP {stato}")
    if stato >= 400 or "access_token" not in corpo:
        errore = str(corpo.get("error") or "")
        descrizione = corpo.get("error_description") or errore or f"HTTP {stato}"
        if errore == "invalid_grant" and parametri.get("grant_type") == "authorization_code":
            raise CodiceSbagliato(f"Il codice del login non vale più (scade in pochi minuti): {descrizione}")
        if errore == "invalid_client":
            raise ErroreCredenziali(f"Il Volvo ID non riconosce client id e client secret: {descrizione}")
        raise ErroreCredenziali(f"Il Volvo ID rifiuta l'accesso: {descrizione}")
    return _token_da(corpo, vecchio)


async def scambia_codice(
    http: aiohttp.ClientSession,
    client_id: str,
    client_secret: str,
    codice: str,
    verifier: str,
    redirect_uri: str = REDIRECT_URI,
) -> dict[str, Any]:
    """Dal codice del login ai token (con l'id_token, se il Volvo ID lo dà)."""
    return await chiedi_token(
        http,
        client_id,
        client_secret,
        {
            "grant_type": "authorization_code",
            "code": codice,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        },
    )


# --- il conto delle chiamate -----------------------------------------------------------


class Quota:
    """Le chiamate delle ultime 24 ore, per applicazione (cioè per chiave VCC)."""

    def __init__(self, orologio: Callable[[], float] = time.monotonic, limite: int = QUOTA_LETTURE) -> None:
        self._orologio = orologio
        self.limite = limite
        self._fatte: deque[float] = deque()

    @property
    def usate(self) -> int:
        adesso = self._orologio()
        while self._fatte and adesso - self._fatte[0] >= 24 * 3600:
            self._fatte.popleft()
        return len(self._fatte)

    def prendi(self, cosa: str, comando: bool = False) -> None:
        """Una chiamata in più. Le letture si fermano prima del tetto: il resto resta ai comandi."""
        if self.usate >= (QUOTA_GIORNALIERA if comando else self.limite):
            raise ErroreRete(f"{cosa}: questa chiave VCC ha già fatto {self.usate} chiamate nelle ultime 24 ore")
        self._fatte.append(self._orologio())


_QUOTE: dict[str, Quota] = {}


def quota_di(api_key: str) -> Quota:
    """La stessa chiave in due voci conta una volta sola."""
    chiave = hashlib.sha256(api_key.encode()).hexdigest()
    return _QUOTE.setdefault(chiave, Quota())


def _json(testo: str) -> dict[str, Any]:
    if not testo:
        return {}
    try:
        corpo = json.loads(testo)
    except ValueError:
        return {"message": testo[:200]}
    return corpo if isinstance(corpo, dict) else {"data": corpo}


def _messaggio(corpo: dict[str, Any]) -> str:
    errore = corpo.get("error")
    if isinstance(errore, dict):
        return " ".join(str(errore.get(k)) for k in ("message", "description") if errore.get(k)).strip()
    return str(corpo.get("message") or corpo.get("error_description") or errore or "").strip()


def _codice(corpo: dict[str, Any]) -> Any:
    errore = corpo.get("error")
    if isinstance(errore, dict):
        return errore.get("code") or corpo.get("status")
    return corpo.get("statusCode") or corpo.get("status") or errore


# --- il cliente --------------------------------------------------------------------------


class ClienteVolvo:
    def __init__(
        self,
        sessione_http: aiohttp.ClientSession,
        api_key: str,
        client_id: str,
        client_secret: str,
        token: dict[str, Any],
        salva: Callable[[dict[str, Any]], None] | None = None,
        *,
        quota: Quota | None = None,
        orologio: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = sessione_http
        self.api_key = api_key.strip()
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.token = dict(token)
        self._salva = salva
        self._quota = quota or quota_di(self.api_key)
        self._orologio = orologio
        self._serratura = asyncio.Lock()
        self._un_comando = asyncio.Lock()
        #: per auto: le letture lente e quando, le risorse negate fino a quando, cosa l'Energy API sa fare
        self._lente: dict[str, tuple[dict[str, Any], float]] = {}
        self._vietate: dict[tuple[str, str], float] = {}
        self._energia_v2: dict[str, bool] = {}
        self._senza_v1: set[str] = set()
        self._v2l_visto: set[str] = set()

    # --- i token ------------------------------------------------------------

    async def _token_valido(self) -> str:
        async with self._serratura:
            scade = self.token.get("scade")
            try:
                scaduto = not scade or datetime.fromisoformat(scade) - _MARGINE < datetime.now(UTC)
            except ValueError:
                scaduto = True
            if scaduto or not self.token.get("access_token"):
                await self._rinnova_adesso()
            return str(self.token["access_token"])

    async def _rinnova(self, se_ancora: str) -> None:
        """Rinnova, a meno che un'altra lettura non l'abbia appena fatto."""
        async with self._serratura:
            if self.token.get("access_token") == se_ancora:
                await self._rinnova_adesso()

    async def _rinnova_adesso(self) -> None:
        if not self.token.get("refresh_token"):
            raise ErroreCredenziali("Manca il refresh token del Volvo ID: serve rientrare")
        nuovo = await chiedi_token(
            self._http,
            self.client_id,
            self.client_secret,
            {"grant_type": "refresh_token", "refresh_token": str(self.token["refresh_token"])},
            self.token,
        )
        nuovo.pop("id_token", None)
        self.token = nuovo
        if self._salva:
            self._salva(dict(self.token))

    # --- la rete -------------------------------------------------------------

    async def _chiedi(
        self, metodo: str, cosa: str, percorso: str, *, comando: bool = False, ripeti: bool = True
    ) -> dict[str, Any]:
        self._quota.prendi(cosa, comando)
        token = await self._token_valido()
        intestazioni = {
            "Authorization": f"Bearer {token}",
            "vcc-api-key": self.api_key,
            "Accept": "application/json",
        }
        if metodo == "POST":
            intestazioni["Content-Type"] = "application/json"
        try:
            async with self._http.request(metodo, f"{API}{percorso}", headers=intestazioni, timeout=_TIMEOUT) as r:
                http = r.status
                testo = await r.text()
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise ErroreRete(f"{cosa}: Volvo non risponde: {err}") from err
        corpo = _json(testo)
        messaggio = _messaggio(corpo)
        if http == 401:
            if "subscription key" in messaggio.lower() or "api key" in messaggio.lower():
                raise ErroreCredenziali(f"Volvo rifiuta la chiave VCC: {messaggio}")
            if ripeti:
                # Un token appena ruotato a volte non vale ancora: si rinnova e si riprova una volta.
                await self._rinnova(token)
                return await self._chiedi(metodo, cosa, percorso, comando=comando, ripeti=False)
            raise ErroreCredenziali(f"{cosa}: Volvo rifiuta il token ({messaggio or 'HTTP 401'})")
        if http == 404:
            return {}  # quell'auto non ha quella risorsa
        if http == 429:
            raise ErroreRete(f"{cosa}: Volvo dice troppe richieste per oggi ({messaggio or 'HTTP 429'})")
        if http >= 500:
            raise ErroreRete(f"{cosa}: Volvo ha un guasto (HTTP {http})")
        if comando and http == 422:
            raise ErroreComando(f"Volvo: {cosa} non si può fare adesso ({messaggio or 'HTTP 422'})")
        if http >= 400:
            raise ErroreApi(cosa, _codice(corpo), messaggio or testo[:200], http)
        return corpo

    async def _risorsa(self, vin: str, nome: str, percorso: str) -> dict[str, Any] | None:
        """Una lettura facoltativa: ``None`` se l'auto non ce l'ha o il login non la permette."""
        fino = self._vietate.get((vin, nome))
        if fino is not None and self._orologio() < fino:
            return None
        try:
            corpo = await self._chiedi("GET", nome, percorso)
        except ErroreApi as err:
            if err.http == 403:
                _LOGGER.debug("Volvo nega «%s» per questa auto: %s", nome, err.messaggio)
                self._vietate[(vin, nome)] = self._orologio() + _PAUSA_VIETATA_S
                return None
            raise
        dati = corpo.get("data")
        return dati if isinstance(dati, dict) else (corpo or None)

    # --- leggere -----------------------------------------------------------------

    async def accedi(self) -> None:
        await self._token_valido()

    async def vetture(self) -> list[Vettura]:
        try:
            elenco = await self._chiedi("GET", "elenco delle auto", "/connected-vehicle/v2/vehicles")
        except ErroreApi as err:
            if err.http == 403:
                raise ErroreCredenziali(
                    f"Volvo non lascia elencare le auto: controlla la chiave VCC e gli scope ({err.messaggio})"
                ) from err
            raise
        vins = [str(x["vin"]) for x in elenco.get("data") or [] if isinstance(x, dict) and x.get("vin")]
        concessi = set(str(self.token.get("scope") or "").split())
        risultato: list[Vettura] = []
        for vin in vins:
            try:
                dettagli = (
                    await self._chiedi("GET", "dettagli dell'auto", f"/connected-vehicle/v2/vehicles/{vin}")
                ).get("data") or {}
            except ErroreApi as err:
                _LOGGER.debug("Dettagli della Volvo %s non letti: %s", vin[-4:], err)
                dettagli = {}
            try:
                comandi = comandi_da(
                    await self._chiedi("GET", "comandi", f"/connected-vehicle/v2/vehicles/{vin}/commands")
                )
            except ErroreApi as err:
                _LOGGER.debug("Comandi della Volvo %s non letti: %s", vin[-4:], err)
                comandi = {}
            if concessi:
                comandi = {n: p for n, p in comandi.items() if SCOPE_DEI_COMANDI.get(n, "openid") in concessi}
            v = vettura_da({**dettagli, "vin": vin}, ID, comandi)
            if not ha_batteria(v.extra.get("carburante")):
                _LOGGER.info("La Volvo %s va solo a carburante: GdAAuto non la porta", v.modello)
                continue
            risultato.append(v)
        return risultato

    async def stato(self, v: Vettura) -> StatoVettura:
        vin = v.vin
        cv = f"/connected-vehicle/v2/vehicles/{vin}"
        percorsi = {nome: f"{cv}/{nome}" for nome in VELOCI}
        carburante = v.extra.get("carburante")
        if ha_batteria(carburante):
            if await self._energia_supportata(vin):
                percorsi["energia"] = f"/energy/v2/vehicles/{vin}/state"
            elif vin not in self._senza_v1:
                percorsi["ricarica_v1"] = f"/energy/v1/vehicles/{vin}/recharge-status"
        if ha_serbatoio(carburante):
            percorsi["fuel"] = f"{cv}/fuel"
        percorsi["location"] = f"/location/v1/vehicles/{vin}/location"

        letture = await self._tutte(vin, percorsi)
        if "ricarica_v1" in percorsi and not letture.get("ricarica_v1"):
            self._senza_v1.add(vin)  # la v1 non c'è (o non è permessa): non si chiede più
        letture.update(await self._lente_di(vin))
        stato = interpreta(letture, v)
        # Il V2L si dice solo per le auto che l'hanno fatto vedere almeno una volta.
        if stato.v2l_attivo:
            self._v2l_visto.add(vin)
        elif vin in self._v2l_visto and stato.stato_ricarica is not None:
            stato.v2l_attivo = False
        return stato

    async def _tutte(self, vin: str, percorsi: dict[str, str]) -> dict[str, Any]:
        """Le letture insieme. Credenziali rifiutate fermano tutto; un guasto solo se nessuna è andata."""
        risultati = await asyncio.gather(
            *(self._risorsa(vin, nome, percorso) for nome, percorso in percorsi.items()), return_exceptions=True
        )
        letture: dict[str, Any] = {}
        errori: list[ErroreMarca] = []
        for nome, r in zip(percorsi, risultati, strict=True):
            if isinstance(r, ErroreCredenziali):
                raise r
            if isinstance(r, ErroreMarca):
                _LOGGER.debug("Volvo, «%s» non letto: %s", nome, r)
                errori.append(r)
            elif isinstance(r, BaseException):
                raise r
            elif r:
                letture[nome] = r
        if errori and len(errori) == len(percorsi):
            raise errori[0]
        return letture

    async def _energia_supportata(self, vin: str) -> bool:
        """L'Energy API v2 la sa leggere? Si chiede una volta sola per auto."""
        if vin in self._energia_v2:
            return self._energia_v2[vin]
        try:
            corpo = await self._chiedi("GET", "capacità energetiche", f"/energy/v2/vehicles/{vin}/capabilities")
        except ErroreRete:
            return True  # si saprà al prossimo giro
        except ErroreApi as err:
            _LOGGER.debug("Capacità energetiche della Volvo non lette: %s", err)
            corpo = {}
        capacita = corpo.get("getEnergyState") or corpo.get("data") or corpo
        supportata = bool(capacita.get("isSupported", True)) if isinstance(capacita, dict) else True
        self._energia_v2[vin] = supportata
        return supportata

    async def _lente_di(self, vin: str) -> dict[str, Any]:
        """Gomme, avvisi, freni, tagliando: ogni mezz'ora. Se una non va, resta quella di prima."""
        letture, quando = self._lente.get(vin, ({}, 0.0))
        if quando and self._orologio() - quando < _LENTE_OGNI_S:
            return letture
        letture = dict(letture)
        cv = f"/connected-vehicle/v2/vehicles/{vin}"
        for nome in LENTE:
            try:
                r = await self._risorsa(vin, nome, f"{cv}/{nome}")
            except ErroreCredenziali:
                raise
            except ErroreMarca as err:
                _LOGGER.debug("Volvo, «%s» non letto: %s", nome, err)
                continue
            if r:
                letture[nome] = r
        self._lente[vin] = (letture, self._orologio())
        return letture

    # --- comandare -----------------------------------------------------------------

    async def _comanda(self, v: Vettura, nomi: tuple[str, ...], cosa: str) -> None:
        comandi = v.extra.get("comandi") or {}
        nome = next((n for n in nomi if n in comandi), None)
        if nome is None:
            raise ErroreComando(f"La {v.titolo} non accetta «{cosa}» dalle API Volvo")
        # Un comando alla volta: l'auto ne esegue uno solo.
        async with self._un_comando:
            corpo = await self._chiedi(
                "POST", cosa, f"/connected-vehicle/v2/vehicles/{v.vin}/commands/{comandi[nome]}", comando=True
            )
        esito = corpo.get("data") if isinstance(corpo.get("data"), dict) else corpo
        stato = str(esito.get("invokeStatus") or esito.get("invoke_status") or "").upper()
        if stato in _RIUSCITI:
            return
        motivo = _MOTIVI.get(stato) or (stato.lower().replace("_", " ") if stato else "nessuna risposta")
        dettaglio = f" ({esito['message']})" if esito.get("message") else ""
        raise ErroreComando(f"{v.titolo}: {cosa} non riuscito, {motivo}{dettaglio}")

    async def chiudi(self, v: Vettura, _pin: str | None = None) -> None:
        await self._comanda(v, COMANDI_NEUTRI["chiudi"], "chiusura")

    async def apri(self, v: Vettura, _pin: str | None = None) -> None:
        await self._comanda(v, COMANDI_NEUTRI["apri"], "apertura")

    async def trova(self, v: Vettura, _pin: str | None = None) -> None:
        await self._comanda(v, COMANDI_NEUTRI["trova"], "clacson e luci")

    async def clima(self, v: Vettura, acceso: bool, _pin: str | None = None, _temperatura: int = 22) -> None:
        """Accende o spegne il clima. La temperatura non si sceglie: è quella impostata nell'auto."""
        if acceso:
            await self._comanda(v, ("CLIMATIZATION_START",), "accensione del clima")
        else:
            await self._comanda(v, ("CLIMATIZATION_STOP",), "spegnimento del clima")

    async def ricarica(self, v: Vettura, _avvia: bool, _pin: str | None = None) -> None:
        raise ErroreComando("Le API Volvo non avviano né fermano la ricarica: usa la wallbox")

    async def limite_ricarica(self, v: Vettura, _percento: int, _pin: str | None = None) -> None:
        raise ErroreComando("Le API Volvo leggono il limite di carica ma non lo cambiano")

    async def destinazione(self, v: Vettura, nome: str, lat: float, lon: float, indirizzo: str = "") -> None:
        raise ErroreComando("Le API Volvo non mandano destinazioni all'auto")
