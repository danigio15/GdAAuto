"""La chiave del telefono: quello che firma i comandi per Rivian.

Rivian accetta un comando dal cloud solo se è firmato da un «telefono»
registrato per quell'auto e **abbinato in Bluetooth** dentro l'auto (menu
«Drivers and Keys»). GdAAuto non parla Bluetooth: per comandare serve la
chiave privata di un telefono già abbinato, per esempio quella che
l'integrazione Rivian di HACS tiene nelle sue opzioni (``private_key``).

La firma, come in bretterer/rivian-python-client (MIT):

1. ECDH su P-256 fra la chiave privata del telefono e la chiave pubblica
   dell'auto (``vehiclePublicKey``, esadecimale, punto non compresso);
2. HKDF-SHA256 (32 byte, senza sale né info) del segreto comune;
3. HMAC-SHA256 di ``comando + timestamp`` con quella chiave, in esadecimale.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..errori import ErroreCertificato


def carica(testo: str) -> ec.EllipticCurvePrivateKey:
    """La chiave privata, in PEM o in base64 del PEM (come la salva l'integrazione HACS)."""
    testo = (testo or "").strip()
    if not testo:
        raise ErroreCertificato("Manca la chiave del telefono")
    if "-----BEGIN" in testo:
        grezzo = testo.encode()
    else:
        try:
            grezzo = base64.b64decode(testo, validate=False)
        except (binascii.Error, ValueError) as err:
            raise ErroreCertificato("La chiave del telefono non è né PEM né base64") from err
    try:
        chiave = serialization.load_pem_private_key(grezzo, password=None)
    except (ValueError, TypeError) as err:
        raise ErroreCertificato(f"La chiave del telefono non si apre: {err}") from err
    if not isinstance(chiave, ec.EllipticCurvePrivateKey) or not isinstance(chiave.curve, ec.SECP256R1):
        raise ErroreCertificato("La chiave del telefono deve essere una chiave EC P-256")
    return chiave


def pubblica(chiave: ec.EllipticCurvePrivateKey) -> str:
    """La chiave pubblica come la conosce Rivian: punto non compresso, in esadecimale."""
    return (
        chiave.public_key()
        .public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        .hex()
    )


def firma(comando: str, timestamp: str, chiave: ec.EllipticCurvePrivateKey, chiave_auto: str) -> str:
    """L'HMAC del comando, con la chiave condivisa fra telefono e auto."""
    try:
        auto = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), bytes.fromhex(chiave_auto))
    except ValueError as err:
        raise ErroreCertificato("La chiave pubblica dell'auto non si legge") from err
    segreto = chiave.exchange(ec.ECDH(), auto)
    condivisa = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"").derive(segreto)
    return hmac.new(condivisa, (comando + timestamp).encode(), hashlib.sha256).hexdigest()
