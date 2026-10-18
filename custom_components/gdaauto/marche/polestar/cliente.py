"""Il filo col cloud dell'app Polestar: Polestar ID e l'API GraphQL ``mystar-v2``.

Si entra come il sito di Polestar: Polestar ID (``polestarid.eu.polestar.com``)
è un server OpenID Connect. GdAAuto chiede un'autorizzazione con PKCE, riceve
la pagina del login, ci scrive l'email e la password, e dal rinvio che segue
prende il codice da scambiare con i token. Il refresh token dura e ruota: il
cliente lo conserva con la funzione ``salva`` di chi lo crea. Se il refresh
non va più, si rientra da capo con l'email e la password salvate.

I dati vengono dall'API GraphQL dell'app (``pc-api.polestar.com``):
``getConsumerCarsV2`` elenca le auto dell'account, ``carTelematicsV2`` dice
batteria, autonomia, ricarica e contachilometri. Sono letture del cloud: l'auto
non si sveglia.

Indirizzi, client id, query GraphQL e nomi dei campi vengono da
pypolestar/pypolestar (licenza MIT): vedi RINGRAZIAMENTI.md. Il codice è
riscritto da capo per ``aiohttp``.
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
import re
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import aiohttp

from ...vettura import StatoVettura, Vettura
from ..errori import ErroreApi, ErroreCredenziali, ErroreRete
from .dati import ID, interpreta, vettura_da

_LOGGER = logging.getLogger(__name__)

NOME = "Polestar"

OIDC = "https://polestarid.eu.polestar.com"
CLIENT_ID = "l3oopkc_10"
REDIRECT_URI = "https://www.polestar.com/sign-in-callback"
SCOPE = "openid profile email customer:attributes"
API = "https://pc-api.polestar.com/eu-north-1/mystar-v2/"

_TIMEOUT = aiohttp.ClientTimeout(total=30)
#: Il token si rinnova cinque minuti prima che scada, come fa l'app.
_MARGINE = timedelta(minutes=5)
_RINVII = (301, 302, 303, 307, 308)
#: La pagina del login dice dove mandare il modulo: ``action: "/as/…/resume/…"``.
_RIPRESA = re.compile(r'(?:url|action):\s*"([^"]+)"')

#: Il login usa i biscotti di Polestar ID nella sessione di tutti: due login
#: insieme (due account) si pesterebbero i piedi, quindi uno alla volta.
_SERRATURA_LOGIN = asyncio.Lock()

Q_AUTO = """
query GetConsumerCarsV2 {
    getConsumerCarsV2 {
        vin
        internalVehicleIdentifier
        registrationNo
        modelYear
        modelName
        pno34
        structureWeek
    }
}
"""

Q_TELEMATICA = """
query CarTelematicsV2($vins: [String!]!) {
    carTelematicsV2(vins: $vins) {
        battery {
            vin
            chargingStatusV2
            batteryChargeLevelPercentage
            estimatedChargingTimeToFullMinutes
            estimatedDistanceToEmptyKm
            timestamp { seconds nanos }
        }
        odometer {
            vin
            odometerMeters
            timestamp { seconds nanos }
        }
    }
}
"""


def comandi_di(_v: Vettura) -> set[str]:
    """L'API dell'app non ha comandi stabili per chi non è l'app: si legge soltanto."""
    return set()


def _b64(dati: bytes) -> str:
    return base64.urlsafe_b64encode(dati).decode().rstrip("=")


class _Rifiutato(Exception):
    """Il server dei token ha detto di no (4xx): per il refresh vuol dire «rientra»."""


class ClientePolestar:
    def __init__(
        self,
        sessione_http: aiohttp.ClientSession,
        email: str,
        password: str,
        token: dict[str, Any] | None = None,
        salva: Callable[[dict[str, Any]], None] | None = None,
        *,
        oidc: str | None = None,
        api: str | None = None,
    ) -> None:
        self._http = sessione_http
        self.email = email.strip()
        self._password = password
        self.token: dict[str, Any] = dict(token or {})
        self._salva = salva
        self.oidc = (oidc or OIDC).rstrip("/")
        self.api = api or API
        self._serratura = asyncio.Lock()
        self._config_oidc: dict[str, str] | None = None

    # --- Polestar ID ------------------------------------------------------------

    async def _configurazione(self) -> dict[str, str]:
        """Dove sono l'autorizzazione e i token: lo dice il server stesso."""
        if self._config_oidc is None:
            url = f"{self.oidc}/.well-known/openid-configuration"
            try:
                async with self._http.get(url, timeout=_TIMEOUT) as r:
                    http = r.status
                    corpo = await r.json(content_type=None) if http < 400 else {}
            except (aiohttp.ClientError, TimeoutError, OSError, ValueError) as err:
                raise ErroreRete(f"Polestar ID non risponde: {err}") from err
            if http >= 400 or not isinstance(corpo, dict):
                raise ErroreRete(f"Polestar ID non dà la sua configurazione: HTTP {http}")
            try:
                self._config_oidc = {
                    "autorizza": str(corpo["authorization_endpoint"]),
                    "token": str(corpo["token_endpoint"]),
                }
            except KeyError as err:
                raise ErroreRete("La configurazione di Polestar ID non si capisce") from err
        return self._config_oidc

    def _pulisci_biscotti(self) -> None:
        """Via i biscotti di Polestar ID dalla sessione di tutti: il prossimo login
        non deve ritrovarsi dentro l'account di un altro."""
        host = urlparse(self.oidc).hostname
        barattolo = getattr(self._http, "cookie_jar", None)
        if host and barattolo is not None:
            barattolo.clear_domain(host)

    async def _richiesta(self, metodo: str, url: str, biscotti: dict[str, str], **kw: Any) -> tuple[int, str, str]:
        """Un passo del login: stato, indirizzo del rinvio e testo. I biscotti si
        portano a mano, così valgono anche dove la sessione non li tiene."""
        intestazioni = {"Accept": "text/html,application/xhtml+xml,application/json"}
        if biscotti:
            intestazioni["Cookie"] = "; ".join(f"{k}={v}" for k, v in biscotti.items())
        try:
            async with self._http.request(
                metodo, url, headers=intestazioni, allow_redirects=False, timeout=_TIMEOUT, **kw
            ) as r:
                for nome, morsello in r.cookies.items():
                    biscotti[nome] = morsello.value
                testo = await r.text()
                return r.status, urljoin(url, r.headers.get("Location", "")), testo
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise ErroreRete(f"Il login di Polestar non risponde: {err}") from err

    async def _codice(self, verificatore: str) -> str:
        """Dall'email e dalla password al codice d'autorizzazione."""
        config = await self._configurazione()
        sfida = _b64(hashlib.sha256(verificatore.encode()).digest())
        parametri = {
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": SCOPE,
            "state": _b64(os.urandom(32)),
            "code_challenge": sfida,
            "code_challenge_method": "S256",
            "response_mode": "query",
        }
        biscotti: dict[str, str] = {}

        # 1. L'autorizzazione: la pagina del login (dopo qualche rinvio dentro Polestar ID).
        url, query = config["autorizza"], dict(parametri)
        for _ in range(5):
            http, dove, testo = await self._richiesta("GET", url, biscotti, params=query)
            if http not in _RINVII:
                break
            if codice := (parse_qs(urlparse(dove).query).get("code") or [""])[0]:
                return codice  # già dentro: non doveva succedere, ma il codice è buono
            if urlparse(dove).hostname != urlparse(url).hostname:
                # Un rinvio fuori da Polestar ID senza codice è un rifiuto (``error=…``).
                errore = (parse_qs(urlparse(dove).query).get("error") or ["rinvio inatteso"])[0]
                raise ErroreApi("login di Polestar", "autorizzazione", errore, http)
            url, query = dove, {}
        if http >= 500:
            raise ErroreRete(f"Il login di Polestar ha un guasto: HTTP {http}")
        if not (m := _RIPRESA.search(testo)):
            raise ErroreApi("login di Polestar", "modulo", "la pagina del login non ha il modulo", http)
        ripresa = urljoin(self.oidc + "/", m.group(1))

        # 2. Il modulo: email e password.
        dati = {"pf.username": self.email, "pf.pass": self._password}
        http, dove, testo = await self._richiesta("POST", ripresa, biscotti, params=parametri, data=dati)
        if http not in _RINVII:
            if "ERR001" in testo:  # authMessage: "ERR001", email o password sbagliate
                raise ErroreCredenziali("Polestar ID rifiuta l'email o la password")
            if http >= 500:
                raise ErroreRete(f"Il login di Polestar ha un guasto: HTTP {http}")
            raise ErroreApi("login di Polestar", "modulo", "il login non rinvia all'app", http)
        q = parse_qs(urlparse(dove).query)

        # 3. Condizioni nuove da accettare: il cloud manda un «uid» al posto del
        #    codice. Accettare delle condizioni è una scelta della persona, non di
        #    GdAAuto: ci si ferma e glielo si dice.
        if not q.get("code") and q.get("uid"):
            raise ErroreCredenziali(
                "Polestar ha condizioni d'uso nuove da accettare: entra una volta nell'app o su polestar.com, "
                "accettale, poi riprova"
            )
        if not q.get("code"):
            errore = (q.get("error_description") or q.get("error") or [""])[0]
            if errore:
                raise ErroreCredenziali(f"Polestar ID rifiuta l'accesso: {errore}")
            raise ErroreApi("login di Polestar", "codice", "nessun codice nel rinvio", http)
        return q["code"][0]

    async def _token(self, dati: dict[str, str]) -> dict[str, Any]:
        config = await self._configurazione()
        try:
            async with self._http.post(config["token"], data=dati, timeout=_TIMEOUT) as r:
                http = r.status
                testo = await r.text()
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise ErroreRete(f"I token di Polestar non rispondono: {err}") from err
        if http >= 500:
            raise ErroreRete(f"I token di Polestar hanno un guasto: HTTP {http}")
        try:
            corpo = json.loads(testo) if testo else {}
        except ValueError as err:
            if http >= 400:
                raise _Rifiutato(f"HTTP {http}") from err
            raise ErroreRete("I token di Polestar non si capiscono") from err
        if http >= 400 or not isinstance(corpo, dict) or "access_token" not in corpo:
            descrizione = corpo.get("error_description") or corpo.get("error") if isinstance(corpo, dict) else ""
            raise _Rifiutato(str(descrizione or f"HTTP {http}"))
        scade = datetime.now(UTC) + timedelta(seconds=int(corpo.get("expires_in") or 3600))
        return {
            "access_token": corpo["access_token"],
            "refresh_token": corpo.get("refresh_token") or self.token.get("refresh_token"),
            "scade": scade.isoformat(),
        }

    async def _login(self) -> None:
        """Da capo, con l'email e la password."""
        async with _SERRATURA_LOGIN:
            self._pulisci_biscotti()
            try:
                verificatore = _b64(os.urandom(32))
                codice = await self._codice(verificatore)
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
                    raise ErroreApi("login di Polestar", "token", str(err)) from err
            finally:
                self._pulisci_biscotti()
        self._conserva()

    async def _rinnova(self) -> None:
        """Col refresh token; se non vale più, si rientra con la password."""
        if self.token.get("refresh_token"):
            try:
                self.token = await self._token(
                    {
                        "grant_type": "refresh_token",
                        "client_id": CLIENT_ID,
                        "refresh_token": self.token["refresh_token"],
                    }
                )
            except _Rifiutato as err:
                _LOGGER.debug("Il refresh token di Polestar non vale più (%s): si rientra", err)
            else:
                self._conserva()
                return
        await self._login()

    def _conserva(self) -> None:
        if self._salva:
            self._salva(dict(self.token))

    async def _token_valido(self) -> str:
        async with self._serratura:
            scade = self.token.get("scade")
            if not self.token.get("access_token"):
                await self._login()
            elif not scade or datetime.fromisoformat(scade) - _MARGINE < datetime.now(UTC):
                await self._rinnova()
            return str(self.token["access_token"])

    # --- GraphQL ------------------------------------------------------------------

    async def _chiedi(
        self, cosa: str, operazione: str, query: str, variabili: dict[str, Any], ripeti: bool = True
    ) -> dict[str, Any]:
        intestazioni = {
            "Authorization": f"Bearer {await self._token_valido()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        corpo_richiesta = {"operationName": operazione, "query": query, "variables": variabili}
        try:
            async with self._http.post(self.api, json=corpo_richiesta, headers=intestazioni, timeout=_TIMEOUT) as r:
                http = r.status
                testo = await r.text()
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise ErroreRete(f"{cosa}: Polestar non risponde: {err}") from err
        if http >= 500:
            raise ErroreRete(f"{cosa}: Polestar ha un guasto: HTTP {http}")
        try:
            corpo = json.loads(testo) if testo else {}
        except ValueError:
            corpo = {"errors": [{"message": testo[:200]}]}
        if not isinstance(corpo, dict):
            raise ErroreRete(f"{cosa}: la risposta di Polestar non si capisce")
        errori = [e for e in corpo.get("errors") or [] if isinstance(e, dict)]
        if http == 401 or any(_non_autenticato(e) for e in errori):
            if not ripeti:
                raise ErroreCredenziali(f"{cosa}: Polestar rifiuta il token")
            # Un token revocato prima del tempo: si rinnova (o si rientra) e si riprova una volta.
            async with self._serratura:
                await self._rinnova()
            return await self._chiedi(cosa, operazione, query, variabili, ripeti=False)
        dati = corpo.get("data")
        if http >= 400 or (errori and not isinstance(dati, dict)):
            primo = errori[0] if errori else {}
            codice = (primo.get("extensions") or {}).get("code") or primo.get("errorType")
            raise ErroreApi(cosa, codice, str(primo.get("message") or testo[:200]), http)
        if errori:
            _LOGGER.debug("%s: Polestar risponde con degli avvisi: %s", cosa, errori)
        return dati if isinstance(dati, dict) else {}

    # --- leggere --------------------------------------------------------------------

    async def accedi(self) -> None:
        await self._token_valido()

    async def vetture(self) -> list[Vettura]:
        dati = await self._chiedi("elenco delle auto", "GetConsumerCarsV2", Q_AUTO, {})
        elenco = dati.get("getConsumerCarsV2") or []
        if isinstance(elenco, dict):
            elenco = [elenco]
        return [v for voce in elenco if (v := vettura_da(voce))]

    async def stato(self, v: Vettura) -> StatoVettura:
        dati = await self._chiedi("stato dell'auto", "CarTelematicsV2", Q_TELEMATICA, {"vins": [v.vin]})
        return interpreta(dati, v.vin)


def _non_autenticato(errore: dict[str, Any]) -> bool:
    codice = str((errore.get("extensions") or {}).get("code") or errore.get("errorType") or "").upper()
    return codice in ("UNAUTHENTICATED", "UNAUTHORIZED", "UNAUTHORIZEDEXCEPTION")


__all__ = ["API", "CLIENT_ID", "ID", "NOME", "OIDC", "REDIRECT_URI", "ClientePolestar", "comandi_di"]
