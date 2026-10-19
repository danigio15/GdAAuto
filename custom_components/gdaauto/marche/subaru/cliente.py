"""Il filo con MySubaru Connected Services (STARLINK), il cloud dell'app MySubaru.

Il cloud (``mobileapi.prod.subarucs.com`` negli Stati Uniti,
``mobileapi.ca.prod.subarucs.com`` in Canada, sotto ``/g2v33``) è JSON con una
sessione a cookie (``JSESSIONID``). Il giro:

1. ``login.json`` con email, password e un ``deviceId`` scelto da GdAAuto. Se
   il dispositivo non è ancora autorizzato (``deviceRegistered: false``),
   Subaru vuole un codice: ``twoStepAuthContacts.json`` dice dove mandarlo,
   ``twoStepAuthSendVerification.json`` lo manda, ``twoStepAuthVerify.json``
   lo conferma e ricorda il dispositivo. Dopo, la password basta;
2. ``selectVehicle.json?vin=`` sceglie l'auto su cui lavorare (la sessione ne
   ha una per volta) e dice modello, telematica e abbonamento;
3. ``validateSession.json`` prima di ogni giro: una sessione scade dopo al
   più quattro ore, e allora si rientra con la password (per questo si salva).

Le letture sono quelle del cloud, senza svegliare l'auto. I comandi
(``service/g2/<comando>/execute.json``) vogliono il PIN dell'app; il cloud
risponde con un ``serviceRequestId`` e si chiede a ``remoteService/status.json``
finché l'auto non ha finito. Un PIN sbagliato blocca l'account dopo pochi
tentativi: dopo il primo rifiuto GdAAuto non lo riprova finché non cambia.

Quando Subaru alza la versione (``/g2v33`` → ``/g2v34``) le vecchie strade
rispondono 404: si prova da soli la versione dopo, come fa subarulink.

I cookie si tengono qui, a mano, e non nella sessione HTTP: due account non
si mescolano. Indirizzi, campi e codici di errore vengono da G-Two/subarulink
(Apache 2.0): vedi RINGRAZIAMENTI.md.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
import json
import logging
import re
import time
from typing import Any

import aiohttp

from ...vettura import StatoVettura, Vettura
from ..errori import (
    CodiceInviato,
    CodiceSbagliato,
    ErroreApi,
    ErroreComando,
    ErroreCredenziali,
    ErrorePin,
    ErroreRete,
)
from .dati import ID, comandi_di, interpreta, vettura_da

_LOGGER = logging.getLogger(__name__)

NOME = "Subaru"

SERVER = {"USA": "https://mobileapi.prod.subarucs.com", "CAN": "https://mobileapi.ca.prod.subarucs.com"}
APP = {"USA": "com.subaru.telematics.app.remote", "CAN": "ca.subaru.telematics.remote"}
VERSIONE = "/g2v33"
#: quante versioni in più si provano dopo un 404
_TENTATIVI_VERSIONE = 5

_TIMEOUT = aiohttp.ClientTimeout(total=30)
#: una sessione Subaru dura al più quattro ore
DURATA_SESSIONE = 240 * 60
#: ogni quanto si rileggono le spie
OGNI_SALUTE = 30 * 60
#: le attese dei comandi: fra una domanda e l'altra, e quante domande
PAUSA_COMANDO = 2.0
TENTATIVI_COMANDO = 20
#: dopo «comando già in corso» si aspetta un po' e si riprova
PAUSA_GIA_IN_CORSO = 10.0
#: dopo la verifica del codice Subaru registra il dispositivo con un po' di ritardo
PAUSA_REGISTRAZIONE = 3.0
_ATTESA_CODICE = 15 * 60

_CREDENZIALI = {
    "invalidAccount",
    "InvalidCredentials",
    "passwordWarning",
    "accountLocked",
    "tooManyAttempts",
    "accountNotFound",
}
_SESSIONE_PERSA = {"403-soa-unableToParseResponseBody", "InvalidToken"}
_GIA_IN_CORSO = {"ServiceAlreadyStarted", "SXM40009"}
_PIN_SBAGLIATO = {"InvalidCredentials", "SXM40006"}
_PIN_BLOCCATO = {"SXM40017"}
_DISPOSITIVO = "DEVICE_NOT_AUTHENTICATED"

_MOTIVI = {
    "otherCommandsOngoing": "un altro comando è ancora in corso",
    "lowFuel": "c'è poco carburante",
    "SXM40004": "l'abbonamento non è attivo",
    "SXM40005": "l'auto risulta rubata",
}


def server_di(paese: str) -> str:
    return SERVER.get(paese.upper(), SERVER["USA"])


class ErroreSubaru(ErroreApi):
    """Subaru ha risposto ``success: false`` con un ``errorCode`` suo."""


class Sessione:
    """La sessione a cookie con MySubaru: login, verifica del dispositivo, scelta dell'auto."""

    def __init__(
        self,
        sessione_http: aiohttp.ClientSession,
        email: str,
        password: str,
        dispositivo: str,
        *,
        paese: str = "USA",
        nome_dispositivo: str = "GdAAuto",
        server: str | None = None,
    ) -> None:
        self._http = sessione_http
        self.email = email.strip()
        self._password = password
        self.dispositivo = str(dispositivo)
        self.nome_dispositivo = nome_dispositivo
        self.paese = paese.upper() if paese.upper() in SERVER else "USA"
        self._server = (server or server_di(self.paese)).rstrip("/")
        self.versione = VERSIONE
        self._salti = 0
        self.biscotti: dict[str, str] = {}
        self.registrato = False
        self.vin_elenco: list[str] = []
        self._vin_attuale = ""
        self._entrata = 0.0
        self._serratura = asyncio.Lock()
        self._intestazioni = {
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 10; Android SDK built for x86 Build/QSR1.191030.002; wv) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/74.0.3729.185 Mobile Safari/537.36"
            ),
            "Origin": "file://",
            "X-Requested-With": APP[self.paese],
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "*/*",
        }

    # --- la rete -----------------------------------------------------------

    def _salta_versione(self) -> bool:
        if self._salti >= _TENTATIVI_VERSIONE or not (m := re.search(r"\d+$", self.versione)):
            return False
        self.versione = f"{self.versione[: m.start()]}{int(m.group()) + 1}"
        self._salti += 1
        return True

    async def chiedi(
        self,
        cosa: str,
        metodo: str,
        percorso: str,
        *,
        modulo: Mapping[str, Any] | None = None,
        json_: Any = None,
        parametri: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Una richiesta: restituisce il JSON (anche con ``success: false``)."""
        while True:
            intestazioni = dict(self._intestazioni)
            if self.biscotti:
                intestazioni["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.biscotti.items())
            try:
                async with self._http.request(
                    metodo,
                    f"{self._server}{self.versione}{percorso}",
                    headers=intestazioni,
                    data=dict(modulo) if modulo is not None else None,
                    json=json_,
                    params=dict(parametri) if parametri else None,
                    timeout=_TIMEOUT,
                ) as r:
                    for nome, morsel in r.cookies.items():
                        self.biscotti[nome] = morsel.value
                    stato = r.status
                    testo = await r.text()
            except (aiohttp.ClientError, TimeoutError, OSError) as err:
                raise ErroreRete(f"{cosa}: Subaru non risponde: {err}") from err
            if stato == 404 and self._salta_versione():
                _LOGGER.warning("Subaru risponde 404: si prova la versione %s", self.versione)
                continue
            break
        if stato >= 500:
            raise ErroreRete(f"{cosa}: Subaru risponde HTTP {stato}")
        try:
            corpo = json.loads(testo)
        except ValueError:
            corpo = None
        if not isinstance(corpo, dict) or ("success" not in corpo and "serviceType" not in corpo):
            if stato in (401, 403):
                raise ErroreCredenziali(f"{cosa}: Subaru rifiuta la sessione (HTTP {stato})")
            raise ErroreRete(f"{cosa}: Subaru risponde una cosa che non si capisce (HTTP {stato})")
        return corpo

    # --- entrare -------------------------------------------------------------

    async def entra(self) -> dict[str, Any]:
        """Il login con la password. Restituisce ``data`` (``deviceRegistered``, le auto…)."""
        self.biscotti.clear()
        self._vin_attuale = ""
        # Come l'app (e subarulink): i campi vuoti vanno lo stesso, e nel modulo diventano «None».
        corpo = await self.chiedi(
            "login",
            "POST",
            "/login.json",
            modulo={
                "env": "cloudprod",
                "loginUsername": self.email,
                "password": self._password,
                "deviceId": self.dispositivo,
                "passwordToken": "None",
                "selectedVin": "None",
                "pushToken": "None",
                "deviceType": "android",
            },
        )
        if not corpo.get("success"):
            codice = str(corpo.get("errorCode") or "")
            if codice in _CREDENZIALI:
                raise ErroreCredenziali(f"login: Subaru rifiuta l'accesso ({codice})")
            raise ErroreSubaru("login", codice or None, json.dumps(corpo.get("data"))[:200])
        dati = corpo.get("data") or {}
        self.registrato = bool(dati.get("deviceRegistered"))
        self.vin_elenco = [str(v["vin"]).upper() for v in dati.get("vehicles") or [] if v.get("vin")]
        self._entrata = time.monotonic()
        return dati

    async def contatti(self) -> dict[str, str]:
        corpo = await self.chiedi("verifica", "POST", "/twoStepAuthContacts.json")
        dati = corpo.get("data")
        return {str(k): str(v) for k, v in dati.items() if v} if isinstance(dati, dict) else {}

    async def manda_codice(self, contatto: str) -> None:
        corpo = await self.chiedi(
            "verifica",
            "POST",
            "/twoStepAuthSendVerification.json",
            parametri={"contactMethod": contatto, "languagePreference": "EN"},
        )
        if not corpo.get("success"):
            raise ErroreSubaru("verifica", corpo.get("errorCode"), "Subaru non ha mandato il codice")

    async def conferma_codice(self, codice: str) -> None:
        codice = codice.strip()
        if not (codice.isdecimal() and len(codice) == 6):
            raise CodiceSbagliato("Il codice di Subaru ha sei cifre")
        corpo = await self.chiedi(
            "verifica",
            "POST",
            "/twoStepAuthVerify.json",
            parametri={
                "deviceId": self.dispositivo,
                "deviceName": self.nome_dispositivo,
                "verificationCode": codice,
                "rememberDevice": "on",
            },
        )
        if not corpo.get("success"):
            raise CodiceSbagliato("Il codice di Subaru non va, o è scaduto: controlla l'ultimo messaggio")

    async def valida(self, vin: str) -> None:
        """Una sessione buona, con quell'auto scelta: se serve si rientra."""
        async with self._serratura:
            vecchia = not self.biscotti or time.monotonic() - self._entrata > DURATA_SESSIONE
            if not vecchia:
                corpo = await self.chiedi("sessione", "GET", "/validateSession.json")
                vecchia = not corpo.get("success")
            if vecchia:
                await self.entra()
                if not self.registrato:
                    raise ErroreCredenziali("Subaru non riconosce più questo dispositivo: serve rientrare col codice")
            if vin != self._vin_attuale:
                await self._scegli(vin)

    async def _scegli(self, vin: str) -> dict[str, Any]:
        corpo = await self.chiedi(
            "scelta dell'auto", "GET", "/selectVehicle.json", parametri={"vin": vin, "_": int(time.time())}
        )
        if not corpo.get("success"):
            codice = str(corpo.get("errorCode") or "")
            # «VEHICLESETUPERROR» capita ogni tanto: si butta la sessione e si riparte al giro dopo.
            self.biscotti.clear()
            self._vin_attuale = ""
            if codice == _DISPOSITIVO:
                raise ErroreCredenziali("Subaru non riconosce più questo dispositivo: serve rientrare col codice")
            raise ErroreSubaru("scelta dell'auto", codice or None, f"Subaru non sceglie l'auto {vin}")
        self._vin_attuale = vin
        return dict(corpo.get("data") or {})

    async def auto(self) -> list[dict[str, Any]]:
        """``selectVehicle`` di ogni auto dell'account."""
        elenco = []
        async with self._serratura:
            for vin in self.vin_elenco:
                elenco.append(await self._scegli(vin))
        return elenco

    def dimentica(self) -> None:
        """La sessione non vale più: al prossimo giro si rientra."""
        self.biscotti.clear()
        self._vin_attuale = ""


@dataclass
class _InSospeso:
    """Una verifica avviata, che aspetta il codice."""

    biscotti: dict[str, str] = field(repr=False)
    dispositivo: str
    scade: float


_IN_SOSPESO: dict[str, _InSospeso] = {}


async def autorizza(sessione: Sessione, codice: str = "") -> None:
    """Il login, con la verifica del dispositivo se Subaru la vuole.

    Senza codice manda il codice e solleva ``CodiceInviato``; col codice
    conferma, e aspetta che Subaru registri il dispositivo.
    """
    chiave = f"{sessione.paese}|{sessione.email.lower()}"
    adesso = time.monotonic()
    for k in [k for k, s in _IN_SOSPESO.items() if s.scade < adesso]:
        del _IN_SOSPESO[k]
    sospeso = _IN_SOSPESO.get(chiave)
    if sospeso and codice.strip():
        sessione.biscotti = dict(sospeso.biscotti)
        sessione.dispositivo = sospeso.dispositivo
        await sessione.conferma_codice(codice)
        del _IN_SOSPESO[chiave]
        for _ in range(3):
            await sessione.entra()
            if sessione.registrato:
                return
            await asyncio.sleep(PAUSA_REGISTRAZIONE)
        raise ErroreSubaru("verifica", None, "Subaru non ha ancora registrato il dispositivo: riprova fra un minuto")
    await sessione.entra()
    if sessione.registrato:
        return
    contatti = await sessione.contatti()
    contatto = "userName" if "userName" in contatti else next(iter(contatti), "")
    if not contatto:
        raise ErroreSubaru("verifica", None, "Subaru non ha un'email o un telefono per mandare il codice")
    await sessione.manda_codice(contatto)
    _IN_SOSPESO[chiave] = _InSospeso(dict(sessione.biscotti), sessione.dispositivo, adesso + _ATTESA_CODICE)
    raise CodiceInviato(
        f"Subaru vuole autorizzare GdAAuto: ha mandato un codice a {contatti.get(contatto) or contatto}. "
        "Scrivilo nel campo «codice» e conferma di nuovo."
    )


class ClienteSubaru:
    def __init__(self, sessione: Sessione) -> None:
        self.sessione = sessione
        self._auto: dict[str, Vettura] = {}
        self._salute: dict[str, tuple[dict[str, Any] | None, float]] = {}
        self._serratura_comandi = asyncio.Lock()
        #: il PIN che Subaru ha rifiutato: non si riprova finché non cambia
        self._pin_rifiutato: str | None = None

    # --- leggere ---------------------------------------------------------------

    async def accedi(self) -> None:
        await self.sessione.entra()
        if not self.sessione.registrato:
            raise ErroreCredenziali("Subaru non riconosce più questo dispositivo: serve rientrare col codice")

    async def vetture(self) -> list[Vettura]:
        if not self.sessione.vin_elenco:
            await self.accedi()
        elenco = [v for d in await self.sessione.auto() if (v := vettura_da(d)) is not None]
        self._auto = {v.vin: v for v in elenco}
        return elenco

    async def _leggi(self, cosa: str, percorso: str, vin: str) -> dict[str, Any] | None:
        """Una lettura; se la sessione è persa si rientra e si riprova una volta."""
        for tentativo in range(2):
            await self.sessione.valida(vin)
            corpo = await self.sessione.chiedi(cosa, "GET", percorso)
            if corpo.get("success"):
                dati = corpo.get("data")
                return dati if isinstance(dati, dict) else None
            codice = str(corpo.get("errorCode") or "")
            if codice == _DISPOSITIVO:
                raise ErroreCredenziali("Subaru non riconosce più questo dispositivo: serve rientrare col codice")
            if codice in _SESSIONE_PERSA and tentativo == 0:
                self.sessione.dimentica()
                continue
            raise ErroreSubaru(cosa, codice or None, f"Subaru non dà {cosa}")
        return None

    async def stato(self, v: Vettura) -> StatoVettura:
        stato = await self._leggi("lo stato dell'auto", "/vehicleStatus.json", v.vin)
        condizioni = posizione = None
        if v.extra.get("remoto") and v.extra.get("percorso") == "g2":
            # Con l'abbonamento Security: serrature, porte, ricarica e una posizione più fresca.
            for cosa, percorso in (
                ("le condizioni", "/service/g2/condition/execute.json"),
                ("la posizione", "/service/g2/locate/execute.json"),
            ):
                try:
                    dati = await self._leggi(cosa, percorso, v.vin)
                except ErroreSubaru as err:
                    _LOGGER.debug("%s di %s non lette: %s", cosa, v.titolo, err)
                    continue
                risultato = (dati or {}).get("result")
                if cosa == "le condizioni":
                    condizioni = risultato if isinstance(risultato, dict) else None
                else:
                    posizione = risultato if isinstance(risultato, dict) else None
        salute = await self._salute_di(v)
        return interpreta(stato, condizioni, posizione, salute, elettrica=bool(v.extra.get("elettrica", True)))

    async def _salute_di(self, v: Vettura) -> dict[str, Any] | None:
        """Le spie, rilette ogni mezz'ora; se non vanno, lo stato arriva lo stesso."""
        salute, quando = self._salute.get(v.vin, (None, 0.0))
        if quando and time.monotonic() - quando < OGNI_SALUTE:
            return salute
        try:
            salute = await self._leggi("le spie", "/vehicleHealth.json", v.vin)
        except ErroreSubaru as err:
            _LOGGER.debug("Spie di %s non lette: %s", v.titolo, err)
        self._salute[v.vin] = (salute, time.monotonic())
        return salute

    # --- comandare ----------------------------------------------------------------

    def _controlla(self, v: Vettura, comando: str, pin: str | None) -> str:
        if comando not in comandi_di(v):
            raise ErroreComando(f"{v.titolo} non lo fa da remoto (serve l'abbonamento MySubaru Security attivo)")
        if not pin:
            raise ErrorePin("Subaru vuole il PIN dell'app MySubaru per i comandi: aggiungilo nelle opzioni")
        if pin == self._pin_rifiutato:
            raise ErrorePin("Subaru ha già rifiutato questo PIN: correggilo nelle opzioni prima di riprovare")
        return pin

    async def _comanda(
        self, v: Vettura, comando: str, percorso: str, pin: str | None, extra: dict[str, Any] | None = None
    ) -> None:
        codice_pin = self._controlla(v, comando, pin)
        gen = str(v.extra.get("percorso") or "g2")
        stato_url = (
            "/service/g1/hornLights/status.json"
            if gen == "g1" and "hornLights" in percorso
            else (f"/service/{gen}/remoteService/status.json")
        )
        corpo_comando = {"pin": codice_pin, "delay": 0, "vin": v.vin, **(extra or {})}
        async with self._serratura_comandi:
            for tentativo in range(3):
                await self.sessione.valida(v.vin)
                risposta = await self.sessione.chiedi(
                    f"comando {comando}", "POST", percorso.replace("{gen}", gen), json_=corpo_comando
                )
                codice = str(risposta.get("errorCode") or "")
                if codice in _PIN_SBAGLIATO:
                    self._pin_rifiutato = codice_pin
                    raise ErrorePin(
                        "Subaru rifiuta il PIN: controllalo nell'app MySubaru (dopo pochi errori blocca l'account)"
                    )
                if codice in _PIN_BLOCCATO:
                    self._pin_rifiutato = codice_pin
                    raise ErrorePin("Subaru ha bloccato il PIN per troppi tentativi: sbloccalo dall'app")
                if codice in _SESSIONE_PERSA and tentativo < 2:
                    self.sessione.dimentica()
                    continue
                if codice in _GIA_IN_CORSO and tentativo < 2:
                    await asyncio.sleep(PAUSA_GIA_IN_CORSO)
                    continue
                if not risposta.get("success"):
                    motivo = _MOTIVI.get(codice) or codice or "errore sconosciuto"
                    raise ErroreComando(f"{v.titolo} non esegue il comando ({comando}): {motivo}")
                richiesta = (risposta.get("data") or {}).get("serviceRequestId")
                if not richiesta:
                    return
                await self._aspetta(v, comando, stato_url, str(richiesta))
                return
            raise ErroreComando(f"{v.titolo} non esegue il comando ({comando}): Subaru non risponde come dovrebbe")

    async def _aspetta(self, v: Vettura, comando: str, percorso: str, richiesta: str) -> None:
        pausa = PAUSA_COMANDO
        for _ in range(TENTATIVI_COMANDO):
            risposta = await self.sessione.chiedi(
                f"comando {comando}", "GET", percorso, parametri={"serviceRequestId": richiesta}
            )
            codice = str(risposta.get("errorCode") or "")
            if codice in _SESSIONE_PERSA:
                self.sessione.dimentica()
                await self.sessione.valida(v.vin)
                continue
            if codice:
                raise ErroreComando(f"{v.titolo} non esegue il comando ({comando}): {_MOTIVI.get(codice, codice)}")
            dati = risposta.get("data") or {}
            if dati.get("remoteServiceState") == "finished":
                if dati.get("success"):
                    return
                motivo = str(dati.get("errorCode") or "").split(":")[-1]
                descrizione = _MOTIVI.get(motivo) or dati.get("errorDescription") or motivo or "rifiutato dall'auto"
                raise ErroreComando(f"{v.titolo} non esegue il comando ({comando}): {descrizione}")
            await asyncio.sleep(pausa)
            pausa = min(pausa * 1.5, 15.0)
        raise ErroreComando(f"{v.titolo}: il comando ({comando}) non ha avuto risposta dall'auto")

    async def chiudi(self, v: Vettura, pin: str | None) -> None:
        await self._comanda(v, "chiudi", "/service/{gen}/lock/execute.json", pin, {"forceKeyInCar": False})

    async def apri(self, v: Vettura, pin: str | None) -> None:
        await self._comanda(v, "apri", "/service/{gen}/unlock/execute.json", pin, {"unlockDoorType": "ALL_DOORS_CMD"})

    async def trova(self, v: Vettura, pin: str | None) -> None:
        await self._comanda(v, "trova", "/service/{gen}/hornLights/execute.json", pin)

    async def clima(self, v: Vettura, acceso: bool, pin: str | None, temperatura: int = 22) -> None:
        if not acceso:
            await self._comanda(v, "clima", "/service/g2/engineStop/execute.json", pin)
            return
        codice_pin = self._controlla(v, "clima", pin)
        impostazioni = clima_da(temperatura, elettrica=bool(v.extra.get("elettrica")))
        # Come l'app: prima si salvano le impostazioni dell'avvio rapido, poi si avvia.
        await self.sessione.valida(v.vin)
        risposta = await self.sessione.chiedi(
            "impostazioni del clima", "POST", "/service/g2/remoteEngineQuickStartSettings/save.json", json_=impostazioni
        )
        if not risposta.get("success"):
            raise ErroreComando(
                f"{v.titolo}: Subaru non accetta le impostazioni del clima ({risposta.get('errorCode')})"
            )
        await self._comanda(v, "clima", "/service/g2/engineStart/execute.json", codice_pin, impostazioni)

    async def ricarica(self, v: Vettura, avvia: bool, pin: str | None) -> None:
        if not avvia:
            raise ErroreComando(f"{v.titolo} non ferma la ricarica da remoto: si ferma dall'auto o dalla colonnina")
        await self._comanda(v, "ricarica", "/service/g2/phevChargeNow/execute.json", pin)


def clima_da(gradi: int, *, elettrica: bool) -> dict[str, Any]:
    """Le impostazioni di un avvio del clima, come un «preset» dell'app (gradi Fahrenheit, da 60 a 85)."""
    fahrenheit = max(60, min(85, round(int(gradi) * 9 / 5 + 32)))
    return {
        "name": "GdAAuto",
        "runTimeMinutes": "10",
        "climateZoneFrontTemp": str(fahrenheit),
        "climateZoneFrontAirMode": "AUTO",
        "climateZoneFrontAirVolume": "AUTO",
        "outerAirCirculation": "outsideAir",
        "heatedRearWindowActive": "false",
        "heatedSeatFrontLeft": "OFF",
        "heatedSeatFrontRight": "OFF",
        "airConditionOn": "false",
        "canEdit": "true",
        "disabled": "false",
        "presetType": "userPreset",
        "startConfiguration": (
            "START_CLIMATE_CONTROL_ONLY_ALLOW_KEY_IN_IGNITION" if elettrica else "START_ENGINE_ALLOW_KEY_IN_IGNITION"
        ),
    }


__all__ = ["ID", "NOME", "SERVER", "ClienteSubaru", "Sessione", "autorizza", "clima_da", "comandi_di", "server_di"]
