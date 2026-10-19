"""La crittografia del login di My Honda+, come la fa l'app.

L'app non manda mai email e password in chiaro: le chiude in una busta.

- una chiave AES-256 e un IV nuovi a ogni richiesta, per cifrare il JSON
  (AES-CBC con il riempimento PKCS#7);
- la chiave e l'IV, scritti in base64, cifrati con la chiave pubblica RSA
  del server Honda (PKCS#1 v1.5), che è scritta dentro l'app;
- e ``keyId``, che dice al server quale delle sue chiavi usare.

In più ogni installazione ha la sua coppia di chiavi RSA («il dispositivo»):
la pubblica si registra una volta presso Honda (con il link che arriva per
email), la privata firma (SHA256withRSA) la sfida del secondo passo del
login. La coppia si salva nella configurazione: senza, servirebbe un'email
nuova a ogni rientro.

La chiave pubblica del server e il suo ``keyId`` vengono da
enricobattocchi/pymyhondaplus (licenza MIT): vedi RINGRAZIAMENTI.md. Questo
modulo non parla con la rete e non importa Home Assistant. Generare la coppia
RSA è lento: va fatto fuori dal loop (``run_in_executor``).
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, padding as riempimento, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

#: La chiave pubblica RSA del server di login, scritta nell'app (DER X.509 in base64).
CHIAVE_SERVER = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAyCLLwyLwpI1vsPcUNTgJ"
    "1dr2pJ53luEx/BuU4HVSVtz6HtkPpEDSiFDOOrpJJOTYUjzqV93bm7Q2t2g8pRqK"
    "0zjijLm4w1tdcZkxEwYVQJr8SOYza/zbeac2TMu4iu9SbbJM0fzUwX6IrBu/EE4G"
    "diIF3Dwm4tzNpqZeh1fXEy9A2MHzmZIdWdkowZlUUyLtXuGBcbTOBY7LFRLKK0bV"
    "UsC06w/dCD3Rhs48IXhAPyqSZYCIqofUvAq5NE0YzIPSSMKtrcPPL+Ae0F9/pz8q"
    "YisH8TWyZZ6ih0Y5HufjuDzNYfJLNt4CEiohs7+hZtfbshkKuw+vr3sS4g9zM0Ot"
    "SQIDAQAB"
)
#: Quale chiave del server è quella sopra.
ID_CHIAVE_SERVER = "1bff05258b984f278f70ed8c9580ba79"


def _pubblica(der_b64: str) -> RSAPublicKey:
    chiave = serialization.load_der_public_key(base64.b64decode(der_b64))
    assert isinstance(chiave, RSAPublicKey)
    return chiave


def busta(
    dati: dict[str, Any], *, chiave_server: str = CHIAVE_SERVER, id_chiave: str = ID_CHIAVE_SERVER
) -> dict[str, str]:
    """Il JSON chiuso come lo vuole ``/auth/*``: AES per il testo, RSA per la chiave."""
    chiave, iv = os.urandom(32), os.urandom(16)
    imbottitore = riempimento.PKCS7(128).padder()
    chiaro = imbottitore.update(json.dumps(dati).encode()) + imbottitore.finalize()
    cifratore = Cipher(algorithms.AES(chiave), modes.CBC(iv)).encryptor()
    cifrato = cifratore.update(chiaro) + cifratore.finalize()
    server = _pubblica(chiave_server)

    def per_il_server(testo: bytes) -> str:
        # Il server vuole la chiave e l'IV scritti in base64, e poi cifrati.
        return base64.b64encode(server.encrypt(base64.b64encode(testo), padding.PKCS1v15())).decode()

    return {
        "encryptedOneTimeKey": per_il_server(chiave),
        "encryptedOneTimeSalt": per_il_server(iv),
        "encryptedPayload": base64.b64encode(cifrato).decode(),
        "keyId": id_chiave,
    }


def apri_busta(corpo: dict[str, Any], privata: RSAPrivateKey) -> dict[str, Any]:
    """Il contrario di ``busta``, con la chiave privata del server: serve solo al cloud finto delle prove."""
    chiave = base64.b64decode(privata.decrypt(base64.b64decode(corpo["encryptedOneTimeKey"]), padding.PKCS1v15()))
    iv = base64.b64decode(privata.decrypt(base64.b64decode(corpo["encryptedOneTimeSalt"]), padding.PKCS1v15()))
    decifratore = Cipher(algorithms.AES(chiave), modes.CBC(iv)).decryptor()
    imbottito = decifratore.update(base64.b64decode(corpo["encryptedPayload"])) + decifratore.finalize()
    togli = riempimento.PKCS7(128).unpadder()
    return json.loads(togli.update(imbottito) + togli.finalize())


class Dispositivo:
    """La coppia RSA di questa installazione: la pubblica si registra, la privata firma."""

    def __init__(self, pem: str | bytes | None = None) -> None:
        if pem:
            chiave = serialization.load_pem_private_key(pem.encode() if isinstance(pem, str) else pem, password=None)
            if not isinstance(chiave, RSAPrivateKey):
                raise ValueError("La chiave del dispositivo Honda non è una chiave RSA")
            self._privata = chiave
        else:
            # Lento (qualche decimo di secondo): chi chiama lo fa nell'executor.
            self._privata = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    @property
    def pem(self) -> str:
        return self._privata.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ).decode()

    @property
    def pubblica(self) -> str:
        """La chiave pubblica in DER X.509, base64: è anche l'identificativo del dispositivo."""
        der = self._privata.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        return base64.b64encode(der).decode()

    def firma(self, sfida: str) -> str:
        """La sfida del login firmata SHA256withRSA, in base64."""
        return base64.b64encode(self._privata.sign(sfida.encode(), padding.PKCS1v15(), hashes.SHA256())).decode()


def verifica_firma(pubblica_b64: str, sfida: str, firma_b64: str) -> bool:
    """Per il cloud finto delle prove: la firma è di quella chiave?"""
    try:
        _pubblica(pubblica_b64).verify(base64.b64decode(firma_b64), sfida.encode(), padding.PKCS1v15(), hashes.SHA256())
    except (InvalidSignature, ValueError):
        return False
    return True
