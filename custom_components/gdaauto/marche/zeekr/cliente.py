"""Il filo col cloud Zeekr fuori dalla Cina (l'app Zeekr «overseas» e «global»).

Si entra come l'app, con le sue API JSON (non una pagina web):

1. ``auth/checkUserV2``: l'account esiste in quella regione?
2. ``auth/loginByEmailEncrypt``: email e password cifrata RSA; il centro utenti
   risponde con un suo token;
3. ``user/tspCode``: con quel token, un codice per il gateway TSP;
4. ``ms-user-auth/v1.0/auth/login`` sul gateway: il codice diventa il token
   («bearer») di tutte le altre richieste.

Il token non ha un rinnovo leggero: quando il gateway dice che è scaduto
(``079012``) si rifà il login con la password salvata. Il cloud tiene **una
sessione per account**: un login qui fa uscire l'app sul telefono, e viceversa
(``079021``). Per questo serve un account dedicato, con l'auto condivisa.

Ogni richiesta è firmata (``firma.py``) con le chiavi dell'app, che chi
configura incolla: GdAAuto non le contiene.

Le regioni hanno host diversi: l'Europa (``gateway-pub-azure.zeekr.eu`` e
``eu-snc-tsp-api-gw``) si usa direttamente; per gli altri paesi gli indirizzi si
chiedono al server dell'app (``region/url``), come fa l'app.

Indirizzi, corpi e codici vengono da zeekr_ev_api e zeekr_homeassistant
(Fryyyyy, licenza MIT) e da OpenZeekr (borconi, licenza MIT): vedi
RINGRAZIAMENTI.md.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
import logging
import time
from typing import Any
import uuid

import aiohttp
from yarl import URL

from ...vettura import StatoRicarica, StatoVettura, Vettura
from ..errori import ErroreApi, ErroreCertificato, ErroreComando, ErroreCredenziali, ErroreRete
from . import dati as d
from .firma import ChiaviApp, cifra_password, cifra_vin, firma_centro_utenti, firma_tsp, json_canonico

_LOGGER = logging.getLogger(__name__)

ID = "zeekr"
NOME = "Zeekr"

#: Gli host per regione (zeekr_ev_api ``const.py``, OpenZeekr ``Region.kt``).
REGIONI: dict[str, dict[str, str]] = {
    "EU": {
        "centro_utenti": "https://gateway-pub-azure.zeekr.eu/zeekr-cuc-idaas/",
        "app": "https://gateway-pub-azure.zeekr.eu/overseas-app/",
        "tsp": "https://eu-snc-tsp-api-gw.zeekrlife.com/",
    },
    "SEA": {"tsp": "https://sea-snc-tsp-api-gw.zeekrlife.com/"},
    "UAE": {"tsp": "https://me-snc-tsp-api-gw.zeekrlife.com/"},
    "LA": {"tsp": "https://la-snc-tsp-api-gw.zeekrlife.com/"},
}
#: Il server dell'app che dice gli host dei paesi fuori dall'Europa.
APP_GLOBALE = "https://gateway-pub-hw-em-sg.zeekrlife.com/overseas-app/"
#: Il progetto che il gateway vuole in ``X-PROJECT-ID`` (le regioni non verificate stanno su SEA).
PROGETTI = {"EU": "ZEEKR_EU", "LA": "ZEEKR_LA"}

REGIONE_URL = "region/url"
CONTROLLA_UTENTE = "auth/checkUserV2"
LOGIN = "auth/loginByEmailEncrypt"
PROFILO = "user/info"
CODICE_TSP = "user/tspCode"
LOGIN_TSP = "ms-user-auth/v1.0/auth/login"
AUTO = "ms-app-bff/api/v4.0/veh/vehicle-list"
STATO = "ms-vehicle-status/api/v1.0/vehicle/status/latest"
RICARICA_ORA = "ms-vehicle-status/api/v1.0/vehicle/status/qrvs"
STATO_COMANDI = "ms-app-bff/api/v1.0/remoteControl/getVehicleState"
COMANDO = "ms-remote-control/v1.0/remoteControl/control"
COMANDO_RICARICA = "ms-charge-manage/api/v1.0/charge/control"
LIMITE = "ms-charge-manage/api/v1.0/charge/getLatestSoc"
PIANO = "ms-charge-manage/api/v1.0/charge/getChargingPlan"
DIARIO = "ms-vehicle-trail/v1.0/journalLog/trip/listForPage"
DESTINAZIONE = "ms-lbs-service/api/v2.0/sendToCar"

#: Il client dell'app per il codice TSP e le intestazioni fisse del centro utenti (zeekr_ev_api, MIT).
CLIENT_ID = "1JwLroFkFFIpgFGdTRrm4_nzkkwDkfHj7RxJQb7J8tc"
_CENTRO_UTENTI = {
    "accept-language": "en-GB",
    "app-authorization": "1003",
    "app-code": "32816dbd-ff17-47b7-e250-5dae7d9f8cd4",
    "appcode": "eu-app",
    "appid": "TSP",
    "appsecret": "zeekr_tis",
    "appversion": "1.4.1",
    "call-source": "android",
    "client-id": CLIENT_ID,
    "Content-Type": "application/json; charset=UTF-8",
    "device-name": "sdk_gphone64_x86_64",
    "device-type": "app",
    "language": "en",
    "msgappid": "11002",
    "msgclientid": "1003",
    "tmp-tenant-code": "3300743799505195008",
    "user-agent": "Device/GoogleAppName/com.zeekr.globalAppVersion/1.4.1Platform/androidOSVersion/16Ditto/true",
}
_GATEWAY = {
    "ACCEPT-LANGUAGE": "en-GB",
    "AppId": "ONEX97FB91F061405",
    "Content-Type": "application/json; charset=UTF-8",
    "user-agent": "okhttp/4.12.0",
    "X-API-SIGNATURE-VERSION": "2.0",
    "X-APP-ID": "ZEEKRCNCH001M0001",
    "x-p": "Android",
    "X-PLATFORM": "APP",
}
#: Il telefono che il login dichiara (zeekr_ev_api).
_TELEFONO = {
    "loginDeviceId": "google-sdk_gphone64_x86_64-36-16",
    "loginDeviceJgId": "",
    "loginDeviceType": 1,
    "loginPhoneBrand": "google",
    "loginPhoneModel": "sdk_gphone64_x86_64",
    "loginSystem": "Android",
}

_OK = "000000"
#: Il token del gateway è scaduto: si rifà il login.
_SCADUTO = "079012"
#: L'account è entrato da un altro dispositivo (l'app sul telefono).
_ALTROVE = "079021"
#: Il gateway non riconosce la firma (079025: l'MD5 del corpo; 079024 la firma).
_FIRMA_RIFIUTATA = ("079024", "079025", "079026")
#: L'account non esiste in questa regione; password rifiutata.
_ACCOUNT_SCONOSCIUTO = "9007"
_PASSWORD_RIFIUTATA = ("9300", "9301")

_TIMEOUT = aiohttp.ClientTimeout(total=30)
_STORICO_OGNI_S = 1800
#: Dopo un «entrato altrove» si rientra da soli al massimo una volta ogni tanto,
#: per non litigare con l'app sul telefono.
_ALTROVE_OGNI_S = 1800

LIMITE_MIN, LIMITE_MAX, LIMITE_PASSO = 50, 100, 5
CLIMA_MIN, CLIMA_MAX = 16, 28
CLIMA_DURATA_MIN = 15


class _Rientra(Exception):
    """Il gateway vuole un login nuovo."""

    def __init__(self, codice: str, messaggio: str) -> None:
        self.codice = codice
        super().__init__(f"{codice}: {messaggio}")


def comandi_di(_v: Vettura) -> set[str]:
    return {"chiudi", "apri", "trova", "clima", "ricarica", "limite_ricarica", "destinazione"}


def _successo(r: dict[str, Any]) -> bool:
    return r.get("success") is True or str(r.get("code")) == _OK


def _messaggio(r: dict[str, Any]) -> str:
    return str(r.get("msg") or r.get("message") or r.get("code") or "")


class ClienteZeekr:
    def __init__(
        self,
        sessione_http: aiohttp.ClientSession,
        email: str,
        password: str,
        paese: str,
        regione: str,
        chiavi: ChiaviApp,
        token: dict[str, Any] | None = None,
        salva: Callable[[dict[str, Any]], None] | None = None,
        *,
        regioni: dict[str, dict[str, str]] | None = None,
        app_globale: str = APP_GLOBALE,
    ) -> None:
        self._http = sessione_http
        self.email = email.strip()
        self._password = password
        self.paese = (paese or "IT").upper()
        # La regione trovata al login (fuori dall'Europa la dice il cloud) vince su quella del paese.
        self.regione = str((token or {}).get("regione") or regione or "EU").upper()
        self._chiavi = chiavi
        self._regioni = regioni or REGIONI
        self._app_globale = app_globale
        self.token: dict[str, Any] = dict(token or {})
        self.token.setdefault("dispositivo", str(uuid.uuid4()))
        self._salva = salva
        self._serratura = asyncio.Lock()
        self._comando = asyncio.Lock()
        self._vin_cifrati: dict[str, str] = {}
        self._storici: dict[str, tuple[dict[str, Any], float]] = {}
        self._ultimo_altrove = 0.0

    # --- la rete ------------------------------------------------------------------------

    async def _richiesta(
        self, cosa: str, metodo: str, url: str, intestazioni: dict[str, str], corpo: str | None
    ) -> tuple[int, dict[str, Any]]:
        try:
            async with self._http.request(
                metodo,
                URL(url, encoded=True),
                headers=intestazioni,
                data=corpo.encode() if corpo is not None else None,
                timeout=_TIMEOUT,
            ) as r:
                http = r.status
                grezzo = await r.text()
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise ErroreRete(f"{cosa}: il cloud Zeekr non risponde: {err}") from err
        if http >= 500 or http == 429:
            raise ErroreRete(f"{cosa}: il cloud Zeekr ha un guasto o frena (HTTP {http})")
        try:
            risposta = json.loads(grezzo) if grezzo else {}
        except ValueError as err:
            raise ErroreRete(f"{cosa}: il cloud Zeekr risponde una cosa che non si capisce (HTTP {http})") from err
        return http, risposta if isinstance(risposta, dict) else {"data": risposta}

    async def _centro_utenti(
        self, cosa: str, metodo: str, url: str, corpo: dict[str, Any] | None = None, *, con_token: bool = True
    ) -> tuple[int, dict[str, Any]]:
        testo = json.dumps(corpo, separators=(",", ":"), ensure_ascii=False) if corpo is not None else None
        h = {**_CENTRO_UTENTI, "country": self.paese, "registcountry": self.paese}
        if con_token and self.token.get("centro_utenti"):
            h["authorization"] = str(self.token["centro_utenti"])
        h.update(firma_centro_utenti(metodo, url, testo, self._chiavi.access_key, self._chiavi.secret_key))
        return await self._richiesta(cosa, metodo, url, h, testo)

    async def _gateway(
        self,
        cosa: str,
        metodo: str,
        percorso: str,
        corpo: dict[str, Any] | None = None,
        *,
        vin: str | None = None,
        con_token: bool = True,
    ) -> tuple[int, dict[str, Any]]:
        regione = self._regioni.get(self.regione) or self._regioni["EU"]
        url = str(self.token.get("tsp") or regione["tsp"]) + percorso
        testo = json_canonico(corpo) if corpo is not None else None
        h = {
            **_GATEWAY,
            "X-PROJECT-ID": PROGETTI.get(self.regione, "ZEEKR_SEA"),
            "x-device-id": str(self.token["dispositivo"]),
        }
        if con_token and self.token.get("bearer"):
            h["authorization"] = str(self.token["bearer"])
        if vin:
            h["X-VIN"] = self._vin(vin)
        h = firma_tsp(metodo, url, h, testo, self._chiavi.prod_secret)
        return await self._richiesta(cosa, metodo, url, h, testo)

    def _vin(self, vin: str) -> str:
        if vin not in self._vin_cifrati:
            self._vin_cifrati[vin] = cifra_vin(vin, self._chiavi.vin_key, self._chiavi.vin_iv)
        return self._vin_cifrati[vin]

    @staticmethod
    def _esito(cosa: str, http: int, r: dict[str, Any]) -> dict[str, Any]:
        codice = str(r.get("code") or "")
        messaggio = _messaggio(r)
        if codice in _FIRMA_RIFIUTATA:
            raise ErroreCertificato(f"{cosa}: il cloud Zeekr rifiuta la firma ({codice}): controlla le chiavi")
        if codice in (_SCADUTO, _ALTROVE) or messaggio.lower() == "token expired" or http == 401:
            raise _Rientra(codice or "401", messaggio)
        if http == 403:
            raise ErroreRete(f"{cosa}: il cloud Zeekr blocca le richieste (HTTP 403): {messaggio}")
        if http >= 400 or not _successo(r):
            raise ErroreApi(cosa, codice or None, messaggio, http)
        return r

    async def _chiama(
        self, cosa: str, metodo: str, percorso: str, corpo: dict[str, Any] | None = None, *, vin: str | None = None
    ) -> dict[str, Any]:
        """Una richiesta al gateway col token, che rifà il login quando il gateway lo chiede."""
        for tentativo in range(2):
            await self._accesso_valido()
            usato = self.token.get("bearer")
            http, r = await self._gateway(cosa, metodo, percorso, corpo, vin=vin)
            try:
                return self._esito(cosa, http, r)
            except _Rientra as err:
                if tentativo:
                    raise ErroreApi(cosa, err.codice, "il token nuovo non vale") from err
                if err.codice == _ALTROVE:
                    adesso = time.monotonic()
                    if self._ultimo_altrove and adesso - self._ultimo_altrove < _ALTROVE_OGNI_S:
                        raise ErroreApi(
                            cosa, err.codice, "l'account Zeekr è usato da un altro dispositivo: usane uno dedicato"
                        ) from err
                    self._ultimo_altrove = adesso
                async with self._serratura:
                    if self.token.get("bearer") == usato:
                        await self._login()
        raise ErroreApi(cosa, None, "nessuna risposta valida")

    # --- il login -----------------------------------------------------------------------------

    async def _accesso_valido(self) -> None:
        async with self._serratura:
            if not self.token.get("bearer"):
                await self._login()

    async def accedi(self) -> None:
        """Entra, se non c'è già un token."""
        await self._accesso_valido()

    async def _host(self) -> dict[str, str]:
        """Gli host del paese: l'Europa è nota, gli altri si chiedono al server dell'app."""
        nota = self._regioni.get(self.regione, {})
        if nota.get("centro_utenti") and nota.get("tsp"):
            return {"centro_utenti": nota["centro_utenti"], "tsp": nota["tsp"]}
        _http, r = await self._centro_utenti("regione", "GET", self._app_globale + REGIONE_URL, con_token=False)
        for voce in r.get("data") or []:
            if not isinstance(voce, dict) or str(voce.get("countryCode") or "").upper() != self.paese:
                continue
            centro = str((voce.get("url") or {}).get("userCenterUrl") or "")
            regione = str(voce.get("regionCode") or self.regione).upper()
            tsp = (self._regioni.get(regione) or self._regioni.get(self.regione) or {}).get("tsp")
            if centro and tsp:
                self.regione = regione
                return {"centro_utenti": centro if centro.endswith("/") else centro + "/", "tsp": tsp}
        raise ErroreApi("regione", None, f"il cloud Zeekr non conosce il paese {self.paese}")

    async def _login(self) -> None:
        """Il login completo dell'app (va chiamato con la serratura presa)."""
        if not self.email or not self._password:
            raise ErroreCredenziali("Mancano email o password Zeekr")
        host = await self._host()
        centro = host["centro_utenti"]
        self.token.pop("centro_utenti", None)
        self.token.pop("bearer", None)

        http, r = await self._centro_utenti(
            "controllo dell'account", "POST", centro + CONTROLLA_UTENTE, {"email": self.email, "checkType": "1"}
        )
        if not _successo(r):
            codice = str(r.get("code") or "")
            if codice == _ACCOUNT_SCONOSCIUTO:
                raise ErroreCredenziali("Zeekr non conosce questo account in questa regione: controlla email e paese")
            if http in (401, 403) or "sign" in _messaggio(r).lower():
                raise ErroreCertificato(f"Il cloud Zeekr rifiuta le chiavi dell'app: {_messaggio(r)}")
            raise ErroreApi("controllo dell'account", codice or None, _messaggio(r), http)

        corpo = {
            "code": "",
            "codeId": "",
            "email": self.email,
            "password": cifra_password(self._password, self._chiavi.chiave_pubblica),
        }
        http, r = await self._centro_utenti("login", "POST", centro + LOGIN, corpo, con_token=False)
        dati = r.get("data") if isinstance(r.get("data"), dict) else {}
        if not _successo(r) or dati.get("tokenName") != "Authorization" or not dati.get("tokenValue"):
            codice = str(r.get("code") or "")
            if http in (400, 401) or codice in _PASSWORD_RIFIUTATA or not _successo(r):
                raise ErroreCredenziali(f"Zeekr rifiuta il login: {_messaggio(r) or codice}")
            raise ErroreApi("login", codice or None, "il cloud non dà il token", http)
        self.token["centro_utenti"] = dati["tokenValue"]

        try:
            _http, r = await self._centro_utenti("profilo", "POST", centro + PROFILO)
            profilo = r.get("data") if isinstance(r.get("data"), dict) else {}
            if utente := profilo.get("userId") or profilo.get("id") or profilo.get("uuid"):
                self.token["utente"] = str(utente)
        except ErroreRete as err:
            _LOGGER.debug("Profilo Zeekr non letto: %s", err)

        http, r = await self._centro_utenti("codice TSP", "GET", f"{centro}{CODICE_TSP}?tspClientId={CLIENT_ID}")
        codice_tsp = (r.get("data") or {}).get("code") if isinstance(r.get("data"), dict) else None
        if not codice_tsp:
            raise ErroreApi("codice TSP", r.get("code"), _messaggio(r), http)

        self.token["tsp"] = host["tsp"]
        http, r = await self._gateway(
            "login al gateway",
            "POST",
            LOGIN_TSP,
            {"identifier": codice_tsp, "identityType": 10, **_TELEFONO},
            con_token=False,
        )
        codice = str(r.get("code") or "")
        if codice in _FIRMA_RIFIUTATA:
            raise ErroreCertificato(f"Il gateway Zeekr rifiuta la firma ({codice}): controlla il prod secret")
        bearer = (r.get("data") or {}).get("accessToken") if isinstance(r.get("data"), dict) else None
        if not bearer:
            raise ErroreApi("login al gateway", codice or None, _messaggio(r), http)
        self.token["bearer"] = bearer
        self.token["regione"] = self.regione
        if self._salva:
            self._salva(dict(self.token))

    # --- leggere ------------------------------------------------------------------------

    async def vetture(self) -> list[Vettura]:
        r = await self._chiama("elenco delle auto", "GET", f"{AUTO}?needSharedCar=true")
        elenco: list[Vettura] = []
        for voce in d.viaggi_da(r):
            vin = str(voce.get("vin") or "")
            if not vin:
                continue
            modello = d.modello_di(voce)
            soprannome = str(voce.get("nickName") or voce.get("vehicleNickname") or voce.get("vehNickname") or "")
            proprietario = str(voce.get("isOwner") or "").lower() in ("true", "1")
            elenco.append(
                Vettura(
                    vin=vin,
                    marca=ID,
                    modello=modello,
                    nome=soprannome or f"Zeekr {modello}",
                    targa=str(voce.get("plateNo") or voce.get("plateNumber") or ""),
                    colore=str(voce.get("colorName") or ""),
                    condivisa="isOwner" in voce and not proprietario,
                    extra={"codice_modello": voce.get("appModelCode") or voce.get("seriesCode")},
                )
            )
        return elenco

    async def stato(self, v: Vettura) -> StatoVettura:
        r = await self._chiama("stato dell'auto", "GET", f"{STATO}?latest=false&target=new", vin=v.vin)
        s = d.interpreta(r.get("data") if isinstance(r.get("data"), dict) else {})
        # Le letture a parte sono facoltative: se una non va, lo stato arriva lo stesso.
        try:
            limite = await self._chiama("limite di ricarica", "GET", LIMITE, vin=v.vin)
            s.limite_ricarica = d.limite_da_soc((limite.get("data") or {}).get("soc"))
        except Exception as err:
            _LOGGER.debug("Limite di ricarica della Zeekr non letto: %s", err)
        try:
            comandi = await self._chiama("stato dei comandi", "GET", STATO_COMANDI, vin=v.vin)
            s.sentinella = d.sentinella_da(comandi.get("data") or {})
        except Exception as err:
            _LOGGER.debug("Stato dei comandi della Zeekr non letto: %s", err)
        if s.cavo_collegato or s.stato_ricarica == StatoRicarica.IN_CARICA:
            try:
                ricarica = await self._chiama("ricarica in corso", "GET", RICARICA_ORA, vin=v.vin)
                d.aggiungi_ricarica(s, ricarica.get("data") or {})
            except Exception as err:
                _LOGGER.debug("Ricarica della Zeekr non letta: %s", err)
        for campo, valore in (await self._storico(v)).items():
            setattr(s, campo, valore)
        return s

    async def _storico(self, v: Vettura) -> dict[str, Any]:
        """La ricarica programmata e il diario dei viaggi, riletti ogni mezz'ora; facoltativi."""
        campi, quando = self._storici.get(v.vin, ({}, 0.0))
        if quando and time.monotonic() - quando < _STORICO_OGNI_S:
            return campi
        nuovi = dict(campi)
        try:
            piano = await self._chiama("ricarica programmata", "GET", PIANO, vin=v.vin)
            nuovi.update(d.piano_di_ricarica(piano.get("data") or {}))
        except Exception as err:
            _LOGGER.debug("Ricarica programmata della Zeekr non letta: %s", err)
        try:
            fine = int(time.time() * 1000)
            corpo = {
                "currentPage": 1,
                "endTime": fine,
                "lastId": -1,
                "pageSize": 50,
                "startTime": fine - 7 * 86_400_000,
            }
            diario = await self._chiama("diario dei viaggi", "POST", DIARIO, corpo, vin=v.vin)
            nuovi.update(d.ultimi_7_giorni(d.viaggi_da(diario.get("data"))))
        except Exception as err:
            _LOGGER.debug("Diario della Zeekr non letto: %s", err)
        self._storici[v.vin] = (nuovi, time.monotonic())
        return nuovi

    # --- comandare -------------------------------------------------------------------------

    async def _comanda(
        self, v: Vettura, cosa: str, servizio: str, comando: str, parametri: list[tuple[str, str]]
    ) -> None:
        percorso = COMANDO_RICARICA if servizio == "RCS" else COMANDO
        corpo = {
            "command": comando,
            "serviceId": servizio,
            "setting": {"serviceParameters": [{"key": k, "value": val} for k, val in parametri]},
        }
        async with self._comando:
            try:
                await self._chiama(cosa, "POST", percorso, corpo, vin=v.vin)
            except ErroreApi as err:
                raise ErroreComando(f"La Zeekr non esegue «{cosa}»: {err.messaggio or err.codice}") from err

    async def chiudi(self, v: Vettura, _pin: str | None) -> None:
        await self._comanda(v, "chiudi", "RDL", "start", [("door", "all")])

    async def apri(self, v: Vettura, _pin: str | None) -> None:
        # Lo sblocco è «stop» del servizio RDU, come nell'app.
        await self._comanda(v, "apri", "RDU", "stop", [("door", "all")])

    async def trova(self, v: Vettura, _pin: str | None) -> None:
        # Le luci, senza clacson.
        await self._comanda(v, "trova", "RHL", "start", [("rhl", "light-flash")])

    async def clima(self, v: Vettura, acceso: bool, _pin: str | None, temperatura: int = 22) -> None:
        # Il servizio ZAF vuole sempre «start»: acceso o spento lo dice il parametro AC.
        if acceso:
            gradi = float(min(max(temperatura, CLIMA_MIN), CLIMA_MAX))
            parametri = [("AC", "true"), ("AC.temp", f"{gradi:.1f}"), ("AC.duration", str(CLIMA_DURATA_MIN))]
        else:
            parametri = [("AC", "false")]
        await self._comanda(v, "clima", "ZAF", "start", parametri)

    async def ricarica(self, v: Vettura, avvia: bool, _pin: str | None) -> None:
        if avvia:
            await self._comanda(v, "ricarica", "RCS", "start", [("rcs.restart", "1")])
        else:
            await self._comanda(v, "ricarica", "RCS", "stop", [("rcs.terminate", "1")])

    async def limite_ricarica(self, v: Vettura, percento: int, _pin: str | None) -> None:
        if not LIMITE_MIN <= percento <= LIMITE_MAX:
            raise ErroreComando(f"La Zeekr accetta un limite fra {LIMITE_MIN} e {LIMITE_MAX} %")
        percento = round(percento / LIMITE_PASSO) * LIMITE_PASSO
        # L'ordine conta: «soc» per primo, o il cloud risponde «parametro sbagliato» (OpenZeekr).
        await self._comanda(
            v,
            "limite di ricarica",
            "RCS",
            "start",
            [("soc", str(percento * 10)), ("rcs.setting", "1"), ("altCurrent", "1")],
        )

    async def destinazione(self, v: Vettura, nome: str, lat: float, lon: float, indirizzo: str = "") -> None:
        corpo = {
            "address": indirizzo,
            "city": "",
            "content": "",
            "csys": "WGS-84",
            "latitude": float(lat),
            "longitude": float(lon),
            "name": nome or "GdAAuto",
            "source": "zeekr",
        }
        async with self._comando:
            try:
                await self._chiama("destinazione", "POST", DESTINAZIONE, corpo, vin=v.vin)
            except ErroreApi as err:
                raise ErroreComando(f"La Zeekr non riceve la destinazione: {err.messaggio or err.codice}") from err
