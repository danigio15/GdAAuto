"""Le tre operazioni RSA dell'app Mazda 6e.

1. **Credenziali e PIN cifrati.** Email, password e PIN dei comandi non viaggiano
   in chiaro: si cifrano con la chiave pubblica RSA a 2048 bit del cloud
   (PKCS#1 v1.5, a pezzi di 245 byte, in base64 con gli a capo come
   ``Base64.DEFAULT`` di Android). La chiave pubblica del cloud GdAAuto non la
   contiene: la scarica (vedi ``__init__.py``).
2. **La chiave dei comandi.** Al login l'app manda una sua chiave pubblica RSA
   (``pubKey``); il cloud la usa per cifrare il «numero di serie» monouso di
   ogni comando. GdAAuto ne genera una sua a ogni login e tiene la privata.
3. **La firma dei comandi.** Ogni comando è firmato (RSA PKCS#1 v1.5, SHA-256)
   sui suoi campi in ordine alfabetico, ``chiave=valore`` uniti da ``&``, con i
   booleani in minuscolo; ``sign`` e i campi indicati restano fuori.

Il procedimento è riscritto dai fatti di fano0001/home-assistant-mazda-6e
(Apache 2.0, ``credential_crypto.py``): vedi RINGRAZIAMENTI.md.
"""

from __future__ import annotations

import base64
from typing import Any

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from ..errori import ErroreCertificato

#: quanti byte di testo stanno in un blocco RSA 2048 con PKCS#1 v1.5
_PEZZO = 245


def _der(testo: str) -> bytes:
    return base64.b64decode("".join(testo.split()), validate=True)


def carica_pubblica(chiave_b64: str) -> rsa.RSAPublicKey:
    """La chiave pubblica del cloud, in base64 (DER SubjectPublicKeyInfo)."""
    try:
        chiave = serialization.load_der_public_key(_der(chiave_b64))
    except (ValueError, TypeError, UnsupportedAlgorithm) as err:
        raise ErroreCertificato(f"La chiave pubblica del cloud Mazda non si apre: {err}") from err
    if not isinstance(chiave, rsa.RSAPublicKey) or chiave.key_size != 2048:
        raise ErroreCertificato("La chiave pubblica del cloud Mazda non è una RSA a 2048 bit")
    return chiave


def cifra(valore: str, chiave_b64: str) -> str:
    """Email, password o PIN come li manda l'app."""
    chiave = carica_pubblica(chiave_b64)
    testo = valore.encode("utf-8")
    cifrato = b"".join(chiave.encrypt(testo[i : i + _PEZZO], padding.PKCS1v15()) for i in range(0, len(testo), _PEZZO))
    return base64.encodebytes(cifrato).decode("ascii")


def nuova_coppia() -> tuple[str, str]:
    """(pubblica, privata) in base64 DER per firmare i comandi. Lenta: va nell'executor."""
    privata = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pubblica = privata.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    segreta = privata.private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    return base64.encodebytes(pubblica).decode("ascii"), base64.encodebytes(segreta).decode("ascii")


def _privata(chiave_b64: str) -> rsa.RSAPrivateKey:
    try:
        chiave = serialization.load_der_private_key(_der(chiave_b64), None)
    except (ValueError, TypeError, UnsupportedAlgorithm) as err:
        raise ErroreCertificato(f"La chiave dei comandi Mazda non si apre: {err}") from err
    if not isinstance(chiave, rsa.RSAPrivateKey):
        raise ErroreCertificato("La chiave dei comandi Mazda non è una chiave RSA")
    return chiave


def decifra_seriale(cifrato_b64: str, privata_b64: str) -> str:
    """Il numero di serie monouso del comando, cifrato dal cloud con la nostra chiave."""
    try:
        return _privata(privata_b64).decrypt(_der(cifrato_b64), padding.PKCS1v15()).decode()
    except (ValueError, TypeError, UnicodeError) as err:
        raise ErroreCertificato("Il numero di serie del comando non si decifra con la chiave di GdAAuto") from err


def testo_da_firmare(campi: dict[str, Any], salta: frozenset[str] = frozenset()) -> str:
    def valore(v: Any) -> str:
        return str(v).lower() if isinstance(v, bool) else str(v)

    return "&".join(f"{k}={valore(v)}" for k, v in sorted(campi.items()) if k != "sign" and k not in salta)


def firma(campi: dict[str, Any], privata_b64: str, salta: frozenset[str] = frozenset()) -> str:
    """La firma di un comando, in base64 con gli a capo come l'app."""
    testo = testo_da_firmare(campi, salta).encode()
    firmato = _privata(privata_b64).sign(testo, padding.PKCS1v15(), hashes.SHA256())
    return base64.encodebytes(firmato).decode("ascii")
