"""Come si entra nel cloud Audi: l'identità del gruppo Volkswagen (IDK di CARIAD).

L'app myAudi è un client OpenID Connect pubblico (PKCE) di
``identity.vwgroup.io``; i token li rilascia il server IDK di CARIAD
(``<regione>.bff.cariad.digital``), lo stesso che poi risponde sulle auto. Il
documento ``openid-configuration`` dice dove stanno gli indirizzi che servono;
se non risponde si usano quelli noti.

GdAAuto non vede mai email e password. Si entra in uno di due modi, e tutti e
due danno lo stesso refresh token, che dura e ruota a ogni rinnovo:

- **dal browser** (quello che usa la configurazione): GdAAuto prepara
  l'indirizzo di login dell'app con PKCE, chi configura entra nel suo browser,
  e alla fine incolla l'indirizzo ``myaudi:///?code=…`` (o solo il codice).
  GdAAuto scambia il codice coi token, e poi li rinnova da sola;
- **col codice del dispositivo** (RFC 8628): chi configura conferma un codice
  sul sito di Audi. È pronto qui sotto perché audi_connect_ha (ottobre 2026)
  dice che in Europa e in Canada Audi vuole la verifica dell'app (Play
  Integrity) proprio sullo scambio del codice di login, e non su questo.

Client id, redirect, scope e la chiave dell'intestazione ``X-QMAuth`` vengono
da audiconnect/audi_connect_ha e da acfischer42/CarConnectivity-connector-audi
(licenza MIT): vedi RINGRAZIAMENTI.md. Il codice è riscritto da capo per
``aiohttp``.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import aiohttp

from ..base import InAttesa
from ..errori import CodiceSbagliato, ErroreApi, ErroreCredenziali, ErroreRete

_LOGGER = logging.getLogger(__name__)

#: Il client pubblico (PKCE) dell'app myAudi per Android, e dove rimanda il login.
CLIENT_ID = "09b6cbec-cd19-4589-82fd-363dfa8c24da@apps_vw-dilab_com"
REDIRECT_URI = "myaudi:///"
#: Lo scope del login dell'app (audi_connect_ha) e quello del codice del dispositivo.
SCOPE_LOGIN = (
    "address profile badge birthdate birthplace nationalIdentifier nationality profession email vin phone "
    "nickname name picture mbb gallery openid"
)
SCOPE_DISPOSITIVO = "openid mbb profile badge cars dealers vin"

VERSIONE_APP = "4.31.0"
USER_AGENT = f"Android/{VERSIONE_APP} (Build 800341641.root project 'myaudi_android'.ext.buildTime) Android/13"

IDENTITA = "https://identity.vwgroup.io"


@dataclass(frozen=True)
class Regione:
    """Dove vive l'account: il BFF di CARIAD e la lingua della pagina di login."""

    nome: str
    bff: str
    lingua: str


#: Europa e Canada passano da ``emea`` (il Canada dal 2026), gli Stati Uniti da ``na``.
REGIONI: dict[str, Regione] = {
    "europa": Regione("Europa", "https://emea.bff.cariad.digital", "it-IT it"),
    "canada": Regione("Canada", "https://emea.bff.cariad.digital", "en-CA en"),
    "usa": Regione("USA", "https://na.bff.cariad.digital", "en-US en"),
}

_TIMEOUT = aiohttp.ClientTimeout(total=30)
#: Il rinnovo si fa cinque minuti prima che il token scada.
MARGINE = timedelta(minutes=5)

#: La chiave HMAC dell'intestazione ``X-QMAuth`` che l'app manda allo scambio del
#: codice di login (da audi_connect_ha, MIT): cambia con l'ora, ogni 100 secondi.
_CHIAVE_QMAUTH = bytes(
    (
        *(26, 182, 153, 37, 172, 23, 154, 170, 78, 131, 171, 230, 113, 169, 71, 109),
        *(23, 100, 24, 184, 91, 215, 6, 241, 67, 108, 161, 91, 230, 71, 152, 156),
    )
)


def x_qmauth(ora: float | None = None) -> str:
    """L'intestazione ``X-QMAuth`` dell'app: un HMAC-SHA256 delle centinaia di secondi da 1970."""
    passo = int((time.time() if ora is None else ora) / 100)
    firma = hmac.new(_CHIAVE_QMAUTH, str(passo).encode("ascii"), hashlib.sha256).hexdigest()
    return f"v1:01da27b0:{firma}"


def _b64(dati: bytes) -> str:
    return base64.urlsafe_b64encode(dati).decode().rstrip("=")


def nuovo_pkce() -> tuple[str, str]:
    """Il verifier PKCE e la sua sfida S256."""
    verifier = _b64(os.urandom(48))
    return verifier, _b64(hashlib.sha256(verifier.encode("ascii")).digest())


def chi_e(token: dict[str, Any]) -> dict[str, Any]:
    """Il corpo dell'``id_token`` (email e ``sub``), senza verificarne la firma: serve a riconoscere l'account."""
    id_token = str(token.get("id_token") or "")
    try:
        parte = id_token.split(".")[1]
        corpo = json.loads(base64.urlsafe_b64decode(parte + "=" * (-len(parte) % 4)))
    except (IndexError, ValueError):
        return {}
    return corpo if isinstance(corpo, dict) else {}


def token_da(risposta: dict[str, Any], vecchio: dict[str, Any] | None = None) -> dict[str, Any]:
    """I token da salvare: quelli nuovi, col refresh e l'id token di prima se il server non li ripete."""
    vecchio = vecchio or {}
    try:
        secondi = int(risposta.get("expires_in") or 3600)
    except (TypeError, ValueError):
        secondi = 3600
    return {
        "access_token": str(risposta["access_token"]),
        "refresh_token": str(risposta.get("refresh_token") or vecchio.get("refresh_token") or ""),
        "id_token": str(risposta.get("id_token") or vecchio.get("id_token") or ""),
        "scade": (datetime.now(UTC) + timedelta(seconds=secondi)).isoformat(),
    }


def scaduto(token: dict[str, Any], ora: datetime | None = None) -> bool:
    """Il token è scaduto, o lo sarà fra meno di cinque minuti (o non si sa quando scade)."""
    if not token.get("access_token"):
        return True
    try:
        scade = datetime.fromisoformat(str(token.get("scade")))
    except ValueError:
        return True
    if scade.tzinfo is None:
        scade = scade.replace(tzinfo=UTC)
    return (ora or datetime.now(UTC)) >= scade - MARGINE


def leggi_incollato(testo: str) -> tuple[str, str | None]:
    """Il codice e lo ``state`` da quello che si incolla: ``myaudi:///?code=…&state=…``, o il solo codice."""
    testo = testo.strip().strip("\"'<>")
    if not testo:
        raise CodiceSbagliato("Non hai incollato niente: incolla l'indirizzo finale o il codice")
    if "code=" in testo or "error=" in testo:
        parti = urlparse(testo)
        pezzi = [parti.query, parti.fragment, parti.path.lstrip("/?"), testo.split("?", 1)[-1]]
        for pezzo in pezzi:
            q = parse_qs(pezzo)
            if q.get("code"):
                return q["code"][0], (q.get("state") or [None])[0]
            if q.get("error"):
                motivo = (q.get("error_description") or q["error"])[0]
                raise CodiceSbagliato(f"Il login Audi è tornato con un errore: {motivo}")
        raise CodiceSbagliato("Nell'indirizzo incollato non c'è il codice")
    if " " in testo or "/" in testo:
        raise CodiceSbagliato("Questo non sembra né l'indirizzo finale né il codice")
    return testo, None


class Identita:
    """I token dell'app myAudi: il codice di login, il codice del dispositivo, il rinnovo."""

    def __init__(self, http: aiohttp.ClientSession, bff: str) -> None:
        self._http = http
        self._bff = bff.rstrip("/")
        self._indirizzi: dict[str, str] | None = None

    async def _richiesta(
        self, metodo: str, url: str, cosa: str, *, intestazioni: dict[str, str] | None = None, **altro: Any
    ) -> tuple[int, str]:
        base = {"Accept": "application/json", "Accept-Charset": "utf-8", "User-Agent": USER_AGENT}
        try:
            async with self._http.request(
                metodo, url, headers={**base, **(intestazioni or {})}, timeout=_TIMEOUT, **altro
            ) as risposta:
                return risposta.status, await risposta.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ErroreRete(f"{cosa}: il login Audi non risponde: {err}") from err

    async def _token(self, corpo: dict[str, str], cosa: str, intestazioni: dict[str, str] | None = None) -> dict:
        """Una chiamata al server dei token: la risposta JSON, qualunque sia l'esito (lo giudica chi chiama)."""
        url = (await self.indirizzi())["token"]
        http, testo = await self._richiesta(
            "POST",
            url,
            cosa,
            intestazioni={"Content-Type": "application/x-www-form-urlencoded", **(intestazioni or {})},
            data=corpo,
            allow_redirects=False,
        )
        if http >= 500:
            raise ErroreRete(f"{cosa}: il server dei token Audi ha un guasto (HTTP {http})")
        try:
            r = json.loads(testo)
        except ValueError as err:
            raise ErroreRete(f"{cosa}: risposta che non si capisce (HTTP {http})") from err
        if not isinstance(r, dict):
            raise ErroreRete(f"{cosa}: risposta che non si capisce (HTTP {http})")
        r["_http"] = http
        return r

    async def indirizzi(self) -> dict[str, str]:
        """Autorizzazione, codice del dispositivo e token, dall'``openid-configuration`` del BFF."""
        if self._indirizzi is not None:
            return self._indirizzi
        noti = {
            "autorizza": f"{IDENTITA}/oidc/v1/authorize",
            "dispositivo": f"{IDENTITA}/oidc/v1/device_authorization",
            "token": f"{self._bff}/auth/v1/idk/oidc/token",
        }
        try:
            http, testo = await self._richiesta(
                "GET", f"{self._bff}/auth/v1/idk/oidc/openid-configuration", "configurazione del login"
            )
            documento = json.loads(testo) if http == 200 else {}
        except (ErroreRete, ValueError):
            documento = {}
        if isinstance(documento, dict):
            for chiave, campo in (
                ("autorizza", "authorization_endpoint"),
                ("dispositivo", "device_authorization_endpoint"),
                ("token", "token_endpoint"),
            ):
                if isinstance(documento.get(campo), str) and documento[campo].startswith("http"):
                    noti[chiave] = documento[campo]
        self._indirizzi = noti
        return noti

    # --- il login dal browser -------------------------------------------------------------

    async def indirizzo_login(self, sfida: str, state: str, nonce: str, lingua: str = "it-IT it") -> str:
        """La pagina di login dell'app myAudi, da aprire nel browser di chi configura."""
        parametri = {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPE_LOGIN,
            "state": state,
            "nonce": nonce,
            "prompt": "login",
            "code_challenge": sfida,
            "code_challenge_method": "S256",
            "ui_locales": lingua,
        }
        return f"{(await self.indirizzi())['autorizza']}?{urlencode(parametri)}"

    async def scambia_codice_login(self, codice: str, verifier: str) -> dict[str, Any]:
        """Il codice incollato, coi token. Un codice vecchio o già usato è ``CodiceSbagliato``."""
        r = await self._token(
            {
                "client_id": CLIENT_ID,
                "grant_type": "authorization_code",
                "code": codice,
                "redirect_uri": REDIRECT_URI,
                "response_type": "token id_token",
                "code_verifier": verifier,
            },
            "scambio del codice di login",
            {"X-QMAuth": x_qmauth()},
        )
        if r.get("access_token"):
            return token_da(r)
        errore = str(r.get("error") or "")
        motivo = str(r.get("error_description") or errore or f"HTTP {r['_http']}")
        if "assertion" in motivo.lower() or "attest" in motivo.lower():
            raise ErroreCredenziali(
                "Audi vuole la verifica dell'app per questo login (succede in Europa e in Canada): "
                f"lo scambio del codice è rifiutato ({motivo})"
            )
        if errore in ("invalid_grant", "invalid_request") or r["_http"] in (400, 401):
            raise CodiceSbagliato(f"Audi non accetta il codice (scaduto o già usato?): {motivo}")
        raise ErroreApi("scambio del codice di login", errore, motivo, int(r["_http"]))

    # --- il codice del dispositivo ------------------------------------------------------------

    async def chiedi_codice(self) -> dict[str, Any]:
        """Il codice da confermare sul sito di Audi: ``link``, ``codice``, ``device_code``, ``intervallo``."""
        url = (await self.indirizzi())["dispositivo"]
        http, testo = await self._richiesta(
            "POST",
            url,
            "codice del dispositivo",
            intestazioni={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-App-Name": "myAudi",
                "X-App-Version": VERSIONE_APP,
            },
            data={"client_id": CLIENT_ID, "scope": SCOPE_DISPOSITIVO},
            allow_redirects=False,
        )
        if http >= 500:
            raise ErroreRete(f"Il login Audi ha un guasto (HTTP {http})")
        try:
            r = json.loads(testo)
            device_code = str(r["device_code"])
            codice = str(r["user_code"])
        except (ValueError, KeyError, TypeError) as err:
            raise ErroreRete(f"Audi non ha dato il codice del dispositivo (HTTP {http})") from err
        return {
            "link": str(r.get("verification_uri_complete") or r.get("verification_uri") or ""),
            "codice": codice,
            "device_code": device_code,
            "intervallo": int(r.get("interval") or 5),
        }

    async def scambia_codice_dispositivo(self, device_code: str) -> dict[str, Any]:
        """Un tentativo di ritirare i token: ``InAttesa`` finché il codice non è confermato."""
        r = await self._token(
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": CLIENT_ID,
                "device_code": device_code,
            },
            "ritiro dei token",
        )
        if r.get("access_token"):
            return token_da(r)
        errore = str(r.get("error") or "")
        if errore in ("authorization_pending", "slow_down"):
            raise InAttesa("Il codice non è ancora confermato sul sito di Audi")
        if errore == "expired_token":
            raise ErroreCredenziali("Il codice è scaduto: ricomincia la configurazione per averne uno nuovo")
        if errore == "access_denied":
            raise ErroreCredenziali("Il codice è stato rifiutato sul sito di Audi")
        raise ErroreApi("ritiro dei token", errore, str(r.get("error_description") or ""), int(r["_http"]))

    # --- il rinnovo ---------------------------------------------------------------------------

    async def rinnova(self, token: dict[str, Any]) -> dict[str, Any]:
        """Il refresh token per token nuovi. ``invalid_grant`` vuol dire rientrare; il resto si riprova."""
        refresh = str(token.get("refresh_token") or "")
        if not refresh:
            raise ErroreCredenziali("Manca il refresh token Audi: serve rientrare")
        r = await self._token(
            {
                "client_id": CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "response_type": "token id_token",
            },
            "rinnovo dei token",
        )
        if r.get("access_token"):
            return token_da(r, token)
        errore = str(r.get("error") or "")
        if errore == "invalid_grant":
            raise ErroreCredenziali(f"Audi rifiuta il refresh token: {r.get('error_description') or errore}")
        # invalid_client, un guasto passeggero, una risposta strana: rientrare non servirebbe (audi_connect_ha).
        raise ErroreApi("rinnovo dei token", errore, str(r.get("error_description") or ""), int(r["_http"]))


__all__ = [
    "CLIENT_ID",
    "REDIRECT_URI",
    "REGIONI",
    "USER_AGENT",
    "VERSIONE_APP",
    "Identita",
    "Regione",
    "chi_e",
    "leggi_incollato",
    "nuovo_pkce",
    "scaduto",
    "token_da",
    "x_qmauth",
]
