"""Il cliente del cloud BYD «overseas» (l'app BYD fuori dalla Cina).

Parla con i server DiLink regionali (``dilinkappoversea-*.byd.auto``) usati
dall'app BYD per Dolphin, Atto 3, Seal, Seal U, Sealion, Han e Tang. Legge
batteria, autonomia, odometro, porte, finestrini, serrature, gomme,
temperature, clima e ricarica; comanda chiusura, apertura, trova, clima e
avvio ricarica.

Due strati di crittografia (vedi ``busta.py`` e ``cripto.py``):

1. una **busta Bangcle** (AES a scatola bianca) avvolge tutta la richiesta;
   le tabelle vengono da un file dato dall'utente, non da GdAAuto;
2. dentro, i dati sensibili sono in **AES-128-CBC** con una chiave derivata
   dalla password (al login) o dal token di sessione (dopo).

Il login è una semplice chiamata JSON (``/app/account/login``), non una pagina
web: niente moduli HTML, niente browser. Il cloud tiene una sessione da ~12
ore; quando scade, il cliente rientra da solo con la password salvata.

Questo modulo non importa Home Assistant: si prova da solo.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import json
import logging
import secrets
import time
from typing import Any

import aiohttp

from ...vettura import StatoVettura, Vettura
from ..errori import (
    AutoAddormentata,
    ErroreApi,
    ErroreComando,
    ErroreCredenziali,
    ErrorePin,
    ErroreRete,
)
from . import cripto
from .busta import Busta, ErroreBusta
from .dati import interpreta

ID = "byd"
NOME = "BYD"

_LOG = logging.getLogger(__name__)

#: L'indirizzo predefinito: il nodo europeo. Gli altri nodi hanno la stessa forma.
BASE_URL = "https://dilinkappoversea-eu.byd.auto"

#: Gli indirizzi regionali, per paese ISO. Fuori da questi si usa l'Europa.
NODI: dict[str, str] = {
    "eu": "https://dilinkappoversea-eu.byd.auto",
    "sg": "https://dilinkappoversea-sg.byd.auto",
    "au": "https://dilinkappoversea-au.byd.auto",
    "br": "https://dilinkappoversea-br.byd.auto",
    "jp": "https://dilinkappoversea-jp.byd.auto",
    "mx": "https://dilinkappoversea-mx.byd.auto",
    "id": "https://dilinkappoversea-id.byd.auto",
    "tr": "https://dilinkappoversea-tr.byd.auto",
    "kr": "https://dilinkappoversea-kr.byd.auto",
    "in": "https://dilinkappoversea-in.byd.auto",
}

#: I paesi europei puntano tutti al nodo EU.
_PAESI_EU = {
    "NO",
    "NL",
    "DE",
    "DK",
    "SE",
    "FR",
    "AT",
    "LU",
    "BE",
    "FI",
    "IT",
    "ES",
    "PT",
    "GB",
    "IE",
    "IS",
    "IL",
    "HU",
    "MT",
    "GR",
    "PL",
    "CZ",
    "SK",
    "RO",
    "HR",
    "SI",
    "CY",
    "BG",
    "EE",
    "LV",
    "LT",
}

#: Il nodo DiLink giusto per un paese ISO.
_NODO_DI_PAESE: dict[str, str] = {
    "SG": "sg",
    "AU": "au",
    "BR": "br",
    "JP": "jp",
    "MX": "mx",
    "ID": "id",
    "TR": "tr",
    "KR": "kr",
    "IN": "in",
}

USER_AGENT = "okhttp/4.12.0"

#: La sessione è scaduta: rientrare con la password.
_SESSIONE_SCADUTA = {"1002", "1005", "1010"}
#: L'auto non risponde (spenta, fuori campo): tenere l'ultima lettura.
_AUTO_NON_RAGGIUNGIBILE = {"6002"}
#: Il backend è occupato: ritentare più tardi (transitorio, non un guasto nostro).
_OCCUPATO = {"1008", "6024"}
#: Il PIN dei comandi è sbagliato o l'account è bloccato.
_PIN_SBAGLIATO = {"5005", "5006", "5011"}

#: La busta Bangcle deve girare fuori dal loop (fa crittografia pesante).
_Esecutore = Callable[..., Awaitable[Any]]


def nodo_di_paese(paese: str) -> str:
    """L'indirizzo DiLink del paese ISO dato (Europa come ripiego)."""
    p = (paese or "").upper()
    if p in _PAESI_EU:
        return NODI["eu"]
    return NODI.get(_NODO_DI_PAESE.get(p, ""), NODI["eu"])


def _lingua_di_paese(paese: str) -> str:
    return {
        "IT": "it",
        "FR": "fr",
        "DE": "de",
        "ES": "es",
        "PT": "pt",
        "BR": "pt",
        "JP": "ja",
        "KR": "ko",
        "ID": "id",
    }.get((paese or "").upper(), "en")


class ClienteByd:
    """Entra nel cloud BYD, legge le auto, e manda i comandi.

    La busta Bangcle (``busta``) è obbligatoria per parlare col cloud vero; nelle
    prove se ne passa una finta. ``esecutore`` sposta la crittografia della busta
    fuori dal loop (in Home Assistant è ``hass.async_add_executor_job``).
    """

    def __init__(
        self,
        sessione_http: aiohttp.ClientSession,
        *,
        email: str,
        password: str,
        busta: Busta,
        pin: str = "",
        paese: str = "",
        lingua: str = "",
        fuso: str = "UTC",
        base_url: str = "",
        token: dict[str, Any] | None = None,
        salva: Callable[[dict[str, Any]], None] | None = None,
        esecutore: _Esecutore | None = None,
    ) -> None:
        self._http = sessione_http
        self._email = email.strip()
        self._password = password
        self._busta = busta
        self._pin = (pin or "").strip()
        self._paese = (paese or "").upper()
        self._lingua = lingua or _lingua_di_paese(self._paese)
        self._fuso = fuso or "UTC"
        self.base_url = (base_url or nodo_di_paese(self._paese)).rstrip("/")
        self._salva = salva
        self._esecutore = esecutore
        # la sessione: userId, signToken, encryToken
        self._sessione: dict[str, str] = dict(token or {})
        self._vetture: list[Vettura] = []
        self._pin_verificato = False
        #: attesa fra un tentativo e l'altro quando un dato non è ancora pronto
        self._attesa = 1.5
        self._tentativi = 8

    @property
    def sessione(self) -> dict[str, str]:
        """I token di sessione da salvare nella configurazione (ruotano al login)."""
        return dict(self._sessione)

    # --- busta, fuori dal loop se c'è l'esecutore --------------------------

    async def _chiudi(self, chiaro: str) -> str:
        if self._esecutore is not None:
            return await self._esecutore(self._busta.chiudi, chiaro)
        return self._busta.chiudi(chiaro)

    async def _apri(self, busta: str) -> bytes:
        if self._esecutore is not None:
            return await self._esecutore(self._busta.apri, busta)
        return self._busta.apri(busta)

    # --- la singola richiesta ---------------------------------------------

    async def _posta(self, endpoint: str, outer: dict[str, Any], chiave_contenuto: str) -> dict[str, Any]:
        """Avvolge nella busta, invia, apre la risposta e decifra ``respondData``."""
        try:
            corpo = json.dumps({"request": await self._chiudi(json.dumps(outer, separators=(",", ":")))})
            intestazioni = {
                "accept-encoding": "identity",
                "content-type": "application/json; charset=UTF-8",
                "user-agent": USER_AGENT,
            }
            async with self._http.post(
                f"{self.base_url}{endpoint}",
                data=corpo,
                headers=intestazioni,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as risp:
                testo = await risp.text()
                if risp.status != 200:
                    raise ErroreRete(f"{endpoint}: HTTP {risp.status}")
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ErroreRete(f"{endpoint}: {err}") from err

        try:
            fuori = json.loads(testo)
            busta_risp = fuori.get("response")
            if not isinstance(busta_risp, str) or not busta_risp.strip():
                raise ErroreRete(f"{endpoint}: risposta senza «response»")
            chiaro = (await self._apri(busta_risp)).decode("utf-8").strip()
            if chiaro.startswith(("F{", "F[")):
                chiaro = chiaro[1:]
            risposta = json.loads(chiaro)
        except (ErroreBusta, ValueError) as err:
            raise ErroreRete(f"{endpoint}: risposta illeggibile ({err})") from err

        codice = str(risposta.get("code", ""))
        if codice != "0":
            self._alza_errore(endpoint, codice, str(risposta.get("message", "")))

        dati = risposta.get("respondData")
        if not isinstance(dati, str) or not dati:
            return {}
        try:
            testo_interno = cripto.decifra_hex(dati, chiave_contenuto)
            return json.loads(testo_interno) if testo_interno.strip() else {}
        except Exception as err:
            raise ErroreRete(f"{endpoint}: contenuto illeggibile ({err})") from err

    def _alza_errore(self, endpoint: str, codice: str, messaggio: str) -> None:
        if codice in _SESSIONE_SCADUTA:
            raise ErroreCredenziali(f"sessione scaduta ({codice}): {messaggio}")
        if codice in _AUTO_NON_RAGGIUNGIBILE:
            raise AutoAddormentata(f"l'auto non risponde ({codice})")
        if codice in _OCCUPATO:
            raise ErroreRete(f"{endpoint}: cloud occupato ({codice})")
        if codice in _PIN_SBAGLIATO:
            raise ErrorePin(f"PIN rifiutato ({codice}): {messaggio}")
        raise ErroreApi(endpoint, codice, messaggio)

    # --- le buste: login e con token --------------------------------------

    def _campi_comuni(self) -> dict[str, str]:
        return {
            "ostype": "and",
            "imei": "BANGCLE01234",
            "mac": "00:00:00:00:00:00",
            "model": "POCO F1",
            "sdk": "35",
            "mod": "Xiaomi",
        }

    def _outer_login(self) -> tuple[dict[str, Any], str]:
        ora = str(int(time.time() * 1000))
        imei_md5 = cripto.md5(self._email)
        interno = {
            "agreeStatus": "0",
            "agreementType": "[1,2]",
            "appInnerVersion": "351",
            "appVersion": "3.5.1",
            "deviceName": "XIAOMIPOCO F1",
            "deviceType": "0",
            "imeiMD5": imei_md5,
            "isAuto": "1",
            "mobileBrand": "XIAOMI",
            "mobileModel": "POCO F1",
            "networkType": "wifi",
            "osType": "15",
            "osVersion": "35",
            "random": secrets.token_hex(16).upper(),
            "softType": "0",
            "timeStamp": ora,
            "timeZone": self._fuso,
        }
        encry = cripto.cifra_hex(_compatto(interno), cripto.chiave_login(self._password))
        campi_firma = {
            **interno,
            "appName": "",
            "countryCode": self._paese,
            "functionType": "pwdLogin",
            "identifier": self._email,
            "identifierType": "0",
            "language": self._lingua,
            "reqTimestamp": ora,
        }
        sign = cripto.firma(campi_firma, cripto.md5(self._password))
        outer: dict[str, Any] = {
            "appName": "",
            "countryCode": self._paese,
            "encryData": encry,
            "functionType": "pwdLogin",
            "identifier": self._email,
            "identifierType": "0",
            "imeiMD5": imei_md5,
            "isAuto": "1",
            "language": self._lingua,
            "reqTimestamp": ora,
            "sign": sign,
            "signKey": self._password,
            **self._campi_comuni(),
            "serviceTime": ora,
        }
        outer["checkcode"] = cripto.checkcode(outer)
        return outer, cripto.chiave_login(self._password)

    def _inner_base(self, *, vin: str = "", serial: str = "", extra: dict[str, Any] | None = None) -> dict[str, str]:
        inner: dict[str, str] = {
            "deviceType": "0",
            "imeiMD5": cripto.md5(self._email),
            "networkType": "wifi",
            "random": secrets.token_hex(16).upper(),
            "timeStamp": str(int(time.time() * 1000)),
            "version": "351",
        }
        if vin:
            inner["vin"] = vin
        if serial:
            inner["requestSerial"] = serial
        if extra:
            inner.update({k: str(v) for k, v in extra.items()})
        return inner

    def _outer_token(self, inner: dict[str, str], user_type: str | None = None) -> tuple[dict[str, Any], str]:
        ora = str(int(time.time() * 1000))
        chiave_contenuto = cripto.md5(self._sessione["encryToken"])
        chiave_firma = cripto.md5(self._sessione["signToken"])
        encry = cripto.cifra_hex(_compatto(inner), chiave_contenuto)
        imei_md5 = cripto.md5(self._email)
        campi_firma = {
            **inner,
            "countryCode": self._paese,
            "identifier": self._sessione["userId"],
            "imeiMD5": imei_md5,
            "language": self._lingua,
            "reqTimestamp": ora,
        }
        outer: dict[str, Any] = {
            "countryCode": self._paese,
            "encryData": encry,
            "identifier": self._sessione["userId"],
            "imeiMD5": imei_md5,
            "language": self._lingua,
            "reqTimestamp": ora,
            "sign": cripto.firma(campi_firma, chiave_firma),
            **self._campi_comuni(),
            "serviceTime": ora,
        }
        if user_type is not None:
            outer["userType"] = user_type
        outer["checkcode"] = cripto.checkcode(outer)
        return outer, chiave_contenuto

    # --- accesso -----------------------------------------------------------

    async def accedi(self) -> None:
        """Entra: se c'è una sessione salvata la tiene, altrimenti fa il login."""
        if self._sessione.get("signToken") and self._sessione.get("encryToken"):
            return
        await self._login()

    async def _login(self) -> None:
        outer, chiave = self._outer_login()
        try:
            risposta = await self._posta_login(outer, chiave)
        except ErroreApi as err:
            # un login respinto è «credenziali», non un guasto
            raise ErroreCredenziali(f"login rifiutato: {err.messaggio or err.codice}") from err
        token = (risposta or {}).get("token") or {}
        if not (token.get("userId") and token.get("signToken") and token.get("encryToken")):
            raise ErroreCredenziali("il cloud non ha restituito i token di sessione")
        self._sessione = {
            "userId": str(token["userId"]),
            "signToken": str(token["signToken"]),
            "encryToken": str(token["encryToken"]),
        }
        self._pin_verificato = False
        if self._salva is not None:
            self._salva(dict(self._sessione))

    async def _posta_login(self, outer: dict[str, Any], chiave: str) -> dict[str, Any]:
        """Il login decifra ``respondData`` con la chiave della password, non del token."""
        return await self._posta("/app/account/login", outer, chiave)

    async def _token(
        self, endpoint: str, inner: dict[str, str], *, user_type: str | None = None, ritenta: bool = True
    ) -> dict[str, Any]:
        """Una chiamata con la sessione; se scade, rientra una volta e riprova."""
        if not self._sessione.get("signToken"):
            await self._login()
        outer, chiave = self._outer_token(inner, user_type)
        try:
            return await self._posta(endpoint, outer, chiave)
        except ErroreCredenziali:
            if not ritenta:
                raise
            await self._login()
            outer, chiave = self._outer_token(inner, user_type)
            return await self._posta(endpoint, outer, chiave)

    # --- lettura -----------------------------------------------------------

    async def vetture(self) -> list[Vettura]:
        """Elenca le auto dell'account."""
        elenco = await self._token("/app/account/getAllListByUserId", self._inner_base())
        auto: list[Vettura] = []
        for v in elenco if isinstance(elenco, list) else []:
            vin = str(v.get("vin") or "").strip()
            if not vin:
                continue
            auto.append(
                Vettura(
                    vin=vin,
                    marca=ID,
                    modello=str(v.get("modelName") or v.get("outModelType") or "").strip(),
                    nome=str(v.get("autoAlias") or "").strip(),
                    targa=str(v.get("autoPlate") or "").strip(),
                    extra={"tipo_energia": v.get("energyType"), "modello_id": v.get("modelId")},
                )
            )
        self._vetture = auto
        return auto

    async def _attiva_e_attendi(
        self, attiva: str, risultato: str, vin: str, pronto: Callable[[dict[str, Any]], bool]
    ) -> dict[str, Any]:
        """Endpoint «a richiesta»: attiva, poi aspetta il risultato (sveglia il T-Box)."""
        dati = await self._token(attiva, self._inner_base(vin=vin))
        if pronto(dati):
            return dati
        serial = str(dati.get("requestSerial") or "")
        for _ in range(self._tentativi):
            await asyncio.sleep(self._attesa)
            dati = await self._token(risultato, self._inner_base(vin=vin, serial=serial))
            if pronto(dati):
                return dati
        return dati

    async def _realtime(self, vin: str) -> dict[str, Any]:
        def pronto(d: dict[str, Any]) -> bool:
            if not d or str(d.get("onlineState")) == "2":
                return bool(d)
            gomme = any(float(d.get(k) or 0) > 0 for k in ("leftFrontTirepressure", "rightFrontTirepressure"))
            return gomme or int(d.get("time") or 0) > 0 or float(d.get("enduranceMileage") or 0) > 0

        return await self._attiva_e_attendi(
            "/vehicleInfo/vehicle/vehicleRealTimeRequest",
            "/vehicleInfo/vehicle/vehicleRealTimeResult",
            vin,
            pronto,
        )

    async def _gps(self, vin: str) -> dict[str, Any]:
        def pronto(d: dict[str, Any]) -> bool:
            dati = d.get("data") if isinstance(d.get("data"), dict) else d
            return bool(dati) and set(dati.keys()) != {"requestSerial"}

        grezzo = await self._attiva_e_attendi("/control/getGpsInfo", "/control/getGpsInfoResult", vin, pronto)
        dati = grezzo.get("data") if isinstance(grezzo.get("data"), dict) else grezzo
        return dati if isinstance(dati, dict) else {}

    async def _facoltativo(self, endpoint: str, vin: str) -> dict[str, Any]:
        """Una lettura che può mancare: se fallisce, lo stato arriva lo stesso."""
        try:
            dati = await self._token(endpoint, self._inner_base(vin=vin))
            return dati if isinstance(dati, dict) else {}
        except (ErroreApi, ErroreRete, AutoAddormentata) as err:
            _LOG.debug("BYD: %s non disponibile per %s: %s", endpoint, vin[-6:], err)
            return {}

    async def stato(self, vettura: Vettura, *, con_energia: bool = False) -> StatoVettura:
        """Legge l'auto da tutti gli endpoint e compone lo ``StatoVettura``."""
        vin = vettura.vin
        realtime = await self._realtime(vin)
        gps = await self._facoltativo_gps(vin)
        ricarica = await self._facoltativo("/control/smartCharge/homePage", vin)
        clima = await self._facoltativo("/control/getStatusNow", vin)
        energia = await self._facoltativo("/vehicleInfo/vehicle/getEnergyConsumption", vin) if con_energia else None
        return interpreta(realtime, gps=gps, ricarica=ricarica, clima=clima, energia=energia)

    async def _facoltativo_gps(self, vin: str) -> dict[str, Any]:
        try:
            return await self._gps(vin)
        except (ErroreApi, ErroreRete, AutoAddormentata) as err:
            _LOG.debug("BYD: GPS non disponibile per %s: %s", vin[-6:], err)
            return {}

    async def energia(self, vettura: Vettura) -> dict[str, Any]:
        """Il dato lento dei consumi, da leggere di rado (vedi INTERVALLO in __init__)."""
        return await self._facoltativo("/vehicleInfo/vehicle/getEnergyConsumption", vettura.vin)

    # --- comandi -----------------------------------------------------------

    def _pin_md5(self, pin: str | None) -> str:
        p = (pin or self._pin or "").strip()
        if not p:
            raise ErrorePin("manca il PIN dei comandi")
        return cripto.md5(p)

    async def verifica_pin(self, vettura: Vettura, pin: str | None = None) -> None:
        """Controlla il PIN dei comandi una volta, come fa l'app prima di comandare."""
        extra = {"commandPwd": self._pin_md5(pin), "functionType": "remoteControl"}
        inner = self._inner_base(vin=vettura.vin, extra=extra)
        await self._token("/vehicle/vehicleswitch/verifyControlPassword", inner)
        self._pin_verificato = True

    async def _comando(
        self, vettura: Vettura, tipo: str, pin: str | None, parametri: dict[str, Any] | None = None
    ) -> None:
        """Manda un comando remoto e aspetta che il cloud lo accetti."""
        pin_md5 = self._pin_md5(pin)
        if not self._pin_verificato:
            await self.verifica_pin(vettura, pin)
        extra: dict[str, Any] = {"commandPwd": pin_md5, "commandType": tipo}
        if parametri is not None:
            extra["controlParamsMap"] = json.dumps(parametri, separators=(",", ":"), sort_keys=True)
        inner = self._inner_base(vin=vettura.vin, extra=extra)

        def pronto(d: dict[str, Any]) -> bool:
            if not d:
                return False
            stato = d.get("controlState")
            if stato is not None and int(stato) != 0:
                return True
            res = d.get("res")
            return int(res) >= 2 if res is not None else "result" in d

        try:
            dati = await self._attiva_comando(vettura.vin, inner, pronto)
        except ErrorePin:
            raise
        except ErroreApi as err:
            raise ErroreComando(f"il cloud ha rifiutato «{tipo}»: {err.messaggio or err.codice}") from err
        # un risultato terminale negativo (res==3 o controlState di errore) è un rifiuto leggibile
        if dati.get("res") == 3 or str(dati.get("controlState") or "") in ("3", "4"):
            motivo = dati.get("result") or dati.get("message") or "rifiutato"
            raise ErroreComando(f"«{tipo}» non eseguito: {motivo}")

    async def _attiva_comando(
        self, vin: str, inner: dict[str, Any], pronto: Callable[[dict[str, Any]], bool]
    ) -> dict[str, Any]:
        dati = await self._token("/control/remoteControl", inner, user_type="1")
        if pronto(dati):
            return dati
        serial = str(dati.get("requestSerial") or "")
        risultato = self._inner_base(vin=vin, serial=serial, extra={"functionType": "remoteControl"})
        for _ in range(self._tentativi):
            await asyncio.sleep(self._attesa)
            dati = await self._token("/control/remoteControlResult", risultato, user_type="1")
            if pronto(dati):
                return dati
        return dati

    async def chiudi(self, v: Vettura, pin: str | None) -> None:
        await self._comando(v, "LOCKDOOR", pin)

    async def apri(self, v: Vettura, pin: str | None) -> None:
        await self._comando(v, "OPENDOOR", pin)

    async def trova(self, v: Vettura, pin: str | None) -> None:
        await self._comando(v, "FLASHLIGHTNOWHISTLE", pin)

    async def clima(self, v: Vettura, acceso: bool, pin: str | None, temperatura: int = 22) -> None:
        if not acceso:
            await self._comando(v, "CLOSEAIR", pin)
            return
        scala = max(1, min(17, round(float(temperatura) - 14)))
        parametri = {
            "mainSettingTemp": scala,
            "copilotSettingTemp": scala,
            "cycleMode": 2,
            "remoteMode": 4,
            "airAccuracy": 1,
            "airConditioningMode": 1,
            "airSet": None,
        }
        await self._comando(v, "OPENAIR", pin, parametri)

    async def ricarica(self, v: Vettura, avvia: bool, pin: str | None) -> None:
        if not avvia:
            # fatto noto: il cloud BYD non ha un comando funzionante per fermare la ricarica.
            raise ErroreComando("il cloud BYD non ferma la ricarica da remoto")
        inner = self._inner_base(vin=v.vin, extra={"timeZone": "", "status": "1"})
        try:
            await self._token("/control/smartCharge/changeChargeStatue", inner)
        except ErroreApi as err:
            raise ErroreComando(f"avvio ricarica rifiutato: {err.messaggio or err.codice}") from err


def _compatto(d: dict[str, Any]) -> str:
    return json.dumps(d, separators=(",", ":"))


def comandi_di(vettura: Vettura) -> set[str]:
    """I comandi che BYD accetta. Il cloud non ha né limite di carica né destinazione."""
    return {"chiudi", "apri", "trova", "clima", "ricarica"}
