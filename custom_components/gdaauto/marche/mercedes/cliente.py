"""Il filo col cloud Mercedes me, quello dell'app Mercedes-Benz.

Tre pezzi, tutti dell'app ufficiale:

- il login (``id.mercedes-benz.com``): lo stesso sito che l'app apre al primo
  accesso. Si danno email e password come farebbe una persona, e alla fine il
  sito restituisce un codice che diventa un token OAuth (con PKCE). Il token
  dura poco e si rinnova da solo; se il rinnovo non va, si rientra con la
  password salvata. Non c'è captcha, ma l'account non deve avere la verifica
  in due passaggi;
- il «BFF» (``bff.<regione>-prod.mobilesdk.mercedes-benz.com``): REST in JSON
  per l'elenco delle auto e per quello che ognuna sa fare;
- lo stato si legge dal REST dei widget (``vehicleattributes``), che risponde
  con un messaggio protobuf: lo stesso che l'app usa per i widget del telefono
  e che mbapi2020 usa quando il websocket è bloccato. Non sveglia l'auto: è
  l'ultima fotografia che l'auto ha mandato al cloud.

I comandi passano invece dal websocket dell'app: GdAAuto lo apre solo per il
tempo di un comando, manda la richiesta in protobuf, conferma («ack») quello
che il cloud gli spinge, aspetta l'esito e chiude. Un comando alla volta: chi
tiene il websocket aperto a lungo, o lo riapre di continuo, si fa bloccare
l'account per qualche ora (HTTP 429).

Indirizzi, identificativi dell'app e intestazioni vengono da
ReneNulschDE/mbapi2020 (licenza MIT): vedi RINGRAZIAMENTI.md.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
import hashlib
import json
import logging
import re
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
import uuid

import aiohttp

from ...vettura import StatoVettura, Vettura
from ..errori import ErroreApi, ErroreComando, ErroreCredenziali, ErrorePin, ErroreRete
from . import messaggi as m
from .dati import interpreta
from .protobuf import ErroreProtobuf

_LOGGER = logging.getLogger(__name__)

ID = "mercedes"
NOME = "Mercedes-Benz"

# --- l'app, come la descrive mbapi2020 -------------------------------------------------

CLIENT_ID = "62778dc4-1de3-44f4-af95-115f06a3a008"
REDIRECT_URI = "rismycar://login-callback"
SCOPE = "email profile ciam-uid phone openid offline_access"
SISTEMA = "ios"
VERSIONE_SISTEMA = "26.3"
VERSIONE_SDK = "4.10.0"
AGENTE_APP = "Mercedes-Benz/3044 CFNetwork/3860.400.22 Darwin/25.3.0"
AGENTE_SAFARI = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 15_8_3 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/15.6.6 Mobile/15E148 Safari/604.1"
)


@dataclass(frozen=True)
class Indirizzi:
    login: str
    bff: str
    widget: str
    websocket: str
    itunes: str = "https://itunes.apple.com/lookup"


@dataclass(frozen=True)
class Regione:
    nome: str
    indirizzi: Indirizzi
    app: str
    versione: str
    #: lo user agent dell'app nel login e nel websocket: in America e in Asia
    #: porta la versione, ``{versione}`` si riempie da solo.
    agente_login: str
    agente_websocket: str
    negozio: str
    lingua_websocket: str = "de-DE"


_EMEA = Indirizzi(
    login="https://id.mercedes-benz.com",
    bff="https://bff.emea-prod.mobilesdk.mercedes-benz.com",
    widget="https://widget.emea-prod.mobilesdk.mercedes-benz.com",
    websocket="wss://websocket.emea-prod.mobilesdk.mercedes-benz.com/v2/ws",
)
_AMAP = Indirizzi(
    login="https://id.mercedes-benz.com",
    bff="https://bff.amap-prod.mobilesdk.mercedes-benz.com",
    widget="https://widget.amap-prod.mobilesdk.mercedes-benz.com",
    websocket="wss://websocket.amap-prod.mobilesdk.mercedes-benz.com/v2/ws",
)
_FIRMA = f"{SISTEMA} {VERSIONE_SISTEMA}, SDK {VERSIONE_SDK}"

REGIONI: dict[str, Regione] = {
    "europa": Regione("Europa", _EMEA, "mycar-store-ece", "1.68.0 (3060)", AGENTE_APP, AGENTE_APP, "de"),
    "nord_america": Regione(
        "Nord America",
        _AMAP,
        "mycar-store-us",
        "3.67.0",
        AGENTE_APP,
        "mycar-store-us v{versione}, " + _FIRMA,
        "us",
        "en-US",
    ),
    "asia_pacifico": Regione(
        "Asia-Pacifico",
        _AMAP,
        "mycar-store-ap",
        "1.67.0",
        "mycar-store-ap {versione}, " + _FIRMA,
        "mycar-store-ap {versione}, " + _FIRMA,
        "au",
    ),
}

#: I comandi dell'app (``/capabilities/commands``) → i comandi di GdAAuto.
COMANDI_APP = {
    "DOORS_LOCK": "chiudi",
    "DOORS_UNLOCK": "apri",
    "SIGPOS_START": "trova",
    "ZEV_PRECONDITIONING_START": "clima",
    "CHARGING_CONFIGURE": "limite_ricarica",
    "CHARGE_PROGRAM_CONFIGURE": "limite_ricarica",
    "BATTERY_MAX_SOC_CONFIGURE": "limite_ricarica",
}

_TIMEOUT = aiohttp.ClientTimeout(total=30)
_MARGINE = timedelta(minutes=2)
_CONTROLLO_VERSIONE = 6 * 3600
_ATTESA_COMANDO = 60.0
_REDIRECT_MASSIMI = 10
_RIPROVE_GATEWAY = 2  # il login a volte risponde 502/503/504: si riprova
_STATI_GATEWAY = (502, 503, 504)


def comandi_di(v: Vettura) -> set[str]:
    comandi = set(v.extra.get("comandi") or [])
    fatti = {COMANDI_APP[c] for c in comandi if c in COMANDI_APP}
    # Accendere il clima senza poterlo spegnere non è un comando da dare.
    if "clima" in fatti and "ZEV_PRECONDITIONING_STOP" not in comandi:
        fatti.discard("clima")
    return fatti


def _b64url(dati: bytes) -> str:
    return base64.urlsafe_b64encode(dati).decode().rstrip("=")


def _limite_ammesso(percento: int) -> int:
    """Mercedes accetta il limite a decine, dal 50 al 100 %."""
    return max(50, min(100, round(percento / 10) * 10))


class ClienteMercedes:
    def __init__(
        self,
        sessione_http: aiohttp.ClientSession,
        email: str,
        password: str,
        regione: str = "europa",
        token: dict[str, Any] | None = None,
        dispositivo: str | None = None,
        salva: Callable[[dict[str, Any]], None] | None = None,
        *,
        indirizzi: Indirizzi | None = None,
    ) -> None:
        if regione not in REGIONI:
            raise ErroreApi("regione", regione, "regione Mercedes sconosciuta")
        self._http = sessione_http
        self.email = email.strip()
        self._password = password
        self.regione = REGIONI[regione]
        self.indirizzi = indirizzi or self.regione.indirizzi
        self.token: dict[str, Any] = dict(token or {})
        #: Un identificativo del «telefono», fisso per account: il login lo vuole nel cookie CIAM.DEVICE.
        self.dispositivo = dispositivo or str(uuid.uuid4())
        self._salva = salva
        self._versione = self.regione.versione
        self._versione_controllata = 0.0
        self._sessione = str(uuid.uuid4()).upper()
        self._serratura = asyncio.Lock()
        self._un_comando = asyncio.Lock()
        self._ultimi: dict[str, dict[str, dict[str, Any]]] = {}

    # --- le intestazioni dell'app ----------------------------------------------------

    def _agente(self, modello: str) -> str:
        return modello.format(versione=self._versione)

    def _intestazioni_app(self) -> dict[str, str]:
        """Quelle del login e del ``/v1/config``."""
        return {
            "Ris-Os-Name": SISTEMA,
            "Ris-Os-Version": VERSIONE_SISTEMA,
            "Ris-Sdk-Version": VERSIONE_SDK,
            "Ris-Application-Version": self._versione,
            "X-Applicationname": self.regione.app,
            "X-Locale": "en-GB",
            "X-Trackingid": str(uuid.uuid4()),
            "X-Sessionid": self._sessione,
            "User-Agent": self._agente(self.regione.agente_login),
            "Accept-Language": "en-GB",
        }

    def _intestazioni_api(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "X-SessionId": self._sessione,
            "X-TrackingId": str(uuid.uuid4()).upper(),
            "ris-os-name": SISTEMA,
            "ris-os-version": VERSIONE_SISTEMA,
            "ris-sdk-version": VERSIONE_SDK,
            "ris-application-version": self._versione,
            "X-ApplicationName": self.regione.app,
            "X-Locale": "de-DE",
            "User-Agent": AGENTE_APP,
            "Content-Type": "application/json; charset=UTF-8",
        }

    def _intestazioni_websocket(self, token: str) -> dict[str, str]:
        return {
            # Nel websocket il token va senza «Bearer».
            "Authorization": token,
            "APP-SESSION-ID": self._sessione,
            "OUTPUT-FORMAT": "PROTO",
            "X-SessionId": self._sessione,
            "X-TrackingId": str(uuid.uuid4()).upper(),
            "ris-os-name": SISTEMA,
            "ris-os-version": VERSIONE_SISTEMA,
            "ris-sdk-version": VERSIONE_SDK,
            "ris-application-version": self._versione,
            "X-ApplicationName": self.regione.app,
            "X-Locale": self.regione.lingua_websocket,
            "User-Agent": self._agente(self.regione.agente_websocket),
        }

    def _intestazioni_safari(self, accetta: str = "application/json, text/plain, */*") -> dict[str, str]:
        return {
            "Accept": accetta,
            "Content-Type": "application/json",
            "Origin": self.indirizzi.login,
            "Referer": f"{self.indirizzi.login}/ciam/auth/login",
            "Accept-Language": "it-IT,it;q=0.9",
            "User-Agent": AGENTE_SAFARI,
        }

    # --- la versione dell'app ----------------------------------------------------------

    async def _controlla_versione(self) -> None:
        """Se Mercedes chiede di aggiornare l'app, si prende la versione nuova dall'App Store.

        Ogni sei ore al più, e senza mai fallire: è una cortesia, non un requisito.
        """
        if time.monotonic() - self._versione_controllata < _CONTROLLO_VERSIONE and self._versione_controllata:
            return
        self._versione_controllata = time.monotonic()
        try:
            async with self._http.get(
                f"{self.indirizzi.bff}/v1/config", headers=self._intestazioni_app(), timeout=_TIMEOUT
            ) as r:
                config = await r.json(content_type=None) if r.status < 400 else None
            aggiorna = (config or {}).get("forceUpdate") if isinstance(config, dict) else None
            if not isinstance(aggiorna, dict) or aggiorna.get("status") not in ("FORCE", "INFORM_ALWAYS"):
                return
            trovato = re.search(r"/id(\d+)", str(aggiorna.get("storeUrl") or ""))
            if not trovato:
                return
            q = {"id": trovato.group(1), "country": self.regione.negozio}
            async with self._http.get(f"{self.indirizzi.itunes}?{urlencode(q)}", timeout=_TIMEOUT) as r:
                negozio = await r.json(content_type=None) if r.status < 400 else None
            risultati = (negozio or {}).get("results") if isinstance(negozio, dict) else None
            versione = risultati[0].get("version") if isinstance(risultati, list) and risultati else None
            if isinstance(versione, str) and versione and versione != self._versione:
                _LOGGER.info("Mercedes chiede l'app %s: da ora GdAAuto si presenta così", versione)
                self._versione = versione
        except (aiohttp.ClientError, TimeoutError, OSError, ValueError, AttributeError, TypeError) as err:
            _LOGGER.debug("Controllo della versione dell'app Mercedes non riuscito: %s", err)

    # --- il login ------------------------------------------------------------------------

    async def _passo(
        self, cosa: str, metodo: str, url: str, cookie: dict[str, str], **opzioni: Any
    ) -> tuple[int, str, dict[str, str], str]:
        """Un passo del login: stato, indirizzo, intestazioni e testo; i cookie si raccolgono a mano.

        La sessione di Home Assistant è di tutti: i cookie del login restano qui, in un dizionario.
        """
        for tentativo in range(_RIPROVE_GATEWAY + 1):
            try:
                async with self._http.request(
                    metodo, url, cookies=cookie, allow_redirects=False, timeout=_TIMEOUT, **opzioni
                ) as r:
                    for nome, morsel in r.cookies.items():
                        cookie[nome] = morsel.value
                    stato, testo = r.status, await r.text()
                    intestazioni = {k.lower(): v for k, v in r.headers.items()}
            except (aiohttp.ClientError, TimeoutError, OSError) as err:
                raise ErroreRete(f"{cosa}: il login di Mercedes non risponde: {err}") from err
            if stato in _STATI_GATEWAY and tentativo < _RIPROVE_GATEWAY:
                await asyncio.sleep(2 * (tentativo + 1))
                continue
            if stato >= 500 or stato == 429:
                raise ErroreRete(f"{cosa}: il login di Mercedes ha un guasto ({stato})")
            return stato, url, intestazioni, testo
        raise ErroreRete(f"{cosa}: il login di Mercedes non risponde")  # pragma: no cover

    @staticmethod
    def _json(testo: str) -> dict[str, Any]:
        try:
            corpo = json.loads(testo) if testo else {}
        except ValueError:
            return {}
        return corpo if isinstance(corpo, dict) else {}

    async def _entra(self) -> None:
        """Il login completo con email e password, come nel sito che apre l'app."""
        if not self.email or not self._password:
            raise ErroreCredenziali("Mancano email o password di Mercedes")
        await self._controlla_versione()
        login = self.indirizzi.login
        # La sessione di Home Assistant è di tutti e tiene i cookie: quelli vecchi del sito di login
        # (una sessione rimasta aperta) farebbero saltare i passi. Si parte puliti, coi cookie in mano.
        vaso = getattr(self._http, "cookie_jar", None)
        if vaso is not None and hasattr(vaso, "clear_domain"):
            vaso.clear_domain(urlparse(login).hostname or "")
        cookie = {"CIAM.DEVICE": self.dispositivo}
        verificatore = _b64url(secrets.token_bytes(32))
        sfida = _b64url(hashlib.sha256(verificatore.encode()).digest())

        # 1. L'autorizzazione, che dopo qualche rimbalzo dà il parametro «resume».
        url = f"{login}/as/authorization.oauth2?" + urlencode(
            {
                "client_id": CLIENT_ID,
                "code_challenge": sfida,
                "code_challenge_method": "S256",
                "redirect_uri": REDIRECT_URI,
                "response_type": "code",
                "scope": SCOPE,
            }
        )
        pagina = {
            "User-Agent": AGENTE_SAFARI,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "it-IT,it;q=0.9",
        }
        # I rimbalzi si seguono a mano fino alla pagina del login, come un browser, raccogliendo i cookie.
        for _ in range(_REDIRECT_MASSIMI):
            stato, url, intestazioni, _testo = await self._passo("autorizzazione", "GET", url, cookie, headers=pagina)
            if 300 <= stato < 400 and intestazioni.get("location"):
                url = urljoin(url, intestazioni["location"])
                continue
            if stato >= 400:
                raise ErroreApi("login Mercedes", stato, "autorizzazione rifiutata", stato)
            break
        resume = (parse_qs(urlparse(url).query).get("resume") or [None])[0]
        if not resume:
            raise ErroreApi("login Mercedes", None, "il sito non ha dato il parametro resume")

        # 2. Chi siamo (un iPhone con Safari), poi email e password.
        await self._passo(
            "browser",
            "POST",
            f"{login}/ciam/auth/ua",
            cookie,
            json={"browserName": "Mobile Safari", "browserVersion": "15.6.6", "osName": "iOS"},
            headers=self._intestazioni_safari("*/*"),
        )
        stato, _u, _i, testo = await self._passo(
            "email",
            "POST",
            f"{login}/ciam/auth/login/user",
            cookie,
            json={"username": self.email},
            headers=self._intestazioni_safari(),
        )
        if stato >= 400:
            raise ErroreCredenziali(f"Mercedes non riconosce l'email ({stato})")
        rid = secrets.token_urlsafe(24)
        credenziali = {"username": self.email, "password": self._password, "rememberMe": False, "rid": rid}
        stato, _u, _i, testo = await self._passo(
            "password",
            "POST",
            f"{login}/ciam/auth/login/pass",
            cookie,
            json=credenziali,
            headers=self._intestazioni_safari(),
        )
        if stato >= 400:
            raise ErroreCredenziali(f"Mercedes rifiuta email o password ({stato})")
        esito = self._json(testo)
        if esito.get("passkeyDemoEnabled"):
            # Il sito propone le passkey: si dice di no e si continua con la password.
            stato, _u, _i, testo = await self._passo(
                "passkey",
                "POST",
                f"{login}/ciam/auth/disablePasskeyDemo",
                cookie,
                json={**credenziali, "disablePasskeyDemo": True},
                headers=self._intestazioni_safari(),
            )
            if stato >= 400:
                raise ErroreApi("login Mercedes", stato, "rifiuto delle passkey non accettato", stato)
            esito = self._json(testo)
        risultato = str(esito.get("result") or "")
        if risultato == "GOTO_LOGIN_OTP":
            raise ErroreCredenziali(
                "L'account Mercedes ha la verifica in due passaggi: GdAAuto non la supporta, usa un account senza"
            )
        if risultato == "GOTO_LOGIN_LEGAL_TEXTS":
            raise ErroreCredenziali("Mercedes chiede di accettare condizioni nuove: aprile nell'app e accettale")
        if risultato != "RESUME2OIDCP" or not esito.get("token"):
            raise ErroreCredenziali(f"Login Mercedes non riuscito: {risultato or 'risposta inattesa'}")

        # 3. Si riprende l'autorizzazione: il sito rimanda a rismycar://…?code=…
        stato, _u, intestazioni, _t = await self._passo(
            "codice",
            "POST",
            f"{login}{resume}" if resume.startswith("/") else urljoin(f"{login}/", resume),
            cookie,
            data={"token": str(esito["token"])},
            headers={
                **self._intestazioni_safari("text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        destinazione = intestazioni.get("location", "")
        codice = (parse_qs(urlparse(destinazione).query).get("code") or [None])[0]
        if not 300 <= stato < 400 or not destinazione.startswith("rismycar://") or not codice:
            raise ErroreApi("login Mercedes", stato, "il sito non ha dato il codice")

        # 4. Dal codice ai token.
        await self._token_da(
            {
                "client_id": CLIENT_ID,
                "code": codice,
                "code_verifier": verificatore,
                "grant_type": "authorization_code",
                "redirect_uri": REDIRECT_URI,
            }
        )

    async def _token_da(self, parametri: dict[str, str]) -> None:
        intestazioni = {
            **self._intestazioni_app(),
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Device-Id": self.dispositivo,
            "X-Request-Id": str(uuid.uuid4()),
        }
        try:
            async with self._http.post(
                f"{self.indirizzi.login}/as/token.oauth2",
                data=urlencode(parametri),
                headers=intestazioni,
                timeout=_TIMEOUT,
            ) as r:
                stato, testo = r.status, await r.text()
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise ErroreRete(f"Il login di Mercedes non risponde: {err}") from err
        if stato >= 500 or stato == 429:
            raise ErroreRete(f"Il login di Mercedes ha un guasto ({stato})")
        corpo = self._json(testo)
        if stato >= 400 or "access_token" not in corpo:
            raise ErroreCredenziali(f"Mercedes rifiuta il token: {corpo.get('error_description') or stato}")
        scade = datetime.now(UTC) + timedelta(seconds=int(corpo.get("expires_in") or 3600))
        self.token = {
            "access_token": corpo["access_token"],
            "refresh_token": corpo.get("refresh_token") or self.token.get("refresh_token"),
            "scade": scade.isoformat(),
        }
        if self._salva:
            self._salva(dict(self.token))

    async def _rinnova(self) -> None:
        """Col refresh token; se Mercedes non lo accetta più, si rientra con la password."""
        await self._controlla_versione()
        if self.token.get("refresh_token"):
            try:
                await self._token_da({"grant_type": "refresh_token", "refresh_token": self.token["refresh_token"]})
                return
            except ErroreCredenziali as err:
                _LOGGER.info("Il refresh token di Mercedes non vale più (%s): si rientra con la password", err)
        await self._entra()

    def _scaduto(self) -> bool:
        scade = self.token.get("scade")
        if not scade or not self.token.get("access_token"):
            return True
        try:
            return datetime.fromisoformat(scade) - _MARGINE < datetime.now(UTC)
        except ValueError:
            return True

    async def _token_valido(self) -> str:
        async with self._serratura:
            if self._scaduto():
                await self._rinnova()
            return str(self.token["access_token"])

    async def _forza_rinnovo(self, vecchio: str) -> None:
        async with self._serratura:
            if self.token.get("access_token") == vecchio:
                await self._rinnova()

    # --- il REST ---------------------------------------------------------------------------

    async def _chiedi(self, cosa: str, url: str, *, binario: bool = False, tollera: bool = False) -> Any:
        """Una lettura dal BFF o dai widget. ``tollera``: un rifiuto dà ``None`` invece di un errore."""
        for tentativo in (1, 2):
            token = await self._token_valido()
            try:
                async with self._http.get(url, headers=self._intestazioni_api(token), timeout=_TIMEOUT) as r:
                    stato = r.status
                    corpo = await r.read()
            except (aiohttp.ClientError, TimeoutError, OSError) as err:
                raise ErroreRete(f"{cosa}: Mercedes non risponde: {err}") from err
            if stato == 401 and not tollera and tentativo == 1:
                # Un token appena rinnovato a volte non vale ancora: si rinnova e si riprova una volta.
                await self._forza_rinnovo(token)
                continue
            break
        if stato >= 500:
            raise ErroreRete(f"{cosa}: Mercedes ha un guasto ({stato})")
        if stato == 429:
            raise ErroreRete(f"{cosa}: Mercedes chiede di rallentare (429)")
        if stato >= 400 and tollera:
            _LOGGER.debug("%s: Mercedes risponde %s", cosa, stato)
            return None
        if stato == 401:
            raise ErroreCredenziali(f"{cosa}: Mercedes rifiuta il token")
        if stato >= 400:
            raise ErroreApi(cosa, None, corpo[:200].decode(errors="replace"), stato)
        if binario:
            return corpo
        try:
            return json.loads(corpo) if corpo else {}
        except ValueError as err:
            raise ErroreRete(f"{cosa}: Mercedes risponde una cosa che non si capisce") from err

    # --- leggere -------------------------------------------------------------------------------

    async def accedi(self) -> None:
        await self._token_valido()

    async def _capacita(self, vin: str) -> tuple[list[str], bool]:
        """Quello che l'auto sa fare. Alcune auto rispondono 401: allora niente comandi, solo lettura."""
        comandi: list[str] = []
        base = f"{self.indirizzi.bff}/v1/vehicle/{vin}/capabilities"
        elenco = await self._chiedi("comandi dell'auto", f"{base}/commands", tollera=True)
        voci = elenco.get("commands") if isinstance(elenco, dict) else None
        for c in voci if isinstance(voci, list) else []:
            if not isinstance(c, dict) or not c.get("isAvailable"):
                continue
            nome = str(c.get("commandName") or "")
            if nome == "CHARGE_PROGRAM_CONFIGURE":
                # Serve solo se il programma accetta il limite di carica.
                parametri = c.get("parameters") or []
                if not any(isinstance(p, dict) and p.get("parameterName") == "MAX_SOC" for p in parametri):
                    continue
            comandi.append(nome)
        funzioni = await self._chiedi("funzioni dell'auto", base, tollera=True)
        caratteristiche = funzioni.get("features") if isinstance(funzioni, dict) else None
        clima_ora = bool(isinstance(caratteristiche, dict) and caratteristiche.get("precondNow"))
        return sorted(set(comandi)), clima_ora

    async def vetture(self) -> list[Vettura]:
        dati = await self._chiedi("elenco delle auto", f"{self.indirizzi.bff}/v2/vehicles")
        voci = list((dati or {}).get("assignedVehicles") or [])
        for flotta in (dati or {}).get("fleets") or []:
            if isinstance(flotta, dict):
                voci.extend(flotta.get("bookedVehicles") or [])
        elenco: list[Vettura] = []
        visti: set[str] = set()
        for voce in voci:
            if not isinstance(voce, dict):
                continue
            vin = str(voce.get("vin") or voce.get("fin") or "")
            if not vin or vin in visti:
                continue
            visti.add(vin)
            vendita = voce.get("salesRelatedInformation") or {}
            modello = str(((vendita.get("baumuster") or {}).get("baumusterDescription")) or "").strip() or NOME
            comandi, clima_ora = await self._capacita(vin)
            elenco.append(
                Vettura(
                    vin=vin,
                    marca=ID,
                    modello=modello,
                    nome=modello,
                    targa=str(voce.get("licensePlate") or "").strip(),
                    condivisa=voce.get("isOwner") is False,
                    extra={"comandi": comandi, "clima_ora": clima_ora},
                )
            )
        return elenco

    async def stato(self, v: Vettura) -> StatoVettura:
        corpo = await self._chiedi(
            "stato dell'auto", f"{self.indirizzi.widget}/v1/vehicle/{v.vin}/vehicleattributes", binario=True
        )
        if corpo[:1] in (b"{", b"["):
            raise ErroreApi("stato dell'auto", None, corpo[:200].decode(errors="replace"))
        try:
            agg = m.vep(corpo)
        except ErroreProtobuf as err:
            raise ErroreRete(f"stato dell'auto: Mercedes manda dati che non si leggono ({err})") from err
        # Un comando come il limite di carica vuole sapere quale programma è attivo.
        self._ultimi[v.vin] = {**self._ultimi.get(v.vin, {}), **agg.attributi}
        return interpreta(agg)

    # --- comandare --------------------------------------------------------------------------------

    async def _apri_websocket(self) -> aiohttp.ClientWebSocketResponse:
        for tentativo in (1, 2):
            token = await self._token_valido()
            try:
                return await self._http.ws_connect(
                    self.indirizzi.websocket,
                    headers=self._intestazioni_websocket(token),
                    timeout=aiohttp.ClientWSTimeout(ws_receive=_ATTESA_COMANDO, ws_close=10),
                    heartbeat=25,
                )
            except aiohttp.WSServerHandshakeError as err:
                if err.status in (401, 403) and tentativo == 1:
                    await self._forza_rinnovo(token)
                    continue
                if err.status in (401, 403):
                    raise ErroreCredenziali(f"Mercedes rifiuta il token del websocket ({err.status})") from err
                if err.status == 429:
                    raise ErroreRete(
                        "Mercedes ha bloccato per qualche ora i comandi di questo account (troppe connessioni)"
                    ) from err
                raise ErroreRete(f"Il websocket di Mercedes non accetta la connessione ({err.status})") from err
            except (aiohttp.ClientError, TimeoutError, OSError) as err:
                raise ErroreRete(f"Il websocket di Mercedes non risponde: {err}") from err
        raise ErroreRete("Il websocket di Mercedes non risponde")  # pragma: no cover

    async def _comanda(self, v: Vettura, cosa: str, numero: int, corpo: bytes) -> None:
        """Apri il websocket, manda il comando, aspetta l'esito, chiudi. Uno alla volta."""
        async with self._un_comando:
            richiesta = str(uuid.uuid4())
            ws = await self._apri_websocket()
            try:
                await self._attendi_esito(ws, cosa, m.comando(v.vin, richiesta, numero, corpo), richiesta)
            finally:
                await ws.close()

    async def _attendi_esito(self, ws: aiohttp.ClientWebSocketResponse, cosa: str, dati: bytes, richiesta: str) -> None:
        accettato = False
        fine = asyncio.get_running_loop().time() + _ATTESA_COMANDO
        try:
            await ws.send_bytes(dati)
            while (resta := fine - asyncio.get_running_loop().time()) > 0:
                try:
                    msg = await ws.receive(timeout=resta)
                except TimeoutError:
                    break
                if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED):
                    break
                if msg.type == aiohttp.WSMsgType.ERROR:
                    raise ErroreRete(f"{cosa}: il websocket di Mercedes si è interrotto")
                if msg.type != aiohttp.WSMsgType.BINARY:
                    continue
                try:
                    p = m.push(msg.data)
                    risposta = m.ricevuta(p)
                    esiti = m.esiti(p.corpo) if p.tipo == m.PUSH_ESITI else []
                except ErroreProtobuf as err:
                    _LOGGER.debug("Messaggio del websocket Mercedes che non si legge: %s", err)
                    continue
                if risposta:
                    # Senza la ricevuta il cloud rimanda lo stesso messaggio, all'infinito.
                    await ws.send_bytes(risposta)
                for e in esiti:
                    if e.richiesta != richiesta:
                        continue
                    if e.stato == m.FINITO:
                        return
                    if e.stato == m.FALLITO:
                        self._fallito(cosa, e)
                    accettato = accettato or e.stato in m.IN_CORSO
        except (aiohttp.ClientError, OSError) as err:
            raise ErroreRete(f"{cosa}: il websocket di Mercedes si è interrotto: {err}") from err
        if not accettato:
            raise ErroreRete(f"{cosa}: Mercedes non ha risposto al comando")
        # Il cloud l'ha preso in carico: l'auto lo farà quando lo riceve. Lo dirà la prossima lettura.
        _LOGGER.info("Mercedes ha accettato «%s» ma non ha ancora detto com'è andata", cosa)

    @staticmethod
    def _fallito(cosa: str, e: m.Esito) -> None:
        spiegazione = "; ".join(f"{c} {s}".strip() for c, s in e.errori) or "senza spiegazione"
        if e.blocco_secondi:
            raise ErrorePin(f"PIN Mercedes bloccato per {e.blocco_secondi} secondi")
        if e.tentativi_pin or "pin" in spiegazione.lower():
            raise ErrorePin(f"Mercedes non accetta il PIN: {spiegazione}")
        raise ErroreComando(f"Mercedes non esegue «{cosa}»: {spiegazione}")

    def _ha(self, v: Vettura, *comandi: str) -> bool:
        return any(c in (v.extra.get("comandi") or []) for c in comandi)

    async def chiudi(self, v: Vettura, _pin: str | None) -> None:
        await self._comanda(v, "chiudi", m.CHIUDI, m.corpo_chiudi())

    async def apri(self, v: Vettura, pin: str | None) -> None:
        if not pin:
            raise ErrorePin("Per aprire Mercedes vuole il PIN dell'app: mettilo nelle opzioni")
        await self._comanda(v, "apri", m.APRI, m.corpo_apri(pin))

    async def trova(self, v: Vettura, _pin: str | None) -> None:
        await self._comanda(v, "trova", m.TROVA, m.corpo_trova())

    async def clima(self, v: Vettura, acceso: bool, _pin: str | None, temperatura: int = 22) -> None:
        # La temperatura resta quella impostata in auto: Mercedes la cambia con un comando a parte, per zona.
        if acceso:
            tipo = m.CLIMA_ORA if v.extra.get("clima_ora") else m.CLIMA_SUBITO
            await self._comanda(v, "clima", m.CLIMA_AVVIA, m.corpo_clima_avvia(tipo))
        else:
            await self._comanda(v, "clima", m.CLIMA_FERMA, m.corpo_clima_ferma())

    async def limite_ricarica(self, v: Vettura, percento: int, _pin: str | None) -> None:
        limite = _limite_ammesso(int(percento))
        if self._ha(v, "CHARGING_CONFIGURE"):
            await self._comanda(v, "limite di carica", m.CONFIGURA_RICARICA, m.corpo_configura_ricarica(limite))
            return
        scelto = (self._ultimi.get(v.vin, {}).get("selectedChargeProgram") or {}).get("valore")
        programma = scelto if isinstance(scelto, int) and scelto in (0, 2, 3) else 0
        await self._comanda(v, "limite di carica", m.PROGRAMMA_RICARICA, m.corpo_programma_ricarica(programma, limite))


def con_indirizzi(regione: str, **cambi: str) -> Indirizzi:
    """Gli indirizzi di una regione, con qualcosa cambiato (le prove usano un cloud finto)."""
    return replace(REGIONI[regione].indirizzi, **cambi)
