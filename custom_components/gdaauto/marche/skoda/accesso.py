"""Il login Škoda: nel browser di chi configura, poi il codice incollato.

GdAAuto non scrive la password di nessuno nelle pagine del costruttore. Il
login si fa sul sito del gruppo Volkswagen, ``identity.vwgroup.io``, nel
proprio browser:

1. GdAAuto prepara il link di ``/oidc/v1/authorize`` col client dell'app
   MyŠkoda e un PKCE suo: il ``code_verifier`` resta in GdAAuto, nel link va
   solo la ``code_challenge``;
2. chi configura entra con email e password del suo account Škoda (e accetta
   le condizioni d'uso, se il sito le chiede); alla fine il sito rimanda
   all'app, a un indirizzo ``myskoda://redirect/login/?code=…`` che il browser
   non sa aprire;
3. quell'indirizzo (o anche solo il ``code``) si incolla in GdAAuto, che lo
   scambia col ``code_verifier`` sul cloud dell'app
   (``/api/v1/authentication/exchange-authorization-code``) per l'access
   token, il refresh token e l'id token.

Poi i token si rinnovano da soli col refresh token
(``/api/v1/authentication/refresh-token``). Se il rinnovo non va più, Home
Assistant chiede di rifare il login nel browser.

Indirizzi, client id, scope, indirizzo di ritorno e forma delle richieste di
scambio e rinnovo vengono da skodaconnect/myskoda (``const.py``,
``auth/authorization.py``), licenza MIT: vedi RINGRAZIAMENTI.md. Il codice è
scritto da capo, e fa solo lo scambio e il rinnovo: niente moduli HTML.
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

#: Il cloud dell'app MyŠkoda.
BASE = "https://mysmob.api.connect.skoda-auto.cz"
#: Il login del gruppo Volkswagen.
IDENTITA = "https://identity.vwgroup.io"
#: Il client dell'app MyŠkoda per Android.
CLIENT_ID = "7f045eee-7003-4379-9968-9355ed2adb06@apps_vw-dilab_com"
#: Dove il login torna all'app: il browser si ferma lì, e l'indirizzo si incolla.
RITORNO = "myskoda://redirect/login/"
SCOPE = (
    "address badge birthdate cars driversLicense dealers email mileage mbb nationalIdentifier openid phone "
    "profession profile vin"
)

_TIMEOUT = aiohttp.ClientTimeout(total=30)
#: Un refresh token che scade fra meno di questo non si prova nemmeno.
_MARGINE_REFRESH_S = 60

#: Il testo per chi incolla la pagina delle condizioni d'uso invece dell'indirizzo finale.
CONDIZIONI = (
    "Škoda chiede di accettare le condizioni d'uso aggiornate: accettale nell'app MyŠkoda (o nella pagina del "
    "login che le mostra), poi rifai il login e incolla l'indirizzo che comincia con myskoda://"
)
#: E per chi si ferma alla pagina del consenso al marketing.
MARKETING = (
    "Il login si è fermato alla domanda sul consenso al marketing: rispondi (sì o no) nella pagina o nell'app "
    "MyŠkoda, poi rifai il login e incolla l'indirizzo che comincia con myskoda://"
)


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
    """Il link da aprire nel browser (il metodo ``s256`` scritto come lo scrive l'app)."""
    domanda = urlencode(
        {
            "client_id": CLIENT_ID,
            "nonce": nonce,
            "state": state,
            "redirect_uri": RITORNO,
            "response_type": "code",
            "scope": SCOPE,
            "code_challenge": sfida,
            "code_challenge_method": "s256",
            "prompt": "login",
        }
    )
    return f"{(identita or IDENTITA).rstrip('/')}/oidc/v1/authorize?{domanda}"


def codice_da(incollato: str) -> dict[str, str]:
    """Quello che il login consegna, dall'indirizzo finale intero o dal solo codice.

    L'indirizzo ha i valori nella domanda (``?code=…&state=…``), o a volte nel
    frammento; un testo senza ``=`` è il codice da solo.
    """
    testo = incollato.strip().strip("\"'<>").strip()
    if not testo:
        raise ErroreCredenziali("Non è stato incollato niente: serve l'indirizzo finale del login, o il codice")
    minuscolo = testo.lower()
    if "terms-and-conditions" in minuscolo:
        raise ErroreCredenziali(CONDIZIONI)
    if "consent/marketing" in minuscolo:
        raise ErroreCredenziali(MARKETING)
    if "=" not in testo:
        return {"code": testo}
    parti = urlsplit(testo) if "://" in testo else urlsplit("x://x?" + testo.lstrip("?#"))
    valori = {**dict(parse_qsl(parti.query)), **dict(parse_qsl(parti.fragment))}
    if errore := valori.get("error"):
        motivo = valori.get("error_description") or errore
        raise ErroreCredenziali(f"Il login Škoda è tornato con un errore: {motivo}")
    if not valori.get("code"):
        raise ErroreCredenziali("Nell'indirizzo incollato non c'è il codice del login (code=…)")
    return valori


def token_da(risposta: dict[str, Any], refresh_vecchio: str | None = None) -> dict[str, Any]:
    """I token come li salva GdAAuto, dalla risposta ``{"accessToken", "refreshToken", "idToken"}``."""
    access = risposta.get("accessToken") or risposta.get("access_token")
    if not access:
        raise ErroreRete("Il cloud Škoda non ha dato l'access token")
    durata = risposta.get("expiresIn") or risposta.get("expires_in")
    try:
        scade = time.time() + float(durata) if durata else float(dal_jwt(access)["exp"])
    except (KeyError, TypeError, ValueError):
        scade = time.time() + 3600
    return {
        "access_token": str(access),
        "id_token": str(risposta.get("idToken") or risposta.get("id_token") or ""),
        "refresh_token": str(risposta.get("refreshToken") or risposta.get("refresh_token") or refresh_vecchio or ""),
        "scade": scade,
    }


class TokenSkoda:
    """Lo scambio del codice col PKCE, e il rinnovo dei token. Non sa niente delle auto."""

    def __init__(self, sessione_http: aiohttp.ClientSession, *, base: str | None = None) -> None:
        self._http = sessione_http
        # Letto adesso, non nella firma: le prove cambiano gli indirizzi del modulo.
        self._base = (base or BASE).rstrip("/")

    async def _posta(self, percorso: str, corpo: dict[str, str], cosa: str) -> tuple[int, Any]:
        try:
            async with self._http.post(
                f"{self._base}/api/v1/authentication/{percorso}?tokenType=CONNECT",
                json=corpo,
                headers={"Accept": "application/json"},
                timeout=_TIMEOUT,
                allow_redirects=False,
            ) as risposta:
                http, testo = risposta.status, await risposta.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ErroreRete(f"{cosa}: il cloud Škoda non risponde: {err}") from err
        try:
            return http, json.loads(testo) if testo.strip() else None
        except ValueError:
            return http, None

    async def scambia(self, valori: dict[str, str], verifier: str) -> dict[str, Any]:
        """Il codice incollato, col ``code_verifier``, diventa i token."""
        corpo = {"code": valori["code"], "redirectUri": RITORNO, "verifier": verifier}
        http, r = await self._posta("exchange-authorization-code", corpo, "scambio del codice")
        if http == 200 and isinstance(r, dict):
            return token_da(r)
        _LOGGER.debug("Scambio del codice Škoda: HTTP %s %s", http, r)
        if http in (400, 401, 403, 404):
            raise ErroreCredenziali("Il codice del login Škoda è scaduto o è già stato usato: rifai il login")
        raise ErroreRete(f"Il cloud Škoda non ha scambiato il codice del login (HTTP {http})")

    async def rinnova(self, refresh_token: str) -> dict[str, Any]:
        """Token nuovi col refresh token; ``ErroreCredenziali`` se il cloud non lo accetta più."""
        if not refresh_token:
            raise ErroreCredenziali("Il login Škoda non ha un refresh token: rifai il login nel browser")
        scade = dal_jwt(refresh_token).get("exp")
        if isinstance(scade, int | float) and scade - _MARGINE_REFRESH_S < time.time():
            raise ErroreCredenziali("Il refresh token Škoda è scaduto: rifai il login nel browser")
        http, r = await self._posta("refresh-token", {"token": refresh_token}, "rinnovo dei token")
        if http in (400, 401, 403):
            raise ErroreCredenziali(f"Škoda non rinnova più i token (HTTP {http}): rifai il login nel browser")
        if http != 200 or not isinstance(r, dict):
            raise ErroreRete(f"Rinnovo dei token Škoda: HTTP {http}")
        return token_da(r, refresh_token)
