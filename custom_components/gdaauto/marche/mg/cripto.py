"""La crittografia del cloud MG iSMART (SAIC): corpo cifrato e firma.

Non importa niente di Home Assistant e niente di rete: sono funzioni pure, e
si provano da sole con i vettori di SAIC-iSmart-API/saic-python-client-ng.

Come parla l'app, in parole semplici:

1. **Il corpo** di ogni richiesta viaggia cifrato in AES-128-CBC, scritto in
   esadecimale. La chiave è un MD5 di un MD5: dentro ci sono il percorso
   (con la query), il tenant, il token dell'utente, l'ora in millisecondi e
   il tipo del contenuto; l'IV è l'MD5 dell'ora. Ogni richiesta, quindi, ha
   la sua chiave.
2. **La firma** (``APP-VERIFICATION-STRING``) è un HMAC-SHA256 degli stessi
   pezzi più il corpo cifrato, con una chiave ricavata da quella del corpo.
3. **La risposta** torna cifrata allo stesso modo, ma la chiave dipende solo
   dall'ora che il server scrive in ``APP-SEND-DATE`` e dal tipo del
   contenuto in ``ORIGINAL-CONTENT-TYPE``.
4. **Il VIN** nelle richieste non va mai in chiaro: si manda il suo SHA-256;
   **la password** del login si manda come SHA-1.

Gli MD5 e gli SHA-1 qui non proteggono niente: è il formato che l'app usa.
"""

from __future__ import annotations

import binascii
import hashlib
import hmac

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

TIPO_JSON = "application/json"
TIPO_FORM = "application/x-www-form-urlencoded"


def md5_hex(testo: str) -> str:
    return hashlib.md5(testo.encode(), usedforsecurity=False).hexdigest()


def sha1_hex(testo: str) -> str:
    return hashlib.sha1(testo.encode(), usedforsecurity=False).hexdigest()


def sha256_hex(testo: str) -> str:
    return hashlib.sha256(testo.encode()).hexdigest()


def tipo_normale(tipo: str | None) -> str:
    """Il tipo del contenuto come entra nelle chiavi: senza charset."""
    if tipo and "multipart" in tipo:
        return "multipart/form-data"
    if tipo and "x-www-form-urlencoded" in tipo:
        return TIPO_FORM
    return TIPO_JSON


def cifra_aes(testo: str, chiave_hex: str, iv_hex: str) -> str:
    """AES-CBC con PKCS#7; chiave e IV in esadecimale, il risultato pure."""
    imbottitura = padding.PKCS7(128).padder()
    dati = imbottitura.update(testo.encode()) + imbottitura.finalize()
    cifratore = Cipher(algorithms.AES(bytes.fromhex(chiave_hex)), modes.CBC(bytes.fromhex(iv_hex))).encryptor()
    return (cifratore.update(dati) + cifratore.finalize()).hex()


def decifra_aes(testo_hex: str, chiave_hex: str, iv_hex: str) -> str:
    try:
        cifrato = bytes.fromhex(testo_hex.strip())
    except ValueError as err:
        raise ValueError("Il testo cifrato non è esadecimale") from err
    decifratore = Cipher(algorithms.AES(bytes.fromhex(chiave_hex)), modes.CBC(bytes.fromhex(iv_hex))).decryptor()
    dati = decifratore.update(cifrato) + decifratore.finalize()
    togli = padding.PKCS7(128).unpadder()
    return (togli.update(dati) + togli.finalize()).decode()


def chiave_richiesta(percorso: str, tenant: str, token: str, ora_ms: str, tipo: str) -> str:
    """La chiave AES del corpo di una richiesta, in esadecimale."""
    return md5_hex(md5_hex(percorso + tenant + token + "app") + ora_ms + "1" + tipo)


def stringa_verifica(*, percorso: str, ora_ms: str, tenant: str, tipo: str, contenuto: str, token: str) -> str:
    """L'intestazione ``APP-VERIFICATION-STRING``: l'HMAC che il cloud ricalcola."""
    chiave = chiave_richiesta(percorso, tenant, token, ora_ms, tipo)
    iv = md5_hex(ora_ms)
    cifrato = cifra_aes(contenuto, chiave, iv) if contenuto else ""
    messaggio = percorso + tenant + token + "app" + ora_ms + "1" + tipo + cifrato
    chiave_hmac = md5_hex(chiave + ora_ms)
    return hmac.new(chiave_hmac.encode(), messaggio.encode(), hashlib.sha256).hexdigest()


def cifra_richiesta(*, percorso: str, ora_ms: str, tenant: str, tipo: str, contenuto: str, token: str) -> str:
    """Il corpo cifrato da mandare (vuoto se non c'è corpo)."""
    contenuto = contenuto.strip()
    if not contenuto:
        return ""
    return cifra_aes(contenuto, chiave_richiesta(percorso, tenant, token, ora_ms, tipo), md5_hex(ora_ms))


def decifra_richiesta(*, percorso: str, ora_ms: str, tenant: str, tipo: str, cifrato: str, token: str) -> str:
    """Il contrario di ``cifra_richiesta``: serve al cloud finto delle prove."""
    if not cifrato.strip():
        return ""
    return decifra_aes(cifrato, chiave_richiesta(percorso, tenant, token, ora_ms, tipo), md5_hex(ora_ms))


def cifra_risposta(testo: str, ora_ms: str, tipo: str = TIPO_JSON) -> str:
    """Come il cloud cifra le risposte: serve al cloud finto delle prove."""
    return cifra_aes(testo, md5_hex(ora_ms + "1" + tipo), md5_hex(ora_ms))


def decifra_risposta(testo: str, ora_ms: str, tipo: str) -> str:
    """La risposta in chiaro, con l'ora e il tipo che il server ha messo nelle intestazioni."""
    try:
        return decifra_aes(testo, md5_hex(ora_ms + "1" + tipo), md5_hex(ora_ms))
    except (ValueError, binascii.Error) as err:
        raise ValueError(f"La risposta non si decifra: {err}") from err
