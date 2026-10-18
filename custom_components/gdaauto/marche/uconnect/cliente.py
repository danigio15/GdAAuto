"""Il filo col cloud Uconnect dei marchi ex-FCA: entra, legge, comanda.

Come si entra, in quattro passi (gli stessi dell'app e del sito):

1. **Gigya** (il login di SAP sul sito del marchio, ``loginmyuconnect.fiat.com``
   e simili): email e password danno un ``login_token`` che vale 90 giorni,
   e l'``UID`` dell'account. Il token si salva, così la password non si
   manda a ogni ora: con lui Gigya rilascia un JWT nuovo quando serve.
2. Il JWT di Gigya va a ``authz…/v2/cognito/identity/token``, che risponde
   con un'identità **AWS Cognito** e il suo token.
3. **Cognito** (``GetCredentialsForIdentity``, una chiamata JSON senza firma)
   consegna credenziali AWS temporanee, valide circa un'ora.
4. Ogni richiesta ai canali (``channels.sdpr-01.fcagcv.com`` in Europa) si
   firma con quelle credenziali, alla AWS SigV4 (``firma.py``).

Si legge solo quello che il cloud ha già: lo stato, la posizione e le
serrature. Il «deep refresh», che sveglia l'auto, non si usa mai per leggere.

I comandi vogliono il PIN dell'app: prima lo si verifica (``mfa…/ignite/pin
/authenticate``), e il servizio dà un token monouso che accompagna il
comando. Il cloud risponde «pending» con un ``correlationId``, e l'esito
arriva qualche secondo dopo. Un comando alla volta.

Non sa niente di Home Assistant: gli si passa una sessione ``aiohttp``, le
credenziali e una funzione ``salva`` per conservare il token di Gigya.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import json
import logging
import time
from typing import Any
from urllib.parse import quote, urlencode
import uuid

import aiohttp
from yarl import URL

from ...vettura import StatoVettura, Vettura
from ..errori import ErroreApi, ErroreComando, ErroreCredenziali, ErrorePin, ErroreRete
from . import firma
from .dati import interpreta
from .regioni import Configurazione, configurazione

_LOGGER = logging.getLogger(__name__)

ID = "uconnect"
NOME = "Uconnect"

_TIMEOUT = aiohttp.ClientTimeout(total=30)
_MARGINE = timedelta(minutes=2)
#: 90 giorni: quanto dura il login di Gigya prima di chiedere di nuovo la password.
_DURATA_SESSIONE = "7776000"
_AGENTE = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_CAMPI_JWT = "profile.firstName,profile.lastName,profile.email,country,locale,data.disclaimerCodeGSDP"

#: Gli errori di Gigya che vogliono dire «queste credenziali non vanno».
_GIGYA_CREDENZIALI = {
    403042,  # email o password sbagliate
    403047,  # account che non esiste
    401020,  # il sito vuole un captcha: si entra prima dal sito o dall'app
    206001,  # registrazione da completare (condizioni da accettare)
    206002,  # email da verificare
    403043,  # account collegato a un altro
    401030,  # password vecchia, da cambiare
}
#: Gigya: il login_token salvato non vale più (si rientra con la password).
_GIGYA_SESSIONE_SCADUTA = {403005, 403007, 401000, 400006, 400003}
#: Gigya: troppi tentativi. Non è la password sbagliata: si aspetta.
_GIGYA_BLOCCATO = {403120, 403048}

#: Le risposte dell'API Gateway che vogliono dire «firma o credenziali AWS scadute».
_AWS_SCADUTE = (
    "ExpiredToken",
    "UnrecognizedClient",
    "InvalidSignature",
    "IncompleteSignature",
    "MissingAuthenticationToken",
    "InvalidClientTokenId",
)

#: Le versioni dello stato: le app nuove usano la v3; le auto vecchie la v4 o la v2.
_VERSIONI_STATO = ("v3", "v4", "v2")

# I comandi del cloud (nome, percorso, versione dell'API).
CMD_CHIUDI = ("RDL", "remote", "v1")
CMD_APRI = ("RDU", "remote", "v1")
CMD_TROVA = ("HBLF", "remote", "v1")
CMD_CLIMA_ON = ("ROPRECOND", "remote", "v1")
CMD_CLIMA_OFF = ("ROPRECOND_OFF", "remote", "v1")
CMD_RICARICA = ("CNOW", "ev/chargenow", "v1")
#: Se la v1 risponde 403, le auto nuove vogliono questa.
CMD_RICARICA_V4 = ("START_CHARGE", "ev/chargenow", "v4")

#: Il comando neutro di GdAAuto → i servizi Uconnect che lo permettono.
SERVIZI = {
    "chiudi": {"RDL", "RDU"},
    "trova": {"HBLF"},
    "clima": {"ROPRECOND"},
    "ricarica": {"CNOW", "START_CHARGE"},
}


def comandi_di(v: Vettura) -> set[str]:
    """I comandi dai servizi attivi dell'auto. Senza elenco decide il cloud."""
    servizi = set(v.extra.get("servizi") or [])
    if not servizi:
        return set(SERVIZI)
    return {nome for nome, richiesti in SERVIZI.items() if servizi & richiesti}


def vettura_da_json(voce: dict[str, Any]) -> Vettura:
    """L'anagrafica dall'elenco dell'account."""
    servizi = sorted(
        str(s.get("service"))
        for s in voce.get("services") or []
        if isinstance(s, dict) and s.get("service") and _si(s.get("vehicleCapable")) and _si(s.get("serviceEnabled"))
    )
    marca = str(voce.get("make") or "").strip()
    modello = str(voce.get("modelDescription") or voce.get("model") or "").strip()
    anno = str(voce.get("tsoModelYear") or voce.get("year") or "")
    nome = str(voce.get("nickname") or "").strip() or " ".join(p for p in (marca.title(), modello) if p)
    return Vettura(
        vin=str(voce["vin"]),
        marca=ID,
        modello=modello or marca.title() or NOME,
        nome=nome or NOME,
        anno=int(anno) if anno.isdigit() else None,
        colore=str(voce.get("color") or ""),
        extra={
            "servizi": servizi,
            "marca": marca,
            "alimentazione": voce.get("fuelType"),
            "sdp": voce.get("sdp"),
        },
    )


def _intero(v: Any, predefinito: int = 0) -> int:
    try:
        return int(v) if v not in (None, "") else predefinito
    except (TypeError, ValueError):
        return predefinito


def _si(v: Any) -> bool:
    return v is True or (isinstance(v, str) and v.lower() == "true")


def _messaggio(corpo: Any) -> str:
    if not isinstance(corpo, dict):
        return str(corpo)[:200]
    for chiave in ("debugMsg", "message", "Message", "errorDetails", "errorMessage", "name"):
        if corpo.get(chiave):
            return str(corpo[chiave])
    return json.dumps(corpo)[:200]


class ClienteUconnect:
    def __init__(
        self,
        sessione_http: aiohttp.ClientSession,
        *,
        email: str,
        password: str,
        marchio: str = "fiat",
        regione: str = "eu",
        pin: str | None = None,
        sessione: dict[str, Any] | None = None,
        salva: Callable[[dict[str, Any]], None] | None = None,
        config: Configurazione | None = None,
    ) -> None:
        self._http = sessione_http
        self.email = email
        self.password = password
        self.marchio = marchio
        self.config = config or configurazione(marchio, regione)
        self.pin = pin or None
        #: quello che si salva: l'UID e il login_token di Gigya
        self.sessione: dict[str, Any] = dict(sessione or {})
        self._salva = salva
        self._credenziali: dict[str, Any] | None = None
        self._biscotti: dict[str, str] = {}
        self._serratura = asyncio.Lock()
        self._serratura_comandi = asyncio.Lock()
        self._versione_stato: dict[str, str] = {}
        self._codice_marchio: str | None = None
        #: ogni quanto e per quanto si aspetta l'esito di un comando
        self.attesa_esito = 3.0
        self.tempo_esito = 45.0

    @property
    def uid(self) -> str:
        return str(self.sessione.get("uid") or "")

    # --- Gigya -----------------------------------------------------------------------

    def _parametri_gigya(self, **altro: str) -> dict[str, str]:
        return {
            "APIKey": self.config.chiave_login,
            "sdk": "js_latest",
            "sdkBuild": "12234",
            "format": "json",
            **altro,
        }

    async def _gigya(
        self, cosa: str, metodo: str, campi: dict[str, str], cookie: dict[str, str] | None = None
    ) -> tuple[dict[str, Any], dict[str, str]]:
        url = f"{self.config.login}/{metodo}"
        intestazioni = {"User-Agent": _AGENTE, "Accept": "*/*"}
        if cookie:
            intestazioni["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookie.items())
        try:
            if metodo == "accounts.webSdkBootstrap":
                richiesta = self._http.get(url, params=campi, headers=intestazioni, timeout=_TIMEOUT)
            else:
                intestazioni["Content-Type"] = "application/x-www-form-urlencoded"
                richiesta = self._http.post(url, data=urlencode(campi), headers=intestazioni, timeout=_TIMEOUT)
            async with richiesta as r:
                http = r.status
                testo = await r.text()
                biscotti = {k: m.value for k, m in r.cookies.items()}
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise ErroreRete(f"{cosa}: il login Uconnect non risponde: {err}") from err
        if http >= 500:
            raise ErroreRete(f"{cosa}: il login Uconnect ha un guasto (HTTP {http})")
        try:
            corpo = json.loads(testo)
        except ValueError as err:
            raise ErroreRete(f"{cosa}: risposta non JSON (HTTP {http}): {testo[:120]}") from err
        if not isinstance(corpo, dict):
            raise ErroreRete(f"{cosa}: risposta inattesa (HTTP {http})")
        return corpo, biscotti

    @staticmethod
    def _controlla_gigya(cosa: str, corpo: dict[str, Any]) -> None:
        codice = _intero(corpo.get("errorCode"))
        stato = _intero(corpo.get("statusCode"), 200)
        if codice == 0 and stato == 200:
            return
        messaggio = str(corpo.get("errorDetails") or corpo.get("errorMessage") or "")
        if stato >= 500 or codice >= 500000:
            raise ErroreRete(f"{cosa}: il login Uconnect ha un guasto: {messaggio or codice}")
        if codice in _GIGYA_BLOCCATO:
            raise ErroreApi(cosa, codice, f"account bloccato per troppi tentativi: {messaggio}", stato)
        if codice in _GIGYA_CREDENZIALI:
            raise ErroreCredenziali(f"Uconnect rifiuta l'accesso: {messaggio or codice}")
        raise ErroreApi(cosa, codice, messaggio, stato)

    async def _login_password(self) -> None:
        """Il login vero, con email e password: una volta ogni 90 giorni."""
        _, biscotti = await self._gigya(
            "avvio del login",
            "accounts.webSdkBootstrap",
            {"apiKey": self.config.chiave_login, "sdk": "js_latest", "sdkBuild": "12234", "format": "json"},
        )
        corpo, altri = await self._gigya(
            "login",
            "accounts.login",
            self._parametri_gigya(
                loginID=self.email,
                password=self.password,
                sessionExpiration=_DURATA_SESSIONE,
                targetEnv="jssdk",
                include="profile,data,emails",
                includeUserInfo="true",
                loginMode="standard",
                authMode="cookie",
            ),
            biscotti,
        )
        self._controlla_gigya("login", corpo)
        login_token = (corpo.get("sessionInfo") or {}).get("login_token")
        if not corpo.get("UID") or not login_token:
            raise ErroreRete("Il login Uconnect non ha consegnato l'UID o il login_token")
        self.sessione = {"uid": str(corpo["UID"]), "login_token": str(login_token)}
        self._biscotti = {**biscotti, **altri}
        if self._salva:
            self._salva(dict(self.sessione))

    async def _jwt(self, ripeti: bool = True) -> str:
        """Un JWT nuovo da Gigya, col login_token salvato; se non vale più, con la password."""
        salvato = bool(self.sessione.get("login_token"))
        if not salvato:
            await self._login_password()
        corpo, _ = await self._gigya(
            "JWT di Gigya",
            "accounts.getJWT",
            self._parametri_gigya(login_token=self.sessione["login_token"], fields=_CAMPI_JWT, authMode="cookie"),
            self._biscotti,
        )
        codice = _intero(corpo.get("errorCode"))
        scaduta = codice in _GIGYA_SESSIONE_SCADUTA or _intero(corpo.get("statusCode")) in (401, 403)
        if salvato and ripeti and codice and scaduta:
            _LOGGER.debug("Il login_token di Uconnect non vale più (%s): si rientra con la password", codice)
            self.sessione.pop("login_token", None)
            return await self._jwt(ripeti=False)
        self._controlla_gigya("JWT di Gigya", corpo)
        if not corpo.get("id_token"):
            raise ErroreRete("Gigya non ha consegnato il JWT")
        return str(corpo["id_token"])

    # --- Cognito ---------------------------------------------------------------------

    async def _posta_json(self, cosa: str, url: str, intestazioni: dict[str, str], corpo: dict[str, Any]) -> Any:
        try:
            async with self._http.post(
                url, data=json.dumps(corpo).encode(), headers=intestazioni, timeout=_TIMEOUT
            ) as r:
                http = r.status
                testo = await r.text()
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise ErroreRete(f"{cosa}: Uconnect non risponde: {err}") from err
        if http >= 500:
            raise ErroreRete(f"{cosa}: guasto del cloud (HTTP {http})")
        try:
            dati = json.loads(testo) if testo else {}
        except ValueError as err:
            raise ErroreRete(f"{cosa}: risposta non JSON (HTTP {http})") from err
        if http >= 400:
            tipo = str(dati.get("__type") or "") if isinstance(dati, dict) else ""
            raise ErroreApi(cosa, tipo or None, _messaggio(dati), http)
        return dati

    async def accedi(self) -> None:
        async with self._serratura:
            await self._accedi()

    async def _accedi(self) -> None:
        id_token = await self._jwt()
        identita = await self._posta_json(
            "identità Cognito",
            self.config.token,
            {**self._intestazioni(self.config.chiave_api), "Content-Type": "application/json"},
            {"gigya_token": id_token},
        )
        if not isinstance(identita, dict) or not identita.get("Token") or not identita.get("IdentityId"):
            raise ErroreRete("Uconnect non ha consegnato l'identità Cognito")
        risposta = await self._posta_json(
            "credenziali AWS",
            self.config.cognito,
            {
                "Content-Type": "application/x-amz-json-1.1",
                "X-Amz-Target": "AWSCognitoIdentityService.GetCredentialsForIdentity",
            },
            {"IdentityId": identita["IdentityId"], "Logins": {"cognito-identity.amazonaws.com": identita["Token"]}},
        )
        c = risposta.get("Credentials") if isinstance(risposta, dict) else None
        if not isinstance(c, dict) or not c.get("AccessKeyId") or not c.get("SecretKey"):
            raise ErroreRete("Cognito non ha consegnato le credenziali AWS")
        try:
            scade = datetime.fromtimestamp(float(c.get("Expiration") or 0), UTC)
        except (TypeError, ValueError, OverflowError):
            scade = datetime.now(UTC) + timedelta(minutes=50)
        if scade <= datetime.now(UTC):
            scade = datetime.now(UTC) + timedelta(minutes=50)
        self._credenziali = {
            "chiave": str(c["AccessKeyId"]),
            "segreto": str(c["SecretKey"]),
            "token": str(c.get("SessionToken") or ""),
            "scade": scade,
        }
        _LOGGER.debug("Entrati nel cloud Uconnect")

    async def _credenziali_valide(self) -> dict[str, Any]:
        async with self._serratura:
            c = self._credenziali
            if c is None or c["scade"] - _MARGINE < datetime.now(UTC):
                await self._accedi()
            assert self._credenziali is not None
            return self._credenziali

    # --- i canali, con la firma ----------------------------------------------------------

    def _intestazioni(self, chiave: str) -> dict[str, str]:
        return {
            "x-clientapp-name": "CWP",
            "x-clientapp-version": "1.0",
            "clientrequestid": uuid.uuid4().hex.upper()[:16],
            "x-api-key": chiave,
            "locale": self.config.locale,
            "x-originator-type": "web",
        }

    async def _chiama(
        self,
        cosa: str,
        metodo: str,
        url: str,
        *,
        corpo: dict[str, Any] | None = None,
        chiave: str | None = None,
        accetta: tuple[int, ...] = (),
        ripeti: bool = True,
    ) -> tuple[int, Any]:
        """Una richiesta firmata. Restituisce (HTTP, corpo); un codice in
        ``accetta`` torna a chi chiama invece di diventare un errore."""
        c = await self._credenziali_valide()
        dati = json.dumps(corpo, separators=(",", ":")).encode() if corpo is not None else None
        intestazioni = {
            **self._intestazioni(chiave or self.config.chiave_api),
            "accept": "application/json",
            "content-type": "application/json",
        }
        firmate = firma.firma(
            metodo,
            url,
            intestazioni,
            dati,
            chiave_accesso=c["chiave"],
            segreto=c["segreto"],
            token_sessione=c["token"],
            regione=self.config.regione_aws,
        )
        firmate["User-Agent"] = _AGENTE
        try:
            async with self._http.request(
                metodo, URL(url, encoded=True), data=dati, headers=firmate, timeout=_TIMEOUT
            ) as r:
                http = r.status
                testo = await r.text()
                tipo_errore = r.headers.get("x-amzn-ErrorType", "")
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise ErroreRete(f"{cosa}: Uconnect non risponde: {err}") from err
        try:
            risposta: Any = json.loads(testo) if testo.strip() else {}
        except ValueError:
            risposta = {"message": testo[:200]}
        if http in (401, 403) and _aws_scadute(tipo_errore, risposta):
            if not ripeti:
                raise ErroreApi(cosa, tipo_errore or None, _messaggio(risposta), http)
            _LOGGER.debug("Credenziali AWS di Uconnect scadute: si rientra")
            async with self._serratura:
                self._credenziali = None
                await self._accedi()
            return await self._chiama(cosa, metodo, url, corpo=corpo, chiave=chiave, accetta=accetta, ripeti=False)
        if http in accetta:
            return http, risposta
        if http >= 500:
            raise ErroreRete(f"{cosa}: guasto del cloud Uconnect (HTTP {http})")
        if http >= 400:
            codice = risposta.get("name") if isinstance(risposta, dict) else None
            raise ErroreApi(cosa, codice, _messaggio(risposta), http)
        return http, risposta

    def _conto(self, percorso: str, versione: str = "v1") -> str:
        return f"{self.config.api}/{versione}/accounts/{quote(self.uid, safe='')}{percorso}"

    # --- leggere ---------------------------------------------------------------------

    async def vetture(self) -> list[Vettura]:
        await self._credenziali_valide()
        codici: list[str | None] = (
            [self._codice_marchio] if self._codice_marchio else [*self.config.codici_marchio, None]
        )
        for codice in codici:
            parametri = {"stage": "ALL", "sdp": "ALL"}
            if codice:
                parametri["brand"] = codice
            url = f"{self._conto('/vehicles', 'v4')}?{urlencode(parametri)}"
            http, r = await self._chiama("elenco delle auto", "GET", url, accetta=(400, 404))
            if http >= 400 or not isinstance(r, dict):
                continue
            voci = [v for v in (r.get("vehicles") or []) if isinstance(v, dict) and v.get("vin")]
            if voci:
                self._codice_marchio = codice
                return [vettura_da_json(v) for v in voci]
        return []

    async def _stato_grezzo(self, vin: str) -> dict[str, Any]:
        provata = self._versione_stato.get(vin)
        versioni = [provata] if provata else list(_VERSIONI_STATO)
        for versione in versioni:
            coda = "/status/" if versione != "v2" else "/status"
            http, r = await self._chiama(
                "stato dell'auto",
                "GET",
                self._conto(f"/vehicles/{vin}{coda}", versione),
                accetta=(400, 404, 502),
            )
            if http < 400 and isinstance(r, dict):
                self._versione_stato[vin] = versione
                return r
            _LOGGER.debug("Lo stato %s della %s risponde %s: si prova la versione dopo", versione, vin[-6:], http)
        if provata:
            # Quella che andava non va più: alla prossima si riprovano tutte.
            self._versione_stato.pop(vin, None)
        return {}

    async def _facoltativa(self, cosa: str, url: str) -> dict[str, Any]:
        """Una lettura che non tutte le auto hanno: se non c'è, pazienza."""
        try:
            http, r = await self._chiama(cosa, "GET", url, accetta=(400, 403, 404, 502))
        except (ErroreApi, ErroreRete) as err:
            _LOGGER.debug("%s non disponibile: %s", cosa, err)
            return {}
        return r if http < 400 and isinstance(r, dict) else {}

    async def stato(self, v: Vettura) -> StatoVettura:
        stato = await self._stato_grezzo(v.vin)
        posizione = await self._facoltativa("posizione", self._conto(f"/vehicles/{v.vin}/location/lastknown"))
        remoto = await self._facoltativa("serrature", self._conto(f"/vehicles/{v.vin}/remote/status"))
        if not stato and not posizione and not remoto:
            raise ErroreApi("stato dell'auto", None, "il cloud non ha dati per questa auto", 404)
        return interpreta(stato, posizione, remoto)

    # --- comandare -----------------------------------------------------------------------

    async def _token_pin(self, pin: str) -> str:
        url = f"{self.config.pin}/v1/accounts/{quote(self.uid, safe='')}/ignite/pin/authenticate"
        try:
            _, r = await self._chiama(
                "verifica del PIN",
                "POST",
                url,
                corpo={"pin": base64.b64encode(pin.encode()).decode()},
                chiave=self.config.chiave_pin,
            )
        except ErroreApi as err:
            if 400 <= err.http < 500:
                raise ErrorePin(f"Uconnect non accetta il PIN: {err.messaggio}") from err
            raise
        if not isinstance(r, dict) or not r.get("token"):
            raise ErrorePin(f"Uconnect non accetta il PIN: {_messaggio(r)}")
        return str(r["token"])

    async def _invia(self, v: Vettura, comando: tuple[str, str, str], token_pin: str) -> str:
        nome, percorso, versione = comando
        _, r = await self._chiama(
            f"comando {nome}",
            "POST",
            self._conto(f"/vehicles/{v.vin}/{percorso}", versione),
            corpo={"command": nome, "pinAuth": token_pin},
        )
        if not isinstance(r, dict) or str(r.get("responseStatus") or "").lower() != "pending":
            raise ErroreComando(f"Uconnect non ha accettato {nome}: {_messaggio(r)}")
        return str(r.get("correlationId") or "")

    async def comanda(
        self,
        v: Vettura,
        comando: tuple[str, str, str],
        pin: str | None,
        riserva: tuple[str, str, str] | None = None,
    ) -> None:
        """Un comando all'auto, col PIN; se la versione principale è vietata, la riserva."""
        pin = pin or self.pin
        if not pin:
            raise ErrorePin("I comandi Uconnect vogliono il PIN dell'app: scrivilo nelle opzioni di GdAAuto")
        async with self._serratura_comandi:
            token_pin = await self._token_pin(pin)
            try:
                correlazione = await self._invia(v, comando, token_pin)
            except ErroreApi as err:
                if err.http == 403 and riserva is not None:
                    _LOGGER.debug("%s vietato (403): si prova %s", comando[0], riserva[0])
                    try:
                        correlazione = await self._invia(v, riserva, token_pin)
                    except ErroreApi as err2:
                        raise ErroreComando(f"Uconnect rifiuta {riserva[0]}: {err2.messaggio}") from err2
                else:
                    raise ErroreComando(f"Uconnect rifiuta {comando[0]}: {err.messaggio}") from err
            if correlazione:
                await self._aspetta_esito(v, correlazione, comando[0])

    async def _aspetta_esito(self, v: Vettura, correlazione: str, nome: str) -> None:
        scadenza = time.monotonic() + self.tempo_esito
        url = self._conto(f"/vehicles/{v.vin}/remote/{quote(correlazione, safe='')}/status/")
        while time.monotonic() < scadenza:
            await asyncio.sleep(self.attesa_esito)
            try:
                http, r = await self._chiama("esito del comando", "GET", url, accetta=(400, 403, 404))
            except ErroreRete as err:
                _LOGGER.debug("Esito di %s non letto: %s", nome, err)
                continue
            if http >= 400:
                # Alcune regioni non hanno questo indirizzo: il comando è partito, l'esito non si sa.
                return
            esito = str((r.get("status") or r.get("responseStatus") or "") if isinstance(r, dict) else "").lower()
            if esito in ("success", "succeeded", "completed", "complete"):
                return
            if esito in ("failure", "failed", "error", "rejected", "timeout"):
                raise ErroreComando(f"L'auto non ha eseguito {nome}: {_messaggio(r)}")
        _LOGGER.info("Uconnect non ha confermato %s in %.0f s: il comando resta in coda", nome, self.tempo_esito)

    # --- i comandi che conosciamo ---------------------------------------------------------

    async def chiudi(self, v: Vettura, pin: str | None) -> None:
        await self.comanda(v, CMD_CHIUDI, pin)

    async def apri(self, v: Vettura, pin: str | None) -> None:
        await self.comanda(v, CMD_APRI, pin)

    async def trova(self, v: Vettura, pin: str | None) -> None:
        await self.comanda(v, CMD_TROVA, pin)

    async def clima(self, v: Vettura, acceso: bool, pin: str | None, temperatura: int = 22) -> None:
        # Il precondizionamento Uconnect non prende la temperatura: usa quella impostata in auto.
        await self.comanda(v, CMD_CLIMA_ON if acceso else CMD_CLIMA_OFF, pin)

    async def ricarica(self, v: Vettura, avvia: bool, pin: str | None) -> None:
        if not avvia:
            raise ErroreComando("Uconnect sa avviare la ricarica, non fermarla: fermala dall'auto o dalla colonnina")
        await self.comanda(v, CMD_RICARICA, pin, riserva=CMD_RICARICA_V4)


def _aws_scadute(tipo: str, corpo: Any) -> bool:
    if any(t in tipo for t in _AWS_SCADUTE):
        return True
    testo = _messaggio(corpo).lower() if isinstance(corpo, dict) else ""
    return "security token" in testo or "signature" in testo or "token has expired" in testo
