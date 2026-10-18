"""Il filo col cloud MG iSMART (SAIC, API «ng»): entra, legge, comanda.

Il cloud è quello dell'app MG iSMART (``gateway-mg-<regione>.soimt.com``).
In parole semplici:

- **Si entra** con un OAuth «password»: email e SHA-1 della password, con le
  credenziali fisse dell'app. Il token vale qualche giorno; quando scade, o
  il cloud risponde 401/403, si rientra con email e password. Lo stesso
  account usato due volte butta fuori l'altra sessione: per questo serve un
  account secondario dell'app, e il token si salva per non rientrare a ogni
  riavvio.
- **Ogni richiesta** ha il corpo cifrato e una firma (``cripto.py``), e il VIN
  viaggia come SHA-256.
- **Lo stato e i comandi sono asincroni**: la prima richiesta porta
  ``event-id: 0``; il cloud risponde con un ``event-id`` nuovo e senza
  ``data`` finché l'auto non ha risposto, e si richiede con quell'id finché
  i dati arrivano (al massimo 30 secondi). Ogni lettura **sveglia l'auto**
  (e la batteria a 12 V lo sente): per questo la si legge di rado.
- **I codici** del cloud: 0 bene, 4 l'auto non si raggiunge (dorme o non ha
  campo), 8 comando rifiutato (non chiusa, troppi comandi…), 401/403 token
  non più valido.

Il protocollo viene da SAIC-iSmart-API/saic-python-client-ng (MIT); i limiti
e le scale da saic-python-mqtt-gateway e ad-ha/mg-saic-ha (MIT). Non sa
niente di Home Assistant: gli si passa una sessione ``aiohttp``.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
import json
import logging
import time
from typing import Any
from urllib.parse import urlencode

import aiohttp
from yarl import URL

from ...vettura import StatoVettura, Vettura
from ..errori import ErroreApi, ErroreComando, ErroreCredenziali, ErroreRete
from . import cripto
from .dati import codice_da_limite, indice_temperatura, interpreta, regge_il_limite, vettura_da_json

_LOGGER = logging.getLogger(__name__)

NOME = "MG"

#: Le regioni del cloud: (indirizzo, intestazione REGION). L'India ha un
#: altro cloud (TAP) e la Cina un'altra app: qui non ci sono.
REGIONI: dict[str, tuple[str, str]] = {
    "eu": ("https://gateway-mg-eu.soimt.com/api.app/v1/", "eu"),
    "au": ("https://gateway-mg-au.soimt.com/api.app/v1/", "au"),
    "br": ("https://gateway-mg-br.soimt.com/api.app/v1/", "br"),
    "il": ("https://gateway-mg-il.soimt.com/api.app/v1/", "il"),
    "tr": ("https://gateway-mg-tr.soimt.com/api.app/v1/", "tr"),
    "th": ("https://gateway-mg-th.soimt.com/api.app/v1/", "th"),
}
NOMI_REGIONI = {
    "eu": "Europe (eu)",
    "au": "Australia / New Zealand (au)",
    "br": "Brasil (br)",
    "il": "Israel (il)",
    "tr": "Türkiye (tr)",
    "th": "Thailand (th)",
}

#: Il tenant dell'app europea: entra nelle chiavi e nella firma.
TENANT = "459771"
#: Le credenziali OAuth dell'app («sword:sword_secret»), le stesse per tutti.
AUTORIZZAZIONE_APP = "Basic c3dvcmQ6c3dvcmRfc2VjcmV0"
AGENTE = "Europe/2.1.0 (iPad; iOS 18.5; Scale/2.00)"

_TIMEOUT = aiohttp.ClientTimeout(total=30)
#: Quanto aspettare la risposta dell'auto, e ogni quanto richiedere.
PAZIENZA_SECONDI = 30.0
ATTESA_STATO = 3.0
ATTESA_COMANDO = 1.0
#: Il token si rinnova un po' prima che scada.
_MARGINE = timedelta(minutes=5)

CODICE_NON_RAGGIUNGIBILE = 4
_CODICI_FINALI = {2, 3, 7}

COMANDI = {"chiudi", "trova", "clima", "ricarica", "limite_ricarica"}

# I tipi di comando di /vehicle/control (rvcReqType) e i loro parametri.
_TROVA, _CHIUDI, _APRI, _CLIMA = "0", "1", "2", "6"
_FINE = (255, b"\x00\x00\x00\x00")


def comandi_di(v: Vettura) -> set[str]:
    comandi = set(COMANDI)
    if not regge_il_limite(v):
        comandi.discard("limite_ricarica")
    return comandi


def _parametri(*coppie: tuple[int, bytes]) -> list[dict[str, Any]]:
    return [{"paramId": i, "paramValue": base64.b64encode(valore).decode()} for i, valore in coppie]


class _Riprova(Exception):
    """Il cloud sta ancora aspettando l'auto: si richiede con questo event-id."""

    def __init__(self, evento: str) -> None:
        super().__init__(evento)
        self.evento = evento


class _Scaduto(Exception):
    """Il cloud non accetta più il token (401/403)."""


class _NonRisponde(Exception):
    """L'auto non ha risposto in tempo."""


class ClienteMG:
    def __init__(
        self,
        sessione_http: aiohttp.ClientSession,
        *,
        email: str,
        password: str,
        regione: str = "eu",
        token: dict[str, Any] | None = None,
        salva: Callable[[dict[str, Any]], None] | None = None,
        base_url: str | None = None,
        tenant: str = TENANT,
        pazienza: float = PAZIENZA_SECONDI,
        attesa_stato: float = ATTESA_STATO,
        attesa_comando: float = ATTESA_COMANDO,
    ) -> None:
        self._http = sessione_http
        self.email = email.strip()
        self.password = password
        self.regione = regione if regione in REGIONI else "eu"
        indirizzo, self._intestazione_regione = REGIONI[self.regione]
        self.base_url = (base_url or indirizzo).rstrip("/") + "/"
        self.tenant = tenant
        self._token: dict[str, Any] = dict(token or {})
        self._salva = salva
        self._pazienza = pazienza
        self._attesa_stato = attesa_stato
        self._attesa_comando = attesa_comando
        self._ultimo: dict[str, StatoVettura] = {}
        self._serratura = asyncio.Lock()

    # --- il token --------------------------------------------------------------

    @property
    def token(self) -> dict[str, Any]:
        return dict(self._token)

    @property
    def _token_valido(self) -> bool:
        if not self._token.get("access_token"):
            return False
        try:
            scade = datetime.fromisoformat(str(self._token.get("scade")))
        except ValueError:
            return False
        return scade - _MARGINE > datetime.now(UTC)

    # --- la rete -------------------------------------------------------------

    async def _chiama(
        self,
        cosa: str,
        metodo: str,
        percorso: str,
        *,
        query: dict[str, str] | None = None,
        corpo: dict[str, Any] | None = None,
        form: dict[str, str] | None = None,
        intestazioni: dict[str, str] | None = None,
        token: str | None = None,
        evento: str | None = None,
    ) -> Any:
        """Una richiesta cifrata e firmata; restituisce il ``data`` della risposta."""
        token = self._token.get("access_token", "") if token is None else token
        url = self.base_url + percorso.lstrip("/")
        if query:
            url += "?" + urlencode(query)
        percorso_firmato = url.replace(self.base_url, "/")
        if corpo is not None:
            tipo, contenuto = cripto.TIPO_JSON, json.dumps(corpo, ensure_ascii=False, separators=(",", ":"))
        elif form is not None:
            tipo, contenuto = cripto.TIPO_FORM, urlencode(form)
        else:
            tipo, contenuto = cripto.TIPO_JSON, ""
        ora = str(int(time.time() * 1000))
        firma = {"percorso": percorso_firmato, "ora_ms": ora, "tenant": self.tenant, "tipo": tipo, "token": token}
        h = {
            **(intestazioni or {}),
            "User-Agent": AGENTE,
            "Content-Type": f"{tipo};charset=utf-8",
            "Accept": "application/json",
            "REGION": self._intestazione_regione,
            "APP-SEND-DATE": ora,
            "APP-CONTENT-ENCRYPTED": "1",
            "tenant-id": self.tenant,
            "User-Type": "app",
            "APP-LANGUAGE-TYPE": "en",
            "APP-VERIFICATION-STRING": cripto.stringa_verifica(contenuto=contenuto.strip(), **firma),
            "ORIGINAL-CONTENT-TYPE": tipo,
        }
        if token:
            h["blade-auth"] = token
        cifrato = cripto.cifra_richiesta(contenuto=contenuto, **firma)
        try:
            async with self._http.request(
                metodo, URL(url, encoded=True), data=cifrato.encode() if cifrato else None, headers=h, timeout=_TIMEOUT
            ) as r:
                testo = (await r.read()).decode("utf-8", errors="replace")
                http = r.status
                risposta_h = r.headers
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise ErroreRete(f"{cosa}: il cloud MG non risponde: {err}") from err

        ora_risposta = risposta_h.get("APP-SEND-DATE")
        tipo_risposta = risposta_h.get("ORIGINAL-CONTENT-TYPE")
        if 200 <= http < 300 and testo.strip() and ora_risposta and tipo_risposta:
            try:
                testo = cripto.decifra_risposta(testo, ora_risposta, tipo_risposta)
            except ValueError as err:
                raise ErroreRete(f"{cosa}: {err}") from err
        try:
            dati = json.loads(testo)
        except ValueError as err:
            if http in (401, 403):
                raise _Scaduto(testo[:120]) from err
            if http >= 500 or 200 <= http < 300:
                raise ErroreRete(f"{cosa}: risposta non JSON (HTTP {http}): {testo[:120]}") from err
            raise ErroreApi(cosa, None, testo[:120], http) from err
        if not isinstance(dati, dict):
            raise ErroreRete(f"{cosa}: risposta inattesa (HTTP {http})")
        codice = dati.get("code", -1)
        messaggio = str(dati.get("message") or "")
        if codice in (401, 403) or http in (401, 403):
            raise _Scaduto(messaggio)
        if http >= 500:
            raise ErroreRete(f"{cosa}: il cloud MG ha un guasto (HTTP {http}): {messaggio}")
        if codice in _CODICI_FINALI:
            raise ErroreApi(cosa, codice, messaggio, http)
        if "event-id" in risposta_h and "data" not in dati:
            raise _Riprova(risposta_h["event-id"])
        if codice != 0:
            if evento not in (None, "0"):
                raise _Riprova(evento)
            raise ErroreApi(cosa, codice, messaggio, http)
        return dati.get("data")

    async def _con_evento(self, cosa: str, metodo: str, percorso: str, *, attesa: float, **kwargs: Any) -> Any:
        """Una richiesta all'auto: si ripete con l'event-id finché l'auto risponde."""
        evento = "0"
        scadenza = time.monotonic() + self._pazienza
        while True:
            try:
                dati = await self._chiama(
                    cosa, metodo, percorso, intestazioni={"event-id": evento}, evento=evento, **kwargs
                )
            except _Riprova as r:
                evento = r.evento
                if time.monotonic() + attesa > scadenza:
                    raise _NonRisponde(cosa) from None
                await asyncio.sleep(attesa)
                continue
            if dati is None:
                raise ErroreApi(cosa, 0, "risposta senza dati")
            return dati

    # --- entrare --------------------------------------------------------------

    async def _entra(self) -> None:
        """Il login OAuth «password» dell'app."""
        dispositivo = "simulator" + "*" * 45 + str(int(time.time()))
        form = {
            "grant_type": "password",
            "username": self.email,
            "password": cripto.sha1_hex(self.password),
            "scope": "all",
            "deviceId": f"{dispositivo}###com.saicmotor.europecar",
            "deviceType": "0",
            "language": "EN",
            "loginType": "2",
        }
        try:
            d = await self._chiama(
                "login",
                "POST",
                "oauth/token",
                form=form,
                intestazioni={"Authorization": AUTORIZZAZIONE_APP},
                token="",
            )
        except _Scaduto as err:
            raise ErroreCredenziali(f"Email o password rifiutate: {err}") from err
        except ErroreApi as err:
            if err.http in (0, 200, 400, 401, 403):
                raise ErroreCredenziali(err.messaggio or "Email o password rifiutate") from err
            raise ErroreRete(str(err)) from err
        d = d if isinstance(d, dict) else {}
        try:
            scade_fra = int(d["expires_in"])
            access_token = str(d["access_token"])
        except (KeyError, TypeError, ValueError) as err:
            raise ErroreCredenziali("Il login non ha consegnato un token: controlla email e password") from err
        if not access_token:
            raise ErroreCredenziali("Il login non ha consegnato un token")
        self._token = {
            "access_token": access_token,
            "scade": (datetime.now(UTC) + timedelta(seconds=scade_fra)).isoformat(),
            "utente": str(d.get("user_id") or d.get("account") or ""),
        }
        if self._salva:
            self._salva(self.token)
        _LOGGER.debug("Entrati nel cloud MG (%s)", self.regione)

    async def accedi(self) -> None:
        """Si entra solo se il token salvato non va più bene."""
        async with self._serratura:
            if not self._token_valido:
                await self._entra()

    async def _con_sessione[T](self, azione: Callable[[], Awaitable[T]]) -> T:
        """Una cosa alla volta; col token scaduto si rientra e si riprova una volta."""
        async with self._serratura:
            if not self._token_valido:
                await self._entra()
            try:
                return await azione()
            except _Scaduto:
                _LOGGER.debug("Il cloud MG non accetta più il token: si rientra")
                self._token = {}
                await self._entra()
                try:
                    return await azione()
                except _Scaduto as err:
                    raise ErroreCredenziali("Il cloud MG rifiuta anche il token nuovo") from err

    # --- leggere ----------------------------------------------------------

    async def vetture(self) -> list[Vettura]:
        async def leggi() -> list[Vettura]:
            d = await self._chiama("elenco delle auto", "GET", "vehicle/list")
            elenco = (d.get("vinList") or []) if isinstance(d, dict) else []
            return [vettura_da_json(voce) for voce in elenco if isinstance(voce, dict) and voce.get("vin")]

        return await self._con_sessione(leggi)

    async def stato(self, v: Vettura) -> StatoVettura:
        vin = cripto.sha256_hex(v.vin)

        async def leggi() -> StatoVettura:
            try:
                stato = await self._con_evento(
                    "stato dell'auto",
                    "GET",
                    "vehicle/status",
                    query={"vin": vin, "vehStatusReqType": "2"},
                    attesa=self._attesa_stato,
                )
            except _NonRisponde:
                return self._addormentata(v)
            except ErroreApi as err:
                if err.codice == CODICE_NON_RAGGIUNGIBILE:
                    return self._addormentata(v)
                raise
            try:
                ricarica = await self._con_evento(
                    "dati della ricarica",
                    "GET",
                    "vehicle/charging/mgmtData",
                    query={"vin": vin},
                    attesa=self._attesa_stato,
                )
            except (_NonRisponde, ErroreApi) as err:
                # Senza la ricarica si tiene lo stato: è già qualcosa.
                _LOGGER.debug("Dati della ricarica di %s non arrivati: %s", v.titolo, err)
                ricarica = None
            s = interpreta(stato if isinstance(stato, dict) else {}, ricarica if isinstance(ricarica, dict) else None)
            self._ultimo[v.vin] = s
            return s

        return await self._con_sessione(leggi)

    def _addormentata(self, v: Vettura) -> StatoVettura:
        """L'auto non risponde: gli ultimi dati, segnati come «dorme»."""
        ultimo = self._ultimo.get(v.vin) or v.stato
        if ultimo is None:
            return StatoVettura(in_sonno=True)
        ultimo.in_sonno = True
        ultimo.velocita_kmh = 0.0 if ultimo.velocita_kmh is not None else None
        return ultimo

    # --- comandare ---------------------------------------------------------

    async def _comanda(self, v: Vettura, cosa: str, percorso: str, corpo: dict[str, Any]) -> None:
        corpo = {**corpo, "vin": cripto.sha256_hex(v.vin)}

        async def esegui() -> None:
            try:
                await self._con_evento(cosa, "POST", percorso, corpo=corpo, attesa=self._attesa_comando)
            except _NonRisponde as err:
                raise ErroreComando(f"{v.titolo} non ha confermato «{cosa}» in tempo") from err
            except ErroreApi as err:
                if err.codice == CODICE_NON_RAGGIUNGIBILE:
                    raise ErroreComando(f"{v.titolo} non si raggiunge (dorme o non ha campo)") from err
                raise ErroreComando(f"MG non esegue «{cosa}»: {err.messaggio or err.codice}") from err

        await self._con_sessione(esegui)

    async def _controllo(self, v: Vettura, cosa: str, tipo: str, parametri: list[dict[str, Any]] | None) -> None:
        await self._comanda(v, cosa, "vehicle/control", {"rvcParams": parametri, "rvcReqType": tipo})

    async def chiudi(self, v: Vettura, _pin: str | None) -> None:
        await self._controllo(v, "chiudi", _CHIUDI, None)

    async def apri(self, v: Vettura, _pin: str | None) -> None:
        # Il 7 dice cosa aprire: 3 le porte (2 sarebbe il portellone).
        parametri = _parametri((4, b"\x00"), (5, b"\x00"), (6, b"\x00"), (7, b"\x03"), _FINE)
        await self._controllo(v, "apri", _APRI, parametri)

    async def trova(self, v: Vettura, _pin: str | None) -> None:
        # Clacson e luci insieme, come il pulsante dell'app.
        await self._controllo(v, "trova", _TROVA, _parametri((1, b"\x01"), (2, b"\x01"), (3, b"\x01"), _FINE))

    async def clima(self, v: Vettura, acceso: bool, _pin: str | None, temperatura: int = 22) -> None:
        if acceso:
            indice = indice_temperatura(v, temperatura)
            parametri = _parametri((19, b"\x02"), (20, bytes([indice])), _FINE)
        else:
            parametri = _parametri((19, b"\x00"), (22, b"\x00"), _FINE)
        await self._controllo(v, "clima", _CLIMA, parametri)

    async def ricarica(self, v: Vettura, avvia: bool, _pin: str | None) -> None:
        corpo = {"chrgCtrlReq": 1 if avvia else 2, "tboxEleccLckCtrlReq": 0, "tboxV2XReq": 0}
        await self._comanda(v, "ricarica", "vehicle/charging/control", corpo)

    async def limite_ricarica(self, v: Vettura, percento: int, _pin: str | None) -> None:
        corpo = {"altngChrgCrntReq": 0, "onBdChrgTrgtSOCReq": codice_da_limite(percento), "tboxV2XSpSOCReq": 0}
        await self._comanda(v, "limite di ricarica", "vehicle/charging/setting", corpo)
