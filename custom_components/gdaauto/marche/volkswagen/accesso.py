"""Il login Volkswagen: nel browser di chi configura, poi il codice incollato.

GdAAuto non scrive la password di nessuno nelle pagine del costruttore. Il
login si fa sul sito del gruppo, ``identity.vwgroup.io``, nel proprio browser:

1. GdAAuto prepara il link di ``/oidc/v1/authorize`` col client dell'app
   Volkswagen (We Connect / My Volkswagen) e un PKCE suo (S256): il
   ``code_verifier`` resta in GdAAuto, nel link va solo la ``code_challenge``;
2. chi configura entra con email e password (e accetta le condizioni d'uso,
   se il sito le chiede); alla fine il sito rimanda all'app, a un indirizzo
   ``weconnect://authenticated#…`` che il browser non sa aprire;
3. quell'indirizzo (o anche solo il ``code``) si incolla in GdAAuto, che lo
   scambia col ``code_verifier`` per l'access token, l'id token e il refresh
   token, sul punto dei token del BFF di CARIAD (``/login/v1/idk/token``) o,
   se quello non lo accetta, su quello del sito di login (``/oidc/v1/token``).

Poi i token si rinnovano da soli col refresh token, sullo stesso punto. Se il
rinnovo non va più, Home Assistant chiede di rifare il login nel browser.

Indirizzi, client id, scope, indirizzo di ritorno e intestazioni dell'app
vengono da tillsteinbach/CarConnectivity-connector-volkswagen
(``auth/we_connect_session.py``, ``auth/openid_session.py``), licenza MIT:
vedi RINGRAZIAMENTI.md. Il codice è scritto da capo, asincrono, su ``aiohttp``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

import aiohttp

from ..errori import ErroreCredenziali, ErroreRete

_LOGGER = logging.getLogger(__name__)

#: Il cloud europeo dell'app (il «backend for frontend» di CARIAD).
BFF = "https://emea.bff.cariad.digital"
#: Il login del gruppo Volkswagen.
IDENTITA = "https://identity.vwgroup.io"
#: Il client dell'app We Connect / My Volkswagen.
CLIENT_ID = "a24fba63-34b3-4d43-b181-942111e6bda8@apps_vw-dilab_com"
SCOPE = "openid profile badge cars dealers vin"
#: Dove il login torna all'app: il browser si ferma lì, e l'indirizzo si incolla.
RITORNO = "weconnect://authenticated"
PACCHETTO_APP = "com.volkswagen.weconnect"
USER_AGENT = "Volkswagen/3.51.1-android/14"

#: Dove si scambiano e si rinnovano i token: nell'ordine in cui si provano.
PUNTO_BFF = "bff"
PUNTO_IDENTITA = "identita"

_TIMEOUT = aiohttp.ClientTimeout(total=30)

#: Il testo per chi incolla la pagina delle condizioni d'uso invece dell'indirizzo finale.
CONDIZIONI = (
    "Volkswagen chiede di accettare le condizioni d'uso aggiornate: accettale nell'app Volkswagen (o nella "
    "pagina del login che le mostra), poi rifai il login e incolla l'indirizzo che comincia con weconnect://"
)

#: Errori dello scambio che vogliono dire «il codice non vale»: si rifà il login.
_CODICE_NON_VALE = {"invalid_grant", "invalid_request", "access_denied", "unauthorized_client"}


def _b64url(dati: bytes) -> str:
    return base64.urlsafe_b64encode(dati).decode().rstrip("=")


def coppia_pkce() -> tuple[str, str]:
    """Il ``code_verifier`` e la sua ``code_challenge`` S256."""
    verifier = _b64url(secrets.token_bytes(48))
    return verifier, sfida_di(verifier)


def sfida_di(verifier: str) -> str:
    return _b64url(hashlib.sha256(verifier.encode()).digest())


def dal_jwt(token: str | None) -> dict[str, Any]:
    """Il contenuto di un JWT, senza verificarne la firma: serve solo a leggere ``exp``, ``sub`` ed ``email``."""
    try:
        parte = str(token).split(".")[1]
        contenuto = json.loads(base64.urlsafe_b64decode(parte + "=" * (-len(parte) % 4)))
    except (IndexError, ValueError, TypeError):
        return {}
    return contenuto if isinstance(contenuto, dict) else {}


def indirizzo_login(sfida: str, state: str, nonce: str, identita: str | None = None) -> str:
    """Il link da aprire nel browser."""
    domanda = urlencode(
        {
            "client_id": CLIENT_ID,
            "redirect_uri": RITORNO,
            "response_type": "code id_token token",
            "scope": SCOPE,
            "state": state,
            "nonce": nonce,
            "code_challenge": sfida,
            "code_challenge_method": "S256",
            "prompt": "login",
        }
    )
    return f"{(identita or IDENTITA).rstrip('/')}/oidc/v1/authorize?{domanda}"


def codice_da(incollato: str) -> dict[str, str]:
    """Quello che il login consegna, dall'indirizzo finale intero o dal solo codice.

    L'indirizzo può avere i valori nella domanda (``?code=…``) o nel frammento
    (``#state=…&code=…&id_token=…``); un testo senza ``=`` è il codice da solo.
    """
    testo = incollato.strip().strip("\"'<>").strip()
    if not testo:
        raise ErroreCredenziali("Non è stato incollato niente: serve l'indirizzo finale del login, o il codice")
    if "terms-and-conditions" in testo.lower() or "updated=dataprivacy" in testo:
        raise ErroreCredenziali(CONDIZIONI)
    if "=" not in testo:
        return {"code": testo}
    parti = urlsplit(testo) if "://" in testo else urlsplit("x://x?" + testo.lstrip("?#"))
    valori = {**dict(parse_qsl(parti.query)), **dict(parse_qsl(parti.fragment))}
    if errore := valori.get("error"):
        motivo = valori.get("error_description") or errore
        raise ErroreCredenziali(f"Il login Volkswagen è tornato con un errore: {motivo}")
    if not valori.get("code"):
        raise ErroreCredenziali("Nell'indirizzo incollato non c'è il codice del login (code=…)")
    return valori


def token_da(risposta: dict[str, Any], punto: str, refresh_vecchio: str | None = None) -> dict[str, Any]:
    """I token come li salva GdAAuto, da una risposta in ``camelCase`` o in ``snake_case``."""
    access = risposta.get("access_token") or risposta.get("accessToken")
    if not access:
        raise ErroreRete("Il cloud Volkswagen non ha dato l'access token")
    durata = risposta.get("expires_in") or risposta.get("expiresIn")
    try:
        scade = time.time() + float(durata) if durata else float(dal_jwt(access)["exp"])
    except (KeyError, TypeError, ValueError):
        scade = time.time() + 3600
    return {
        "access_token": str(access),
        "id_token": str(risposta.get("id_token") or risposta.get("idToken") or ""),
        "refresh_token": str(risposta.get("refresh_token") or risposta.get("refreshToken") or refresh_vecchio or ""),
        "scade": scade,
        "punto": punto,
    }


class TokenVolkswagen:
    """Lo scambio del codice col PKCE, e il rinnovo dei token. Non sa niente delle auto."""

    def __init__(
        self, sessione_http: aiohttp.ClientSession, *, bff: str | None = None, identita: str | None = None
    ) -> None:
        self._http = sessione_http
        # Letti adesso, non nella firma: le prove cambiano gli indirizzi del modulo.
        self._punti = {
            PUNTO_BFF: f"{(bff or BFF).rstrip('/')}/login/v1/idk/token",
            PUNTO_IDENTITA: f"{(identita or IDENTITA).rstrip('/')}/oidc/v1/token",
        }

    async def _posta(self, punto: str, modulo: dict[str, str], cosa: str) -> tuple[int, Any]:
        intestazioni = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
            "x-android-package-name": PACCHETTO_APP,
        }
        try:
            async with self._http.post(
                self._punti[punto], data=modulo, headers=intestazioni, timeout=_TIMEOUT, allow_redirects=False
            ) as risposta:
                http, testo = risposta.status, await risposta.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ErroreRete(f"{cosa}: il cloud Volkswagen non risponde: {err}") from err
        try:
            return http, json.loads(testo) if testo.strip() else None
        except ValueError:
            return http, None

    async def scambia(self, valori: dict[str, str], verifier: str) -> dict[str, Any]:
        """Il codice incollato, col ``code_verifier``, diventa i token."""
        modulo = {
            "grant_type": "authorization_code",
            "code": valori["code"],
            "code_verifier": verifier,
            "client_id": CLIENT_ID,
            "redirect_uri": RITORNO,
        }
        rifiutato = False
        for punto in (PUNTO_BFF, PUNTO_IDENTITA):
            http, r = await self._posta(punto, modulo, "scambio del codice")
            if http == 200 and isinstance(r, dict):
                return token_da(r, punto)
            errore = str(r.get("error") or "") if isinstance(r, dict) else ""
            rifiutato = rifiutato or errore in _CODICE_NON_VALE
            _LOGGER.debug("Scambio del codice Volkswagen su %s: HTTP %s %s", punto, http, errore)
        if valori.get("access_token"):
            # Il login ha dato già i token nel frammento: valgono per il BFF, ma senza
            # refresh token, e quando scadono bisogna rifare il login nel browser.
            _LOGGER.warning(
                "Il cloud Volkswagen non ha scambiato il codice: si usano i token dell'indirizzo, che non si rinnovano"
            )
            return token_da(valori, PUNTO_BFF)
        if rifiutato:
            raise ErroreCredenziali("Il codice del login Volkswagen è scaduto o è già stato usato: rifai il login")
        raise ErroreRete("Il cloud Volkswagen non ha scambiato il codice del login")

    async def rinnova(self, refresh_token: str, punto: str = PUNTO_BFF) -> dict[str, Any]:
        """Token nuovi col refresh token; ``ErroreCredenziali`` se il cloud non lo accetta più."""
        if not refresh_token:
            raise ErroreCredenziali("Il login Volkswagen non ha un refresh token: rifai il login nel browser")
        punto = punto if punto in self._punti else PUNTO_BFF
        modulo = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": CLIENT_ID}
        http, r = await self._posta(punto, modulo, "rinnovo dei token")
        if http in (400, 401, 403):
            raise ErroreCredenziali(f"Volkswagen non rinnova più i token (HTTP {http}): rifai il login nel browser")
        if http != 200 or not isinstance(r, dict):
            raise ErroreRete(f"Rinnovo dei token Volkswagen: HTTP {http}")
        return token_da(r, punto, refresh_token)
