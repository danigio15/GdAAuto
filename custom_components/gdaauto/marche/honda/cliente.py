"""Il filo con My Honda+ (Honda Connect Europe), il cloud delle Honda europee.

Un solo indirizzo, ``mobile-api.connected.honda-eu.com``, per tutto:

- il login, in due passi JSON (quelli dell'app, niente pagine web):
  ``/auth/initiate-login`` con email e password chiuse nella busta di
  ``cripto.py`` risponde con una sfida; ``/auth/complete-login`` la vuole
  firmata dalla chiave del dispositivo e restituisce i token. Il primo login
  di un dispositivo nuovo risponde ``device-authenticator-not-registered``:
  allora ``/auth/reset-device-authenticator`` registra la chiave pubblica e
  Honda manda un'email con un link. Il link NON va aperto: si copia e si
  incolla nel campo «codice»; GdAAuto lo conferma (``GET /auth/verify-link``)
  e rifà il login;
- il rinnovo: ``/auth/isv-prod/refresh`` con il refresh token (l'access token
  dura un'ora);
- l'account e le auto: ``/user/get-login-info`` (l'id personale, che va in
  ``x-app-personal-id``, e ``vehiclesInfo`` con le capacità di ogni auto);
- lo stato: ``/tsp/dashboard-latest``, l'ultimo cruscotto che l'auto ha mandato
  al cloud. Leggerlo non sveglia l'auto (``POST /tsp/dashboard`` sì: qui non si usa);
- i comandi: ``POST /tsp/<comando>?vin=…`` risponde subito con un indirizzo
  ``statusQueryGetUri``; l'esito si chiede a ``/euw/tsp/async-command-status``
  finché l'auto non risponde (fino a un minuto e mezzo, se dorme);
- i viaggi: ``/tsp/journey-history``, un mese per volta.

Indirizzi, campi e comandi vengono da enricobattocchi/pymyhondaplus (licenza
MIT): vedi RINGRAZIAMENTI.md. Questo modulo non importa Home Assistant.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
import json
import logging
import time
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import aiohttp
from yarl import URL

from ...vettura import StatoVettura, Vettura
from ..errori import CodiceInviato, CodiceSbagliato, ErroreApi, ErroreComando, ErroreCredenziali, ErroreRete
from . import cripto
from .dati import in_miglia, interpreta, righe, storico

_LOGGER = logging.getLogger(__name__)

ID = "honda"
NOME = "Honda"

API = "https://mobile-api.connected.honda-eu.com"
INTESTAZIONI = {
    "user-agent": "okhttp/4.12.0",
    "accept-encoding": "gzip",
    "x-app-device-os": "android",
    "x-app-device-osversion": "26",
    "x-app-device-model": "HomeAssistant",
}
_TIMEOUT = aiohttp.ClientTimeout(total=40)
_MARGINE_TOKEN = 120
#: quanto si aspetta il link dell'email, prima di doverne chiedere un altro
_ATTESA_LINK = 60 * 60
#: ogni quanto si rileggono i viaggi
OGNI_STORICO = 30 * 60
#: il limite di carica: solo questi valori
LIMITI = (80, 85, 90, 95, 100)

#: le capacità di ``vehicleCapability`` → i comandi di GdAAuto
CAPACITA = {
    "telematicsRemoteLockUnlock": ("chiudi", "apri"),
    "telematicsRemoteHorn": ("trova",),
    "telematicsRemoteClimate": ("clima",),
    "telematicsRemoteCharge": ("ricarica",),
    "telematicsMaxChargeSettings": ("limite_ricarica",),
}
#: il codice di errore di Honda finché l'account non accetta i termini nuovi
_TERMINI = "10052"


def comandi_di(v: Vettura) -> set[str]:
    attive = set(v.extra.get("capacita") or ())
    return {c for chiave, comandi in CAPACITA.items() if chiave in attive for c in comandi}


def _testo_errore(testo: str) -> str:
    return testo[:300].replace("\n", " ")


async def _in_disparte(funzione: Callable[..., Any], *argomenti: Any) -> Any:
    """La crittografia RSA fuori dal loop."""
    return await asyncio.get_running_loop().run_in_executor(None, funzione, *argomenti)


async def _chiedi(
    http: aiohttp.ClientSession,
    cosa: str,
    metodo: str,
    url: str | URL,
    *,
    json_: Any = None,
    intestazioni: dict[str, str] | None = None,
) -> tuple[int, str]:
    try:
        async with http.request(
            metodo, url, json=json_, headers={**INTESTAZIONI, **(intestazioni or {})}, timeout=_TIMEOUT
        ) as r:
            return r.status, await r.text()
    except (aiohttp.ClientError, TimeoutError, OSError) as err:
        raise ErroreRete(f"{cosa}: My Honda+ non risponde: {err}") from err


def _json(testo: str) -> Any:
    try:
        return json.loads(testo) if testo.strip() else {}
    except ValueError:
        return None


# --- il login ----------------------------------------------------------------------


@dataclass
class _InSospeso:
    """Un dispositivo appena registrato che aspetta il link dell'email."""

    pem: str
    scade: float


#: email → il dispositivo in attesa del link (tra un invio del modulo e il successivo)
_IN_SOSPESO: dict[str, _InSospeso] = {}


def leggi_link(incollato: str) -> tuple[str, str]:
    """(key, type) dal link dell'email di Honda; ("", "") se non ce n'è."""
    testo = incollato.strip()
    parametri: dict[str, str] = {}
    for parte in urlsplit(testo).query.split("&") if "?" in testo else testo.split("&"):
        if "=" in parte:
            k, v = parte.split("=", 1)
            parametri[k] = unquote(v)
    return parametri.get("key", ""), parametri.get("type", "mfa")


def _token_da(risposta: dict[str, Any], vecchio: dict[str, Any] | None = None) -> dict[str, Any]:
    vecchio = vecchio or {}
    accesso = str(risposta.get("access_token") or "")
    if not accesso:
        raise ErroreApi("login", None, "Honda non ha dato l'access token")
    try:
        durata = float(risposta.get("expires_in") or 3599)
    except (TypeError, ValueError):
        durata = 3599.0
    return {
        **vecchio,
        "access_token": accesso,
        "refresh_token": str(risposta.get("refresh_token") or vecchio.get("refresh_token") or ""),
        "scade": time.time() + durata,
        "user_id": utente_da_jwt(accesso) or vecchio.get("user_id", ""),
    }


def utente_da_jwt(token: str) -> str:
    """Il ``sub`` dell'access token (è l'id dell'utente per ``get-login-info``)."""
    parti = token.split(".")
    if len(parti) < 2:
        return ""
    try:
        corpo = json.loads(base64.urlsafe_b64decode(parti[1] + "=" * (-len(parti[1]) % 4)))
    except ValueError:
        return ""
    return str(corpo.get("sub") or "") if isinstance(corpo, dict) else ""


def _errore_login(cosa: str, http: int, testo: str) -> Exception:
    basso = testo.lower()
    if http >= 500 or http == 429:
        return ErroreRete(f"{cosa}: My Honda+ ha un guasto (HTTP {http})")
    if "locked-account" in basso:
        return ErroreCredenziali(
            "L'account My Honda+ è bloccato (troppi tentativi): aspetta un po' o reimposta la password dall'app"
        )
    if "invalid-credentials" in basso or "invalid_creds" in basso or http == 401:
        return ErroreCredenziali("My Honda+ rifiuta email o password")
    return ErroreApi(cosa, None, _testo_errore(testo), http)


async def _passo(
    http: aiohttp.ClientSession, base: str, percorso: str, dati: dict[str, Any], cosa: str, *, chiave_server: str
) -> tuple[int, str]:
    corpo = await _in_disparte(lambda: cripto.busta(dati, chiave_server=chiave_server))
    return await _chiedi(
        http, cosa, "POST", f"{base}{percorso}", json_=corpo, intestazioni={"content-type": "application/json"}
    )


async def _login(
    http: aiohttp.ClientSession,
    base: str,
    email: str,
    password: str,
    dispositivo: cripto.Dispositivo,
    lingua: str,
    chiave_server: str,
) -> dict[str, Any]:
    """I due passi del login. Solleva ``_NonRegistrato`` se il dispositivo è nuovo."""
    status, testo = await _passo(
        http,
        base,
        "/auth/initiate-login",
        {
            "emailAddress": email,
            "userPassword": password,
            "devicePublicKey": dispositivo.pubblica,
            "keyIdentifier": dispositivo.pubblica,
            "locale": lingua,
            "fingerprintSupport": False,
            "frontCameraSupport": False,
            "faceSupport": False,
            "pushToken": "",
            "deviceId": "homeassistant",
            "deviceName": "Home Assistant",
            "deviceType": "HomeAssistant",
            "platformType": "android",
            "osVersion": "26",
            "applicationId": "com.honda_eu.connected.app",
            "applicationVersion": "3.0.0",
        },
        "login",
        chiave_server=chiave_server,
    )
    if status not in (200, 202):
        if "device-authenticator-not-registered" in testo:
            raise _NonRegistrato
        raise _errore_login("login", status, testo)
    sfida = _json(testo) or {}
    if not isinstance(sfida, dict) or not sfida.get("transactionId") or not sfida.get("signatureChallenge"):
        raise ErroreRete("login: My Honda+ non ha mandato la sfida da firmare")
    firmata = await _in_disparte(dispositivo.firma, str(sfida["signatureChallenge"]))
    status, testo = await _passo(
        http,
        base,
        "/auth/complete-login",
        {
            "emailAddress": email,
            "userPassword": password,
            "keyIdentifier": dispositivo.pubblica,
            "identityProvider": "isv-prod",
            "locale": lingua,
            "transactionId": sfida["transactionId"],
            "signedChallengeResponse": firmata,
        },
        "login",
        chiave_server=chiave_server,
    )
    if status not in (200, 202):
        raise _errore_login("login", status, testo)
    risposta = _json(testo)
    if not isinstance(risposta, dict):
        raise ErroreRete("login: My Honda+ risponde una cosa che non si capisce")
    return _token_da(risposta)


class _NonRegistrato(Exception):
    """Honda non conosce ancora la chiave di questo dispositivo."""


async def entra(
    http: aiohttp.ClientSession,
    email: str,
    password: str,
    *,
    codice: str = "",
    chiave: str | None = None,
    lingua: str = "it",
    base: str = API,
    chiave_server: str = cripto.CHIAVE_SERVER,
) -> tuple[dict[str, Any], str]:
    """Il login con la password: restituisce i token e la chiave del dispositivo (PEM) da salvare.

    Con un dispositivo nuovo Honda manda un'email con un link e qui si solleva
    ``CodiceInviato``: si richiama con lo stesso account e il link nel ``codice``.
    """
    indirizzo = email.strip().lower()
    adesso = time.monotonic()
    for k in [k for k, s in _IN_SOSPESO.items() if s.scade < adesso]:
        del _IN_SOSPESO[k]
    sospeso = _IN_SOSPESO.get(indirizzo)
    pem = sospeso.pem if sospeso else chiave
    dispositivo = await _in_disparte(cripto.Dispositivo, pem)
    codice = codice.strip()
    if sospeso and codice:
        key, tipo = leggi_link(codice)
        if not key:
            raise CodiceSbagliato("Nel campo «codice» va incollato tutto il link dell'email di Honda")
        url = URL(
            f"{base}/auth/verify-link?type={quote(tipo)}&key={quote(key, safe='+/=')}&dontRedirect=true", encoded=True
        )
        status, testo = await _chiedi(http, "conferma del link", "GET", url)
        if status >= 500:
            raise ErroreRete(f"conferma del link: My Honda+ ha un guasto (HTTP {status})")
        if status >= 400:
            raise CodiceSbagliato(f"Honda non accetta il link ({_testo_errore(testo)}): usa l'ultima email arrivata")
    try:
        token = await _login(http, base, email.strip(), password, dispositivo, lingua, chiave_server)
    except _NonRegistrato:
        if sospeso and codice:
            raise CodiceSbagliato("Il link non ha registrato il dispositivo: usa l'ultima email arrivata") from None
        status, testo = await _passo(
            http,
            base,
            "/auth/reset-device-authenticator",
            {
                "emailAddress": email.strip(),
                "userPassword": password,
                "resetType": "Add",
                "devicePublicKey": dispositivo.pubblica,
                "keyIdentifier": dispositivo.pubblica,
            },
            "registrazione del dispositivo",
            chiave_server=chiave_server,
        )
        # «currently blocked»: l'email è già partita poco fa, si aspetta quella.
        if status not in (200, 202) and "currently blocked" not in testo.lower():
            raise _errore_login("registrazione del dispositivo", status, testo) from None
        _IN_SOSPESO[indirizzo] = _InSospeso(dispositivo.pem, adesso + _ATTESA_LINK)
        raise CodiceInviato(
            "Honda ha mandato un'email con un link per registrare Home Assistant. NON aprirlo: copialo, "
            "incollalo nel campo «codice» e conferma di nuovo."
        ) from None
    _IN_SOSPESO.pop(indirizzo, None)
    return token, dispositivo.pem


# --- il cliente ----------------------------------------------------------------------


class ClienteHonda:
    def __init__(
        self,
        sessione_http: aiohttp.ClientSession,
        token: dict[str, Any],
        salva: Callable[[dict[str, Any]], None] | None = None,
        *,
        lingua: str = "it",
        paese: str = "IT",
        base: str = API,
        attesa_comando: float = 90.0,
        passo_comando: float = 2.0,
    ) -> None:
        self._http = sessione_http
        self.token: dict[str, Any] = dict(token)
        self._salva = salva
        self.lingua = lingua
        self.paese = paese
        self._base = base
        self._attesa_comando = attesa_comando
        self._passo_comando = passo_comando
        self._serratura_token = asyncio.Lock()
        self._serratura_comandi = asyncio.Lock()
        #: per auto: (quando, campi dello storico); l'unità dell'ultimo cruscotto; l'ultimo stato
        self._storici: dict[str, tuple[float, dict[str, Any]]] = {}
        self._miglia: dict[str, bool] = {}
        self._ultimo: dict[str, StatoVettura] = {}

    # --- i token ------------------------------------------------------------

    async def _token_valido(self, *, nuovo: bool = False) -> str:
        async with self._serratura_token:
            if (
                not nuovo
                and self.token.get("access_token")
                and time.time() < float(self.token.get("scade") or 0) - _MARGINE_TOKEN
            ):
                return str(self.token["access_token"])
            rinnovo = str(self.token.get("refresh_token") or "")
            if not rinnovo:
                raise ErroreCredenziali("Manca il refresh token di My Honda+: serve rientrare")
            status, testo = await _chiedi(
                self._http,
                "rinnovo del token",
                "POST",
                f"{self._base}/auth/isv-prod/refresh",
                json_={"refreshToken": rinnovo},
                intestazioni={"content-type": "application/json"},
            )
            if status >= 500 or status == 429:
                raise ErroreRete(f"rinnovo del token: My Honda+ ha un guasto (HTTP {status})")
            corpo = _json(testo)
            if status != 200 or not isinstance(corpo, dict):
                raise ErroreCredenziali(f"My Honda+ non rinnova più il token (HTTP {status}): serve rientrare")
            self.token = _token_da(corpo, self.token)
            if self._salva:
                self._salva(dict(self.token))
            return str(self.token["access_token"])

    # --- la rete ------------------------------------------------------------

    async def _api(
        self, cosa: str, metodo: str, percorso: str, *, json_: Any = None, ok: tuple[int, ...] = (200,)
    ) -> tuple[int, Any]:
        for tentativo in (1, 2):
            token = await self._token_valido(nuovo=tentativo == 2)
            intestazioni = {"authorization": f"Bearer {token}"}
            if self.token.get("personal_id"):
                intestazioni["x-app-personal-id"] = str(self.token["personal_id"])
            if json_ is not None:
                intestazioni["content-type"] = "application/json"
            status, testo = await _chiedi(
                self._http, cosa, metodo, f"{self._base}{percorso}", json_=json_, intestazioni=intestazioni
            )
            if status == 401 and tentativo == 1:
                # Scaduto prima del previsto: un token nuovo, e si riprova una volta.
                continue
            break
        if status == 401:
            raise ErroreCredenziali(f"{cosa}: My Honda+ rifiuta anche il token appena rinnovato")
        corpo = _json(testo)
        if status in ok:
            if corpo is None:
                raise ErroreRete(f"{cosa}: My Honda+ risponde una cosa che non si capisce")
            return status, corpo
        if status >= 500 or status == 429:
            raise ErroreRete(f"{cosa}: My Honda+ ha un guasto (HTTP {status})")
        codice = str(corpo.get("errorCode") or "") if isinstance(corpo, dict) else ""
        if codice == _TERMINI:
            raise ErroreApi(
                cosa, codice, "Honda chiede di accettare i termini nuovi: aprili nell'app My Honda+", status
            )
        raise ErroreApi(cosa, codice or None, _testo_errore(testo), status)

    # --- l'account e le auto ---------------------------------------------------

    async def accedi(self) -> None:
        await self._token_valido()

    async def _account(self) -> dict[str, Any]:
        utente = self.token.get("user_id") or utente_da_jwt(str(self.token.get("access_token") or ""))
        _, corpo = await self._api(
            "account",
            "GET",
            f"/user/get-login-info?userid={quote(str(utente))}&agreementType=1&country={self.paese}&language={self.lingua}",
        )
        if not isinstance(corpo, dict):
            raise ErroreRete("account: My Honda+ risponde una cosa che non si capisce")
        personale = str(corpo.get("personalId") or "")
        if personale and personale != self.token.get("personal_id"):
            self.token["personal_id"] = personale
            if self._salva:
                self._salva(dict(self.token))
        return corpo

    async def vetture(self) -> list[Vettura]:
        corpo = await self._account()
        elenco: list[Vettura] = []
        for v in corpo.get("vehiclesInfo") or []:
            if not isinstance(v, dict) or not v.get("vin"):
                continue
            ui = v.get("vehicleUIConfiguration") if isinstance(v.get("vehicleUIConfiguration"), dict) else {}
            capacita = (
                ((v.get("vehicleCapability") or {}).get("capabilities") or {})
                if isinstance(v.get("vehicleCapability"), dict)
                else {}
            )
            attive = sorted(
                k for k, c in capacita.items() if isinstance(c, dict) and c.get("featureStatus") == "active"
            )
            modello = str(ui.get("friendlyModelName") or v.get("grade") or "Honda").strip()
            anno = str(v.get("modelYear") or "")
            elenco.append(
                Vettura(
                    vin=str(v["vin"]),
                    marca=ID,
                    modello=modello,
                    nome=str(v.get("vehicleNickName") or "").strip() or modello,
                    anno=int(anno) if anno.isdigit() and int(anno) > 1990 else None,
                    targa=str(v.get("vehicleRegNumber") or ""),
                    condivisa=str(v.get("role") or "primary").lower() != "primary",
                    extra={"capacita": attive, "carburante": str(v.get("fuelType") or "")},
                )
            )
        return elenco

    # --- lo stato -----------------------------------------------------------------

    async def stato(self, v: Vettura) -> StatoVettura:
        _, cruscotto = await self._api(
            "stato dell'auto", "GET", f"/tsp/dashboard-latest?vin={quote(v.vin)}&languageCode={self.lingua}"
        )
        if not isinstance(cruscotto, dict):
            raise ErroreRete("stato dell'auto: My Honda+ risponde una cosa che non si capisce")
        s = interpreta(cruscotto)
        unita = (cruscotto.get("evStatus") or {}).get("rangeUnit") or (cruscotto.get("odometer") or {}).get("unit")
        self._miglia[v.vin] = in_miglia(unita)
        for campo, valore in (await self._storico(v)).items():
            setattr(s, campo, valore)
        self._ultimo[v.vin] = s
        return s

    async def _storico(self, v: Vettura) -> dict[str, Any]:
        """I viaggi del mese (e di quello prima, nei primi giorni), riletti ogni mezz'ora.

        Facoltativo: se non va, lo stato arriva lo stesso con lo storico di prima.
        """
        if "telematicsJourneyHistory" not in set(v.extra.get("capacita") or ()):
            return {}
        quando, campi = self._storici.get(v.vin, (0.0, {}))
        if quando and time.monotonic() - quando < OGNI_STORICO:
            return campi
        oggi = datetime.now(UTC).date()
        mesi = [oggi.replace(day=1)]
        if oggi.day < 7:
            mesi.append(date(oggi.year - (oggi.month == 1), (oggi.month - 2) % 12 + 1, 1))
        viaggi: list[dict[str, Any]] = []
        try:
            for mese in mesi:
                inizio = quote(f"{mese.isoformat()}T00:00:00.000Z", safe="")
                pagina, ultima = 1, 1
                while pagina <= min(ultima, 10):
                    _, corpo = await self._api(
                        "viaggi", "GET", f"/tsp/journey-history?vin={quote(v.vin)}&monthStart={inizio}&page={pagina}"
                    )
                    if not isinstance(corpo, dict):
                        break
                    viaggi.extend(righe(corpo))
                    ultima = int(corpo.get("maxPage") or 1)
                    pagina += 1
        except (ErroreApi, ErroreRete, TypeError, ValueError) as err:
            _LOGGER.debug("Viaggi della %s non letti: %s", v.titolo, err)
            self._storici[v.vin] = (time.monotonic(), campi)
            return campi
        campi = storico(
            viaggi, oggi, elettrica=str(v.extra.get("carburante") or "E") == "E", miglia=self._miglia.get(v.vin, False)
        )
        self._storici[v.vin] = (time.monotonic(), campi)
        return campi

    # --- i comandi ------------------------------------------------------------------

    async def _comanda(self, v: Vettura, cosa: str, metodo: str, percorso: str, corpo: dict[str, Any]) -> None:
        async with self._serratura_comandi:
            try:
                _, risposta = await self._api(cosa, metodo, percorso, json_=corpo, ok=(200, 202))
            except ErroreApi as err:
                raise ErroreComando(
                    f"{v.titolo} non esegue il comando ({cosa}): {err.messaggio or err.codice}"
                ) from err
            indirizzo = str((risposta or {}).get("statusQueryGetUri") or "") if isinstance(risposta, dict) else ""
            if "id=" not in indirizzo:
                # Nessun esito da aspettare: il cloud l'ha preso.
                return
            await self._aspetta(v, cosa, indirizzo.split("id=", 1)[1].split("&", 1)[0])

    async def _aspetta(self, v: Vettura, cosa: str, id_comando: str) -> None:
        """Chiede l'esito finché l'auto non risponde, o finché Honda non dice che non risponde."""
        fine = time.monotonic() + self._attesa_comando
        while True:
            status, corpo = await self._api(
                f"esito di {cosa}", "GET", f"/euw/tsp/async-command-status?id={quote(id_comando)}", ok=(200, 202, 204)
            )
            uscita = (corpo.get("output") or {}) if isinstance(corpo, dict) and status == 200 else None
            if isinstance(uscita, dict) and uscita:
                if uscita.get("functionTimedOut"):
                    raise ErroreComando(f"{v.titolo} non ha risposto al comando ({cosa}): forse dorme o non ha campo")
                esito = str(uscita.get("RequestStatus") or "").lower()
                if esito == "success":
                    return
                motivo = uscita.get("StatusReason") or esito or "motivo sconosciuto"
                raise ErroreComando(f"{v.titolo} non ha eseguito il comando ({cosa}): {motivo}")
            if time.monotonic() >= fine:
                raise ErroreComando(f"{v.titolo} non ha risposto al comando ({cosa}) in tempo")
            await asyncio.sleep(self._passo_comando)

    def _percorso(self, comando: str, v: Vettura) -> str:
        return f"/tsp/{comando}?vin={quote(v.vin)}"

    async def chiudi(self, v: Vettura, _pin: str | None) -> None:
        await self._comanda(v, "chiudere", "POST", self._percorso("remote-lock", v), {"command": "allLock"})

    async def apri(self, v: Vettura, _pin: str | None) -> None:
        await self._comanda(v, "aprire", "POST", self._percorso("remote-lock", v), {"command": "doorUnlock"})

    async def trova(self, v: Vettura, _pin: str | None) -> None:
        await self._comanda(v, "clacson e luci", "POST", self._percorso("remote-horn-light", v), {"command": "horn"})

    async def clima(self, v: Vettura, acceso: bool, _pin: str | None, temperatura: int = 22) -> None:
        # La temperatura la sceglie l'auto (le impostazioni del clima dell'app): qui si accende e si spegne.
        if acceso:
            corpo = {
                "command": "start",
                "temperatureMode": "specific",
                "temperature": 0,
                "autoDefrosterSetting": "autoOn",
            }
            await self._comanda(v, "accendere il clima", "POST", self._percorso("remote-climate", v), corpo)
        else:
            await self._comanda(
                v, "spegnere il clima", "POST", self._percorso("remote-climate", v), {"command": "stop"}
            )

    async def ricarica(self, v: Vettura, avvia: bool, _pin: str | None) -> None:
        cosa = "avviare la ricarica" if avvia else "fermare la ricarica"
        corpo = {"command": "start" if avvia else "stop"}
        await self._comanda(v, cosa, "POST", self._percorso("remote-charge", v), corpo)

    async def limite_ricarica(self, v: Vettura, percento: int, _pin: str | None) -> None:
        """Honda ha due limiti, a casa e fuori: si cambia quello di dove è l'auto adesso (tutti e due se non si sa)."""
        valore = min(LIMITI, key=lambda x: abs(x - percento))
        ev = ((self._ultimo.get(v.vin) or v.stato or StatoVettura()).grezzo.get("evStatus")) or {}
        dove = str(ev.get("homeAway") or "").lower()

        def vecchio(chiave: str) -> int:
            try:
                n = int(float(ev.get(chiave)))
            except (TypeError, ValueError):
                return valore
            return n if n in LIMITI else valore

        casa = valore if dove != "away" else vecchio("chargeLimitHome")
        fuori = valore if dove != "home" else vecchio("chargeLimitAway")
        corpo = {"vin": v.vin, "locationHome": {"maxCharge": casa}, "locationAway": {"maxCharge": fuori}}
        await self._comanda(v, "cambiare il limite di carica", "PUT", "/tsp/maximum-charge-config", corpo)
