"""La crittografia «interna» del cloud BYD, tutta da fatti noti.

Dentro la busta Bangcle (vedi ``busta.py``) c'è un secondo strato, questo sì
fatto di crittografia standard che si riscrive da zero:

- il campo ``encryData`` (richiesta) e ``respondData`` (risposta) sono
  **AES-128-CBC** con IV a zero e riempimento PKCS#7, in esadecimale maiuscolo;
  la chiave è ``MD5(password)`` ripetuto al login, ``MD5(encry_token)`` dopo;
- la richiesta porta una **firma** ``sign``: SHA-1 dei campi ordinati più la
  chiave, con un rimescolamento di maiuscole/minuscole e qualche zero tolto;
- e un ``checkcode``: MD5 del JSON compatto, con i quattro pezzi rimessi in un
  ordine fisso.

Nessuna tabella e nessun segreto estratto dall'app: solo ``hashlib`` e
``cryptography``, già dipendenze di Home Assistant.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_IV_ZERO = b"\x00" * 16


def md5(testo: str) -> str:
    """MD5 di una stringa UTF-8, in esadecimale maiuscolo."""
    return hashlib.md5(testo.encode("utf-8")).hexdigest().upper()


def chiave_login(password: str) -> str:
    """La chiave AES del login: ``MD5(MD5(password))``."""
    return md5(md5(password))


def cifra_hex(chiaro: str, chiave_hex: str) -> str:
    """AES-128-CBC con IV a zero, in esadecimale maiuscolo."""
    chiave = bytes.fromhex(chiave_hex)
    riempi = padding.PKCS7(128).padder()
    dati = riempi.update(chiaro.encode("utf-8")) + riempi.finalize()
    cifratore = Cipher(algorithms.AES(chiave), modes.CBC(_IV_ZERO)).encryptor()
    return (cifratore.update(dati) + cifratore.finalize()).hex().upper()


def decifra_hex(cifrato_hex: str, chiave_hex: str) -> str:
    """Decifra un AES-128-CBC esadecimale e torna la stringa UTF-8."""
    chiave = bytes.fromhex(chiave_hex)
    cifrato = bytes.fromhex(cifrato_hex)
    decifratore = Cipher(algorithms.AES(chiave), modes.CBC(_IV_ZERO)).decryptor()
    dati = decifratore.update(cifrato) + decifratore.finalize()
    togli = padding.PKCS7(128).unpadder()
    return (togli.update(dati) + togli.finalize()).decode("utf-8")


def _stringa_firma(campi: dict[str, str], chiave: str) -> str:
    """I campi in ordine alfabetico, ``nome=valore`` uniti da ``&``, più la chiave."""
    pezzi = "&".join(f"{k}={'null' if campi[k] is None else campi[k]}" for k in sorted(campi))
    return f"{pezzi}&password={chiave}"


def _sha1_mescolato(testo: str) -> str:
    """SHA-1 con esadecimale a maiuscole alterne e gli zeri in posizione pari tolti.

    È il «sha1Mixed» dell'app: byte pari in maiuscolo, dispari in minuscolo,
    poi via gli ``0`` che cadono in posizione pari nella stringa risultante.
    """
    digest = hashlib.sha1(testo.encode("utf-8")).digest()
    mescolato = "".join((f"{b:02x}".upper() if i % 2 == 0 else f"{b:02x}") for i, b in enumerate(digest))
    return "".join(c for j, c in enumerate(mescolato) if not (c == "0" and j % 2 == 0))


def firma(campi: dict[str, str], chiave: str) -> str:
    """La firma ``sign`` di una richiesta."""
    return _sha1_mescolato(_stringa_firma(campi, chiave))


def checkcode(corpo: dict[str, Any]) -> str:
    """MD5 del JSON compatto, con i quattro pezzi da 8 cifre rimessi in ordine fisso."""
    testo = json.dumps(corpo, separators=(",", ":"), ensure_ascii=False)
    h = hashlib.md5(testo.encode("utf-8")).hexdigest()
    return h[24:32] + h[8:16] + h[16:24] + h[0:8]
