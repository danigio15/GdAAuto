"""Certificati e chiavi in qualunque forma arrivino, portati a PEM.

Chi configura carica quello che ha: un PEM (testo), un DER (binario), o un
PKCS#12 (``.p12``/``.pfx``, certificato e chiave insieme). Le marche vogliono
sempre PEM, e qui si converte con ``cryptography``, che Home Assistant ha già.
"""

from __future__ import annotations

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12

CERTIFICATO = "certificato"
CHIAVE = "chiave"


class FormatoSconosciuto(ValueError):
    """Il file non è un certificato né una chiave, in nessuna delle forme note."""


def _chiave_pem(chiave: object) -> str:
    return chiave.private_bytes(  # type: ignore[attr-defined]
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


def _cert_pem(cert: x509.Certificate) -> str:
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def da_pkcs12(dati: bytes, password: str | None = None) -> tuple[str | None, str | None]:
    """Il certificato e la chiave dentro un PKCS#12, in PEM. Si prova senza password e con quella data."""
    for tentativo in (None, b"", password.encode() if password else None):
        try:
            chiave, cert, _altri = pkcs12.load_key_and_certificates(dati, tentativo)
        except (ValueError, TypeError):
            continue
        return (_cert_pem(cert) if cert else None, _chiave_pem(chiave) if chiave else None)
    raise FormatoSconosciuto("non è un PEM, un DER né un PKCS#12 senza password")


def in_pem(dati: bytes, tipo: str) -> str:
    """Un certificato o una chiave in PEM, da PEM, DER o PKCS#12."""
    if b"-----BEGIN" in dati:
        # Si apre e si riscrive: resta un PEM pulito, senza le «Bag Attributes» che OpenSSL mette davanti.
        try:
            if tipo == CERTIFICATO:
                return _cert_pem(x509.load_pem_x509_certificate(dati))
            return _chiave_pem(serialization.load_pem_private_key(dati, None))
        except (ValueError, TypeError) as err:
            raise FormatoSconosciuto(f"PEM che non si apre: {err}") from err
    try:
        if tipo == CERTIFICATO:
            return _cert_pem(x509.load_der_x509_certificate(dati))
        return _chiave_pem(serialization.load_der_private_key(dati, None))
    except (ValueError, TypeError):
        pass
    cert, chiave = da_pkcs12(dati)
    scelto = cert if tipo == CERTIFICATO else chiave
    if not scelto:
        raise FormatoSconosciuto(f"il PKCS#12 non contiene un {tipo}")
    return scelto


def tipo_di(nome_campo: str) -> str:
    """``cert_app`` è un certificato, ``chiave_app`` una chiave."""
    return CHIAVE if nome_campo.startswith("chiave") else CERTIFICATO
