"""Il login del gruppo Volkswagen (``identity.vwgroup.io``) fatto nel browser: i pezzi che non parlano con la rete.

GdAAuto non compila le pagine del login e non vede la password. Costruisce
l'indirizzo ``/oidc/v1/authorize`` dell'app giusta (My CUPRA o My SEAT) con una
sfida PKCE; chi configura lo apre nel suo browser ed entra come nell'app. Alla
fine il login rimanda all'indirizzo dell'app (``cupra://oauth-callback?code=…``
o ``seat://oauth-callback?code=…``): il browser non sa aprirlo, e chi
configura lo copia e lo incolla in GdAAuto, intero o solo il ``code``. Il
``code`` col ``code_verifier`` diventa i token (vedi ``cliente.py``).

I parametri del login (client, scope, ``response_type=code`` con PKCE S256)
vengono da WulfgarW/pycupra (Apache 2.0) e da
tillsteinbach/CarConnectivity-connector-seatcupra (MIT): vedi RINGRAZIAMENTI.md.
Il codice è scritto da capo, con la libreria standard.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from ..errori import CodiceSbagliato, ErroreCredenziali
from .app import IDENTITA, SCOPE, AppMarchio


def pkce() -> tuple[str, str]:
    """Il ``code_verifier`` a caso e la sua sfida S256."""
    verifier = secrets.token_urlsafe(48)
    return verifier, sfida(verifier)


def sfida(verifier: str) -> str:
    impronta = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(impronta).decode("ascii").rstrip("=")


def indirizzo_login(app: AppMarchio, sfida_: str, stato: str, nonce: str, identita: str | None = None) -> str:
    """La pagina del login del gruppo per l'app ``app``, da aprire nel browser."""
    domanda = {
        "client_id": app.client_id,
        "redirect_uri": app.redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "state": stato,
        "nonce": nonce,
        "code_challenge": sfida_,
        "code_challenge_method": "S256",
    }
    # L'indirizzo si legge adesso, non nella firma: le prove lo cambiano.
    return f"{(identita or IDENTITA).rstrip('/')}/oidc/v1/authorize?{urlencode(domanda)}"


def parametri(url: str) -> dict[str, str]:
    """I parametri di un indirizzo, dalla query e dal frammento (``#``)."""
    parti = urlparse(url)
    trovati: dict[str, str] = {}
    for pezzo in (parti.fragment, parti.query):
        for k, v in parse_qs(pezzo).items():
            if v:
                trovati.setdefault(k, v[0])
    return trovati


def leggi_incollato(incollato: str) -> tuple[str, str | None]:
    """Il ``code`` (e lo ``state``, se c'è) da quello che chi configura ha incollato.

    Va bene l'indirizzo intero (``cupra://oauth-callback?code=…&state=…``),
    un pezzo (``code=…&state=…``) o il codice da solo.
    """
    testo = incollato.strip().strip("\"'<>")
    if not testo:
        raise CodiceSbagliato("Non hai incollato niente: copia l'indirizzo finale del login, o il codice")
    if "=" in testo or "://" in testo or "?" in testo or "#" in testo:
        p = parametri(testo if "://" in testo or "?" in testo or "#" in testo else f"?{testo}")
        if errore := p.get("error"):
            motivo = p.get("error_description") or errore
            raise ErroreCredenziali(f"Il login del gruppo Volkswagen non è andato: {motivo}")
        codice = p.get("code", "").strip()
        if not codice:
            raise CodiceSbagliato("Nell'indirizzo incollato non c'è il codice («code=…»): riprova il login")
        return codice, p.get("state") or None
    if any(c.isspace() for c in testo):
        raise CodiceSbagliato(
            "Questo non sembra il codice del login: incolla l'indirizzo che inizia con cupra:// o seat://"
        )
    return testo, None


def rivendicazioni(token: str) -> dict[str, Any]:
    """Quello che c'è dentro un JWT (``sub``, ``exp``, ``email``…), senza verificarne la firma; o ``{}``."""
    try:
        corpo = token.split(".")[1]
        dati = json.loads(base64.urlsafe_b64decode(corpo + "=" * (-len(corpo) % 4)))
    except (IndexError, ValueError):
        return {}
    return dati if isinstance(dati, dict) else {}


def sub_del_jwt(token: str) -> str:
    """Il ``sub`` (l'id dell'utente del gruppo) dentro un JWT; "" se non si legge."""
    return str(rivendicazioni(token).get("sub") or "")


def scadenza_del_jwt(token: str) -> float | None:
    """Il ``exp`` di un JWT (secondi dal 1970), se c'è."""
    try:
        return float(rivendicazioni(token)["exp"])
    except (KeyError, TypeError, ValueError):
        return None
