"""Il filo col cloud My Porsche: i token di Porsche ID (Auth0) e l'API dell'app.

**Il login lo fa la persona, nel suo browser.** Porsche ID è un server Auth0
(``identity.porsche.com``) che chiede spesso un captcha e a volte una
passkey: GdAAuto non compila le sue pagine e non vede la password. Prepara il
link d'autorizzazione dell'app My Porsche, con PKCE (``indirizzo_browser``);
la persona entra sul sito di Porsche come fa sempre, e alla fine Porsche
rinvia all'indirizzo dell'app (``my-porsche-app://auth0/callback?code=…``),
che il browser non sa aprire. Quell'indirizzo, o il solo codice, si incolla
in GdAAuto (``codice_da``), che lo scambia con i token (``scambia_codice``).

**I token.** L'access token dura poco; il refresh token dura e può ruotare:
il cliente lo conserva con la funzione ``salva`` di chi lo crea. Se il
refresh non vale più, il cliente lo dice con ``ErroreCredenziali`` e Home
Assistant chiede di rientrare dal browser.

**I dati.** ``GET /connect/v1/vehicles`` elenca le auto dell'account;
``GET /connect/v1/vehicles/{vin}?mf=…`` dà le misure scelte così come il
cloud le ha già ricevute dall'auto: non la sveglia (svegliarla è un'altra
richiesta, ``wakeUpJob``, che GdAAuto non fa). Le statistiche dei viaggi si
rileggono ogni mezz'ora, e sono facoltative.

**I comandi.** ``POST /connect/v1/vehicles/{vin}/commands`` con una chiave
(``LOCK``, ``HONK_FLASH``, ``REMOTE_CLIMATIZER_START``…) e un contenuto; il
cloud risponde ``ACCEPTED`` con un id, e si chiede l'esito finché non dice
``PERFORMED`` o ``ERROR``. Per aprire serve il PIN dell'app: il cloud manda
una sfida (``SPIN_CHALLENGE``) e vuole lo SHA-512 del PIN unito alla sfida.

Indirizzi, client id, intestazioni dell'app, chiavi dei comandi e nomi dei
campi vengono da CJNE/pyporscheconnectapi (licenza MIT): vedi
RINGRAZIAMENTI.md. Il codice è riscritto da capo per ``aiohttp``.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import hashlib
import json
import logging
import os
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import aiohttp

from ...vettura import StatoVettura, Vettura
from ..errori import ErroreApi, ErroreComando, ErroreCredenziali, ErrorePin, ErroreRete
from .dati import (
    COMANDI_CLOUD,
    ELETTRICHE,
    ID,
    MISURE,
    MISURE_VIAGGI,
    capacita_comandi,
    consumo_medio,
    interpreta,
    vettura_da,
)

_LOGGER = logging.getLogger(__name__)

NOME = "Porsche"

IDENTITA = "https://identity.porsche.com"
API = "https://api.ppa.porsche.com/app"
CLIENT_ID = "XhygisuebbrqQ80byOuU5VncxLIm8E6H"
X_CLIENT_ID = "41843fb4-691d-4970-85c7-2673e8ecef40"
USER_AGENT = "de.porsche.one/20.26.36-row+186611 (android)"
REDIRECT_URI = "my-porsche-app://auth0/callback"
AUDIENCE = "https://api.porsche.com"
SCOPE = " ".join(
    (
        "openid",
        "profile",
        "email",
        "offline_access",
        "mbb",
        "ssodb",
        "badge",
        "vin",
        "dealers",
        "cars",
        "charging",
        "manageCharging",
        "plugAndCharge",
        "climatisation",
        "manageClimatisation",
        "pid:user_profile.porscheid:read",
        "pid:user_profile.name:read",
        "pid:user_profile.vehicles:read",
        "pid:user_profile.dealers:read",
        "pid:user_profile.emails:read",
        "pid:user_profile.phones:read",
        "pid:user_profile.addresses:read",
        "pid:user_profile.birthdate:read",
        "pid:user_profile.locale:read",
        "pid:user_profile.legal:read",
    )
)


_TIMEOUT = aiohttp.ClientTimeout(total=60)
#: Il token si rinnova cinque minuti prima che scada.
_MARGINE = timedelta(minutes=5)
#: Le statistiche dei viaggi si rileggono ogni mezz'ora.
_VIAGGI_OGNI_S = 30 * 60
#: Quanto aspettare l'esito di un comando, e ogni quanto chiederlo.
_ESITO_ENTRO_S = 120.0
_ESITO_OGNI_S = 2.0
#: Il limite di ricarica che il cloud accetta.
_LIMITE_MIN, _LIMITE_MAX = 25, 100

#: I comandi di GdAAuto e la chiave del cloud che li fa.
_COMANDI = {
    "chiudi": ("LOCK",),
    "apri": ("UNLOCK",),
    "trova": ("HONK_FLASH",),
    "clima": ("REMOTE_CLIMATIZER_START",),
    "ricarica": ("DIRECT_CHARGING_START",),
    "limite_ricarica": ("CHARGING_SETTINGS_EDIT", "CHARGING_PROFILES_EDIT"),
}


def comandi_di(v: Vettura) -> set[str]:
    """I comandi che l'auto accetta, da quello che il cloud ha detto di lei.

    Senza l'elenco del cloud: tutti quelli di un'elettrica, o soltanto
    serrature e lampeggio per un'auto a benzina; decide poi il cloud.
    """
    if v.extra.get("remoto") is False:
        return set()
    accesi = v.extra.get("comandi")
    if isinstance(accesi, list):
        return {nome for nome, chiavi in _COMANDI.items() if any(k in accesi for k in chiavi)}
    if v.extra.get("motore") == "COMBUSTION":
        return {"chiudi", "apri", "trova"}
    return set(_COMANDI)


def _b64(dati: bytes) -> str:
    return base64.urlsafe_b64encode(dati).decode().rstrip("=")


def sfida_pkce(verificatore: str) -> str:
    return _b64(hashlib.sha256(verificatore.encode()).digest())


def nuovo_verificatore() -> str:
    return _b64(os.urandom(48))


def parametri_autorizzazione(verificatore: str, stato: str) -> dict[str, str]:
    return {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "audience": AUDIENCE,
        "scope": SCOPE,
        "code_challenge": sfida_pkce(verificatore),
        "code_challenge_method": "S256",
        "state": stato,
    }


def indirizzo_browser(verificatore: str, stato: str, identita: str | None = None) -> str:
    """Il link del login dal browser: lo stesso dell'app, da aprire a mano."""
    return f"{(identita or IDENTITA).rstrip('/')}/authorize?{urlencode(parametri_autorizzazione(verificatore, stato))}"


def codice_da(incollato: str) -> tuple[str, str | None]:
    """Dall'indirizzo finale (``my-porsche-app://auth0/callback?code=…``) o dal solo codice:
    il codice e lo ``state``, se c'era."""
    testo = incollato.strip()
    if "code=" in testo or "error=" in testo:
        q = parse_qs(urlparse(testo).query or testo.split("?", 1)[-1])
        if not q.get("code"):
            errore = (q.get("error_description") or q.get("error") or ["nessun codice"])[0]
            raise ErroreCredenziali(f"Porsche ID non ha dato il codice: {errore}")
        return q["code"][0], (q.get("state") or [None])[0]
    if not testo or any(c.isspace() for c in testo):
        raise ErroreCredenziali("Quello che hai incollato non è un codice di Porsche ID")
    return testo, None


def dal_jwt(token: str) -> dict[str, Any]:
    """Il contenuto di un JWT, senza verificarlo: serve solo a sapere chi è."""
    try:
        parte = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(parte + "=" * (-len(parte) % 4)))
    except (IndexError, ValueError):
        return {}


def hash_pin(pin: str, sfida: str) -> str:
    """Come l'app: SHA-512 dei byte di «PIN + sfida» letti come esadecimale, in maiuscolo."""
    try:
        dati = bytes.fromhex(pin + sfida)
    except ValueError as err:
        raise ErrorePin("Il PIN di Porsche è fatto di sole cifre") from err
    return hashlib.sha512(dati).hexdigest().upper()


class _Rifiutato(Exception):
    """Il server dei token ha detto di no (4xx): per il refresh vuol dire «rientra»."""


class ClientePorsche:
    def __init__(
        self,
        sessione_http: aiohttp.ClientSession,
        token: dict[str, Any] | None = None,
        salva: Callable[[dict[str, Any]], None] | None = None,
        *,
        identita: str | None = None,
        api: str | None = None,
    ) -> None:
        self._http = sessione_http
        self.token: dict[str, Any] = dict(token or {})
        self._salva = salva
        self.identita = (identita or IDENTITA).rstrip("/")
        self.api = (api or API).rstrip("/")
        self._serratura = asyncio.Lock()
        self._serratura_comandi = asyncio.Lock()
        self._viaggi: dict[str, tuple[float | None, float]] = {}
        #: chi è, dall'id token: l'email e l'id di Porsche ID
        self.chi: dict[str, str] = {}

    # --- Porsche ID: solo i token ----------------------------------------------------

    @property
    def _intestazioni_app(self) -> dict[str, str]:
        return {"User-Agent": USER_AGENT, "X-Client-ID": X_CLIENT_ID}

    async def _token(self, dati: dict[str, str]) -> dict[str, Any]:
        url = f"{self.identita}/oauth/token"
        try:
            async with self._http.post(url, data=dati, headers=self._intestazioni_app, timeout=_TIMEOUT) as r:
                http = r.status
                testo = await r.text()
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise ErroreRete(f"I token di Porsche non rispondono: {err}") from err
        if http >= 500 or http == 429:
            raise ErroreRete(f"I token di Porsche hanno un guasto: HTTP {http}")
        try:
            corpo = json.loads(testo) if testo else {}
        except ValueError as err:
            if http >= 400:
                raise _Rifiutato(f"HTTP {http}") from err
            raise ErroreRete("I token di Porsche non si capiscono") from err
        if http >= 400 or not isinstance(corpo, dict) or not corpo.get("access_token"):
            descrizione = (corpo.get("error_description") or corpo.get("error")) if isinstance(corpo, dict) else ""
            raise _Rifiutato(str(descrizione or f"HTTP {http}"))
        if isinstance(corpo.get("id_token"), str):
            utente = dal_jwt(corpo["id_token"])
            self.chi = {k: str(utente[k]) for k in ("email", "sub") if utente.get(k)}
        scade = datetime.now(UTC) + timedelta(seconds=int(corpo.get("expires_in") or 3600))
        return {
            "access_token": corpo["access_token"],
            # Auth0 manda un refresh token nuovo solo se lo ruota: se no vale quello di prima.
            "refresh_token": corpo.get("refresh_token") or self.token.get("refresh_token"),
            "scade": scade.isoformat(),
        }

    async def scambia_codice(self, codice: str, verificatore: str) -> dict[str, Any]:
        """Il codice d'autorizzazione (dal login o dal browser) → i token."""
        try:
            self.token = await self._token(
                {
                    "grant_type": "authorization_code",
                    "client_id": CLIENT_ID,
                    "code": codice,
                    "redirect_uri": REDIRECT_URI,
                    "code_verifier": verificatore,
                }
            )
        except _Rifiutato as err:
            raise ErroreCredenziali(f"Porsche ID non accetta il codice: {err}") from err
        self._conserva()
        return dict(self.token)

    async def _rinnova(self) -> None:
        """Col refresh token. Se non vale più, serve che la persona rientri dal browser."""
        if not self.token.get("refresh_token"):
            raise ErroreCredenziali("Non c'è un refresh token di Porsche: serve rientrare dal browser")
        try:
            self.token = await self._token(
                {
                    "grant_type": "refresh_token",
                    "client_id": CLIENT_ID,
                    "refresh_token": self.token["refresh_token"],
                }
            )
        except _Rifiutato as err:
            raise ErroreCredenziali(f"Il refresh token di Porsche non vale più ({err}): rientra dal browser") from err
        self._conserva()

    def _conserva(self) -> None:
        if self._salva:
            self._salva(dict(self.token))

    async def _token_valido(self) -> str:
        async with self._serratura:
            scade = self.token.get("scade")
            if (
                not self.token.get("access_token")
                or not scade
                or datetime.fromisoformat(scade) - _MARGINE < datetime.now(UTC)
            ):
                await self._rinnova()
            return str(self.token["access_token"])

    # --- l'API dell'app ----------------------------------------------------------------

    async def _chiedi(self, cosa: str, metodo: str, percorso: str, *, json_: Any = None, ripeti: bool = True) -> Any:
        intestazioni = {
            **self._intestazioni_app,
            "Authorization": f"Bearer {await self._token_valido()}",
            "Accept": "application/json",
        }
        try:
            async with self._http.request(
                metodo, f"{self.api}{percorso}", headers=intestazioni, json=json_, timeout=_TIMEOUT
            ) as r:
                http = r.status
                testo = await r.text()
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise ErroreRete(f"{cosa}: Porsche non risponde: {err}") from err
        if http == 401:
            if not ripeti:
                raise ErroreCredenziali(f"{cosa}: Porsche rifiuta il token")
            # Un token revocato prima del tempo: si rinnova e si riprova una volta.
            async with self._serratura:
                await self._rinnova()
            return await self._chiedi(cosa, metodo, percorso, json_=json_, ripeti=False)
        if http >= 500 or http in (408, 429):
            raise ErroreRete(f"{cosa}: Porsche ha un guasto o è occupato: HTTP {http}")
        if http == 423:
            raise ErroreCredenziali(f"{cosa}: l'account Porsche è bloccato: sbloccalo dall'app o dal sito")
        try:
            corpo = json.loads(testo) if testo else {}
        except ValueError:
            if http >= 400:
                raise ErroreApi(cosa, "http", testo[:200], http) from None
            raise ErroreRete(f"{cosa}: la risposta di Porsche non si capisce") from None
        if http >= 400:
            messaggio = ""
            if isinstance(corpo, dict):
                messaggio = str(corpo.get("message") or corpo.get("error") or corpo.get("title") or "")
            codice = corpo.get("code") if isinstance(corpo, dict) else None
            raise ErroreApi(cosa, codice, messaggio or testo[:200], http)
        return corpo

    @staticmethod
    def _misure(chiavi: tuple[str, ...], comandi: tuple[str, ...] = ()) -> str:
        return urlencode([("mf", k) for k in chiavi] + [("cf", k) for k in comandi])

    # --- leggere --------------------------------------------------------------------

    async def accedi(self) -> None:
        await self._token_valido()

    async def vetture(self) -> list[Vettura]:
        elenco = await self._chiedi("elenco delle auto", "GET", "/connect/v1/vehicles")
        if isinstance(elenco, dict):
            elenco = elenco.get("vehicles") or elenco.get("items") or []
        vetture = [v for voce in elenco or [] if (v := vettura_da(voce))]
        for v in vetture:
            await self._capacita(v)
        return vetture

    async def _capacita(self, v: Vettura) -> None:
        """Quali comandi accetta quell'auto, e se i servizi remoti sono accesi. Facoltativo."""
        percorso = f"/connect/v1/vehicles/{v.vin}?{self._misure(('REMOTE_ACCESS_AUTHORIZATION',), COMANDI_CLOUD)}"
        try:
            risposta = await self._chiedi("comandi dell'auto", "GET", percorso)
        except (ErroreApi, ErroreRete) as err:
            _LOGGER.debug("I comandi della %s non si leggono: %s", v.modello, err)
            return
        accesi, clima = capacita_comandi(risposta)
        if isinstance(risposta, dict) and risposta.get("commands") is not None:
            v.extra["comandi"] = accesi
        if clima:
            v.extra["clima"] = clima
        for m in (risposta.get("measurements") or []) if isinstance(risposta, dict) else []:
            if isinstance(m, dict) and m.get("key") == "REMOTE_ACCESS_AUTHORIZATION":
                acceso = (m.get("value") or {}).get("isEnabled")
                if isinstance(acceso, bool):
                    v.extra["remoto"] = acceso

    async def stato(self, v: Vettura) -> StatoVettura:
        percorso = f"/connect/v1/vehicles/{v.vin}?{self._misure(MISURE)}"
        risposta = await self._chiedi("stato dell'auto", "GET", percorso)
        if not isinstance(risposta, dict):
            raise ErroreRete("stato dell'auto: la risposta di Porsche non si capisce")
        stato = interpreta(risposta, v)
        if v.extra.get("motore", "") in (*ELETTRICHE, ""):
            stato.consumo_medio_kwh_100km = await self._consumo(v)
        return stato

    async def _consumo(self, v: Vettura) -> float | None:
        """Il consumo medio dalle statistiche dei viaggi, riletto ogni mezz'ora. Facoltativo."""
        valore, quando = self._viaggi.get(v.vin, (None, 0.0))
        if quando and time.monotonic() - quando < _VIAGGI_OGNI_S:
            return valore
        try:
            risposta = await self._chiedi(
                "statistiche dei viaggi", "GET", f"/connect/v1/vehicles/{v.vin}?{self._misure(MISURE_VIAGGI)}"
            )
            valore = consumo_medio(risposta) or valore
        except (ErroreApi, ErroreRete) as err:
            _LOGGER.debug("Le statistiche dei viaggi della %s non si leggono: %s", v.modello, err)
        self._viaggi[v.vin] = (valore, time.monotonic())
        return valore

    # --- comandare -------------------------------------------------------------------

    async def comanda(self, v: Vettura, chiave: str, contenuto: dict[str, Any]) -> None:
        """Un comando all'auto, uno alla volta, fino all'esito."""
        async with self._serratura_comandi:
            await self._comanda(v, chiave, contenuto)

    async def _comanda(self, v: Vettura, chiave: str, contenuto: dict[str, Any]) -> None:
        percorso = f"/connect/v1/vehicles/{v.vin}/commands"
        try:
            r = await self._chiedi(f"comando {chiave}", "POST", percorso, json_={"key": chiave, "payload": contenuto})
        except ErroreApi as err:
            if chiave == "UNLOCK" and err.http in (400, 403) and "pin" in err.messaggio.lower():
                raise ErrorePin(f"Porsche non accetta il PIN: {err.messaggio}") from err
            raise ErroreComando(f"Porsche rifiuta il comando {chiave}: {err.messaggio or err.http}") from err
        esito = (r.get("status") or {}) if isinstance(r, dict) else {}
        risultato, id_ = str(esito.get("result") or "").upper(), esito.get("id")
        if risultato == "ACCEPTED" and id_:
            risultato, esito = await self._aspetta_esito(v, chiave, str(id_))
        if risultato in ("PERFORMED", "ACCEPTED", "SUCCESS"):
            return
        motivo = esito.get("errorType") or esito.get("cause") or esito.get("message") or risultato or "nessun esito"
        if chiave == "UNLOCK" and "PIN" in str(motivo).upper():
            raise ErrorePin(f"Porsche non accetta il PIN: {motivo}")
        raise ErroreComando(f"L'auto non ha eseguito {chiave}: {motivo}")

    async def _aspetta_esito(self, v: Vettura, chiave: str, id_: str) -> tuple[str, dict[str, Any]]:
        scadenza = time.monotonic() + _ESITO_ENTRO_S
        while time.monotonic() < scadenza:
            await asyncio.sleep(_ESITO_OGNI_S)
            r = await self._chiedi(f"esito di {chiave}", "GET", f"/connect/v1/vehicles/{v.vin}/commands/{id_}")
            esito = (r.get("status") or {}) if isinstance(r, dict) else {}
            risultato = str(esito.get("result") or "").upper()
            if risultato and risultato not in ("ACCEPTED", "UNKNOWN", "IN_PROGRESS", "PENDING"):
                return risultato, esito
        raise ErroreComando(f"L'auto non ha confermato {chiave} in {_ESITO_ENTRO_S:.0f} secondi")

    async def chiudi(self, v: Vettura, pin: str | None) -> None:
        await self.comanda(v, "LOCK", {"spin": None})

    async def apri(self, v: Vettura, pin: str | None) -> None:
        """Aprire vuole il PIN dell'app: il cloud manda una sfida e vuole il suo hash."""
        pin = str(pin or "").strip()
        if not pin:
            raise ErrorePin("Aprire una Porsche vuole il PIN dell'app My Porsche: scrivilo nelle opzioni di GdAAuto")
        async with self._serratura_comandi:
            r = await self._chiedi(
                "sfida del PIN",
                "POST",
                f"/connect/v1/vehicles/{v.vin}/commands",
                json_={"key": "SPIN_CHALLENGE", "payload": {"spin": None}},
            )
            sfida = ((r.get("data") or {}).get("challenge")) if isinstance(r, dict) else None
            if not sfida:
                raise ErroreComando("Porsche non ha mandato la sfida per il PIN: l'auto non si apre")
            await self._comanda(v, "UNLOCK", {"spin": {"challenge": sfida, "hash": hash_pin(pin, str(sfida))}})

    async def trova(self, v: Vettura, pin: str | None) -> None:
        """Clacson e frecce, come «trova l'auto» dell'app."""
        await self.comanda(v, "HONK_FLASH", {"mode": "HONK_AND_FLASH", "spin": None})

    async def clima(self, v: Vettura, acceso: bool, pin: str | None, temperatura: int = 22) -> None:
        if not acceso:
            await self.comanda(v, "REMOTE_CLIMATIZER_STOP", {})
            return
        limiti = v.extra.get("clima") or {}
        minimo = float(limiti.get("minTemperature") or 15 + 273.15)
        massimo = float(limiti.get("maxTemperature") or 30 + 273.15)
        kelvin = round(min(max(temperatura + 273.15, minimo), massimo), 2)
        contenuto = {
            "spin": None,
            "climateZonesEnabled": {"frontLeft": False, "frontRight": False, "rearLeft": False, "rearRight": False},
            "targetTemperature": kelvin,
        }
        await self.comanda(v, "REMOTE_CLIMATIZER_START", contenuto)

    async def ricarica(self, v: Vettura, avvia: bool, pin: str | None) -> None:
        """La «ricarica immediata» dell'app: carica subito, senza aspettare il profilo o il timer."""
        await self.comanda(v, "DIRECT_CHARGING_START" if avvia else "DIRECT_CHARGING_STOP", {"spin": None})

    async def limite_ricarica(self, v: Vettura, percento: int, pin: str | None) -> None:
        """Il limite sta nelle impostazioni (auto con le partenze) o nel profilo attivo."""
        percento = int(min(max(percento, _LIMITE_MIN), _LIMITE_MAX))
        misure = v.stato.grezzo if v.stato else {}
        accesi = v.extra.get("comandi")
        profili = ((misure.get("CHARGING_PROFILES") or {}).get("list")) or []
        senza_profili = isinstance(accesi, list) and "CHARGING_PROFILES_EDIT" not in accesi
        if "DEPARTURES" in misure or senza_profili or not profili:
            await self.comanda(v, "CHARGING_SETTINGS_EDIT", {"targetSoc": percento, "spin": None})
            return
        attivo = next((p for p in profili if isinstance(p, dict) and p.get("isEnabled")), None)
        if attivo is None:
            raise ErroreComando("Nessun profilo di ricarica attivo: sceglilo nell'app My Porsche")
        nuovi = [{**p, "minSoc": percento} if p is attivo else p for p in profili]
        await self.comanda(v, "CHARGING_PROFILES_EDIT", {"list": nuovi})


__all__ = [
    "API",
    "CLIENT_ID",
    "ID",
    "IDENTITA",
    "NOME",
    "REDIRECT_URI",
    "ClientePorsche",
    "codice_da",
    "comandi_di",
    "dal_jwt",
    "hash_pin",
    "indirizzo_browser",
    "nuovo_verificatore",
]
