"""Il filo con Enode, il servizio che porta le Xpeng europee fuori dall'app.

Xpeng non apre un'API ai proprietari, e la sua app si legge solo con un
certificato estratto dall'app stessa e un login con captcha: GdAAuto non lo
fa. Le Xpeng si leggono invece da **Enode** (enode.com), un servizio che ha un
accordo con le case e un'API pubblica e documentata:

- chi configura ha un'applicazione Enode sua (client ID e client secret, con
  l'accesso «production» chiesto a Enode);
- ``POST https://oauth.production.enode.io/oauth2/token`` (form,
  ``grant_type=client_credentials``, autenticazione HTTP Basic) dà un token
  di un'ora: niente refresh token, se ne chiede un altro;
- ``POST /users/{utente}/link`` apre una sessione di collegamento: un link
  dove la persona, nel suo browser, entra **sul sito di Enode** con l'account
  Xpeng e autorizza l'auto. GdAAuto non vede la password Xpeng;
- ``GET /vehicles`` (a pagine) elenca le auto collegate, ``GET /vehicles/{id}``
  ne dà lo stato: Enode lo tiene aggiornato da solo, leggerlo non sveglia l'auto;
- ``POST /vehicles/{id}/charging`` con ``{"action": "START"|"STOP"}`` avvia o
  ferma la ricarica e dà un'«azione»; ``GET /vehicles/actions/{id}`` dice come
  è andata (``PENDING``, ``CONFIRMED``, ``FAILED``, ``CANCELLED``).

Gli errori di Enode sono JSON «problem» (``title``, ``detail``).

Indirizzi e flusso vengono da mnordseth/xpeng-homeassistant (licenza MIT) e
dalla documentazione pubblica di Enode; la sessione di collegamento, i
permessi e le azioni anche da decline27/Xpeng (GPL: solo i fatti, il codice è
scritto da capo). Vedi RINGRAZIAMENTI.md. Questo modulo non importa Home Assistant.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

import aiohttp

from ...vettura import StatoVettura, Vettura
from ..errori import ErroreApi, ErroreComando, ErroreCredenziali, ErroreRete
from .dati import ID, NOME, capace, interpreta, vettura_da

OAUTH = "https://oauth.production.enode.io"
API = "https://enode-api.production.enode.io"
#: dove torna il browser alla fine del collegamento: una pagina qualunque, l'indirizzo si incolla
RITORNO = "https://www.home-assistant.io/"
#: leggere i dati e la posizione, e comandare la ricarica
PERMESSI = ("vehicle:read:data", "vehicle:read:location", "vehicle:control:charging")

_TIMEOUT = aiohttp.ClientTimeout(total=30)
_MARGINE_TOKEN = 120
_PAGINA = 50
_MAX_PAGINE = 20
#: ogni quanto si chiede l'esito di un comando, e fino a quando
_ESITO_OGNI_S = 3.0
_ESITO_ENTRO_S = 120.0


def comandi_di(v: Vettura) -> set[str]:
    return {"ricarica"} if v.extra.get("ricarica") else set()


def utente_di(client_id: str) -> str:
    """L'utente Enode di GdAAuto per quell'applicazione: sempre lo stesso, così ricollegare non raddoppia."""
    return "gdaauto-" + hashlib.sha256(client_id.strip().encode()).hexdigest()[:16]


def ritorno_con(stato: str, ritorno: str = RITORNO) -> str:
    return f"{ritorno}?gdaauto={stato}"


def leggi_ritorno(incollato: str) -> tuple[str, str]:
    """(state, errore) dall'indirizzo finale incollato; una parola qualunque («fatto») va bene."""
    testo = incollato.strip()
    if "?" not in testo and "#" not in testo:
        return "", ""
    parti = urlsplit(testo)
    q = parse_qs(parti.query or parti.fragment)
    errore = (q.get("error_description") or q.get("error") or [""])[0]
    return (q.get("gdaauto") or [""])[0], errore


def _messaggio(corpo: Any, status: int) -> str:
    if isinstance(corpo, dict):
        for chiave in ("detail", "title", "error_description", "error", "message"):
            if corpo.get(chiave):
                return str(corpo[chiave])
    return f"HTTP {status}"


class ClienteXpeng:
    """Un'applicazione Enode e le auto collegate."""

    def __init__(
        self,
        sessione_http: aiohttp.ClientSession,
        client_id: str,
        segreto: str,
        *,
        oauth: str = OAUTH,
        api: str = API,
    ) -> None:
        self._http = sessione_http
        self._client_id = client_id.strip()
        self._segreto = segreto.strip()
        self._oauth = oauth.rstrip("/")
        self._api = api.rstrip("/")
        self._token: str | None = None
        self._scade = 0.0
        self._serratura = asyncio.Lock()
        self._comando = asyncio.Lock()

    # --- il token ------------------------------------------------------------

    async def _token_valido(self, *, nuovo: bool = False) -> str:
        async with self._serratura:
            if not nuovo and self._token and time.time() < self._scade - _MARGINE_TOKEN:
                return self._token
            try:
                async with self._http.post(
                    f"{self._oauth}/oauth2/token",
                    data={"grant_type": "client_credentials"},
                    auth=aiohttp.BasicAuth(self._client_id, self._segreto),
                    headers={"Accept": "application/json"},
                    timeout=_TIMEOUT,
                ) as r:
                    status = r.status
                    try:
                        corpo = await r.json(content_type=None)
                    except ValueError:
                        corpo = None
            except (aiohttp.ClientError, TimeoutError, OSError) as err:
                raise ErroreRete(f"token: Enode non risponde: {err}") from err
            if status == 429 or status >= 500:
                raise ErroreRete(f"token: Enode ha un guasto (HTTP {status})")
            if status >= 400:
                raise ErroreCredenziali(f"Enode rifiuta il client ID o il secret: {_messaggio(corpo, status)}")
            if not isinstance(corpo, dict) or not corpo.get("access_token"):
                raise ErroreRete("token: Enode risponde una cosa che non si capisce")
            try:
                durata = float(corpo.get("expires_in") or 3600)
            except (TypeError, ValueError):
                durata = 3600.0
            self._token = str(corpo["access_token"])
            self._scade = time.time() + durata
            return self._token

    async def accedi(self) -> None:
        await self._token_valido()

    # --- la rete -------------------------------------------------------------

    async def _richiesta(
        self, cosa: str, metodo: str, percorso: str, *, json: Any = None, params: dict[str, Any] | None = None
    ) -> tuple[int, Any]:
        for tentativo in (1, 2):
            token = await self._token_valido(nuovo=tentativo == 2)
            try:
                async with self._http.request(
                    metodo,
                    f"{self._api}{percorso}",
                    json=json,
                    params=params,
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                    timeout=_TIMEOUT,
                ) as r:
                    status = r.status
                    try:
                        corpo = await r.json(content_type=None)
                    except ValueError:
                        corpo = None
            except (aiohttp.ClientError, TimeoutError, OSError) as err:
                raise ErroreRete(f"{cosa}: Enode non risponde: {err}") from err
            if status == 401 and tentativo == 1:
                continue
            break
        if status == 401:
            raise ErroreCredenziali(f"{cosa}: Enode rifiuta anche un token nuovo")
        if status == 429 or status >= 500:
            raise ErroreRete(f"{cosa}: Enode ha un guasto o chiede di rallentare (HTTP {status})")
        if status >= 400:
            raise ErroreApi(cosa, status, _messaggio(corpo, status), status)
        return status, corpo

    # --- il collegamento -------------------------------------------------------------

    async def collega(self, utente: str, ritorno: str) -> str:
        """Una sessione di collegamento: il link dove la persona entra con l'account Xpeng."""
        _status, corpo = await self._richiesta(
            "collegamento",
            "POST",
            f"/users/{utente}/link",
            json={"vendorType": "vehicle", "scopes": list(PERMESSI), "language": "en-US", "redirectUri": ritorno},
        )
        link = corpo.get("linkUrl") if isinstance(corpo, dict) else None
        if not link:
            raise ErroreRete("collegamento: Enode non ha dato il link")
        return str(link)

    # --- le auto ---------------------------------------------------------------------

    async def vetture(self) -> list[Vettura]:
        auto: list[Vettura] = []
        dopo: str | None = None
        for _ in range(_MAX_PAGINE):
            parametri: dict[str, Any] = {"pageSize": _PAGINA}
            if dopo:
                parametri["after"] = dopo
            _status, corpo = await self._richiesta("elenco delle auto", "GET", "/vehicles", params=parametri)
            if not isinstance(corpo, dict) or not isinstance(corpo.get("data"), list):
                raise ErroreRete("elenco delle auto: Enode risponde una cosa che non si capisce")
            auto.extend(v for d in corpo["data"] if isinstance(d, dict) and (v := vettura_da(d)) is not None)
            dopo = (corpo.get("pagination") or {}).get("after")
            if not dopo:
                break
        return auto

    async def _veicolo(self, v: Vettura) -> dict[str, Any]:
        _status, corpo = await self._richiesta("stato dell'auto", "GET", f"/vehicles/{v.extra['id']}")
        if isinstance(corpo, dict) and isinstance(corpo.get("data"), dict) and "chargeState" not in corpo:
            corpo = corpo["data"]
        if not isinstance(corpo, dict):
            raise ErroreRete("stato dell'auto: Enode risponde una cosa che non si capisce")
        return corpo

    async def stato(self, v: Vettura) -> StatoVettura:
        veicolo = await self._veicolo(v)
        # I permessi possono cambiare: la ricarica si comanda se Enode lo dice adesso.
        v.extra["ricarica"] = capace(veicolo, "startCharging") or capace(veicolo, "stopCharging")
        return interpreta(veicolo)

    # --- i comandi -------------------------------------------------------------------

    async def ricarica(self, v: Vettura, avvia: bool, pin: str | None = None) -> None:
        azione = "START" if avvia else "STOP"
        async with self._comando:
            try:
                _status, corpo = await self._richiesta(
                    "ricarica", "POST", f"/vehicles/{v.extra['id']}/charging", json={"action": azione}
                )
            except ErroreApi as err:
                raise ErroreComando(f"Enode rifiuta {azione}: {err.messaggio}") from err
            id_azione = str((corpo or {}).get("id") or "") if isinstance(corpo, dict) else ""
            if not id_azione:
                return
            await self._aspetta(id_azione, azione, str(corpo.get("state") or ""))

    async def _aspetta(self, id_azione: str, azione: str, stato: str) -> None:
        fine = time.monotonic() + _ESITO_ENTRO_S
        motivo = ""
        while True:
            if stato == "CONFIRMED":
                return
            if stato in ("FAILED", "CANCELLED"):
                raise ErroreComando(f"L'auto non ha eseguito {azione}: {motivo or stato.lower()}")
            if time.monotonic() >= fine:
                raise ErroreComando(f"L'auto non ha confermato {azione} in {_ESITO_ENTRO_S:.0f} secondi")
            await asyncio.sleep(_ESITO_OGNI_S)
            _s, corpo = await self._richiesta("esito del comando", "GET", f"/vehicles/actions/{id_azione}")
            if isinstance(corpo, dict) and isinstance(corpo.get("data"), dict):
                corpo = corpo["data"]
            corpo = corpo if isinstance(corpo, dict) else {}
            stato = str(corpo.get("state") or "")
            ragione = corpo.get("failureReason")
            if isinstance(ragione, dict):
                motivo = str(ragione.get("detail") or ragione.get("type") or "")
            elif ragione:
                motivo = str(ragione)


__all__ = ["API", "ID", "NOME", "OAUTH", "ClienteXpeng", "comandi_di"]
