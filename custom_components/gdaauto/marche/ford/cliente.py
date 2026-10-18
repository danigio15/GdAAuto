"""Il filo col cloud FordPass (Ford App).

Come si entra, in parole semplici:

1. chi configura apre nel suo browser il login di Ford (Azure AD B2C, con
   PKCE) e alla fine il sito rimanda a ``fordapp://userauthorized/?code=…``;
   quell'indirizzo (o solo il codice) si incolla in Home Assistant;
2. il codice diventa un token B2C, e il token B2C un token FordPass
   (``cat-with-b2c-access-token``); il token FordPass si rinnova col suo
   refresh token, che **ruota**: chi crea il cliente gli dà ``salva``;
3. la telemetria sta su Autonomic, che vuole un token suo, ottenuto
   scambiando il refresh token FordPass (dura pochi minuti, si tiene in
   memoria).

Lo stato si legge aprendo per pochi secondi il WebSocket della telemetria:
appena aperto manda l'istantanea dell'auto, e la si chiude. Se il WebSocket
non dà niente si prova la vecchia lettura REST. Nessuna lettura sveglia
l'auto: il comando ``statusRefresh`` non si usa mai.

Gli indirizzi e i nomi vengono da marq24/ha-fordpass (Apache 2.0): vedi
RINGRAZIAMENTI.md. Il codice è scritto da capo per GdAAuto.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
import logging
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlparse

import aiohttp

from ...vettura import StatoVettura, Vettura
from ..errori import ErroreApi, ErroreComando, ErroreCredenziali, ErroreRete
from .dati import interpreta
from .regioni import CLIENT_ID, OAUTH_ID, Regione

_LOGGER = logging.getLogger(__name__)

ID = "ford"
NOME = "Ford"

_TIMEOUT = aiohttp.ClientTimeout(total=45, connect=20)
_MARGINE = timedelta(seconds=60)
#: Il WebSocket: quanto aspettare l'istantanea, e quanto silenzio vuol dire «finito».
_WS_MASSIMO_S = 20.0
_WS_QUIETE_S = 2.0
#: Un comando: quanto aspettare l'esito dall'auto.
_COMANDO_MASSIMO_S = 90.0
#: I dati lenti (ultima ricarica, limite per luogo, profilo del clima): ogni mezz'ora.
_LENTI_OGNI_S = 30 * 60
#: Rifiuti del rinnovo senza un motivo chiaro prima di chiedere di rientrare.
_RIFIUTI_PRIMA_DEL_REAUTH = 3

_INTESTAZIONI = {"User-Agent": "GdAAuto (Home Assistant)", "Accept": "application/json"}

_COMANDI_TUTTI = {"chiudi", "trova", "clima", "ricarica", "limite_ricarica"}
_NON_PLUGIN = {"ICE", "HEV", "MHEV", "GAS", "DIESEL", "GASOLINE"}


@dataclass(frozen=True)
class Indirizzi:
    """Dove sta ogni pezzo del cloud; le prove li puntano a un cloud finto."""

    fondazionale: str = "https://api.foundational.ford.com/api"
    veicolo: str = "https://api.vehicle.ford.com/api"
    autonomic: str = "https://api.autonomic.ai/v1"
    autonomic_beta: str = "https://api.autonomic.ai/v1beta"
    autonomic_ws: str = "wss://api.autonomic.ai/v1beta"
    account: str = "https://accounts.autonomic.ai/v1"
    #: se c'è, sostituisce l'indirizzo del login della regione
    login: str = ""


# --- il login nel browser ---------------------------------------------------------------


def _b64(dati: bytes) -> str:
    return base64.urlsafe_b64encode(dati).decode().rstrip("=")


def nuovo_verifier() -> str:
    return secrets.token_urlsafe(64)


def sfida(verifier: str) -> str:
    return _b64(hashlib.sha256(verifier.encode()).digest())


def _flusso(r: Regione) -> str:
    return f"B2C_1A_SignInSignUp_{r.aggiunta}{r.lingua}"


def redirect_uri(r: Regione) -> str:
    return f"{r.schema}://userauthorized"


def indirizzo_login(r: Regione, verifier: str, login: str = "") -> str:
    q = {
        "redirect_uri": redirect_uri(r),
        "response_type": "code",
        "max_age": "3600",
        "scope": f"{CLIENT_ID} openid",
        "code_challenge": sfida(verifier),
        "code_challenge_method": "S256",
        "client_id": CLIENT_ID,
        "ui_locales": r.lingua,
        "language_code": r.lingua,
        "ford_application_id": r.app_id,
        "country_code": r.paese,
    }
    return f"{login or r.login}/{OAUTH_ID}/{_flusso(r)}/oauth2/v2.0/authorize?{urlencode(q, quote_via=quote)}"


def codice_da(testo: str) -> str:
    """Il codice, dall'indirizzo finale (``fordapp://userauthorized/?code=…``) o incollato da solo."""
    testo = testo.strip().strip("\"'")
    if "?" in testo or "code=" in testo or "error=" in testo:
        q = parse_qs(urlparse(testo).query or testo.split("?", 1)[-1])
        if q.get("error"):
            descrizione = (q.get("error_description") or q["error"])[0]
            raise ErroreCredenziali(f"Il login di Ford non è riuscito: {descrizione}")
        if q.get("code"):
            return q["code"][0].strip()
        raise ErroreCredenziali("Nell'indirizzo incollato non c'è il codice")
    if not testo:
        raise ErroreCredenziali("Il codice è vuoto")
    return testo


def dal_jwt(token: str) -> dict[str, Any]:
    """Il contenuto di un JWT, senza verificarlo: serve solo a sapere chi è."""
    try:
        corpo = token.split(".")[1]
        corpo += "=" * (-len(corpo) % 4)
        dati = json.loads(base64.urlsafe_b64decode(corpo))
    except (IndexError, ValueError, TypeError):
        return {}
    return dati if isinstance(dati, dict) else {}


# --- i comandi che un'auto accetta -------------------------------------------------------------


def _si(v: Any) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, int | float):
        return v > 0
    if isinstance(v, str):
        return v.strip().lower() in {"display", "true", "yes", "on", "1", "enabled"}
    return None


def comandi_di(v: Vettura) -> set[str]:
    """Dalle capacità del garage; senza capacità si offre tutto e decide il cloud."""
    capacita = v.extra.get("capacita") or {}
    motore = str(v.extra.get("motore") or "").upper()
    if not capacita:
        comandi = set(_COMANDI_TUTTI)
    else:
        comandi = set()
        if _si(capacita.get("remoteLock")):
            comandi.add("chiudi")
        if _si(capacita.get("remotePanicAlarm")):
            comandi.add("trova")
        if _si(capacita.get("remoteStart")) or _si(capacita.get("remoteClimateControl")):
            comandi.add("clima")
        comandi |= {"ricarica", "limite_ricarica"}
    # Avvio e limite della ricarica sono delle elettriche pure.
    if motore and motore != "BEV":
        comandi -= {"ricarica", "limite_ricarica"}
    return comandi


def vettura_da_garage(voce: dict[str, Any]) -> Vettura | None:
    vin = str(voce.get("vin") or "").strip()
    if not vin:
        return None
    profilo = voce.get("profile") or {}
    motore = str(profilo.get("engineType") or "").upper()
    if motore in _NON_PLUGIN:
        return None
    modello = str(profilo.get("model") or "Ford").strip()
    nome = str(profilo.get("nickName") or profilo.get("nickname") or "").strip() or modello
    anno = profilo.get("year")
    return Vettura(
        vin=vin,
        marca=ID,
        modello=modello,
        nome=nome,
        anno=int(anno) if str(anno or "").isdigit() else None,
        extra={"motore": motore or None, "capacita": dict(voce.get("capabilities") or {})},
    )


def _limite_ammesso(percento: int) -> int:
    """Ford accetta 50, 60 e 70 sotto l'80 %, poi qualunque valore fino a 100."""
    percento = max(50, min(100, int(percento)))
    return percento if percento >= 80 else percento // 10 * 10


def _temperatura_rcc(gradi: float) -> str:
    gradi = max(16.0, min(30.0, round(float(gradi) * 2) / 2))
    return f"{int(gradi)}_{'5' if gradi % 1 else '0'}"


def _unisci(dove: dict[str, Any], nuovi: dict[str, Any]) -> None:
    """Un messaggio del WebSocket dentro quello che si sa già: le sezioni si aggiornano voce per voce."""
    for sezione, contenuto in nuovi.items():
        if not isinstance(contenuto, dict):
            dove[sezione] = contenuto
            continue
        vecchio = dove.setdefault(sezione, {})
        if sezione == "states" and isinstance(contenuto.get("commands"), dict):
            contenuto = {**{k: v for k, v in contenuto.items() if k != "commands"}, **contenuto["commands"]}
        for chiave, valore in contenuto.items():
            if chiave in ("customMetrics", "configurations", "customEvents") and isinstance(valore, dict):
                vecchio.setdefault(chiave, {}).update(valore)
            else:
                vecchio[chiave] = valore


class _Ws401(Exception):
    """Il WebSocket rifiuta il token di Autonomic."""


class ClienteFord:
    def __init__(
        self,
        sessione_http: aiohttp.ClientSession,
        regione: Regione,
        token: dict[str, Any],
        salva: Callable[[dict[str, Any]], None] | None = None,
        *,
        indirizzi: Indirizzi | None = None,
    ) -> None:
        self._http = sessione_http
        self.regione = regione
        self.url = indirizzi or Indirizzi()
        self.token = dict(token)
        self._salva = salva
        self._auto: dict[str, Any] = {}
        self._serratura = asyncio.Lock()
        self._un_comando = asyncio.Lock()
        self._rifiuti = 0
        self._telemetria: dict[str, dict[str, Any]] = {}
        self._lenti: dict[str, tuple[dict[str, Any], float]] = {}
        self._vetture: dict[str, Vettura] = {}

    # --- la rete ---------------------------------------------------------------------------

    async def _chiedi(
        self,
        cosa: str,
        metodo: str,
        url: str,
        *,
        intestazioni: dict[str, str] | None = None,
        json_: Any = None,
        forma: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        try:
            async with self._http.request(
                metodo,
                url,
                headers={**_INTESTAZIONI, **(intestazioni or {})},
                json=json_,
                data=forma,
                params=params,
                timeout=_TIMEOUT,
            ) as r:
                http, testo = r.status, await r.text()
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise ErroreRete(f"{cosa}: Ford non risponde: {err}") from err
        if http >= 500:
            raise ErroreRete(f"{cosa}: il cloud di Ford ha un guasto (HTTP {http})")
        try:
            corpo = json.loads(testo) if testo else {}
        except ValueError:
            corpo = {"message": testo[:200]}
        return http, corpo

    @staticmethod
    def _messaggio(corpo: Any) -> str:
        if isinstance(corpo, dict):
            for k in ("error_description", "message", "error"):
                if corpo.get(k):
                    return str(corpo[k])
        return ""

    # --- i token ---------------------------------------------------------------------------------

    def _ford(self) -> dict[str, str]:
        return {"Application-Id": self.regione.app_id, "auth-token": str(self.token.get("access_token") or "")}

    def _dai_token(self, corpo: dict[str, Any]) -> dict[str, Any]:
        adesso = datetime.now(UTC)
        token = {
            "access_token": corpo["access_token"],
            "refresh_token": corpo.get("refresh_token") or self.token.get("refresh_token"),
            "scade": (adesso + timedelta(seconds=int(corpo.get("expires_in") or 300))).isoformat(),
        }
        if corpo.get("refresh_expires_in"):
            token["refresh_scade"] = (adesso + timedelta(seconds=int(corpo["refresh_expires_in"]))).isoformat()
        if self.token.get("utente"):
            token["utente"] = self.token["utente"]
        return token

    async def scambia_codice(self, codice: str, verifier: str) -> dict[str, Any]:
        """Dal codice del browser al token FordPass. Una volta sola, nella configurazione."""
        r = self.regione
        login = self.url.login or r.login
        http, corpo = await self._chiedi(
            "login",
            "POST",
            f"{login}/{OAUTH_ID}/{_flusso(r)}/oauth2/v2.0/token",
            forma={
                "client_id": CLIENT_ID,
                "scope": f"{CLIENT_ID} openid",
                "redirect_uri": redirect_uri(r),
                "grant_type": "authorization_code",
                "resource": "",
                "code": codice,
                "code_verifier": verifier,
            },
        )
        if not isinstance(corpo, dict) or "access_token" not in corpo:
            if http == 403 and not self._messaggio(corpo).startswith(("AADB2C", "invalid")):
                raise ErroreRete("Il login di Ford ha bloccato la richiesta (HTTP 403): riprova più tardi")
            raise ErroreCredenziali(f"Ford non accetta il codice: {self._messaggio(corpo) or http}")
        b2c = corpo
        http, corpo = await self._chiedi(
            "token FordPass",
            "POST",
            f"{self.url.fondazionale}/token/v2/cat-with-b2c-access-token",
            intestazioni={"Application-Id": r.app_id},
            json_={"idpToken": b2c["access_token"]},
        )
        if not isinstance(corpo, dict) or "access_token" not in corpo:
            raise ErroreCredenziali(f"FordPass non accetta il login: {self._messaggio(corpo) or http}")
        self.token = {}
        token = self._dai_token(corpo)
        if utente := self._utente(b2c):
            token["utente"] = utente
        self.token = token
        self._auto = {}
        return dict(token)

    @staticmethod
    def _utente(b2c: dict[str, Any]) -> dict[str, str]:
        """Chi è, dai JWT del login: l'id stabile e, se c'è, l'email."""
        for nome in ("id_token", "access_token"):
            dati = dal_jwt(str(b2c.get(nome) or ""))
            uid = dati.get("oid") or dati.get("sub")
            email = dati.get("email") or next(iter(dati.get("emails") or []), None) or dati.get("signInName")
            if uid:
                return {"id": str(uid), **({"email": str(email)} if email else {})}
        return {}

    def _scaduto(self, quando: Any) -> bool:
        try:
            return datetime.fromisoformat(str(quando)) - _MARGINE < datetime.now(UTC)
        except ValueError:
            return True

    async def _rinnova(self) -> None:
        if not self.token.get("refresh_token"):
            raise ErroreCredenziali("Manca il refresh token di Ford: serve rientrare")
        http, corpo = await self._chiedi(
            "rinnovo del token",
            "POST",
            f"{self.url.fondazionale}/token/v2/cat-with-refresh-token",
            intestazioni={"Application-Id": self.regione.app_id},
            json_={"refresh_token": self.token["refresh_token"]},
        )
        if http == 200 and isinstance(corpo, dict) and "access_token" in corpo:
            self._rifiuti = 0
            self.token = self._dai_token(corpo)
            self._auto = {}
            if self._salva:
                self._salva(dict(self.token))
            return
        self._rifiutato(http, corpo, "il rinnovo del token")
        raise ErroreApi("rinnovo del token", None, self._messaggio(corpo), http)

    def _rifiutato(self, http: int, corpo: Any, cosa: str) -> None:
        """Un 400/401: rientrare se il motivo è chiaro, o dopo qualche rifiuto di fila; se no è un guasto."""
        if http not in (400, 401, 403):
            return
        testo = self._messaggio(corpo).lower()
        codice = str(corpo.get("errorCode") or "") if isinstance(corpo, dict) else ""
        chiaro = codice == "460" or any(p in testo for p in ("invalid", "expired", "revoked", "invalid_grant"))
        self._rifiuti += 1
        if chiaro or self._rifiuti >= _RIFIUTI_PRIMA_DEL_REAUTH:
            raise ErroreCredenziali(f"Ford rifiuta {cosa}: {testo or http}")
        raise ErroreRete(f"Ford rifiuta {cosa} (HTTP {http}), si riprova alla prossima lettura")

    async def _token_valido(self) -> None:
        async with self._serratura:
            if not self.token.get("access_token") or self._scaduto(self.token.get("scade")):
                await self._rinnova()

    async def _token_auto(self, nuovo: bool = False) -> str:
        """Il token di Autonomic, scambiato col refresh token FordPass."""
        await self._token_valido()
        async with self._serratura:
            if nuovo or not self._auto or self._scaduto(self._auto.get("scade")):
                http, corpo = await self._chiedi(
                    "token della telemetria",
                    "POST",
                    f"{self.url.account}/auth/oidc/token",
                    forma={
                        "subject_token": str(self.token.get("refresh_token") or ""),
                        "subject_issuer": "fordpass",
                        "client_id": "fordpass-prod",
                        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                        "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
                    },
                )
                if http != 200 or not isinstance(corpo, dict) or "access_token" not in corpo:
                    self._rifiutato(http, corpo, "il token della telemetria")
                    raise ErroreApi("token della telemetria", None, self._messaggio(corpo), http)
                scade = datetime.now(UTC) + timedelta(seconds=int(corpo.get("expires_in") or 300))
                self._auto = {"access_token": corpo["access_token"], "scade": scade.isoformat()}
            return str(self._auto["access_token"])

    async def _ford_api(self, cosa: str, metodo: str, url: str, **kw: Any) -> Any:
        """Una chiamata all'API di Ford col token FordPass; un 401 rinnova e riprova una volta."""
        await self._token_valido()
        extra = kw.pop("intestazioni", {})
        http, corpo = await self._chiedi(cosa, metodo, url, intestazioni={**self._ford(), **extra}, **kw)
        if http == 401:
            async with self._serratura:
                await self._rinnova()
            http, corpo = await self._chiedi(cosa, metodo, url, intestazioni={**self._ford(), **extra}, **kw)
            if http == 401:
                raise ErroreCredenziali(f"{cosa}: Ford rifiuta il token")
        if http >= 400:
            raise ErroreApi(cosa, corpo.get("errorCode") if isinstance(corpo, dict) else None, str(corpo)[:200], http)
        return corpo

    # --- leggere -----------------------------------------------------------------------------------

    async def accedi(self) -> None:
        await self._token_valido()

    async def vetture(self) -> list[Vettura]:
        corpo = await self._ford_api(
            "elenco delle auto",
            "GET",
            f"{self.url.veicolo}/fpcpl-user-garage-service/v1/user/garage",
            intestazioni={"countryCode": self.regione.paese, "locale": self.regione.lingua},
        )
        voci = corpo if isinstance(corpo, list) else (corpo.get("vehicles") or []) if isinstance(corpo, dict) else []
        elenco = [v for voce in voci if isinstance(voce, dict) and (v := vettura_da_garage(voce))]
        self._vetture = {v.vin: v for v in elenco}
        return elenco

    async def stato(self, v: Vettura) -> StatoVettura:
        vin = v.vin
        conosciuta = vin in self._telemetria
        try:
            arrivata = await self._leggi_ws(vin)
        except ErroreRete as err:
            _LOGGER.debug("Il WebSocket della %s non va: %s", v.titolo, err)
            arrivata = False
        if not arrivata:
            try:
                await self._leggi_rest(vin)
            except (ErroreRete, ErroreApi) as err:
                if not conosciuta:
                    raise ErroreRete(f"La telemetria della {v.titolo} non arriva: {err}") from err
                _LOGGER.info("Telemetria della %s non nuova: si tiene l'ultima (%s)", v.titolo, err)
        return interpreta(self._telemetria.get(vin, {}), await self._dati_lenti(v))

    async def _leggi_ws(self, vin: str) -> bool:
        try:
            return await self._ws_istantanea(vin, await self._token_auto())
        except _Ws401:
            try:
                return await self._ws_istantanea(vin, await self._token_auto(nuovo=True))
            except _Ws401 as err:
                raise ErroreCredenziali("Autonomic rifiuta il token della telemetria") from err

    def _apri_ws(self, vin: str, token: str) -> Any:
        return self._http.ws_connect(
            f"{self.url.autonomic_ws}/telemetry/sources/fordpass/vehicles/{vin}/ws",
            headers={**_INTESTAZIONI, "Authorization": f"Bearer {token}"},
            timeout=aiohttp.ClientWSTimeout(ws_receive=_WS_MASSIMO_S, ws_close=5),
        )

    def _messaggio_ws(self, vin: str, msg: aiohttp.WSMessage) -> bool | None:
        """Un messaggio: True se ha portato dati, None se il WebSocket è finito."""
        if msg.type != aiohttp.WSMsgType.TEXT:
            return (
                None
                if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR)
                else False
            )
        try:
            dati = json.loads(msg.data)
        except ValueError:
            return False
        if not isinstance(dati, dict):
            return False
        if "_error" in dati:
            errore = dati["_error"] if isinstance(dati["_error"], dict) else {}
            if str(errore.get("code")) == "401":
                raise _Ws401
            _LOGGER.debug("Il WebSocket di Ford dice: %s", errore)
            return None
        if isinstance(dati.get("_data"), dict):
            _unisci(self._telemetria.setdefault(vin, {}), dati["_data"])
            return True
        return False

    async def _ws_istantanea(self, vin: str, token: str) -> bool:
        arrivata = False
        fine = time.monotonic() + _WS_MASSIMO_S
        try:
            async with self._apri_ws(vin, token) as ws:
                while (resta := fine - time.monotonic()) > 0:
                    try:
                        msg = await ws.receive(timeout=min(resta, _WS_QUIETE_S) if arrivata else resta)
                    except TimeoutError:
                        break
                    esito = self._messaggio_ws(vin, msg)
                    if esito is None:
                        break
                    arrivata = arrivata or esito
        except aiohttp.WSServerHandshakeError as err:
            if err.status == 401:
                raise _Ws401 from err
            raise ErroreRete(f"Il WebSocket di Ford non si apre: HTTP {err.status}") from err
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise ErroreRete(f"Il WebSocket di Ford non risponde: {err}") from err
        return arrivata

    async def _leggi_rest(self, vin: str) -> None:
        """La lettura REST di prima del WebSocket: Ford la sta togliendo, resta come riserva."""
        url = f"{self.url.autonomic}/telemetry/sources/fordpass/vehicles/{vin}"
        for nuovo in (False, True):
            token = await self._token_auto(nuovo=nuovo)
            http, corpo = await self._chiedi(
                "stato dell'auto",
                "GET",
                url,
                intestazioni={"Authorization": f"Bearer {token}"},
                params={"lrdt": "01-01-1970 00:00:00"},
            )
            if http != 401:
                break
        if http == 401:
            raise ErroreCredenziali("Autonomic rifiuta il token della telemetria")
        if http >= 400 or not isinstance(corpo, dict):
            raise ErroreApi("stato dell'auto", None, self._messaggio(corpo), http)
        self._telemetria[vin] = {}
        _unisci(self._telemetria[vin], corpo)

    async def _dati_lenti(self, v: Vettura) -> dict[str, Any]:
        """Ultima ricarica, limite per luogo e profilo del clima: ogni mezz'ora, e tutti facoltativi."""
        dati, quando = self._lenti.get(v.vin, ({}, 0.0))
        if quando and time.monotonic() - quando < _LENTI_OGNI_S:
            return dati
        dati = dict(dati)
        elettrica = str(v.extra.get("motore") or "BEV").upper() in {"BEV", "PHEV"}
        letture: list[tuple[str, Any]] = []
        if elettrica:
            letture += [("etl", self._ultime_ricariche), ("pct", self._orari_ricarica)]
        if "clima" in comandi_di(v):
            letture.append(("rcc", self._profilo_clima))
        for nome, leggi in letture:
            try:
                dati[nome] = await leggi(v.vin)
            except (ErroreApi, ErroreRete, KeyError, TypeError, ValueError) as err:
                _LOGGER.debug("%s della %s non letto: %s", nome, v.titolo, err)
        self._lenti[v.vin] = (dati, time.monotonic())
        return dati

    async def _ultime_ricariche(self, vin: str) -> dict[str, Any]:
        corpo = await self._ford_api(
            "ultime ricariche",
            "GET",
            f"{self.url.veicolo}/electrification/experiences/v2/devices/energy-transfer-logs",
            intestazioni={"deviceId": vin},
            params={"maxRecords": "1"},
        )
        return corpo if isinstance(corpo, dict) else {}

    async def _orari_ricarica(self, vin: str) -> list[dict[str, Any]]:
        corpo = await self._ford_api(
            "orari di ricarica",
            "GET",
            f"{self.url.veicolo}/electrification/experiences/v2/vehicles/preferred-charge-times",
            intestazioni={"vin": vin},
        )
        voci = corpo if isinstance(corpo, list) else []
        return [x for x in voci if isinstance(x, dict) and str(x.get("vin") or vin).upper() == vin.upper()]

    async def _profilo_clima(self, vin: str) -> dict[str, Any]:
        corpo = await self._ford_api(
            "profilo del clima", "POST", f"{self.url.veicolo}/rcc/profile/status", json_={"vin": vin}
        )
        return corpo if isinstance(corpo, dict) else {}

    # --- comandare -----------------------------------------------------------------------------------

    async def _comanda(
        self,
        v: Vettura,
        tipo: str,
        *,
        beta: bool = False,
        proprieta: dict[str, Any] | None = None,
        versione: str = "1.0.1",
        attendi: bool = True,
    ) -> None:
        """Un comando ad Autonomic; l'esito dall'auto arriva sul WebSocket, aperto prima di mandarlo."""
        async with self._un_comando:
            token = await self._token_auto()
            corpo: dict[str, Any] = {"tags": {}, "type": tipo, "wakeUp": True}
            if proprieta is not None or not beta:
                corpo["properties"] = proprieta or {}
            if beta:
                corpo["version"] = versione
            base = self.url.autonomic_beta if beta else self.url.autonomic
            url = f"{base}/command/vehicles/{v.vin}/commands"
            if not attendi:
                await self._manda(url, corpo, token)
                return
            try:
                async with self._apri_ws(v.vin, token) as ws:
                    id_ = await self._manda(url, corpo, token)
                    await self._esito(v, ws, tipo, id_)
            except aiohttp.WSServerHandshakeError as err:
                raise ErroreRete(f"Il WebSocket di Ford non si apre: HTTP {err.status}") from err
            except (aiohttp.ClientError, OSError) as err:
                raise ErroreRete(f"Il comando a Ford non arriva: {err}") from err

    async def _manda(self, url: str, corpo: dict[str, Any], token: str) -> str:
        http, risposta = await self._chiedi(
            "comando", "POST", url, intestazioni={"Authorization": f"Bearer {token}"}, json_=corpo
        )
        if http == 401:
            token = await self._token_auto(nuovo=True)
            http, risposta = await self._chiedi(
                "comando", "POST", url, intestazioni={"Authorization": f"Bearer {token}"}, json_=corpo
            )
            if http == 401:
                raise ErroreCredenziali("Autonomic rifiuta il token dei comandi")
        if not 200 <= http <= 205:
            raise ErroreComando(f"Ford non accetta il comando {corpo['type']}: {self._messaggio(risposta) or http}")
        if isinstance(risposta, dict):
            for chiave in ("id", "commandId", "correlationId"):
                if risposta.get(chiave):
                    return str(risposta[chiave])
        return ""

    async def _esito(self, v: Vettura, ws: Any, tipo: str, id_: str) -> None:
        fine = time.monotonic() + _COMANDO_MASSIMO_S
        while (resta := fine - time.monotonic()) > 0:
            try:
                msg = await ws.receive(timeout=resta)
            except TimeoutError:
                break
            try:
                esito = self._messaggio_ws(v.vin, msg)
            except _Ws401:
                break
            if esito is None:
                break
            stati = self._telemetria.get(v.vin, {}).get("states") or {}
            voce = stati.get(tipo) or stati.get(f"{tipo}Command") or {}
            if not isinstance(voce, dict) or (id_ and voce.get("commandId") not in (None, id_)):
                continue
            verso = str((voce.get("value") or {}).get("toState") or "").upper()
            if verso in ("SUCCESS", "COMMAND_SUCCEEDED_ON_DEVICE"):
                return
            if verso == "COMMAND_FAILED_ON_DEVICE":
                errore = ((voce.get("value") or {}).get("data") or {}).get("commandError") or {}
                guasto = errore.get("commandExecutionFailure") or {}
                motivo = guasto.get("oemErrorContext") or guasto.get("oemErrorCode") or "motivo sconosciuto"
                raise ErroreComando(f"La {v.titolo} rifiuta il comando: {motivo}")
            if verso == "EXPIRED":
                raise ErroreComando(f"La {v.titolo} non ha risposto al comando (scaduto)")
        _LOGGER.info("Comando %s alla %s accettato dal cloud, esito non ancora arrivato", tipo, v.titolo)

    async def chiudi(self, v: Vettura, _pin: str | None) -> None:
        await self._comanda(v, "lock")

    async def apri(self, v: Vettura, _pin: str | None) -> None:
        await self._comanda(v, "unlock")

    async def trova(self, v: Vettura, _pin: str | None) -> None:
        await self._comanda(v, "startPanicCue", proprieta={"duration": 3}, attendi=False)

    async def clima(self, v: Vettura, acceso: bool, _pin: str | None, temperatura: int = 22) -> None:
        if not acceso:
            await self._comanda(v, "cancelRemoteStart")
            return
        if temperatura:
            await self._imposta_temperatura(v, temperatura)
        await self._comanda(v, "remoteStart")

    async def _imposta_temperatura(self, v: Vettura, gradi: float) -> None:
        """La temperatura sta nel profilo del clima: si cambia solo quella, se il profilo c'è."""
        try:
            profilo = await self._profilo_clima(v.vin)
        except (ErroreApi, ErroreRete) as err:
            _LOGGER.info("Profilo del clima della %s non letto, si parte senza cambiarlo: %s", v.titolo, err)
            return
        preferenze = [dict(p) for p in profilo.get("rccUserProfiles") or [] if isinstance(p, dict)]
        voluta = _temperatura_rcc(gradi)
        voce = next((p for p in preferenze if p.get("preferenceType") == "SetPointTemp_Rq"), None)
        if voce is None or voce.get("preferenceValue") == voluta:
            return
        voce["preferenceValue"] = voluta
        try:
            await self._ford_api(
                "temperatura del clima",
                "PUT",
                f"{self.url.veicolo}/rcc/profile/update",
                json_={"crccStateFlag": "On", "userPreferences": preferenze, "vin": v.vin},
            )
        except (ErroreApi, ErroreRete) as err:
            _LOGGER.info("Temperatura del clima della %s non cambiata: %s", v.titolo, err)
            return
        dati, quando = self._lenti.get(v.vin, ({}, 0.0))
        self._lenti[v.vin] = ({**dati, "rcc": {**profilo, "rccUserProfiles": preferenze}}, quando)

    async def ricarica(self, v: Vettura, avvia: bool, _pin: str | None) -> None:
        tipo = "startGlobalChargeCommand" if avvia else "cancelGlobalChargeCommand"
        await self._comanda(v, tipo, beta=True)

    async def limite_ricarica(self, v: Vettura, percento: int, _pin: str | None) -> None:
        valore = _limite_ammesso(percento)
        impostazioni = {"globalDCTargetSoc": valore, "globalReserveSoc": valore, "globalTargetSoc": valore}
        await self._comanda(v, "updateChargeSettingsCommand", beta=True, proprieta={"chargeSettings": impostazioni})
        self._lenti.pop(v.vin, None)  # il limite per luogo va riletto

    async def destinazione(self, v: Vettura, nome: str, lat: float, lon: float, indirizzo: str = "") -> None:
        raise ErroreComando("Ford non riceve destinazioni da GdAAuto")
