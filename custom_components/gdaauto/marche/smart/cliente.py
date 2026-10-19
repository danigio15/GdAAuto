"""Il filo col cloud di smart in Europa (l'app Hello smart, piattaforma Geely ``ecloudeu``).

Si entra in due passi:

1. il login vero lo fa chi configura, **nel suo browser**, sulla pagina smart
   ID dell'app (OIDC sopra Gigya). Alla fine il sito rimanda a un indirizzo
   con ``access_token`` (e ``refresh_token``) nella query o dopo il ``#``:
   quell'indirizzo si incolla in Home Assistant. Nessuna password passa da
   GdAAuto;
2. con quel token si apre una sessione dell'API (``/auth/account/session/secure``),
   che dà il token delle richieste, il suo refresh token, l'id dell'utente e
   l'id del cliente.

Il rinnovo segue la scala dell'app, dal più leggero: si rifà la sessione col
token OAuth che c'è; se il cloud dice che quello è scaduto (codice 1501) lo si
rinnova col refresh token della sessione (``PUT`` sulla stessa risorsa) e si
rifà la sessione. Se non va nemmeno così, serve rientrare dal browser.

Ogni richiesta è firmata (``firma.py``). Le #1 e le #3 parlano con
``api.ecloudeu.com``, le #5 con ``apiv2.ecloudeu.com``. Il cloud ricorda
un'«auto attiva» per conto: prima dei comandi la si sceglie.

Indirizzi, corpi e codici vengono da pySmartHashtag (DasBasti, licenza MIT):
vedi RINGRAZIAMENTI.md.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import json
import logging
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import aiohttp
from yarl import URL

from ...vettura import StatoVettura, Vettura
from ..errori import ErroreApi, ErroreComando, ErroreCredenziali, ErroreRete
from . import dati as d
from .firma import adesso_ms, intestazioni, unisci_parametri

_LOGGER = logging.getLogger(__name__)

ID = "smart"
NOME = "smart"

#: La chiave Gigya dell'app Hello smart europea (pySmartHashtag, MIT).
CHIAVE_GIGYA = "3_L94eyQ-wvJhWm7Afp1oBhfTGXZArUfSHHW9p9Pncg513hZELXsxCfMWHrF8f5P5a"

INDIRIZZI: dict[str, str] = {
    # L'inizio del login: rimanda (2-3 salti) alla pagina smart ID col «context».
    "login": "https://awsapi.future.smart.com/login-app/api/v1/authorize?uiLocales=it-IT",
    "api": "https://api.ecloudeu.com",
    "api_v2": "https://apiv2.ecloudeu.com",
}

SESSIONE = "/auth/account/session/secure"
AUTO = "/device-platform/user/vehicle/secure"
SCEGLI_AUTO = "/device-platform/user/session/update"
STATO = "/remote-control/vehicle/status/"
SOC = "/remote-control/vehicle/status/soc/"
TELEMATICA = "/remote-control/vehicle/telematics/"
DESTINAZIONE = "/geelyTCAccess/tcservices/ihu/send/to/car"
RICARICA_DC = "/geelyTCAccess/tcservices/vehicle/status/qrvs/"
PERMESSO_DIARIO = "/remote-control/user/authorization/insert"
DIARIO = "/geelyTCAccess/tcservices/vehicle/status/journalLogV4/"

#: L'inizio del ``seriesCodeVs`` dice il modello (pySmartHashtag).
MODELLI = {"HX": "#1", "HC": "#3", "HY": "#5"}
#: La webview dell'app: il primo indirizzo del login risponde 403 agli altri.
AGENTE_WEBVIEW = (
    "Mozilla/5.0 (Linux; Android 14; SM-S911B) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Mobile Safari/537.36"
)

_OK = ("1000", "200", "0")
#: Il token della sessione è scaduto: si rinnova e si riprova.
_SESSIONE_SCADUTA = "1402"
#: Il token OAuth sotto la sessione è scaduto (1501; 8500 si comporta uguale).
_OAUTH_SCADUTO = ("1501", "8500")
#: Il cloud ha perso l'auto attiva: la si risceglie e si riprova.
_AUTO_PERSA = ("8006", "4038")
#: Nonce ripetuto: basta riprovare, la firma nuova ne ha un altro.
_NONCE = "1443"
#: «Nessun dato» del diario dei viaggi.
_VUOTO = "8153"

_SALTI = 5
_TIMEOUT = aiohttp.ClientTimeout(total=30)
_STORICO_OGNI_S = 1800
_MARGINE = timedelta(minutes=5)

LIMITE_MIN, LIMITE_MAX, LIMITE_PASSO = 50, 100, 5
CLIMA_MIN, CLIMA_MAX = 16, 30


class _Riprova(Exception):
    """Un codice del cloud che si risolve rinnovando o riscegliendo l'auto."""

    def __init__(self, codice: str, messaggio: str) -> None:
        self.codice = codice
        super().__init__(f"{codice}: {messaggio}")


def comandi_di(_v: Vettura) -> set[str]:
    return {"chiudi", "apri", "trova", "clima", "ricarica", "limite_ricarica", "destinazione"}


def modello_di(serie: str) -> str:
    return MODELLI.get(serie[:2].upper(), serie or "smart")


def token_da(incollato: str) -> tuple[str, str | None]:
    """Il token OAuth (e il refresh, se c'è) dall'indirizzo finale del login, o il token da solo."""
    testo = incollato.strip()
    if "access_token=" in testo:
        url = urlparse(testo)
        for parte in (url.query, url.fragment, testo.split("?", 1)[-1], testo.split("#", 1)[-1]):
            q = parse_qs(parte)
            if q.get("access_token"):
                return q["access_token"][0], (q.get("refresh_token") or [None])[0]
    if testo and " " not in testo and "://" not in testo:
        return testo, None
    raise ErroreCredenziali("Nell'indirizzo incollato non c'è il token di smart: incolla l'indirizzo finale intero")


class ClienteSmart:
    def __init__(
        self,
        sessione_http: aiohttp.ClientSession,
        token: dict[str, Any],
        salva: Callable[[dict[str, Any]], None] | None = None,
        *,
        indirizzi: dict[str, str] | None = None,
    ) -> None:
        self._http = sessione_http
        self.indirizzi = {**INDIRIZZI, **(indirizzi or {})}
        self.token: dict[str, Any] = dict(token)
        self.token.setdefault("dispositivo", secrets.token_hex(8))
        self._salva = salva
        self._serratura = asyncio.Lock()
        self._comando = asyncio.Lock()
        self._auto: dict[str, dict[str, Any]] = {}
        self._storici: dict[str, tuple[dict[str, Any], float]] = {}
        self._permesso_diario: dict[str, str] = {}

    # --- la rete ------------------------------------------------------------------

    def _base(self, vin: str | None) -> str:
        serie = str((self._auto.get(vin or "") or {}).get("seriesCodeVs") or "")
        return self.indirizzi["api_v2"] if serie.upper().startswith("HY") else self.indirizzi["api"]

    async def _http_firmata(
        self,
        cosa: str,
        metodo: str,
        base: str,
        percorso: str,
        parametri: dict[str, object] | None = None,
        corpo: dict[str, Any] | None = None,
        *,
        vin: str | None = None,
        token: str | None = None,
        extra: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        parametri = parametri or {}
        testo = json.dumps(corpo, separators=(",", ":"), ensure_ascii=False) if corpo is not None else None
        codice_modello = (self._auto.get(vin or "") or {}).get("matCode")
        h = intestazioni(self.token["dispositivo"], token, parametri, metodo, percorso, testo, vin, codice_modello)
        h.update(extra or {})
        url = base + percorso + (f"?{unisci_parametri(parametri)}" if parametri else "")
        try:
            async with self._http.request(
                metodo, URL(url, encoded=True), headers=h, data=testo.encode() if testo else None, timeout=_TIMEOUT
            ) as r:
                http = r.status
                grezzo = await r.text()
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise ErroreRete(f"{cosa}: il cloud smart non risponde: {err}") from err
        if http >= 500 or http == 429:
            raise ErroreRete(f"{cosa}: il cloud smart ha un guasto o frena (HTTP {http})")
        try:
            risposta = json.loads(grezzo) if grezzo else {}
        except ValueError as err:
            if http == 403:
                raise ErroreRete(f"{cosa}: il cloud smart blocca le richieste (HTTP 403)") from err
            raise ErroreRete(f"{cosa}: il cloud smart risponde una cosa che non si capisce (HTTP {http})") from err
        return http, risposta if isinstance(risposta, dict) else {}

    async def _chiama(
        self,
        cosa: str,
        metodo: str,
        percorso: str,
        parametri: dict[str, object] | None = None,
        corpo: dict[str, Any] | None = None,
        *,
        vin: str | None = None,
        base: str | None = None,
    ) -> dict[str, Any]:
        """Una richiesta col token, che si rinnova o risceglie l'auto quando il cloud lo chiede."""
        ultimo: Exception | None = None
        for _ in range(3):
            await self._token_valido()
            try:
                http, r = await self._http_firmata(
                    cosa,
                    metodo,
                    base or self._base(vin),
                    percorso,
                    parametri,
                    corpo,
                    vin=vin,
                    token=self.token.get("api_token"),
                )
                return self._esito(cosa, http, r)
            except _Riprova as err:
                ultimo = err
                if err.codice in _AUTO_PERSA and vin and percorso != SCEGLI_AUTO:
                    await self.scegli_auto(vin)
                elif err.codice == _NONCE or (err.codice in _AUTO_PERSA and percorso == SCEGLI_AUTO):
                    continue
                else:
                    async with self._serratura:
                        await self._rinnova(err.codice in _OAUTH_SCADUTO)
        raise ErroreApi(cosa, getattr(ultimo, "codice", None), str(ultimo))

    @staticmethod
    def _esito(cosa: str, http: int, r: dict[str, Any]) -> dict[str, Any]:
        codice = None if r.get("code") is None else str(r.get("code"))
        messaggio = str(r.get("message") or "")
        if http == 401:
            raise _Riprova(_SESSIONE_SCADUTA, messaggio or "HTTP 401")
        if codice in (_SESSIONE_SCADUTA, _NONCE, *_OAUTH_SCADUTO, *_AUTO_PERSA):
            raise _Riprova(codice, messaggio)
        if http == 403:
            raise ErroreRete(f"{cosa}: il cloud smart blocca le richieste (HTTP 403): {messaggio}")
        if http >= 400 or (codice is not None and codice not in _OK):
            raise ErroreApi(cosa, codice, messaggio, http)
        return r

    # --- i token -----------------------------------------------------------------

    async def apri_sessione(self, token_oauth: str, refresh: str | None = None) -> dict[str, Any]:
        """Dal token del login nel browser alla sessione dell'API."""
        self.token["oauth"] = token_oauth
        if refresh:
            self.token["oauth_refresh"] = refresh
        await self._sessione()
        return dict(self.token)

    async def _sessione(self) -> None:
        """La sessione dell'API col token OAuth che c'è (il primo gradino del rinnovo)."""
        if not self.token.get("oauth"):
            raise ErroreCredenziali("Manca il token di smart: serve rientrare dal browser")
        http, r = await self._http_firmata(
            "sessione",
            "POST",
            self.indirizzi["api"],
            SESSIONE,
            {"identity_type": "smart"},
            {"accessToken": self.token["oauth"]},
        )
        dati = r.get("data") if isinstance(r.get("data"), dict) else {}
        if not dati.get("accessToken"):
            codice = str(r.get("code"))
            if codice in _OAUTH_SCADUTO:
                raise _Riprova(codice, str(r.get("message") or ""))
            if http in (400, 401) or codice in ("1400", "1401", "1500", "1502"):
                raise ErroreCredenziali(f"smart rifiuta il token: {r.get('message') or codice}")
            raise ErroreApi("sessione", codice, str(r.get("message") or ""), http)
        self._prendi(dati)

    def _prendi(self, dati: dict[str, Any]) -> None:
        self.token["api_token"] = dati["accessToken"]
        for chiave, campo in (("api_refresh", "refreshToken"), ("utente", "userId"), ("cliente", "clientId")):
            if dati.get(campo):
                self.token[chiave] = str(dati[campo])
        durata = d.numero(dati.get("expiresIn")) or 3600
        self.token["scade"] = (datetime.now(UTC) + timedelta(seconds=durata)).isoformat()

    async def _scambia_refresh(self) -> None:
        """Il secondo gradino: dal refresh token della sessione a un token OAuth nuovo."""
        if not self.token.get("api_refresh") or not self.token.get("cliente"):
            raise ErroreCredenziali("smart: manca il refresh token, serve rientrare dal browser")
        _http, r = await self._http_firmata(
            "rinnovo",
            "PUT",
            self.indirizzi["api"],
            SESSIONE,
            {},
            {"refreshToken": self.token["api_refresh"], "proprietaryPlatform": "0"},
            extra={"X-CLIENT-ID": self.token["cliente"]},
        )
        dati = r.get("data") if isinstance(r.get("data"), dict) else {}
        if not dati.get("accessToken"):
            raise ErroreCredenziali(f"smart non rinnova il token ({r.get('code')}): serve rientrare dal browser")
        self.token["oauth"] = dati["accessToken"]
        if dati.get("refreshToken"):
            self.token["api_refresh"] = str(dati["refreshToken"])
        if dati.get("clientId"):
            self.token["cliente"] = str(dati["clientId"])

    async def _rinnova(self, oauth_scaduto: bool = False) -> None:
        if not oauth_scaduto:
            try:
                await self._sessione()
                self._salva_token()
                return
            except _Riprova:
                pass
        await self._scambia_refresh()
        try:
            await self._sessione()
        except _Riprova as err:
            raise ErroreCredenziali("smart: il token nuovo non vale, serve rientrare dal browser") from err
        self._salva_token()

    def _salva_token(self) -> None:
        if self._salva:
            self._salva(dict(self.token))

    async def _token_valido(self) -> None:
        async with self._serratura:
            scade = self.token.get("scade")
            scaduto = bool(scade) and datetime.fromisoformat(scade) - _MARGINE < datetime.now(UTC)
            if not self.token.get("api_token") or scaduto:
                await self._rinnova()

    # --- il link del login -------------------------------------------------------

    async def link_login(self) -> str:
        """L'indirizzo della pagina smart ID da aprire nel browser.

        Si seguono da qui i primi salti (senza credenziali) finché compare il
        ``context`` del login, come fa l'app: il primo indirizzo risponde 403 a
        un browser da scrivania. Se non si arriva, si dà l'indirizzo d'inizio.
        """
        corrente = self.indirizzi["login"]
        h = {
            "x-app-id": "SmartAPPEU",
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "x-requested-with": "com.smart.hellosmart",
            "user-agent": AGENTE_WEBVIEW,
        }
        try:
            for _ in range(_SALTI):
                async with self._http.get(corrente, headers=h, allow_redirects=False, timeout=_TIMEOUT) as r:
                    dove = r.headers.get("Location", "")
                    if r.status not in (301, 302, 303, 307, 308) or not dove:
                        break
                prossimo = urljoin(corrente, dove)
                if "context" in parse_qs(urlparse(prossimo).query):
                    return prossimo
                corrente = prossimo
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise ErroreRete(f"La pagina del login smart non risponde: {err}") from err
        return self.indirizzi["login"]

    # --- leggere ----------------------------------------------------------------------

    async def accedi(self) -> None:
        await self._token_valido()

    async def vetture(self) -> list[Vettura]:
        parametri: dict[str, object] = {"needSharedCar": 1, "userId": self.token.get("utente", "")}
        r = await self._chiama("elenco delle auto", "GET", AUTO, parametri, base=self.indirizzi["api"])
        elenco: list[Vettura] = []
        for voce in (r.get("data") or {}).get("list") or []:
            if not isinstance(voce, dict) or not voce.get("vin"):
                continue
            vin = str(voce["vin"])
            self._auto[vin] = voce
            modello = modello_di(str(voce.get("seriesCodeVs") or ""))
            elenco.append(
                Vettura(
                    vin=vin,
                    marca=ID,
                    modello=modello,
                    nome=f"smart {modello}",
                    targa=str(voce.get("plateNo") or ""),
                    colore=str(voce.get("colorName") or ""),
                    condivisa=str(voce.get("shareStatus") or "N").upper() not in ("N", ""),
                    extra={
                        "serie": voce.get("seriesCodeVs"),
                        "codice_modello": voce.get("modelCode"),
                        "mat": voce.get("matCode"),
                    },
                )
            )
        return elenco

    def _ricorda(self, v: Vettura) -> None:
        if v.vin not in self._auto:
            self._auto[v.vin] = {"vin": v.vin, "seriesCodeVs": v.extra.get("serie"), "matCode": v.extra.get("mat")}

    def _e_una_5(self, vin: str) -> bool:
        return str((self._auto.get(vin) or {}).get("seriesCodeVs") or "").upper().startswith("HY")

    async def scegli_auto(self, vin: str) -> None:
        """L'auto attiva del conto: il cloud la vuole prima dei comandi."""
        corpo = {"vin": vin, "sessionToken": self.token.get("api_token"), "language": ""}
        await self._chiama("scelta dell'auto", "POST", SCEGLI_AUTO, {}, corpo, base=self._base(vin))

    async def stato(self, v: Vettura) -> StatoVettura:
        self._ricorda(v)
        parametri: dict[str, object] = {"latest": True, "target": "basic%2Cmore", "userId": self.token.get("utente")}
        r = await self._chiama("stato dell'auto", "GET", STATO + v.vin, parametri, vin=v.vin)
        grezzo = (r.get("data") or {}).get("vehicleStatus") or {}
        s = d.interpreta(grezzo)
        try:
            soc = await self._chiama("limite di ricarica", "GET", SOC + v.vin, {"setting": "charging"}, vin=v.vin)
            s.limite_ricarica = d.limite_da_soc((soc.get("data") or {}).get("soc"))
        except (ErroreApi, ErroreRete) as err:
            _LOGGER.debug("Limite di ricarica della smart non letto: %s", err)
        ev = d.gruppo(grezzo, "additionalVehicleStatus", "electricVehicleStatus")
        if self._e_una_5(v.vin) and str(ev.get("dcDcConnectStatus")) == "3":
            await self._dc_della_5(v, s)
        for campo, valore in (await self._storico(v)).items():
            setattr(s, campo, valore)
        return s

    async def _dc_della_5(self, v: Vettura, s: StatoVettura) -> None:
        """La #5 in DC dice corrente e tensione vere solo da qui (pySmartHashtag)."""
        try:
            r = await self._chiama("ricarica DC", "GET", RICARICA_DC + v.vin, {}, vin=v.vin)
        except (ErroreApi, ErroreRete) as err:
            _LOGGER.debug("Ricarica DC della smart #5 non letta: %s", err)
            return
        misura = d.potenza_dc_smart5(r.get("data") or {})
        if misura:
            corrente, tensione = misura
            s.potenza_ricarica_diretta_kw = round(corrente * tensione / 1000, 2)
            s.ricarica_rapida = True

    async def _storico(self, v: Vettura) -> dict[str, Any]:
        """Il diario dei viaggi (km ed energia degli ultimi sette giorni), riletto ogni mezz'ora.

        Facoltativo: se il diario è spento sull'auto il cloud dice 8153, e lo
        stato arriva lo stesso.
        """
        campi, quando = self._storici.get(v.vin, ({}, 0.0))
        if quando and time.monotonic() - quando < _STORICO_OGNI_S:
            return campi
        try:
            campi = d.ultimi_7_giorni(await self._viaggi(v))
        except ErroreApi as err:
            if str(err.codice) != _VUOTO:
                _LOGGER.debug("Diario della smart non letto: %s", err)
        except ErroreRete as err:
            _LOGGER.debug("Diario della smart non letto: %s", err)
        self._storici[v.vin] = (campi, time.monotonic())
        return campi

    async def _viaggi(self, v: Vettura) -> list[dict[str, Any]]:
        # Il diario vuole un permesso per sessione; rifarlo a ogni lettura fa ruotare il token.
        if self._permesso_diario.get(v.vin) != self.token.get("api_token"):
            corpo = {"serviceCode": "travelLogBusiCode", "authStatus": 1, "vin": v.vin}
            await self._chiama("permesso del diario", "POST", PERMESSO_DIARIO, {}, corpo, vin=v.vin)
            self._permesso_diario[v.vin] = str(self.token.get("api_token"))
        fine = int(time.time() * 1000)
        parametri: dict[str, object] = {
            "endTime": str(fine),
            "pageIndex": "1",
            "pageSize": "50",
            "startTime": str(fine - 7 * 86_400_000),
            "userId": str(self.token.get("utente")),
        }
        r = await self._chiama("diario dei viaggi", "GET", DIARIO + v.vin, parametri, vin=v.vin)
        lista = (r.get("data") or {}).get("list")
        return lista if isinstance(lista, list) else []

    # --- comandare ------------------------------------------------------------------------

    async def _telematica(self, v: Vettura, cosa: str, corpo: dict[str, Any]) -> None:
        self._ricorda(v)
        async with self._comando:
            await self.scegli_auto(v.vin)
            r = await self._chiama(cosa, "PUT", TELEMATICA + v.vin, {}, corpo, vin=v.vin)
        if r.get("success") is False:
            raise ErroreComando(f"La smart non esegue «{cosa}»: {r.get('message') or r.get('code')}")

    @staticmethod
    def _servizio(
        servizio: str,
        parametri: list[dict[str, str]],
        comando: str = "start",
        durata: int = 6,
        chiave_ora: str = "timestamp",
    ) -> dict[str, Any]:
        return {
            "creator": "tc",
            "command": comando,
            "operationScheduling": {"duration": durata, "interval": 0, "occurs": 1, "recurrentOperation": False},
            "serviceId": servizio,
            chiave_ora: adesso_ms(),
            "serviceParameters": parametri,
        }

    async def chiudi(self, v: Vettura, _pin: str | None) -> None:
        await self._telematica(v, "chiudi", self._servizio("RDL_2", [{"key": "door", "value": "all"}]))

    async def apri(self, v: Vettura, _pin: str | None) -> None:
        await self._telematica(v, "apri", self._servizio("RDU_2", [{"key": "door", "value": "all"}]))

    async def trova(self, v: Vettura, _pin: str | None) -> None:
        # Le luci, senza clacson: come il pulsante «trova» dell'app.
        await self._telematica(v, "trova", self._servizio("RHL", [{"key": "rhl", "value": "light-flash"}], durata=0))

    async def clima(self, v: Vettura, acceso: bool, _pin: str | None, temperatura: int = 22) -> None:
        gradi = float(min(max(temperatura, CLIMA_MIN), CLIMA_MAX))
        if self._e_una_5(v.vin):
            corpo = self._zaf(acceso, gradi)
        else:
            corpo = {
                "creator": "tc",
                "command": "start" if acceso else "stop",
                "operationScheduling": {"duration": 180, "interval": 0, "occurs": 1, "recurrentOperation": False},
                "serviceId": "RCE_2",
                "timestamp": adesso_ms(),
                "serviceParameters": [
                    {"key": "rce.conditioner", "value": "1"},
                    {"key": "rce.temp", "value": f"{gradi:.1f}"},
                ],
            }
        await self._telematica(v, "clima", corpo)

    def _zaf(self, acceso: bool, gradi: float) -> dict[str, Any]:
        """Il clima della #5 (servizio ZAF): avvio e spegnimento sono tutti e due «start»."""
        comfort = ("SH.11", "SH.12", "SH.21", "SH.22", "SW", "DF", "SV.11", "SV.19")
        if acceso:
            parametri = {"operation": "4", "AC": "true", "AC.temp": f"{gradi:.1f}", "AC.duration": "180"}
            parametri.update(dict.fromkeys(comfort, "false"))
        else:
            parametri = {"operation": "4", "AC": "false", **dict.fromkeys(comfort, "false")}
        return {
            "serviceId": "ZAF",
            "command": "start",
            "creator": "tc",
            "latest": False,
            "timeStamp": adesso_ms(),
            "userId": str(self.token.get("utente")),
            "operationScheduling": {
                "scheduledTime": None,
                "interval": 0,
                "occurs": 0,
                "recurrentOperation": 1,
                "duration": 60,
            },
            "serviceParameters": [{"key": k, "value": val} for k, val in parametri.items()],
        }

    async def ricarica(self, v: Vettura, avvia: bool, _pin: str | None) -> None:
        corpo = {
            "creator": "tc",
            "operationScheduling": {
                "scheduledTime": None,
                "interval": 0,
                "occurs": 1,
                "recurrentOperation": 0,
                "duration": 6,
            },
            "serviceId": "rcs",
            # Il comando è sempre «start»: avviare o fermare lo dicono i parametri.
            "command": "start",
            "timeStamp": adesso_ms(),
            "serviceParameters": [
                {"key": "operation", "value": "1" if avvia else "0"},
                {"key": "rcs.restart" if avvia else "rcs.terminate", "value": "1"},
            ],
        }
        await self._telematica(v, "ricarica", corpo)

    async def limite_ricarica(self, v: Vettura, percento: int, _pin: str | None) -> None:
        if not LIMITE_MIN <= percento <= LIMITE_MAX:
            raise ErroreComando(f"La smart accetta un limite fra {LIMITE_MIN} e {LIMITE_MAX} %")
        percento = round(percento / LIMITE_PASSO) * LIMITE_PASSO
        corpo = self._servizio(
            "rcs",
            [
                {"key": "soc", "value": str(percento * 10)},
                {"key": "operation", "value": "4"},
                {"key": "rcs.setting", "value": "1"},
            ],
            chiave_ora="timeStamp",
        )
        corpo["operationScheduling"] = {
            "scheduledTime": None,
            "interval": 0,
            "occurs": 1,
            "recurrentOperation": 0,
            "duration": 6,
        }
        await self._telematica(v, "limite di ricarica", corpo)

    async def destinazione(self, v: Vettura, nome: str, lat: float, lon: float, indirizzo: str = "") -> None:
        self._ricorda(v)
        corpo = {"lat": float(lat), "lon": float(lon), "name": nome or "GdAAuto", "address": indirizzo}
        async with self._comando:
            await self.scegli_auto(v.vin)
            r = await self._chiama("destinazione", "POST", DESTINAZIONE, {"vin": v.vin}, corpo, vin=v.vin)
        if r.get("success") is False:
            raise ErroreComando(f"La smart non riceve la destinazione: {r.get('message') or r.get('code')}")
