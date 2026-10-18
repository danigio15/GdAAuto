"""Il cliente del cloud europeo di Kia, Hyundai e Genesis.

Si entra come la OneApp del marchio, con email e password:

1. si registra un ``deviceId`` presso il cloud delle auto (``notifications/register``);
2. si apre la pagina di login (IDPConnect) col client della OneApp, si prende
   la chiave pubblica RSA e si manda la password cifrata: torna un ``code``;
3. il ``code`` diventa i token della OneApp (CCI), con la firma ``X-Fingerprint``;
4. il token CCI si scambia con un token «CCS», quello che il cloud delle auto
   (``/api/v1/spa``) accetta.

I token si salvano, e si rinnovano col ``refreshToken`` della OneApp; la
password serve di nuovo solo se il rinnovo non va. I comandi delle auto
recenti (CCS2) vogliono anche il PIN dell'app, che diventa un «control token».

Percorsi, campi e ordine dei passi vengono da
Hyundai-Kia-Connect/hyundai_kia_connect_api (``KiaUvoApiEU.py``,
``ApiImplType1.py``), licenza MIT: vedi RINGRAZIAMENTI.md. Il codice è
scritto da capo, asincrono, su ``aiohttp``.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import json
import logging
import random
import time
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse
import uuid

import aiohttp
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from ...vettura import StatoVettura, Vettura
from ..errori import ErroreApi, ErroreComando, ErroreCredenziali, ErrorePin, ErroreRete
from . import dati
from .firma import firma_rifiutata, intestazioni_firmate, intestazioni_vecchie, timbro
from .marchi import LINGUE, MARCHI, USER_AGENT_APP, USER_AGENT_LOGIN, AppMarchio

_LOGGER = logging.getLogger(__name__)

ID = "hyundai_kia"
NOME = "Hyundai · Kia · Genesis"

#: I comandi neutri. «chiudi» porta con sé anche «apri», che le opzioni tengono spento di base.
COMANDI = {"chiudi", "clima", "ricarica", "limite_ricarica", "destinazione", "trova"}

#: Le auto che GdAAuto legge: elettriche e ibride plug-in.
_TIPI = {"EV": "EV", "PHEV": "PHEV", "PE": "PHEV"}

_TIMEOUT = aiohttp.ClientTimeout(total=30)
#: Il token CCS si rinnova un po' prima che scada.
_MARGINE = timedelta(minutes=5)
#: 7501 token non valido; 4002 il deviceId non vale più.
_CODICI_TOKEN = {"7501"}
_CODICE_DISPOSITIVO = "4002"
_MESSAGGI_CODICI = {
    "4004": "richiesta doppia, è già in corso",
    "4005": "l'auto non accetta questo comando",
    "4081": "l'auto non ha risposto in tempo",
    "5031": "i comandi da remoto non sono disponibili adesso",
    "5091": "troppe richieste: il cloud chiede di aspettare",
    "5921": "il cloud non ha dati per questa auto",
    "9999": "l'auto non ha risposto in tempo",
}


def comandi_di(v: Vettura) -> set[str]:
    """Clacson e luci ci sono solo sulle auto CCS2; il resto su tutte."""
    comandi = set(COMANDI)
    if not v.extra.get("ccs2"):
        comandi.discard("trova")
    return comandi


def lingua_da(lingua: str | None) -> str:
    corta = (lingua or "en").lower()[:2]
    return corta if corta in LINGUE else "en"


def vettura_da_json(voce: dict[str, Any], marchio: str) -> Vettura | None:
    """Una voce di ``/spa/vehicles``; ``None`` se non è elettrica né plug-in."""
    tipo = _TIPI.get(str(voce.get("type") or ""))
    if tipo is None or not voce.get("vin") or not voce.get("vehicleId"):
        return None
    data = str(voce.get("regDate") or "")
    anno = int(data[:4]) if data[:4].isdigit() else None
    modello = str(voce.get("vehicleName") or "").strip()
    return Vettura(
        vin=str(voce["vin"]),
        marca=MARCHI[marchio].nome,
        modello=modello,
        nome=str(voce.get("nickname") or "").strip() or modello,
        anno=anno,
        extra={
            "id": str(voce["vehicleId"]),
            "tipo": tipo,
            "ccs2": int(voce.get("ccuCCS2ProtocolSupport") or 0),
        },
    )


def cifra_password(password: str, n: str, e: str) -> str:
    """La password cifrata con la chiave RSA del login (PKCS#1 v1.5), in esadecimale."""

    def intero(b64: str) -> int:
        return int.from_bytes(base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4)), "big")

    chiave = rsa.RSAPublicNumbers(intero(e), intero(n)).public_key()
    return chiave.encrypt(password.encode(), padding.PKCS1v15()).hex()


def _gradi_vecchi(temperatura: float) -> str:
    """La temperatura come la vogliono le auto vecchie: un indice esadecimale, «10H» è 22 °C."""
    gradi = min(31.5, max(14.0, round(temperatura * 2) / 2))
    return f"{int((gradi - 14) * 2):02X}H"


class ClienteHyundaiKia:
    def __init__(
        self,
        sessione_http: aiohttp.ClientSession,
        email: str,
        password: str,
        marchio: str = "kia",
        lingua: str = "en",
        pin: str | None = None,
        sessione: dict[str, Any] | None = None,
        salva: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        if marchio not in MARCHI:
            raise ValueError(f"Marchio sconosciuto: {marchio}")
        self._http = sessione_http
        self.email = email
        self._password = password
        self.marchio = marchio
        self.app: AppMarchio = MARCHI[marchio]
        self.lingua = lingua_da(lingua)
        self._pin = pin
        #: quello che si salva: deviceId, token CCS con la scadenza, token CCI
        self.sessione: dict[str, Any] = dict(sessione or {})
        self._salva = salva
        self._controllo: tuple[str, float] | None = None
        self._grezzi: dict[str, dict[str, Any]] = {}

    # --- l'accesso ------------------------------------------------------------

    @property
    def _spa(self) -> str:
        return f"{self.app.api}/api/v1/spa/"

    @property
    def _spa2(self) -> str:
        return f"{self.app.api}/api/v2/spa/"

    def _scade(self) -> datetime | None:
        try:
            return datetime.fromisoformat(str(self.sessione["scade"]))
        except (KeyError, ValueError):
            return None

    def _token_valido(self) -> bool:
        scade = self._scade()
        return bool(self.sessione.get("access_token")) and scade is not None and scade - _MARGINE > datetime.now(UTC)

    async def accedi(self) -> None:
        if self._token_valido():
            return
        if self.sessione.get("cci"):
            try:
                await self._rinnova()
            except (ErroreCredenziali, ErroreApi, ErroreRete) as err:
                _LOGGER.debug("Rinnovo dei token %s non riuscito (%s): si rientra con la password", self.app.nome, err)
            else:
                return
        await self._login()

    def _conserva(self, cci: dict[str, Any], ccs: str, durata: int) -> None:
        self.sessione.update(
            access_token=f"Bearer {ccs}",
            scade=(datetime.now(UTC) + timedelta(seconds=durata)).isoformat(),
            cci={k: str(v) for k, v in cci.items() if v},
        )
        self._controllo = None
        if self._salva:
            self._salva(dict(self.sessione))

    async def _richiesta(
        self,
        metodo: str,
        url: str,
        cosa: str,
        *,
        intestazioni: dict[str, str] | None = None,
        **altro: Any,
    ) -> tuple[int, str, aiohttp.ClientResponse]:
        try:
            async with self._http.request(metodo, url, headers=intestazioni, timeout=_TIMEOUT, **altro) as risposta:
                return risposta.status, await risposta.text(), risposta
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ErroreRete(f"{cosa}: il cloud {self.app.nome} non risponde: {err}") from err

    async def _registra_dispositivo(self) -> str:
        """Il ``deviceId`` del cloud delle auto; se la registrazione non va, uno a caso basta al login."""
        corpo = {"pushRegId": f"{random.getrandbits(256):064x}", "pushType": self.app.push, "uuid": str(uuid.uuid4())}
        intestazioni = {
            "ccsp-service-id": self.app.service_id,
            "ccsp-application-id": self.app.app_id,
            "Stamp": timbro(self.app),
            "Content-Type": "application/json;charset=UTF-8",
            "User-Agent": USER_AGENT_APP,
        }
        try:
            http, testo, _ = await self._richiesta(
                "POST", self._spa + "notifications/register", "registrazione", intestazioni=intestazioni, json=corpo
            )
            device_id = json.loads(testo)["resMsg"]["deviceId"] if http == 200 else None
        except (ErroreRete, ValueError, KeyError, TypeError):
            device_id = None
        if not device_id:
            _LOGGER.debug("Registrazione del dispositivo %s non riuscita: si usa un id a caso", self.app.nome)
            return str(uuid.uuid4())
        return str(device_id)

    async def _codice_login(self) -> str:
        """IDPConnect col client della OneApp: la password cifrata, e torna il ``code``."""
        intestazioni = {"User-Agent": USER_AGENT_LOGIN}
        biscotti: dict[str, str] = {}

        def raccogli(risposta: aiohttp.ClientResponse) -> None:
            for nome, morsel in risposta.cookies.items():
                biscotti[nome] = morsel.value

        def con_biscotti() -> dict[str, str]:
            if not biscotti:
                return intestazioni
            return {**intestazioni, "Cookie": "; ".join(f"{k}={v}" for k, v in biscotti.items())}

        url = (
            f"{self.app.login}/auth/api/v2/user/oauth2/authorize?response_type=code"
            f"&client_id={self.app.oneapp_client_id}&redirect_uri={self.app.oneapp_redirect}"
            "&lang=en&state=ccsp&country=de"
        )
        # Si seguono i rimandi a mano, per tenere i cookie della pagina di login.
        for _ in range(10):
            http, testo, risposta = await self._richiesta(
                "GET", url, "pagina di login", intestazioni=con_biscotti(), allow_redirects=False
            )
            raccogli(risposta)
            dove = risposta.headers.get("Location")
            if http in (301, 302, 303, 307, 308) and dove:
                url = urljoin(url, dove)
                continue
            break
        if "abusing" in testo.lower() or "/error?status=400" in url:
            raise ErroreRete(f"Il login {self.app.nome} blocca le richieste automatiche, per ora: riprova più tardi")

        http, testo, risposta = await self._richiesta(
            "GET", f"{self.app.login}/auth/api/v1/accounts/certs", "chiave del login", intestazioni=con_biscotti()
        )
        raccogli(risposta)
        try:
            jwk = json.loads(testo)["retValue"]
            cifrata = cifra_password(self._password, jwk["n"], jwk["e"])
        except (ValueError, KeyError, TypeError) as err:
            raise ErroreRete(f"Il login {self.app.nome} non ha dato la chiave per la password (HTTP {http})") from err

        modulo = {
            "client_id": self.app.oneapp_client_id,
            "encryptedPassword": "true",
            "password": cifrata,
            "redirect_uri": self.app.oneapp_redirect,
            "scope": "",
            "nonce": "",
            "state": "ccsp",
            "username": self.email,
            "connector_session_key": "",
            "kid": str(jwk.get("kid") or ""),
            "_csrf": "",
        }
        http, testo, risposta = await self._richiesta(
            "POST",
            f"{self.app.login}/auth/account/signin",
            "login",
            intestazioni=con_biscotti(),
            data=modulo,
            allow_redirects=False,
        )
        dove = risposta.headers.get("Location", "")
        domanda = parse_qs(urlparse(dove).query)
        if http != 302:
            if http >= 500:
                raise ErroreRete(f"Il login {self.app.nome} ha un guasto (HTTP {http})")
            raise ErroreCredenziali(f"{self.app.nome} rifiuta email o password (HTTP {http})")
        if codice := domanda.get("code"):
            return codice[0]
        if "/web/v1/user/authorization" in dove:
            raise ErroreCredenziali(
                f"{self.app.nome} chiede di accettare le condizioni: entra una volta dall'app o dal sito, e riprova"
            )
        motivo = (domanda.get("error_description") or domanda.get("error") or ["email o password sbagliate"])[0]
        raise ErroreCredenziali(f"{self.app.nome} rifiuta l'accesso: {motivo}")

    async def _cci(self, percorso: str, cosa: str, *, corpo: dict[str, Any] | None = None, **param: str) -> Any:
        """Una chiamata alla OneApp: prima firmata, e sulla strada vecchia solo se la firma è rifiutata."""
        device_id = str(self.sessione.get("device_id") or "")
        testo_corpo = json.dumps(corpo, separators=(",", ":")) if corpo is not None else ""
        token = self.sessione.get("cci") or {}
        auth: dict[str, str] = {}
        if token.get("accessToken"):
            auth["authorization"] = "Bearer " + str(token["accessToken"]).removeprefix("Bearer ").strip()
            auth["Authentication"] = str(token.get("nonCcsToken") or "")
            if token.get("exchangeableAccessToken"):
                auth["exchangeable-token"] = str(token["exchangeableAccessToken"])
                auth["non-ccs-token"] = str(token.get("nonCcsToken") or "")
        if corpo is not None:
            auth["Content-Type"] = "application/json"

        firmate = {**intestazioni_firmate(self.app, device_id, self.lingua, testo_corpo), **auth}
        http, testo, risposta = await self._richiesta(
            "POST", f"{self.app.cci}/{percorso}", cosa, intestazioni=firmate, params=param, data=testo_corpo or None
        )
        if http != 200 and firma_rifiutata(http, testo):
            _LOGGER.warning(
                "%s rifiuta la firma della OneApp per %s: si prova la strada vecchia. Se capita sempre, le chiavi "
                "della firma in marchi.py vanno aggiornate",
                self.app.nome,
                cosa,
            )
            vecchie = {**intestazioni_vecchie(self.app, device_id, self.lingua), **auth}
            vecchio = percorso.replace("auth/v1/", "domain/api/v1/auth/").replace("auth/v2/", "domain/api/v2/auth/")
            http, testo, risposta = await self._richiesta(
                "POST", f"{self.app.cci}/{vecchio}", cosa, intestazioni=vecchie, params=param, data=testo_corpo or None
            )
        if http in (400, 401, 403):
            raise ErroreCredenziali(f"{self.app.nome}: {cosa} rifiutato (HTTP {http}): {testo[:160]}")
        if http != 200:
            raise ErroreRete(f"{cosa}: il cloud {self.app.nome} risponde HTTP {http}")
        try:
            risultato = json.loads(testo)
        except ValueError as err:
            raise ErroreRete(f"{cosa}: risposta non JSON") from err
        # Il rinnovo può portare il token scambiabile nuovo in un cookie «t».
        if (t := risposta.cookies.get("t")) is not None and t.value and isinstance(risultato, dict):
            risultato.setdefault("exchangeableAccessToken", t.value)
        return risultato

    async def _scambia_ccs(self, cci: dict[str, Any]) -> None:
        self.sessione["cci"] = cci
        r = await self._cci("auth/v1/token-exchange", "scambio del token", serviceType="CCS")
        ccs = (r.get("accessToken") or r.get("ccsAccessToken")) if isinstance(r, dict) else None
        if not ccs:
            raise ErroreRete(f"{self.app.nome} non ha dato il token per le auto")
        self._conserva(cci, str(ccs), int(r.get("expiresTime") or 3600))

    async def _login(self) -> None:
        if not self._password:
            raise ErroreCredenziali(f"Serve di nuovo la password {self.app.nome}")
        self.sessione = {"device_id": await self._registra_dispositivo()}
        codice = await self._codice_login()
        r = await self._cci("auth/v1/token", "token della OneApp", code=codice)
        if not isinstance(r, dict) or not r.get("accessToken"):
            raise ErroreRete(f"{self.app.nome} non ha dato i token della OneApp")
        await self._scambia_ccs(r)

    async def _rinnova(self) -> None:
        vecchi = dict(self.sessione.get("cci") or {})
        corpo = {
            nome: str(vecchi.get(nome) or "")
            for nome in (
                "accessToken",
                "refreshToken",
                "exchangeableAccessToken",
                "exchangeableRefreshToken",
                "nonCcsToken",
                "nonCcsRefreshToken",
                "idToken",
            )
        }
        r = await self._cci("auth/v2/token-refresh", "rinnovo dei token", corpo=corpo)
        if not isinstance(r, dict):
            raise ErroreRete("rinnovo dei token: risposta inattesa")
        await self._scambia_ccs({**vecchi, **{k: v for k, v in r.items() if v and isinstance(v, str)}})

    # --- il cloud delle auto ----------------------------------------------------

    def _intestazioni(self, v: Vettura | None = None) -> dict[str, str]:
        return {
            "Authorization": str(self.sessione.get("access_token") or ""),
            "ccsp-service-id": self.app.service_id,
            "ccsp-application-id": self.app.app_id,
            "Stamp": timbro(self.app),
            "ccsp-device-id": str(self.sessione.get("device_id") or ""),
            "Ccuccs2protocolsupport": str(v.extra.get("ccs2") or 0) if v else "0",
            "User-Agent": USER_AGENT_APP,
        }

    async def _chiama(
        self,
        metodo: str,
        url: str,
        cosa: str,
        v: Vettura | None = None,
        corpo: dict[str, Any] | None = None,
        controllo: bool = False,
        _ripeti: bool = True,
    ) -> dict[str, Any]:
        await self.accedi()
        intestazioni = self._intestazioni(v)
        if controllo:
            token = await self._token_controllo()
            intestazioni |= {"Authorization": token, "AuthorizationCCSP": token}
        http, testo, _ = await self._richiesta(metodo, url, cosa, intestazioni=intestazioni, json=corpo)
        try:
            risposta = json.loads(testo) if testo else {}
        except ValueError:
            risposta = None
        if not isinstance(risposta, dict):
            raise ErroreRete(f"{cosa}: risposta non JSON (HTTP {http})")
        codice = str(risposta.get("resCode") or "")
        scaduto = http == 401 or codice in _CODICI_TOKEN or "expired" in str(risposta.get("error") or "").lower()
        if scaduto or codice == _CODICE_DISPOSITIVO:
            if not _ripeti:
                raise ErroreCredenziali(f"{self.app.nome} non accetta più l'accesso: {cosa}")
            if codice == _CODICE_DISPOSITIVO:
                # Il cloud ha dimenticato il dispositivo: se ne registra un altro, con lo stesso account.
                self.sessione["device_id"] = await self._registra_dispositivo()
                if self._salva:
                    self._salva(dict(self.sessione))
            else:
                self.sessione["scade"] = ""
            self._controllo = None
            return await self._chiama(metodo, url, cosa, v, corpo, controllo, _ripeti=False)
        if risposta.get("retCode") == "F" or (http >= 400 and codice):
            messaggio = _MESSAGGI_CODICI.get(codice) or str(risposta.get("resMsg") or "")
            raise ErroreApi(cosa, codice or None, messaggio, http)
        if http >= 400:
            raise ErroreRete(f"{cosa}: il cloud {self.app.nome} risponde HTTP {http}")
        return risposta

    async def vetture(self) -> list[Vettura]:
        r = await self._chiama("GET", self._spa + "vehicles", "elenco delle auto")
        voci = dati.valore(r, "resMsg.vehicles") or []
        trovate = [vettura_da_json(voce, self.marchio) for voce in voci if isinstance(voce, dict)]
        return [v for v in trovate if v is not None]

    async def stato(self, v: Vettura) -> StatoVettura:
        base = f"{self._spa}vehicles/{v.extra['id']}"
        tipo = str(v.extra.get("tipo") or "EV")
        if v.extra.get("ccs2"):
            r = await self._chiama("GET", base + "/ccs2/carstatus/latest", "stato dell'auto", v)
            grezzo = dati.valore(r, "resMsg.state.Vehicle")
            interpreta = dati.interpreta_ccs2
        else:
            r = await self._chiama("GET", base + "/status/latest", "stato dell'auto", v)
            grezzo = dati.valore(r, "resMsg.vehicleStatusInfo")
            interpreta = dati.interpreta_vecchio
        if not isinstance(grezzo, dict):
            raise ErroreApi("stato dell'auto", None, "il cloud non ha dati per questa auto", 200)
        self._grezzi[v.vin] = grezzo
        s = interpreta(grezzo, tipo)
        # Il parcheggio è più fresco della posizione dentro lo stato, e non sveglia l'auto.
        try:
            parcheggio = await self._chiama("GET", base + "/location/park", "posizione", v)
        except (ErroreApi, ErroreRete) as err:
            _LOGGER.debug("Posizione %s non letta: %s", self.app.nome, err)
        else:
            if punto := dati.posizione_parcheggio(parcheggio.get("resMsg")):
                s.latitudine, s.longitudine = punto
                s.posizione_con_segno = True
        return s

    # --- i comandi --------------------------------------------------------------

    async def _token_controllo(self) -> str:
        if self._controllo and self._controllo[1] > time.time() + 30:
            return self._controllo[0]
        if not self._pin:
            raise ErrorePin(f"I comandi {self.app.nome} vogliono il PIN dell'app: scrivilo nelle opzioni di GdAAuto")
        intestazioni = {
            "Authorization": str(self.sessione.get("access_token") or ""),
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT_APP,
        }
        corpo = {"deviceId": self.sessione.get("device_id"), "pin": self._pin}
        http, testo, _ = await self._richiesta(
            "PUT", f"{self.app.api}/api/v1/user/pin?token=", "PIN", intestazioni=intestazioni, json=corpo
        )
        try:
            r = json.loads(testo)
        except ValueError:
            r = {}
        if not isinstance(r, dict) or not r.get("controlToken"):
            raise ErrorePin(f"{self.app.nome} non accetta il PIN (HTTP {http})")
        token = f"Bearer {r['controlToken']}"
        self._controllo = (token, time.time() + float(r.get("expiresTime") or 600))
        return token

    async def _comanda(
        self, v: Vettura, nome: str, percorso: str, corpo: dict[str, Any], v2: bool, controllo: bool
    ) -> None:
        url = f"{self._spa2 if v2 else self._spa}vehicles/{v.extra['id']}/{percorso}"
        try:
            await self._chiama("POST", url, nome, v, corpo, controllo=controllo)
        except ErroreApi as err:
            raise ErroreComando(f"{self.app.nome} non ha eseguito «{nome}»: {err.messaggio or err.codice}") from err

    def _con_pin(self, pin: str | None) -> None:
        if pin:
            self._pin = pin

    async def _porte(self, v: Vettura, azione: str, nome: str) -> None:
        if v.extra.get("ccs2"):
            await self._comanda(v, nome, "ccs2/control/door", {"command": azione}, v2=True, controllo=True)
        else:
            corpo = {"action": azione, "deviceId": self.sessione.get("device_id")}
            await self._comanda(v, nome, "control/door", corpo, v2=False, controllo=False)

    async def chiudi(self, v: Vettura, pin: str | None) -> None:
        self._con_pin(pin)
        await self._porte(v, "close", "chiudi")

    async def apri(self, v: Vettura, pin: str | None) -> None:
        self._con_pin(pin)
        await self._porte(v, "open", "apri")

    async def trova(self, v: Vettura, pin: str | None) -> None:
        if not v.extra.get("ccs2"):
            raise ErroreComando(f"Questa {self.app.nome} non accetta clacson e luci da remoto")
        self._con_pin(pin)
        await self._comanda(v, "trova", "ccs2/control/hornlight", {"command": "on"}, v2=True, controllo=True)

    def _guida(self, v: Vettura) -> str:
        """«R» per la guida a destra: dove l'auto conta in miglia (Regno Unito, Irlanda)."""
        unita = dati.valore(self._grezzi.get(v.vin) or {}, "Drivetrain.FuelSystem.DTE.Unit")
        return "R" if unita in (2, 3) else "L"

    async def clima(self, v: Vettura, acceso: bool, pin: str | None, temperatura: int = 22) -> None:
        self._con_pin(pin)
        if v.extra.get("ccs2"):
            if not acceso:
                corpo: dict[str, Any] = {"command": "stop"}
            else:
                corpo = {
                    "command": "start",
                    "ignitionDuration": 10,
                    "strgWhlHeating": 0,
                    "hvacTempType": 1,
                    "hvacTemp": min(32, max(14, temperatura)),
                    "sideRearMirrorHeating": 0,
                    "drvSeatLoc": self._guida(v),
                    "seatClimateInfo": {
                        "drvSeatClimateState": 0,
                        "psgSeatClimateState": 0,
                        "rrSeatClimateState": 0,
                        "rlSeatClimateState": 0,
                    },
                    "tempUnit": "C",
                    "windshieldFrontDefogState": False,
                }
            await self._comanda(v, "clima", "ccs2/control/temperature", corpo, v2=True, controllo=True)
            return
        corpo = {
            "action": "start" if acceso else "stop",
            "hvacType": 0,
            "options": {"defrost": False, "heating1": 0, "igniOnDuration": 10},
            "tempCode": _gradi_vecchi(temperatura) if acceso else "10H",
            "unit": "C",
        }
        await self._comanda(v, "clima", "control/temperature", corpo, v2=False, controllo=False)

    async def ricarica(self, v: Vettura, avvia: bool, pin: str | None) -> None:
        self._con_pin(pin)
        azione = "start" if avvia else "stop"
        if v.extra.get("ccs2"):
            await self._comanda(v, "ricarica", "ccs2/control/charge", {"command": azione}, v2=True, controllo=True)
        else:
            corpo = {"action": azione, "deviceId": self.sessione.get("device_id")}
            await self._comanda(v, "ricarica", "control/charge", corpo, v2=False, controllo=False)

    async def limite_ricarica(self, v: Vettura, percento: int, pin: str | None) -> None:
        """Il limite in corrente alternata; quello in continua resta com'era. Il cloud li vuole a decine."""
        self._con_pin(pin)
        ac = max(50, min(100, round(percento / 10) * 10))
        dc = dati.limite_rapida(self._grezzi.get(v.vin) or {}) or ac
        corpo = {
            "targetSOClist": [
                {"plugType": 0, "targetSOClevel": int(dc)},
                {"plugType": 1, "targetSOClevel": int(ac)},
            ]
        }
        await self._comanda(v, "limite di ricarica", "charge/target", corpo, v2=False, controllo=False)

    async def destinazione(self, v: Vettura, nome: str, lat: float, lon: float, indirizzo: str = "") -> None:
        punto = {
            "phone": "",
            "waypointID": 1,
            "lang": 1,
            "src": "HERE",
            "coord": {"lat": lat, "lon": lon, "alt": 0, "type": 0},
            "addr": indirizzo,
            "zip": "",
            "placeid": "",
            "name": nome,
        }
        corpo = {"deviceID": self.sessione.get("device_id"), "poiInfoList": [punto]}
        await self._comanda(v, "destinazione", "location/routes", corpo, v2=True, controllo=bool(self._pin))
