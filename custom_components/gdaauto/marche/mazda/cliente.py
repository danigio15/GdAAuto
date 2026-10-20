"""Il filo col cloud della Mazda 6e: l'app «Mazda 6e» europea, piattaforma Changan.

La 6e non usa il cloud delle altre Mazda (MyMazda): è costruita con Changan e
parla col gateway europeo di Changan, ``cma-m.iov.changanauto.com.de``. Tutto
è ``POST`` di JSON; ogni risposta è ``{"success", "code", "msg", "data"}``.

Come si entra:

1. ``cma-app-auth/api/login/email-pass-in/v2`` con email e password cifrate
   con la chiave pubblica del cloud (vedi ``cripto.py``), un identificativo del
   «telefono» (``deviceid``, generato una volta e tenuto) e la chiave pubblica
   dei comandi. Risponde ``token``, ``refreshToken`` ed ``emailVerify``;
2. se ``emailVerify`` è vero il «telefono» è nuovo: si chiede a Mazda di
   mandare un codice per email (``send-email/device-login/send``) e lo si
   conferma con ``login-device/email-verify``. Il codice va nel campo
   «codice»; dopo, per lo stesso ``deviceid``, non serve più;
3. quando il cloud dice ``APP_1_1_02_004`` (token scaduto) si rinnova con
   ``cma-app-auth/api/auth/refresh-token`` e si riprova una volta. Se il
   rinnovo non va, bisogna rientrare.

La password serve solo al login: GdAAuto tiene i token.

I comandi (``cma-app-car-control``) chiedono un numero di serie monouso
(``serial-no/get``, cifrato con la chiave dei comandi), si firmano, e poi si
guarda ``control/control-result`` finché l'auto risponde. Aprire e chiudere
le portiere vogliono anche il **PIN dei comandi** dell'app (sei cifre),
verificato prima con ``security-code/check-code``.

Indirizzi, intestazioni, campi e codici vengono da
fano0001/home-assistant-mazda-6e (Apache 2.0): vedi RINGRAZIAMENTI.md.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
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
    ErroreComando,
    ErroreCredenziali,
    ErroreMarca,
    ErrorePin,
    ErroreRete,
)
from . import cripto
from .dati import CRITERI, ID, interpreta, vetture_da

_LOGGER = logging.getLogger(__name__)

NOME = "Mazda"

#: Il gateway europeo dell'app Mazda 6e
BASE_URL = "https://cma-m.iov.changanauto.com.de/cma-app-gw"

_NOME_TELEFONO = "iPhone 17 Pro"
_INTESTAZIONI = {
    "content-type": "application/json",
    "devicetype": "iPhone",
    "apptype": "IOS",
    "appid": "cma",
    "accept": "*/*",
    "appversion": "V1.2.3",
    "accept-language": "en-US;q=1.0",
    "user-agent": "overseas/1.2.3 (com.mazda.mazda6e; build:1; iOS 27.0.0) Alamofire/5.5.0",
    "language": "en_US",
}
_TIMEOUT = aiohttp.ClientTimeout(total=30)

#: il codice del cloud per «token scaduto»
TOKEN_SCADUTO = "APP_1_1_02_004"

#: I risultati di ``control-result``
ESITO_FATTO = 0
ESITO_IN_CORSO = -100
ESITO_GIA_COSI = 1015
ESITO_CENTRALINA_MUTA = 1000
#: quante volte, e ogni quanto, si guarda se il comando è arrivato
GIRI_ESITO = 15
PASSO_ESITO = 1.0
#: quanto resta valido un codice mandato per email
_ATTESA_CODICE = 15 * 60

Esecutore = Callable[..., Awaitable[Any]]


async def _subito(funzione: Callable[..., Any], *argomenti: Any) -> Any:
    return funzione(*argomenti)


async def chiama(
    http: aiohttp.ClientSession,
    url: str,
    corpo: dict[str, Any],
    intestazioni: dict[str, str],
    cosa: str,
) -> dict[str, Any]:
    """Un ``POST`` al gateway: la risposta intera, o l'errore giusto per un guasto."""
    try:
        async with http.post(url, json=corpo, headers={**_INTESTAZIONI, **intestazioni}, timeout=_TIMEOUT) as r:
            stato_http = r.status
            testo = await r.text()
    except (aiohttp.ClientError, TimeoutError, OSError) as err:
        raise ErroreRete(f"{cosa}: il cloud Mazda non risponde: {err}") from err
    if stato_http >= 500:
        raise ErroreRete(f"{cosa}: il cloud Mazda ha un guasto (HTTP {stato_http})")
    if stato_http in (401, 403):
        raise ErroreCredenziali(f"{cosa}: il cloud Mazda rifiuta l'accesso (HTTP {stato_http})")
    try:
        risposta = json.loads(testo)
    except ValueError:
        risposta = None
    if not isinstance(risposta, dict):
        if stato_http == 429:
            raise ErroreApi(cosa, 429, "troppe richieste al cloud Mazda", stato_http)
        raise ErroreRete(f"{cosa}: il cloud Mazda risponde una cosa che non si capisce (HTTP {stato_http})")
    if stato_http != 200 and risposta.get("success") is not True:
        raise ErroreApi(cosa, risposta.get("code"), str(risposta.get("msg") or ""), stato_http)
    return risposta


def _rifiuto(cosa: str, risposta: dict[str, Any]) -> ErroreApi:
    return ErroreApi(cosa, risposta.get("code"), str(risposta.get("msg") or ""), 200)


@dataclass
class _InSospeso:
    """Un login che aspetta il codice dell'email."""

    token: dict[str, str] = field(repr=False)
    email_cifrata: str = field(repr=False)
    scade: float


#: I login in attesa del codice, fra un invio del modulo e il successivo (solo in memoria).
_IN_SOSPESO: dict[str, _InSospeso] = {}


async def entra(
    http: aiohttp.ClientSession,
    email: str,
    password: str,
    *,
    chiave_server: str,
    codice: str = "",
    dispositivo: str = "",
    base: str = BASE_URL,
    esecutore: Esecutore = _subito,
) -> dict[str, str]:
    """Il login con email e password, verifica del «telefono» per email compresa.

    Restituisce i token da salvare (con il ``deviceid`` e la chiave privata
    dei comandi). Se Mazda manda il codice solleva ``CodiceInviato``: si
    richiama con lo stesso account e il ``codice``.
    """
    email = email.strip()
    chiave = f"{base}|{email.lower()}"
    adesso = time.monotonic()
    for k in [k for k, s in _IN_SOSPESO.items() if s.scade < adesso]:
        del _IN_SOSPESO[k]
    codice = codice.strip()
    if (sospeso := _IN_SOSPESO.get(chiave)) and codice:
        await _conferma_codice(http, base, sospeso, codice)
        _IN_SOSPESO.pop(chiave, None)
        return dict(sospeso.token)

    dispositivo = dispositivo or str(uuid.uuid4())
    pubblica, privata = await esecutore(cripto.nuova_coppia)
    email_cifrata = await esecutore(cripto.cifra, email, chiave_server)
    password_cifrata = await esecutore(cripto.cifra, password, chiave_server)
    try:
        risposta = await chiama(
            http,
            f"{base}/cma-app-auth/api/login/email-pass-in/v2",
            {
                "loginTime": str(int(time.time())),
                "email": email_cifrata,
                "password": password_cifrata,
                "pubKey": pubblica,
            },
            {"deviceid": dispositivo},
            "login",
        )
    except ErroreApi as err:
        if err.http == 429:
            raise
        raise ErroreCredenziali(f"Mazda rifiuta email o password ({err.codice}: {err.messaggio})") from err
    dati = risposta.get("data") if risposta.get("success") is True else None
    if not isinstance(dati, dict):
        raise ErroreCredenziali(
            f"Mazda rifiuta email o password (codice {risposta.get('code')}: {risposta.get('msg') or ''})".strip()
        )
    if not dati.get("token") or not dati.get("refreshToken"):
        raise ErroreRete("login: Mazda non ha dato i token")
    token = {
        "token": str(dati["token"]),
        "refresh": str(dati["refreshToken"]),
        "dispositivo": dispositivo,
        "chiave_comandi": privata,
    }
    if dati.get("emailVerify") is not True:
        return token
    # Un «telefono» nuovo per Mazda: si chiede il codice per email.
    invio = await chiama(
        http,
        f"{base}/cma-app-user/api/send-email/device-login/send",
        {"email": email_cifrata, "deviceName": _NOME_TELEFONO, "loginTime": str(int(time.time())), "type": "1"},
        {"authorization": token["token"], "deviceid": dispositivo},
        "invio del codice",
    )
    if invio.get("success") is not True:
        raise _rifiuto("invio del codice", invio)
    _IN_SOSPESO[chiave] = _InSospeso(token, email_cifrata, adesso + _ATTESA_CODICE)
    raise CodiceInviato(
        "Mazda vuole confermare il nuovo accesso: ha mandato un codice per email. "
        "Scrivilo nel campo «codice» e conferma di nuovo."
    )


async def _conferma_codice(http: aiohttp.ClientSession, base: str, sospeso: _InSospeso, codice: str) -> None:
    risposta = await chiama(
        http,
        f"{base}/cma-app-user/api/login-device/email-verify",
        {
            "authCode": codice,
            "email": sospeso.email_cifrata,
            "deviceName": _NOME_TELEFONO,
            "lastLoginTime": str(int(time.time())),
            "type": "3",
            "deviceModel": _NOME_TELEFONO,
        },
        {"authorization": sospeso.token["token"], "deviceid": sospeso.token["dispositivo"]},
        "verifica del codice",
    )
    if risposta.get("success") is not True or risposta.get("data") is not True:
        raise CodiceSbagliato("Il codice di Mazda non va: controlla l'ultima email (scade dopo qualche minuto)")


def comandi_di(v: Vettura) -> set[str]:
    """Le portiere sempre (col PIN dei comandi); il resto se l'auto dice di saperlo fare."""
    funzioni = set(v.extra.get("funzioni") or [])
    comandi = {"chiudi", "apri"}
    # Un'auto che non dice le sue funzioni prova tutto, come l'app.
    if not funzioni or "FlashHonk" in funzioni:
        comandi.add("trova")
    if not funzioni or "ACSW" in funzioni:
        comandi.add("clima")
    if not funzioni or "BatteryMaxSoc" in funzioni:
        comandi.add("limite_ricarica")
    return comandi


class ClienteMazda:
    def __init__(
        self,
        sessione_http: aiohttp.ClientSession,
        token: dict[str, Any],
        *,
        chiave_server: str,
        salva: Callable[[dict[str, Any]], None] | None = None,
        base_url: str = BASE_URL,
        esecutore: Esecutore = _subito,
    ) -> None:
        self._http = sessione_http
        self.token: dict[str, Any] = dict(token)
        self._chiave_server = chiave_server
        self._salva = salva
        self._base = base_url.rstrip("/")
        self._esecutore = esecutore
        self._serratura_token = asyncio.Lock()
        self._serratura_comandi = asyncio.Lock()

    # --- i token -------------------------------------------------------------

    def _intestazioni(self) -> dict[str, str]:
        return {"authorization": str(self.token.get("token") or ""), "deviceid": str(self.token.get("dispositivo"))}

    async def _rinnova(self, scaduto: str) -> None:
        async with self._serratura_token:
            if self.token.get("token") != scaduto:
                return  # l'ha già rinnovato un'altra richiesta
            try:
                risposta = await chiama(
                    self._http,
                    f"{self._base}/cma-app-auth/api/auth/refresh-token",
                    {"refreshToken": str(self.token.get("refresh") or "")},
                    {"authorization": scaduto},
                    "rinnovo del token",
                )
            except ErroreApi as err:
                raise ErroreCredenziali(f"Mazda non rinnova la sessione ({err.codice}): serve rientrare") from err
            dati = risposta.get("data") if risposta.get("success") is True else None
            if not isinstance(dati, dict) or not dati.get("token"):
                raise ErroreCredenziali(f"Mazda non rinnova la sessione ({risposta.get('code')}): serve rientrare")
            self.token.update(token=str(dati["token"]), refresh=str(dati.get("refreshToken") or self.token["refresh"]))
            if self._salva:
                self._salva(dict(self.token))

    async def _chiedi(self, percorso: str, corpo: dict[str, Any], cosa: str, *, di_nuovo: bool = True) -> Any:
        if not self.token.get("token"):
            raise ErroreCredenziali("Manca la sessione Mazda: serve rientrare")
        usato = str(self.token["token"])
        risposta = await chiama(self._http, f"{self._base}/{percorso}", corpo, self._intestazioni(), cosa)
        if risposta.get("success") is True:
            return risposta.get("data")
        if risposta.get("code") == TOKEN_SCADUTO:
            if not di_nuovo:
                raise ErroreCredenziali(f"{cosa}: Mazda rifiuta anche il token rinnovato")
            await self._rinnova(usato)
            return await self._chiedi(percorso, corpo, cosa, di_nuovo=False)
        raise _rifiuto(cosa, risposta)

    # --- entrare e leggere -----------------------------------------------------

    async def accedi(self) -> None:
        if not self.token.get("token") or not self.token.get("refresh"):
            raise ErroreCredenziali("Manca la sessione Mazda: serve rientrare")

    async def vetture(self) -> list[Vettura]:
        try:
            elenco = await self._chiedi("cma-app-user/api/vehicle/vehicles", {}, "elenco delle auto")
        except ErroreApi:
            elenco = None
        if not elenco:
            # Alcuni account vedono le auto solo dall'altro indirizzo.
            elenco = await self._chiedi("cma-app-user/api/car/vehicles", {}, "elenco delle auto")
        vetture = vetture_da(elenco)
        for v in vetture:
            v.extra["funzioni"] = sorted(await self._funzioni(v))
        return vetture

    async def _funzioni(self, v: Vettura) -> set[str]:
        """Le funzioni che l'auto dice di avere (``ACSW``, ``FlashHonk``…). Facoltative."""
        try:
            dati = await self._chiedi(
                "cma-app-user/api/vehicle/function-config", {"vehicleId": v.extra["id"]}, "funzioni dell'auto"
            )
        except ErroreCredenziali:
            raise
        except ErroreMarca as err:
            _LOGGER.debug("Funzioni di %s non lette: %s", v.titolo, err)
            return set()
        return {str(x) for x in ((dati or {}).get("confList") or []) if x} if isinstance(dati, dict) else set()

    async def _stato_grezzo(self, v: Vettura) -> dict[str, Any]:
        dati = await self._chiedi(
            "cma-app-car-condition/api/vehicle/condition/v2",
            {"vechileCriteria": CRITERI, "vehicleId": v.extra["id"]},
            "stato dell'auto",
        )
        if not isinstance(dati, dict):
            raise ErroreApi("stato dell'auto", None, f"Mazda non dà lo stato di {v.titolo}")
        return dati

    async def stato(self, v: Vettura) -> StatoVettura:
        return interpreta(await self._stato_grezzo(v))

    # --- comandare -------------------------------------------------------------

    def _chiave_comandi(self, v: Vettura) -> str:
        chiave = str(self.token.get("chiave_comandi") or "")
        if not chiave:
            raise ErroreCredenziali(f"{v.titolo}: manca la chiave dei comandi, serve rientrare")
        return chiave

    async def _seriale(self, v: Vettura, tipo: str) -> str:
        cifrato = await self._chiedi("cma-app-car-control/api/serial-no/get", {"type": tipo}, "numero del comando")
        if not isinstance(cifrato, str) or not cifrato:
            raise ErroreComando(f"{v.titolo}: Mazda non dà il numero del comando")
        return await self._esecutore(cripto.decifra_seriale, cifrato, self._chiave_comandi(v))

    async def _token_pin(self, v: Vettura, pin: str | None) -> str:
        pin = str(pin or "").strip()
        if not pin:
            raise ErrorePin("Aprire e chiudere vogliono il PIN dei comandi dell'app Mazda: scrivilo nelle opzioni")
        if len(pin) != 6 or not pin.isdigit():
            raise ErrorePin("Il PIN dei comandi Mazda ha sei cifre")
        cifrato = await self._esecutore(cripto.cifra, pin, self._chiave_server)
        try:
            dati = await self._chiedi(
                "cma-app-car-control/api/security-code/check-code", {"safeCode": cifrato}, "verifica del PIN"
            )
        except ErroreApi as err:
            raise ErrorePin(f"Mazda non accetta il PIN dei comandi: {err.messaggio or err.codice}") from err
        if not isinstance(dati, dict) or not dati.get("rcToken"):
            raise ErrorePin("Mazda non accetta il PIN dei comandi")
        return str(dati["rcToken"])

    async def _invia(
        self,
        v: Vettura,
        percorso: str,
        campi: dict[str, Any],
        *,
        salta: frozenset[str] = frozenset(),
        gia_cosi: bool = False,
    ) -> dict[str, Any]:
        """Firma, manda e aspetta l'esito di un comando già col suo numero di serie."""
        cosa = f"comando {percorso.rsplit('/', 1)[-1]}"
        firma = await self._esecutore(cripto.firma, campi, self._chiave_comandi(v), salta)
        try:
            dati = await self._chiedi(f"cma-app-car-control/api/{percorso}", {**campi, "sign": firma}, cosa)
        except ErroreApi as err:
            raise ErroreComando(f"{v.titolo} non esegue il {cosa}: {err.messaggio or err.codice}") from err
        if not isinstance(dati, dict) or not dati.get("commandId"):
            raise ErroreComando(f"{v.titolo}: Mazda non ha accettato il {cosa}")
        return await self._esito(v, str(dati["commandId"]), cosa, gia_cosi)

    async def _esito(self, v: Vettura, comando: str, cosa: str, gia_cosi: bool) -> dict[str, Any]:
        for giro in range(GIRI_ESITO):
            if giro:
                await asyncio.sleep(PASSO_ESITO)
            dati = await self._chiedi(
                "cma-app-car-control/api/control/control-result",
                {"commandId": comando, "vehicleId": str(v.extra["id"])},
                "esito del comando",
            )
            if not isinstance(dati, dict) or not isinstance(dati.get("resultCode"), int):
                raise ErroreComando(f"{v.titolo}: Mazda dà un esito del {cosa} che non si capisce")
            codice = dati["resultCode"]
            if codice == ESITO_FATTO or (gia_cosi and codice == ESITO_GIA_COSI):
                return dati
            if codice != ESITO_IN_CORSO:
                motivo = str(dati.get("errorMsg") or "").strip()
                raise ErroreComando(
                    f"{v.titolo} non esegue il {cosa} (esito {codice}{': ' + motivo if motivo else ''})"
                )
        raise ErroreComando(f"{v.titolo}: il {cosa} non ha avuto risposta dall'auto")

    async def _portiere(self, v: Vettura, aperte: bool, pin: str | None) -> None:
        async with self._serratura_comandi:
            rc = await self._token_pin(v, pin)
            campi = {
                "open": aperte,
                "rcToken": rc,
                "seriralNo": await self._seriale(v, "1"),
                "vehicleId": str(v.extra["id"]),
            }
            await self._invia(v, "control/doors", campi, gia_cosi=not aperte)

    async def chiudi(self, v: Vettura, pin: str | None) -> None:
        await self._portiere(v, False, pin)

    async def apri(self, v: Vettura, pin: str | None) -> None:
        await self._portiere(v, True, pin)

    async def trova(self, v: Vettura, _pin: str | None) -> None:
        async with self._serratura_comandi:
            campi = {"type": 1, "seriralNo": await self._seriale(v, "1"), "vehicleId": str(v.extra["id"])}
            await self._invia(v, "control/flashing-honking", campi)

    async def clima(self, v: Vettura, acceso: bool, _pin: str | None, temperatura: int = 22) -> None:
        # Come l'app: da 16 a 30 °C, in decimi; un quarto d'ora.
        gradi = max(16.0, min(30.0, float(temperatura)))
        async with self._serratura_comandi:
            campi = {
                "enabled": acceso,
                "targetTemp": round(gradi * 10),
                "runTime": 15,
                "seriralNo": await self._seriale(v, "1"),
                "vehicleId": str(v.extra["id"]),
            }
            await self._invia(v, "control/air-conditioner", campi, gia_cosi=True)

    async def limite_ricarica(self, v: Vettura, percento: int, _pin: str | None) -> None:
        # L'app accetta dal 60 al 100 %.
        percento = max(60, min(100, int(percento)))
        async with self._serratura_comandi:
            campi = {
                "chargePercentageMax": percento,
                "command": "charge_max",
                "rcToken": "",
                "seriralNo": await self._seriale(v, "2"),
                "vehicleId": str(v.extra["id"]),
            }
            try:
                await self._invia(v, "charge/percentage", campi, salta=frozenset({"command", "rcToken"}))
            except ErroreComando as err:
                # «La centralina non risponde» capita anche quando il limite è già passato.
                if f"esito {ESITO_CENTRALINA_MUTA}" not in str(err):
                    raise
                if interpreta(await self._stato_grezzo(v)).limite_ricarica != percento:
                    raise


__all__ = ["BASE_URL", "ID", "NOME", "ClienteMazda", "comandi_di", "entra"]
