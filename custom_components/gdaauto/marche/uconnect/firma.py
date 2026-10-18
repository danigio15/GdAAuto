"""La firma AWS «Signature Version 4», fatta a mano con hmac e hashlib.

I canali Uconnect stanno dietro un API Gateway di Amazon: ogni richiesta va
firmata con le credenziali temporanee che Cognito consegna dopo il login.
La firma è una catena di HMAC-SHA256 su una «richiesta canonica» (metodo,
percorso, query ordinata, intestazioni in minuscolo e ordinate, hash del
corpo). Si firmano ``host``, ``content-length`` quando c'è un corpo, e tutte
le intestazioni che mandiamo noi; si lasciano fuori quelle che i proxy
toccano (``user-agent``, ``authorization``…), come fa l'SDK di Amazon.

Le prove usano i valori che evcc (``vehicle/fiat/sigv4_test.go``, licenza
MIT) ha registrato dall'SDK ufficiale per le stesse richieste.
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import hmac
from urllib.parse import quote, unquote_plus, urlsplit

ALGORITMO = "AWS4-HMAC-SHA256"
SERVIZIO = "execute-api"

#: Le intestazioni che non si firmano mai.
_NON_FIRMATE = {"authorization", "user-agent", "x-amzn-trace-id", "expect", "transfer-encoding", "content-length"}


def _hmac(chiave: bytes, testo: str) -> bytes:
    return hmac.new(chiave, testo.encode(), hashlib.sha256).digest()


def hash_corpo(corpo: bytes | None) -> str:
    return hashlib.sha256(corpo or b"").hexdigest()


def _codifica(testo: str) -> str:
    return quote(testo, safe="-_.~")


def query_canonica(query: str) -> str:
    """La query ordinata per chiave e valore, codificata alla RFC 3986."""
    coppie = []
    for pezzo in query.split("&"):
        if not pezzo:
            continue
        chiave, _, valore = pezzo.partition("=")
        coppie.append((_codifica(unquote_plus(chiave)), _codifica(unquote_plus(valore))))
    return "&".join(f"{k}={v}" for k, v in sorted(coppie))


def host_di(url: str) -> str:
    """Il valore dell'intestazione Host: con la porta solo se non è quella solita."""
    parti = urlsplit(url)
    host = parti.hostname or ""
    if parti.port and not (
        (parti.scheme == "https" and parti.port == 443) or (parti.scheme == "http" and parti.port == 80)
    ):
        host = f"{host}:{parti.port}"
    return host


def richiesta_canonica(
    metodo: str, url: str, intestazioni: dict[str, str], corpo_hash: str, lunghezza: int
) -> tuple[str, str]:
    """La richiesta canonica e l'elenco delle intestazioni firmate."""
    parti = urlsplit(url)
    firmate: dict[str, str] = {"host": host_di(url)}
    if lunghezza > 0:
        firmate["content-length"] = str(lunghezza)
    for nome, valore in intestazioni.items():
        basso = nome.lower()
        if basso in _NON_FIRMATE or basso == "host":
            continue
        firmate[basso] = " ".join(str(valore).split())
    nomi = sorted(firmate)
    intestazioni_canoniche = "".join(f"{n}:{firmate[n]}\n" for n in nomi)
    elenco = ";".join(nomi)
    canonica = "\n".join(
        [metodo.upper(), parti.path or "/", query_canonica(parti.query), intestazioni_canoniche, elenco, corpo_hash]
    )
    return canonica, elenco


def firma(
    metodo: str,
    url: str,
    intestazioni: dict[str, str],
    corpo: bytes | None,
    *,
    chiave_accesso: str,
    segreto: str,
    token_sessione: str | None,
    regione: str,
    servizio: str = SERVIZIO,
    ora: datetime | None = None,
) -> dict[str, str]:
    """Le intestazioni da mandare: quelle date più ``X-Amz-Date``,
    ``X-Amz-Security-Token`` e ``Authorization``."""
    ora = (ora or datetime.now(UTC)).astimezone(UTC)
    data_amz = ora.strftime("%Y%m%dT%H%M%SZ")
    giorno = data_amz[:8]
    risultato = dict(intestazioni)
    risultato["X-Amz-Date"] = data_amz
    if token_sessione:
        risultato["X-Amz-Security-Token"] = token_sessione
    canonica, elenco = richiesta_canonica(metodo, url, risultato, hash_corpo(corpo), len(corpo or b""))
    ambito = f"{giorno}/{regione}/{servizio}/aws4_request"
    da_firmare = "\n".join([ALGORITMO, data_amz, ambito, hashlib.sha256(canonica.encode()).hexdigest()])
    chiave = ("AWS4" + segreto).encode()
    for pezzo in (giorno, regione, servizio, "aws4_request"):
        chiave = _hmac(chiave, pezzo)
    firma_hex = hmac.new(chiave, da_firmare.encode(), hashlib.sha256).hexdigest()
    risultato["Authorization"] = (
        f"{ALGORITMO} Credential={chiave_accesso}/{ambito}, SignedHeaders={elenco}, Signature={firma_hex}"
    )
    return risultato
