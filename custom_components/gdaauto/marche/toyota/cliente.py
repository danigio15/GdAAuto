"""Il filo col cloud europeo di Toyota e Lexus (Toyota Connected Europe).

È il cloud delle app MyToyota e Lexus Link+: lo stesso per i due marchi, con
lo stesso login. Cambia solo un'intestazione (``x-brand``: ``T`` o ``L``).

Si entra in tre passi, come fa l'app:

1. ``authenticate`` (ForgeRock, su ``b2c-login.toyota-europe.com``): il
   server fa delle domande (lingua, nome utente, password) in forma di
   «callback» da riempire e rimandare, finché non risponde con un ``tokenId``;
2. ``authorize``: con quel ``tokenId`` come cookie, il server rimanda
   (302) all'indirizzo dell'app con un ``code``;
3. ``access_token``: il ``code`` diventa access token, refresh token e un
   ``id_token`` da cui si legge l'``uuid`` dell'utente.

Poi si parla con ``ctpa-oneapi`` col Bearer, la chiave pubblica dell'app
(``x-api-key``) e l'uuid (``x-guid``). Il refresh token ruota: chi crea il
cliente gli dà una funzione ``salva`` che conserva i token nuovi. Se il
rinnovo non va, si rientra con email e password.

Si legge quello che il cloud ha già (non si sveglia l'auto per leggerla).
Si comanda: serrature (``/v1/global/remote/command``) e clima
(``/v2/remote/climate-control``), un comando alla volta.

Indirizzi, intestazioni e chiave vengono da pytoyoda (licenza MIT): vedi
RINGRAZIAMENTI.md.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json
import logging
import re
from typing import Any
from urllib.parse import parse_qs, urlparse
import uuid
from weakref import WeakKeyDictionary

import aiohttp

from ...vettura import StatoVettura, Vettura
from ..errori import ErroreApi, ErroreComando, ErroreCredenziali, ErroreRete
from .dati import interpreta

_LOGGER = logging.getLogger(__name__)

ID = "toyota"
NOME = "Toyota"

#: Il marchio del catalogo, e quello che il cloud vuole nelle intestazioni.
MARCHI = {"toyota": ("Toyota", "T"), "lexus": ("Lexus", "L")}

LOGIN = "https://b2c-login.toyota-europe.com"
API = "https://ctpa-oneapi.tceu-ctp-prd.toyotaconnectedeurope.io"
_REALM = "/root/realms/tme"
AUTENTICA = f"/json/realms{_REALM}/authenticate?authIndexType=service&authIndexValue=oneapp"
AUTORIZZA = f"/oauth2/realms{_REALM}/authorize"
TOKEN = f"/oauth2/realms{_REALM}/access_token"

CLIENT_ID = "oneapp"
REDIRECT_URI = "com.toyota.oneapp:/oauth2Callback"
#: ``oneapp:oneapp`` in base64, come la manda l'app.
BASIC = "basic b25lYXBwOm9uZWFwcA=="
#: La chiave pubblica dell'app MyToyota (da pytoyoda, MIT).
API_KEY = "tTZipv6liF74PwMfk9Ed68AQ0bISswwf3iHQdqcF"
VERSIONE_APP = "2.14.0"

_TIMEOUT = aiohttp.ClientTimeout(total=45)
_MARGINE = timedelta(minutes=2)
_GIRI_LOGIN = 10

#: Il comando del clima è stato accettato.
CLIMA_OK = "000000"
TEMPERATURA_MIN, TEMPERATURA_MAX = 18, 29

_STATI_RIFIUTO = {"error", "failed", "failure", "rejected"}
_CODICE_HTTP = re.compile(r"(?:^|[^0-9])([45][0-9]{2})(?:$|[^0-9])")

# Un login alla volta per sessione: il server mette la sessione in un cookie,
# e la sessione HTTP di Home Assistant è di tutti.
_LOGIN_IN_CORSO: WeakKeyDictionary[aiohttp.ClientSession, asyncio.Lock] = WeakKeyDictionary()


def comandi_di(v: Vettura) -> set[str]:
    """Serrature e clima, se l'auto li dichiara fra le sue capacità."""
    comandi: set[str] = set()
    if v.extra.get("serrature"):
        comandi |= {"chiudi", "apri"}
    if v.extra.get("clima"):
        comandi.add("clima")
    return comandi


def uuid_da(id_token: str) -> str | None:
    """L'``uuid`` dell'utente nel JWT ``id_token``, senza verificarne la firma:
    serve solo a dire al cloud chi siamo, come fa l'app."""
    try:
        corpo = id_token.split(".")[1]
        corpo += "=" * (-len(corpo) % 4)
        valore = json.loads(base64.urlsafe_b64decode(corpo)).get("uuid")
    except (IndexError, ValueError, AttributeError):
        return None
    return str(valore) if valore else None


def _vero(*valori: Any) -> bool:
    """Le capacità arrivano come ``true`` o come ``1`` (i ``features``)."""
    return any(str(v).lower() in ("true", "1", "yes") for v in valori)


def capacita(voce: dict[str, Any]) -> dict[str, bool]:
    """Cosa sa fare l'auto, dai blocchi di capacità dell'elenco (come ha_toyota)."""
    est = voce.get("extendedCapabilities") or {}
    fun = voce.get("features") or {}
    rem = voce.get("remoteServiceCapabilities") or {}
    return {
        "elettrica": _vero(est.get("econnectVehicleStatusCapable"), voce.get("evVehicle"))
        or str(voce.get("fuelType") or "").upper() in ("E", "I"),
        "stato": _vero(est.get("vehicleStatus"), fun.get("vehicleStatus")),
        "telemetria": _vero(est.get("telemetryCapable"), fun.get("telemetry")),
        "posizione": _vero(est.get("lastParkedCapable"), fun.get("lastParked")),
        "clima": _vero(
            fun.get("climateStartEngine"),
            est.get("climateCapable"),
            est.get("econnectClimateCapable"),
            est.get("remoteEngineStartStop"),
        ),
        "serrature": _vero(est.get("doorLockUnlockCapable"), rem.get("dlockUnlockCapable"), fun.get("doorLockCapable")),
    }


def _motivo_rifiuto(corpo: dict[str, Any]) -> str | None:
    """Se la risposta a un comando dice di no, perché."""
    if corpo.get("errors"):
        return f"errore {corpo['errors']}"
    codice = corpo.get("code")
    if isinstance(codice, int) and 400 <= codice < 600:
        return f"HTTP {codice}"
    stato = corpo.get("status")
    if isinstance(stato, str):
        return stato if stato.lower() in _STATI_RIFIUTO else None
    messaggi = stato.get("messages") if isinstance(stato, dict) else None
    for m in messaggi or []:
        if isinstance(m, dict) and _CODICE_HTTP.search(str(m.get("responseCode") or "")):
            return str(m.get("description") or m.get("responseCode"))
    return None


def _messaggio(corpo: Any) -> str:
    if not isinstance(corpo, dict):
        return str(corpo)[:200]
    stato = corpo.get("status")
    if isinstance(stato, dict):
        for m in stato.get("messages") or []:
            if isinstance(m, dict):
                return f"{m.get('responseCode') or ''} {m.get('description') or ''}".strip()
    return str(corpo.get("message") or corpo.get("description") or corpo.get("reason") or corpo)[:200]


class ClienteToyota:
    def __init__(
        self,
        sessione_http: aiohttp.ClientSession,
        email: str,
        password: str,
        marchio: str = "toyota",
        token: dict[str, Any] | None = None,
        salva: Callable[[dict[str, Any]], None] | None = None,
        *,
        login: str = LOGIN,
        api: str = API,
    ) -> None:
        self._http = sessione_http
        self.email = email.strip()
        self._password = password
        self.marchio = marchio if marchio in MARCHI else "toyota"
        self.nome_marchio, self._sigla = MARCHI[self.marchio]
        self.token: dict[str, Any] = dict(token or {})
        self._salva = salva
        self.login = login.rstrip("/")
        self.api = api.rstrip("/")
        self._serratura = asyncio.Lock()
        self._comando = asyncio.Lock()

    # --- il login ------------------------------------------------------------

    async def _posta_login(
        self, cosa: str, url: str, biscotti: dict[str, str], **kw: Any
    ) -> tuple[int, Any, dict[str, str]]:
        intestazioni = dict(kw.pop("headers", {}))
        if biscotti:
            intestazioni["Cookie"] = "; ".join(f"{k}={v}" for k, v in biscotti.items())
        metodo = kw.pop("metodo", "POST")
        try:
            async with self._http.request(
                metodo, url, headers=intestazioni, timeout=_TIMEOUT, allow_redirects=False, **kw
            ) as r:
                testo = await r.text()
                for nome, morsel in r.cookies.items():
                    biscotti[nome] = morsel.value
                http = r.status
                posto = {"location": r.headers.get("Location", "")}
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise ErroreRete(f"{cosa}: il login di {self.nome_marchio} non risponde: {err}") from err
        if http >= 500 or http == 429:
            raise ErroreRete(f"{cosa}: il login di {self.nome_marchio} ha un guasto ({http})")
        try:
            corpo: Any = json.loads(testo) if testo else {}
        except ValueError:
            corpo = {"message": testo[:200]}
        return http, corpo, posto

    def _rispondi(self, callbacks: list[Any]) -> None:
        """Si riempiono le domande del login che si sanno: nome utente e password."""
        for cb in callbacks:
            if not isinstance(cb, dict):
                continue
            uscite = cb.get("output") or [{}]
            domanda = str((uscite[0] or {}).get("value") or "")
            entrate = cb.get("input") or []
            if cb.get("type") == "NameCallback" and domanda == "User Name" and entrate:
                entrate[0]["value"] = self.email
            elif cb.get("type") == "PasswordCallback" and entrate:
                entrate[0]["value"] = self._password
            elif cb.get("type") == "TextOutputCallback" and domanda == "User Not Found":
                raise ErroreCredenziali(f"{self.nome_marchio} non conosce l'utente {self.email}")

    async def _entra_con_password(self) -> dict[str, Any]:
        """I tre passi del login: authenticate, authorize, access_token."""
        lucchetto = _LOGIN_IN_CORSO.setdefault(self._http, asyncio.Lock())
        async with lucchetto:
            dominio = urlparse(self.login).hostname or ""
            # Si parte puliti: una sessione rimasta nel barattolo sarebbe di qualcun altro.
            self._http.cookie_jar.clear_domain(dominio)
            try:
                return await self._tre_passi()
            finally:
                self._http.cookie_jar.clear_domain(dominio)

    async def _tre_passi(self) -> dict[str, Any]:
        biscotti: dict[str, str] = {}
        corpo: Any = {}
        token_id = ""
        for _ in range(_GIRI_LOGIN):
            if isinstance(corpo, dict) and corpo.get("callbacks"):
                self._rispondi(corpo["callbacks"])
            http, corpo, _ = await self._posta_login("accesso", self.login + AUTENTICA, biscotti, json=corpo)
            if http == 401:
                raise ErroreCredenziali(f"{self.nome_marchio} rifiuta email o password: {_messaggio(corpo)}")
            if http != 200 or not isinstance(corpo, dict):
                raise ErroreApi("accesso", None, _messaggio(corpo), http)
            if corpo.get("tokenId"):
                token_id = str(corpo["tokenId"])
                break
        if not token_id:
            raise ErroreApi("accesso", None, "il login non è finito dopo troppe domande")

        biscotti["iPlanetDirectoryPro"] = token_id
        parametri = {
            "client_id": CLIENT_ID,
            "scope": "openid profile write",
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "code_challenge": "plain",
            "code_challenge_method": "plain",
        }
        http, corpo, posto = await self._posta_login(
            "autorizzazione", self.login + AUTORIZZA, biscotti, metodo="GET", params=parametri
        )
        codice = (parse_qs(urlparse(posto["location"]).query).get("code") or [""])[0]
        if http != 302 or not codice:
            raise ErroreApi("autorizzazione", None, _messaggio(corpo), http)

        return await self._chiedi_token(
            {
                "client_id": CLIENT_ID,
                "code": codice,
                "redirect_uri": REDIRECT_URI,
                "grant_type": "authorization_code",
                "code_verifier": "plain",
            },
            biscotti,
        )

    async def _chiedi_token(self, modulo: dict[str, str], biscotti: dict[str, str] | None = None) -> dict[str, Any]:
        http, corpo, _ = await self._posta_login(
            "token", self.login + TOKEN, biscotti or {}, data=modulo, headers={"Authorization": BASIC}
        )
        if http in (400, 401) and modulo["grant_type"] == "refresh_token":
            raise ErroreCredenziali(f"{self.nome_marchio} non rinnova più il token: {_messaggio(corpo)}")
        if http != 200 or not isinstance(corpo, dict) or not corpo.get("access_token"):
            raise ErroreApi("token", None, _messaggio(corpo), http)
        utente = uuid_da(str(corpo.get("id_token") or "")) or self.token.get("uuid")
        if not utente:
            raise ErroreApi("token", None, "manca l'uuid dell'utente nell'id_token", http)
        scade = datetime.now(UTC) + timedelta(seconds=int(corpo.get("expires_in") or 3600))
        return {
            "access_token": corpo["access_token"],
            "refresh_token": corpo.get("refresh_token") or self.token.get("refresh_token"),
            "uuid": utente,
            "scade": scade.isoformat(),
        }

    async def _rinnova(self) -> None:
        """Col refresh token se c'è e vale; se no, con email e password."""
        nuovo: dict[str, Any] | None = None
        if self.token.get("refresh_token"):
            try:
                nuovo = await self._chiedi_token(
                    {
                        "client_id": CLIENT_ID,
                        "redirect_uri": REDIRECT_URI,
                        "grant_type": "refresh_token",
                        "code_verifier": "plain",
                        "refresh_token": str(self.token["refresh_token"]),
                    }
                )
            except (ErroreCredenziali, ErroreApi) as err:
                _LOGGER.debug("Rinnovo del token %s non riuscito, si rientra: %s", self.nome_marchio, err)
        if nuovo is None:
            nuovo = await self._entra_con_password()
        self.token = nuovo
        if self._salva:
            self._salva(dict(self.token))

    async def _token_valido(self, forza: bool = False) -> str:
        async with self._serratura:
            scade = self.token.get("scade")
            if forza or not scade or datetime.fromisoformat(scade) - _MARGINE < datetime.now(UTC):
                await self._rinnova()
            return str(self.token["access_token"])

    # --- la rete -----------------------------------------------------------------

    def _intestazioni(self, token: str, vin: str | None) -> dict[str, str]:
        utente = str(self.token.get("uuid") or "")
        h = {
            "x-api-key": API_KEY,
            "API_KEY": API_KEY,
            "x-guid": utente,
            "guid": utente,
            "x-client-ref": hmac.new(VERSIONE_APP.encode(), utente.encode(), hashlib.sha256).hexdigest(),
            "x-correlationid": str(uuid.uuid4()),
            "x-appversion": VERSIONE_APP,
            "x-channel": "ONEAPP",
            "x-brand": self._sigla,
            "x-region": "EU",
            "x-user-region": "EU",
            "Authorization": f"Bearer {token}",
            "User-Agent": "okhttp/4.10.0",
        }
        if self._sigla != "T":
            h["x-appbrand"] = self._sigla
            h["brand"] = self._sigla
        if vin:
            h["vin"] = vin
        return h

    async def _chiedi(
        self, cosa: str, metodo: str, percorso: str, vin: str | None = None, corpo: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        token = await self._token_valido()
        for tentativo in range(2):
            try:
                async with self._http.request(
                    metodo,
                    self.api + percorso,
                    headers=self._intestazioni(token, vin),
                    json=corpo,
                    timeout=_TIMEOUT,
                ) as r:
                    http = r.status
                    testo = await r.text()
            except (aiohttp.ClientError, TimeoutError, OSError) as err:
                raise ErroreRete(f"{cosa}: {self.nome_marchio} non risponde: {err}") from err
            if http == 401 and tentativo == 0:
                # Il token è scaduto prima del previsto: se ne prende uno nuovo e si riprova una volta.
                token = await self._token_valido(forza=True)
                continue
            break
        try:
            risposta: Any = json.loads(testo) if testo else {}
        except ValueError:
            risposta = {"message": testo[:200]}
        if http == 401:
            raise ErroreCredenziali(f"{cosa}: {self.nome_marchio} rifiuta il token")
        if http == 429 or http >= 500:
            raise ErroreRete(f"{cosa}: {self.nome_marchio} risponde {http} ({_messaggio(risposta)})")
        if http >= 400:
            raise ErroreApi(cosa, None, _messaggio(risposta), http)
        return risposta if isinstance(risposta, dict) else {}

    # --- leggere -------------------------------------------------------------------

    async def accedi(self) -> None:
        await self._token_valido()

    async def vetture(self) -> list[Vettura]:
        r = await self._chiedi("elenco delle auto", "GET", "/v2/vehicle/guid")
        elenco: list[Vettura] = []
        for voce in r.get("payload") or []:
            if not isinstance(voce, dict) or not voce.get("vin"):
                continue
            cap = capacita(voce)
            if not cap["elettrica"]:
                # GdAAuto porta le auto con la spina: un'ibrida «normale» non ha una batteria da leggere.
                _LOGGER.debug("%s: %s non ha la spina, si salta", self.nome_marchio, voce.get("vin"))
                continue
            modello = str(voce.get("carlineName") or voce.get("modelName") or self.nome_marchio)
            anno = str(voce.get("modelYear") or "")
            elenco.append(
                Vettura(
                    vin=str(voce["vin"]),
                    marca=ID,
                    modello=modello,
                    nome=str(voce.get("nickName") or modello),
                    anno=int(anno) if anno.isdigit() else None,
                    targa=str(voce.get("registrationNumber") or ""),
                    colore=str(voce.get("color") or ""),
                    condivisa=voce.get("owner") is False,
                    extra={**cap, "marchio": voce.get("brand"), "alimentazione": voce.get("fuelType")},
                )
            )
        return elenco

    async def _pezzo(self, cosa: str, percorso: str, vin: str) -> dict[str, Any] | None:
        """Un pezzo dello stato. Se il cloud non ce l'ha per quest'auto, si va avanti senza."""
        try:
            r = await self._chiedi(cosa, "GET", percorso, vin)
        except ErroreApi as err:
            _LOGGER.debug("%s: %s non disponibile per %s: %s", self.nome_marchio, cosa, vin[-6:], err)
            return None
        p = r.get("payload")
        return p if isinstance(p, dict) else {}

    async def stato(self, v: Vettura) -> StatoVettura:
        """Uno dopo l'altro, solo i pezzi che l'auto dichiara: il cloud non ama la fretta."""
        x = v.extra
        richieste = [
            ("elettrico", "stato elettrico", "/v1/vehicle/electric/status", True),
            ("stato", "stato dell'auto", "/v1/vehicle/status", x.get("stato", True)),
            ("telemetria", "telemetria", "/v3/telemetry", x.get("telemetria", True)),
            ("posizione", "posizione", "/v1/location", x.get("posizione", True)),
            ("clima", "stato del clima", "/v1/vehicle/climate-status", x.get("clima", False)),
        ]
        parti: dict[str, Any] = {}
        for nome, cosa, percorso, serve in richieste:
            if serve:
                pezzo = await self._pezzo(cosa, percorso, v.vin)
                if pezzo is not None:
                    parti[nome] = pezzo
        if not parti:
            raise ErroreApi("stato dell'auto", None, f"{self.nome_marchio} non ha dati per {v.titolo}")
        return interpreta(parti)

    # --- comandare -----------------------------------------------------------------

    async def _comanda(self, cosa: str, percorso: str, v: Vettura, corpo: dict[str, Any]) -> dict[str, Any]:
        async with self._comando:
            try:
                return await self._chiedi(cosa, "POST", percorso, v.vin, corpo)
            except ErroreApi as err:
                raise ErroreComando(f"{self.nome_marchio} non esegue «{cosa}»: {err.messaggio or err.http}") from err

    async def _serrature(self, v: Vettura, comando: str) -> None:
        r = await self._comanda(comando, "/v1/global/remote/command", v, {"command": comando})
        if motivo := _motivo_rifiuto(r):
            raise ErroreComando(f"{self.nome_marchio} non esegue «{comando}»: {motivo}")

    async def chiudi(self, v: Vettura, _pin: str | None) -> None:
        await self._serrature(v, "door-lock")

    async def apri(self, v: Vettura, _pin: str | None) -> None:
        await self._serrature(v, "door-unlock")

    async def clima(self, v: Vettura, acceso: bool, _pin: str | None, temperatura: int = 22) -> None:
        if acceso:
            gradi = max(TEMPERATURA_MIN, min(TEMPERATURA_MAX, int(temperatura)))
            corpo: dict[str, Any] = {"command": "start", "temperature": {"value": gradi, "unit": "C"}}
        else:
            corpo = {"command": "stop"}
        r = await self._comanda("clima", "/v2/remote/climate-control", v, corpo)
        payload = r.get("payload")
        codice = payload.get("returnCode") if isinstance(payload, dict) else None
        if codice is not None and str(codice) != CLIMA_OK:
            raise ErroreComando(
                f"{self.nome_marchio} non avvia il clima (codice {codice}): di solito l'auto è aperta, una "
                "portiera o un finestrino non è chiuso, la chiave è dentro, o sono finiti gli avvii permessi "
                "prima del prossimo giro di chiave"
            )
        if codice is None and (motivo := _motivo_rifiuto(r)):
            raise ErroreComando(f"{self.nome_marchio} non esegue «clima»: {motivo}")
