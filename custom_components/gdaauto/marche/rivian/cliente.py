"""Il filo col cloud Rivian: GraphQL su ``rivian.com/api/gql``.

Il cloud dell'app è GraphQL (``POST`` di ``{operationName, query,
variables}``) su due indirizzi: ``/gateway/graphql`` per account, auto e
comandi, ``/chrg/user/graphql`` per le ricariche. Si entra così:

1. ``createCsrfToken`` → un ``csrfToken`` e un ``appSessionToken`` (la
   sessione dell'app, intestazione ``A-Sess``). Non chiede credenziali, e se
   ne chiede uno nuovo a ogni avvio e quando il cloud dice ``UNAUTHENTICATED``;
2. ``login(email, password)`` → ``accessToken``, ``refreshToken`` e
   ``userSessionToken`` (la sessione dell'utente, intestazione ``U-Sess``),
   oppure, se l'account ha la verifica in due passaggi, un ``otpToken``: Rivian
   manda un codice per SMS, e ``loginWithOTP(email, codice, otpToken)``
   conclude. La password serve solo qui: GdAAuto tiene la ``U-Sess``.

Se anche con una sessione dell'app nuova il cloud rifiuta la ``U-Sess``,
bisogna rientrare. Gli errori arrivano in ``errors[].extensions.code``
(``UNAUTHENTICATED``, ``RATE_LIMIT``, ``BAD_CURRENT_PASSWORD``…).

Si legge ``vehicleState`` (l'ultimo dato che l'auto ha mandato: leggere non
la sveglia) e, ogni mezz'ora, l'ultima ricarica. I comandi (``sendVehicleCommand``)
vanno firmati con la chiave di un telefono abbinato all'auto (``chiave.py``):
senza quella chiave GdAAuto legge soltanto.

Indirizzi, intestazioni e query vengono da bretterer/rivian-python-client
(MIT): vedi RINGRAZIAMENTI.md.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
import json
import logging
import time
from typing import Any
import uuid

import aiohttp

from ...vettura import StatoVettura, Vettura
from ..errori import (
    CodiceInviato,
    CodiceSbagliato,
    ErroreApi,
    ErroreCertificato,
    ErroreComando,
    ErroreCredenziali,
    ErroreRete,
)
from . import chiave as chiavi
from .dati import ID, frammento, interpreta, ultima_ricarica, vetture_da

_LOGGER = logging.getLogger(__name__)

NOME = "Rivian"

BASE_URL = "https://rivian.com/api/gql"
_GATEWAY = "/gateway/graphql"
_RICARICHE = "/chrg/user/graphql"

_CLIENTE_APOLLO = "com.rivian.ios.consumer-apollo-ios"
_INTESTAZIONI = {
    "User-Agent": "RivianApp/707 CFNetwork/1237 Darwin/20.4.0",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Apollographql-Client-Name": _CLIENTE_APOLLO,
}
_TIMEOUT = aiohttp.ClientTimeout(total=30)

#: dopo un «RATE_LIMIT», quanto si sta zitti
FERMO_QUOTA = 15 * 60
#: ogni quanto si rilegge l'ultima ricarica
OGNI_STORICO = 30 * 60
#: quanto si aspetta che l'auto si svegli prima di un comando, e ogni quanto si guarda
ATTESA_SVEGLIA = 30.0
PASSO_SVEGLIA = 5.0
#: per quanto un'auto appena svegliata resta sveglia senza altri comandi
RESTA_SVEGLIA = 5 * 60
#: quanto resta valido un codice mandato per SMS
_ATTESA_CODICE = 10 * 60

_Q_CSRF = "mutation CreateCSRFToken { createCsrfToken { __typename csrfToken appSessionToken } }"
_Q_LOGIN = (
    "mutation Login($email: String!, $password: String!) { login(email: $email, password: $password) "
    "{ __typename ... on MobileLoginResponse { __typename accessToken refreshToken userSessionToken } "
    "... on MobileMFALoginResponse { __typename otpToken } } }"
)
_Q_OTP = (
    "mutation LoginWithOTP($email: String!, $otpCode: String!, $otpToken: String!) "
    "{ loginWithOTP(email: $email, otpCode: $otpCode, otpToken: $otpToken) "
    "{ __typename ... on MobileLoginResponse { __typename accessToken refreshToken userSessionToken } } }"
)
_Q_UTENTE = (
    "query getUserInfo { currentUser { __typename id "
    "vehicles { id vin name vas { __typename vasVehicleId vehiclePublicKey } roles state createdAt updatedAt "
    "vehicle { __typename id vin modelYear make model "
    "vehicleState { supportedFeatures { __typename name status } } } } "
    "registrationChannels { type } "
    "enrolledPhones { __typename vas { __typename vasPhoneId publicKey } "
    "enrolled { __typename deviceType deviceName vehicleId identityId shortName } } } }"
)
_Q_COMANDO = (
    "mutation sendVehicleCommand($attrs: VehicleCommandAttributes!) "
    "{ sendVehicleCommand(attrs: $attrs) { __typename id command state } }"
)
_Q_RICARICHE = (
    "query getCompletedSessionSummaries { getCompletedSessionSummaries "
    "{ chargerType startInstant endInstant totalEnergyKwh rangeAddedKm vehicleId transactionId } }"
)


def _q_stato() -> str:
    return f"query GetVehicleState($vehicleID: String!) {{ vehicleState(id: $vehicleID) {frammento()} }}"


class ErroreRivian(ErroreApi):
    """Il cloud ha risposto con un errore GraphQL suo."""


def _errore_di(corpo: dict[str, Any]) -> tuple[str, str, str]:
    """(codice, motivo, messaggio) del primo errore GraphQL."""
    for errore in corpo.get("errors") or []:
        if isinstance(errore, dict):
            estensioni = errore.get("extensions") or {}
            return (
                str(estensioni.get("code") or ""),
                str(estensioni.get("reason") or ""),
                str(errore.get("message") or ""),
            )
    return "", "", ""


async def graphql(
    http: aiohttp.ClientSession,
    url: str,
    cosa: str,
    operazione: str,
    query: str,
    variabili: dict[str, Any] | None,
    intestazioni: dict[str, str],
) -> dict[str, Any]:
    """Una richiesta GraphQL: restituisce ``data``, o solleva l'errore giusto.

    ``UNAUTHENTICATED`` diventa ``ErroreCredenziali`` (chi chiama decide se
    rinnovare la sessione dell'app e riprovare); i guasti ``ErroreRete``.
    """
    corpo_richiesta = {"operationName": operazione, "query": query, "variables": variabili}
    try:
        async with http.post(
            url,
            json=corpo_richiesta,
            headers={**_INTESTAZIONI, "dc-cid": f"m-ios-{uuid.uuid4()}", **intestazioni},
            timeout=_TIMEOUT,
        ) as r:
            stato_http = r.status
            testo = await r.text()
    except (aiohttp.ClientError, TimeoutError, OSError) as err:
        raise ErroreRete(f"{cosa}: Rivian non risponde: {err}") from err
    try:
        corpo = json.loads(testo) if testo.strip() else None
    except ValueError:
        corpo = None
    if not isinstance(corpo, dict):
        if stato_http == 401:
            raise ErroreCredenziali(f"{cosa}: Rivian rifiuta la sessione (HTTP 401)")
        if stato_http == 429:
            raise ErroreRivian(cosa, "RATE_LIMIT", "troppe richieste al cloud", stato_http)
        raise ErroreRete(f"{cosa}: Rivian risponde una cosa che non si capisce (HTTP {stato_http})")
    codice, motivo, messaggio = _errore_di(corpo)
    if codice:
        if (codice, motivo) == ("BAD_USER_INPUT", "INVALID_OTP"):
            raise CodiceSbagliato("Il codice di Rivian non va: controlla l'ultimo SMS")
        if (codice, motivo) == ("UNAUTHENTICATED", "OTP_TOKEN_EXPIRED"):
            raise CodiceSbagliato("Il codice di Rivian è scaduto: lascia vuoto il campo per riceverne uno nuovo")
        if codice in ("UNAUTHENTICATED", "BAD_CURRENT_PASSWORD"):
            raise ErroreCredenziali(f"{cosa}: Rivian rifiuta l'accesso ({codice} {motivo})".strip())
        if codice == "SESSION_MANAGER_ERROR":
            raise ErroreCredenziali(f"{cosa}: l'account Rivian è bloccato per un po' (troppi tentativi)")
        if codice == "RATE_LIMIT":
            raise ErroreRivian(cosa, codice, "troppe richieste al cloud: riprova fra un quarto d'ora", stato_http)
        dati = corpo.get("data")
        if isinstance(dati, dict) and any(v is not None for v in dati.values()):
            # Un errore su un campo solo: il resto della risposta vale.
            _LOGGER.debug("%s: Rivian risponde con un errore parziale %s %s", cosa, codice, messaggio)
            return dati
        if codice == "INTERNAL_SERVER_ERROR" or stato_http >= 500:
            raise ErroreRete(f"{cosa}: Rivian ha un guasto ({codice}: {messaggio})")
        raise ErroreRivian(cosa, codice, f"{motivo} {messaggio}".strip(), stato_http)
    if stato_http >= 500:
        raise ErroreRete(f"{cosa}: Rivian risponde HTTP {stato_http}")
    if stato_http == 401:
        raise ErroreCredenziali(f"{cosa}: Rivian rifiuta la sessione (HTTP 401)")
    dati = corpo.get("data")
    if not isinstance(dati, dict):
        raise ErroreRete(f"{cosa}: Rivian non dà dati (HTTP {stato_http})")
    return dati


async def sessione_app(http: aiohttp.ClientSession, base: str = BASE_URL) -> tuple[str, str]:
    """(csrfToken, appSessionToken): la sessione dell'app, senza credenziali."""
    dati = await graphql(http, base + _GATEWAY, "sessione dell'app", "CreateCSRFToken", _Q_CSRF, None, {})
    csrf = dati.get("createCsrfToken") or {}
    if not csrf.get("csrfToken") or not csrf.get("appSessionToken"):
        raise ErroreRete("sessione dell'app: Rivian non ha dato i token")
    return str(csrf["csrfToken"]), str(csrf["appSessionToken"])


@dataclass
class _InSospeso:
    """Un login che aspetta il codice dell'SMS."""

    otp: str = field(repr=False)
    csrf: str = field(repr=False)
    app: str = field(repr=False)
    scade: float


#: I login in attesa del codice, fra un invio del modulo e il successivo (solo in memoria).
_IN_SOSPESO: dict[str, _InSospeso] = {}


def _token_da(risposta: Any) -> dict[str, str]:
    if not isinstance(risposta, dict) or not risposta.get("userSessionToken"):
        raise ErroreRete("login: Rivian non ha dato la sessione dell'utente")
    return {
        "u_sess": str(risposta["userSessionToken"]),
        "access_token": str(risposta.get("accessToken") or ""),
        "refresh_token": str(risposta.get("refreshToken") or ""),
    }


async def entra(
    http: aiohttp.ClientSession, email: str, password: str, *, codice: str = "", base: str = BASE_URL
) -> dict[str, str]:
    """Il login con la password, verifica per SMS compresa.

    Restituisce i token da salvare. Se Rivian chiede il codice solleva
    ``CodiceInviato``: si richiama con lo stesso account e il ``codice``.
    """
    email = email.strip()
    chiave = f"{base}|{email.lower()}"
    adesso = time.monotonic()
    for k in [k for k, s in _IN_SOSPESO.items() if s.scade < adesso]:
        del _IN_SOSPESO[k]
    codice = codice.strip()
    sospeso = _IN_SOSPESO.get(chiave)
    if sospeso and codice:
        intestazioni = {"Csrf-Token": sospeso.csrf, "A-Sess": sospeso.app}
        try:
            dati = await graphql(
                http,
                base + _GATEWAY,
                "verifica del codice",
                "LoginWithOTP",
                _Q_OTP,
                {"email": email, "otpCode": codice, "otpToken": sospeso.otp},
                intestazioni,
            )
        except CodiceSbagliato as err:
            if "scaduto" in str(err):
                _IN_SOSPESO.pop(chiave, None)
            raise
        _IN_SOSPESO.pop(chiave, None)
        return {**_token_da(dati.get("loginWithOTP")), "csrf": sospeso.csrf, "a_sess": sospeso.app}
    csrf, app = await sessione_app(http, base)
    dati = await graphql(
        http,
        base + _GATEWAY,
        "login",
        "Login",
        _Q_LOGIN,
        {"email": email, "password": password},
        {"Csrf-Token": csrf, "A-Sess": app},
    )
    risposta = dati.get("login") or {}
    if isinstance(risposta, dict) and risposta.get("otpToken"):
        _IN_SOSPESO[chiave] = _InSospeso(str(risposta["otpToken"]), csrf, app, adesso + _ATTESA_CODICE)
        raise CodiceInviato(
            "Rivian chiede la verifica in due passaggi: ha mandato un codice per SMS. "
            "Scrivilo nel campo «codice» e conferma di nuovo."
        )
    return {**_token_da(risposta), "csrf": csrf, "a_sess": app}


#: I comandi di GdAAuto → quelli di Rivian
COMANDI = {
    "chiudi": "LOCK_ALL_CLOSURES_FEEDBACK",
    "apri": "UNLOCK_ALL_CLOSURES",
    "trova": "HONK_AND_FLASH_LIGHTS",
    "clima_acceso": "VEHICLE_CABIN_PRECONDITION_ENABLE",
    "clima_spento": "VEHICLE_CABIN_PRECONDITION_DISABLE",
    "temperatura": "CABIN_PRECONDITIONING_SET_TEMP",
    "ricarica_avvia": "START_CHARGING",
    "ricarica_ferma": "STOP_CHARGING",
    "limite": "CHARGING_LIMITS",
    "sveglia": "WAKE_VEHICLE",
}


def comandi_di(v: Vettura) -> set[str]:
    """Solo le auto per cui la chiave del telefono è registrata si comandano."""
    if not v.extra.get("identita") or not v.extra.get("telefono") or not v.extra.get("chiave_auto"):
        return set()
    funzioni = set(v.extra.get("funzioni") or [])
    comandi = {"chiudi", "apri", "trova", "clima"}
    # Un'auto che non dice le sue funzioni (le Gen 2, a volte) prova tutto.
    if not funzioni or "CHARG_CMD" in funzioni:
        comandi.add("ricarica")
    if not funzioni or "CHARG_SLIDER" in funzioni:
        comandi.add("limite_ricarica")
    return comandi


class ClienteRivian:
    def __init__(
        self,
        sessione_http: aiohttp.ClientSession,
        token: dict[str, Any],
        salva: Callable[[dict[str, Any]], None] | None = None,
        *,
        chiave_telefono: str = "",
        base_url: str = BASE_URL,
    ) -> None:
        self._http = sessione_http
        self.token: dict[str, Any] = dict(token)
        self._salva = salva
        self._base = base_url.rstrip("/")
        self._chiave = None
        if chiave_telefono.strip():
            try:
                self._chiave = chiavi.carica(chiave_telefono)
            except ErroreCertificato as err:
                # Controllata nella configurazione: qui una chiave rovinata toglie i comandi, non la lettura.
                _LOGGER.warning("Chiave del telefono Rivian inutilizzabile: %s", err)
        self.chiave_pubblica = chiavi.pubblica(self._chiave) if self._chiave else ""
        self._serratura_sessione = asyncio.Lock()
        self._serratura_comandi = asyncio.Lock()
        self._ultimo: dict[str, StatoVettura] = {}
        self._storici: dict[str, tuple[dict[str, Any], float]] = {}
        self._fermo_fino = 0.0
        #: fino a quando un'auto appena svegliata si considera sveglia
        self._sveglia_fino: dict[str, float] = {}
        self.id_utente = ""

    # --- le sessioni -------------------------------------------------------

    async def _nuova_sessione_app(self) -> None:
        async with self._serratura_sessione:
            csrf, app = await sessione_app(self._http, self._base)
            self.token.update(csrf=csrf, a_sess=app)
            if self._salva:
                self._salva(dict(self.token))

    async def _chiedi(
        self,
        cosa: str,
        operazione: str,
        query: str,
        variabili: dict[str, Any] | None,
        *,
        csrf: bool = False,
        ricariche: bool = False,
        di_nuovo: bool = True,
    ) -> dict[str, Any]:
        if not self.token.get("u_sess"):
            raise ErroreCredenziali("Manca la sessione Rivian: serve rientrare")
        if not self.token.get("a_sess"):
            await self._nuova_sessione_app()
        intestazioni = {"A-Sess": str(self.token["a_sess"]), "U-Sess": str(self.token["u_sess"])}
        if csrf:
            intestazioni["Csrf-Token"] = str(self.token.get("csrf") or "")
        url = self._base + (_RICARICHE if ricariche else _GATEWAY)
        try:
            return await graphql(self._http, url, cosa, operazione, query, variabili, intestazioni)
        except ErroreCredenziali:
            if not di_nuovo:
                raise
            # La sessione dell'app è scaduta: se ne chiede una nuova e si riprova una volta.
            await self._nuova_sessione_app()
            return await self._chiedi(
                cosa, operazione, query, variabili, csrf=csrf, ricariche=ricariche, di_nuovo=False
            )
        except ErroreRivian as err:
            if err.codice == "RATE_LIMIT":
                self._fermo_fino = time.monotonic() + FERMO_QUOTA
            raise

    # --- entrare e leggere -------------------------------------------------

    async def accedi(self) -> None:
        """A ogni avvio una sessione dell'app nuova; la sessione dell'utente resta."""
        if not self.token.get("u_sess"):
            raise ErroreCredenziali("Manca la sessione Rivian: serve rientrare")
        await self._nuova_sessione_app()

    async def vetture(self) -> list[Vettura]:
        dati = await self._chiedi("elenco delle auto", "getUserInfo", _Q_UTENTE, None)
        utente = dati.get("currentUser")
        if not isinstance(utente, dict):
            raise ErroreRete("elenco delle auto: Rivian non dice chi è l'utente")
        self.id_utente = str(utente.get("id") or "")
        return vetture_da(utente, self.chiave_pubblica)

    async def _stato_grezzo(self, v: Vettura) -> dict[str, Any]:
        dati = await self._chiedi(
            "stato dell'auto", "GetVehicleState", _q_stato(), {"vehicleID": str(v.extra.get("id") or v.vin)}
        )
        stato = dati.get("vehicleState")
        if not isinstance(stato, dict):
            raise ErroreApi("stato dell'auto", None, f"Rivian non dà lo stato di {v.titolo}")
        return stato

    async def stato(self, v: Vettura) -> StatoVettura:
        if time.monotonic() < self._fermo_fino and (ultimo := self._ultimo.get(v.vin) or v.stato) is not None:
            # Rivian ha detto «troppe richieste»: per un quarto d'ora si tiene l'ultima lettura.
            return ultimo
        s = interpreta(await self._stato_grezzo(v))
        for campo, valore in (await self._storico(v)).items():
            setattr(s, campo, valore)
        self._ultimo[v.vin] = s
        return s

    async def _storico(self, v: Vettura) -> dict[str, Any]:
        """L'ultima ricarica, riletta ogni mezz'ora. Facoltativa: se non va, lo stato arriva lo stesso."""
        campi, quando = self._storici.get(v.vin, ({}, 0.0))
        if quando and time.monotonic() - quando < OGNI_STORICO:
            return campi
        try:
            dati = await self._chiedi(
                "ultime ricariche", "getCompletedSessionSummaries", _Q_RICARICHE, None, ricariche=True
            )
            campi = ultima_ricarica(dati.get("getCompletedSessionSummaries"), str(v.extra.get("id") or ""))
        except ErroreCredenziali:
            raise
        except (ErroreApi, ErroreRete) as err:
            _LOGGER.debug("Ultime ricariche di %s non lette: %s", v.titolo, err)
        self._storici[v.vin] = (campi, time.monotonic())
        return campi

    # --- comandare -----------------------------------------------------------

    async def _invia(self, v: Vettura, comando: str, parametri: dict[str, Any] | None = None) -> str:
        if self._chiave is None or not comandi_di(v):
            raise ErroreComando(
                f"{v.titolo}: per comandarla serve la chiave di un telefono abbinato all'auto (campo chiave dell'app)"
            )
        adesso = str(int(time.time()))
        attributi: dict[str, Any] = {
            "command": comando,
            "hmac": chiavi.firma(comando, adesso, self._chiave, str(v.extra["chiave_auto"])),
            "timestamp": adesso,
            "vasPhoneId": str(v.extra["telefono"]),
            "deviceId": str(v.extra["identita"]),
            "vehicleId": str(v.extra.get("id") or ""),
        }
        if parametri:
            attributi["params"] = parametri
        try:
            dati = await self._chiedi(
                f"comando {comando}", "sendVehicleCommand", _Q_COMANDO, {"attrs": attributi}, csrf=True
            )
        except ErroreRivian as err:
            raise ErroreComando(f"{v.titolo} non esegue il comando ({comando}): {err.messaggio or err.codice}") from err
        risposta = dati.get("sendVehicleCommand") or {}
        if not isinstance(risposta, dict) or not risposta.get("id"):
            raise ErroreComando(f"{v.titolo}: Rivian non ha accettato il comando {comando}")
        return str(risposta["id"])

    async def _sveglia(self, v: Vettura) -> None:
        """Se l'auto dorme la si sveglia prima del comando, e si aspetta che risponda."""
        ultimo = self._ultimo.get(v.vin) or v.stato
        if ultimo is None or not ultimo.in_sonno or time.monotonic() < self._sveglia_fino.get(v.vin, 0.0):
            return
        await self._invia(v, COMANDI["sveglia"])
        fine = time.monotonic() + ATTESA_SVEGLIA
        while time.monotonic() < fine:
            await asyncio.sleep(PASSO_SVEGLIA)
            potenza = ((await self._stato_grezzo(v)).get("powerState") or {}).get("value")
            if potenza and potenza != "sleep":
                # Sveglia: per qualche minuto i comandi dopo non la risvegliano.
                self._sveglia_fino[v.vin] = time.monotonic() + RESTA_SVEGLIA
                return
        _LOGGER.debug("%s non si è svegliata in tempo: si prova lo stesso", v.titolo)

    async def _comanda(self, v: Vettura, *passi: tuple[str, dict[str, Any] | None]) -> None:
        async with self._serratura_comandi:
            await self._sveglia(v)
            for comando, parametri in passi:
                await self._invia(v, comando, parametri)

    async def chiudi(self, v: Vettura, _pin: str | None) -> None:
        await self._comanda(v, (COMANDI["chiudi"], None))

    async def apri(self, v: Vettura, _pin: str | None) -> None:
        await self._comanda(v, (COMANDI["apri"], None))

    async def trova(self, v: Vettura, _pin: str | None) -> None:
        await self._comanda(v, (COMANDI["trova"], None))

    async def clima(self, v: Vettura, acceso: bool, _pin: str | None, temperatura: int = 22) -> None:
        if not acceso:
            await self._comanda(v, (COMANDI["clima_spento"], None))
            return
        # Come l'app: da 16 a 29 °C, e il valore va come testo.
        gradi = max(16, min(29, int(temperatura)))
        await self._comanda(v, (COMANDI["clima_acceso"], None), (COMANDI["temperatura"], {"HVAC_set_temp": str(gradi)}))

    async def ricarica(self, v: Vettura, avvia: bool, _pin: str | None) -> None:
        await self._comanda(v, (COMANDI["ricarica_avvia" if avvia else "ricarica_ferma"], None))

    async def limite_ricarica(self, v: Vettura, percento: int, _pin: str | None) -> None:
        await self._comanda(v, (COMANDI["limite"], {"SOC_limit": max(50, min(100, int(percento)))}))


__all__ = ["BASE_URL", "ID", "NOME", "ClienteRivian", "comandi_di", "entra", "sessione_app"]
