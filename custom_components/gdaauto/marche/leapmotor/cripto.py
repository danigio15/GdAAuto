"""La crittografia del cloud Leapmotor: firme, chiavi, password del certificato.

Non importa niente di Home Assistant e niente di rete: sono funzioni pure, e
si provano da sole con i vettori in ``tests/test_cripto.py``.

Quello che il cloud vuole, nell'ordine in cui lo chiede:

1. **Il login** si firma con uno SHA-256 di campi concatenati (nessuna chiave:
   il segreto è il certificato dell'app con cui si parla in mTLS).
2. **Dopo il login** ogni richiesta si firma con HMAC-SHA256; la chiave si
   ricava con HKDF da tre stringhe che arrivano nella risposta del login.
3. **Il certificato dell'account** arriva nel login come PKCS#12 in base64;
   la sua password si ricava dall'id dell'utente e dallo ``uid`` con MD5,
   SHA-256 e un blocco SM4 a chiavi di giro fisse.
4. **Il PIN dell'auto** per i comandi viaggia cifrato in AES-128-CBC con
   chiave e IV ricavati dal token della sessione.

Il protocollo è quello che la comunità ha ricostruito dall'app ufficiale
(vedi ``RINGRAZIAMENTI.md``); le costanti di SM4 vengono da
kerniger/leapmotor-ha, licenza MIT.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets

from cryptography.hazmat.primitives import hashes, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# Quello che l'app dichiara di sé a ogni richiesta.
VERSIONE_APP = "1.12.3"
SORGENTE = "leapmotor"
CANALE = "1"
TIPO_DISPOSITIVO = "1"
ALGORITMO_P12 = "1"
POLICY_ID = "20260204"
DEVICE_ID_PREDEFINITO = "bd605e5c599944efb846bcf70f1449d8"

# Chiave e IV di riserva per il PIN, quando il token è assente o corto.
_AES_CHIAVE_RISERVA = "f1cf0c025baec0e2"
_AES_IV_RISERVA = "6b6a1fe94e133fd7"


def nonce() -> str:
    """Un numero di 6-7 cifre, come fa l'app."""
    return str(secrets.randbelow(9_900_000) + 100_000)


# --- il login -----------------------------------------------------------------


def firma_login(*, lingua: str, device_id: str, email: str, password: str, nonce_: str, timestamp: str) -> str:
    """SHA-256 dei campi del login, nell'ordine che il cloud controlla."""
    testo = "".join(
        (
            lingua,
            TIPO_DISPOSITIVO,
            device_id,
            "1",  # loginMethod
            email,
            "0",  # isRecoverAcct
            "1",  # channel
            nonce_,
            password,
            POLICY_ID,
            SORGENTE,
            timestamp,
            VERSIONE_APP,
        )
    )
    return hashlib.sha256(testo.encode()).hexdigest()


# --- dopo il login ------------------------------------------------------------


def chiave_firma(ikm: str, sale: str, info: str) -> bytes:
    """La chiave HMAC, da ``signIkm``, ``signSalt`` e ``signInfo`` del login."""
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=sale.encode(), info=info.encode()).derive(ikm.encode())


def firma_hmac(chiave: bytes, testo: str) -> str:
    return hmac.new(chiave, testo.encode(), hashlib.sha256).hexdigest()


def testo_firma_standard(campi: dict[str, str]) -> str:
    """I valori concatenati, con le chiavi in ordine alfabetico: è la firma di
    quasi tutte le richieste (elenco, stato, rinnovo del token)."""
    return "".join(valore for _, valore in sorted(campi.items()))


def device_id_dal_token(token: str, riserva: str = DEVICE_ID_PREDEFINITO) -> str:
    """Il ``deviceId`` della sessione sta dentro il JWT: ``user_name`` è una
    lista separata da virgole, e il terzo pezzo è lui."""
    try:
        corpo = token.split(".")[1]
        corpo += "=" * (-len(corpo) % 4)
        dati = json.loads(base64.urlsafe_b64decode(corpo))
        pezzi = str(dati.get("user_name") or "").split(",")
        if len(pezzi) >= 4 and pezzi[2]:
            return pezzi[2]
    except (IndexError, ValueError, UnicodeDecodeError):
        pass
    return riserva


# --- il certificato dell'account ---------------------------------------------

_SM4_SBOX = (
    0xD6, 0x90, 0xE9, 0xFE, 0xCC, 0xE1, 0x3D, 0xB7, 0x16, 0xB6, 0x14, 0xC2, 0x28, 0xFB, 0x2C, 0x05,
    0x2B, 0x67, 0x9A, 0x76, 0x2A, 0xBE, 0x04, 0xC3, 0xAA, 0x44, 0x13, 0x26, 0x49, 0x86, 0x06, 0x99,
    0x9C, 0x42, 0x50, 0xF4, 0x91, 0xEF, 0x98, 0x7A, 0x33, 0x54, 0x0B, 0x43, 0xED, 0xCF, 0xAC, 0x62,
    0xE4, 0xB3, 0x1C, 0xA9, 0xC9, 0x08, 0xE8, 0x95, 0x80, 0xDF, 0x94, 0xFA, 0x75, 0x8F, 0x3F, 0xA6,
    0x47, 0x07, 0xA7, 0xFC, 0xF3, 0x73, 0x17, 0xBA, 0x83, 0x59, 0x3C, 0x19, 0xE6, 0x85, 0x4F, 0xA8,
    0x68, 0x6B, 0x81, 0xB2, 0x71, 0x64, 0xDA, 0x8B, 0xF8, 0xEB, 0x0F, 0x4B, 0x70, 0x56, 0x9D, 0x35,
    0x1E, 0x24, 0x0E, 0x5E, 0x63, 0x58, 0xD1, 0xA2, 0x25, 0x22, 0x7C, 0x3B, 0x01, 0x21, 0x78, 0x87,
    0xD4, 0x00, 0x46, 0x57, 0x9F, 0xD3, 0x27, 0x52, 0x4C, 0x36, 0x02, 0xE7, 0xA0, 0xC4, 0xC8, 0x9E,
    0xEA, 0xBF, 0x8A, 0xD2, 0x40, 0xC7, 0x38, 0xB5, 0xA3, 0xF7, 0xF2, 0xCE, 0xF9, 0x61, 0x15, 0xA1,
    0xE0, 0xAE, 0x5D, 0xA4, 0x9B, 0x34, 0x1A, 0x55, 0xAD, 0x93, 0x32, 0x30, 0xF5, 0x8C, 0xB1, 0xE3,
    0x1D, 0xF6, 0xE2, 0x2E, 0x82, 0x66, 0xCA, 0x60, 0xC0, 0x29, 0x23, 0xAB, 0x0D, 0x53, 0x4E, 0x6F,
    0xD5, 0xDB, 0x37, 0x45, 0xDE, 0xFD, 0x8E, 0x2F, 0x03, 0xFF, 0x6A, 0x72, 0x6D, 0x6C, 0x5B, 0x51,
    0x8D, 0x1B, 0xAF, 0x92, 0xBB, 0xDD, 0xBC, 0x7F, 0x11, 0xD9, 0x5C, 0x41, 0x1F, 0x10, 0x5A, 0xD8,
    0x0A, 0xC1, 0x31, 0x88, 0xA5, 0xCD, 0x7B, 0xBD, 0x2D, 0x74, 0xD0, 0x12, 0xB8, 0xE5, 0xB4, 0xB0,
    0x89, 0x69, 0x97, 0x4A, 0x0C, 0x96, 0x77, 0x7E, 0x65, 0xB9, 0xF1, 0x09, 0xC5, 0x6E, 0xC6, 0x84,
    0x18, 0xF0, 0x7D, 0xEC, 0x3A, 0xDC, 0x4D, 0x20, 0x79, 0xEE, 0x5F, 0x3E, 0xD7, 0xCB, 0x39, 0x48,
)  # fmt: skip

# Le chiavi di giro sono fisse: l'app non le ricava da una chiave, le porta
# già espanse. Vengono da kerniger/leapmotor-ha (MIT).
_SM4_CHIAVI_DI_GIRO = (
    0x818FA553, 0xEBA3318D, 0x5FC3C93A, 0xBD1DADD9, 0xBB61CAB9, 0x000FD7EA, 0xDC6E0166, 0xDA937279,
    0x607EE786, 0xB548754C, 0x107330E4, 0xEA17C186, 0x0F56F74B, 0xB21E443C, 0xE1210FE2, 0x009995C8,
    0xE7529A48, 0x6EF474F6, 0x2AB06DF6, 0x43B11BE8, 0x359D4A14, 0xC29E2CDE, 0x30CF6A3E, 0x79D1C806,
    0x7C502387, 0xAAAB9BC6, 0xF0FE744B, 0x1CAFC872, 0x95A9D075, 0x88070D58, 0x22800475, 0x8391938B,
)  # fmt: skip


def _ruota(valore: int, bit: int) -> int:
    return ((valore << bit) & 0xFFFFFFFF) | (valore >> (32 - bit))


def _sm4_blocco(blocco: bytes) -> bytes:
    x = [int.from_bytes(blocco[i : i + 4], "big") for i in range(0, 16, 4)]
    for k in _SM4_CHIAVI_DI_GIRO:
        t = x[1] ^ x[2] ^ x[3] ^ k
        b = (
            (_SM4_SBOX[(t >> 24) & 0xFF] << 24)
            | (_SM4_SBOX[(t >> 16) & 0xFF] << 16)
            | (_SM4_SBOX[(t >> 8) & 0xFF] << 8)
            | _SM4_SBOX[t & 0xFF]
        )
        nuovo = x[0] ^ b ^ _ruota(b, 2) ^ _ruota(b, 10) ^ _ruota(b, 18) ^ _ruota(b, 24)
        x = [x[1], x[2], x[3], nuovo & 0xFFFFFFFF]
    return b"".join(v.to_bytes(4, "big") for v in reversed(x))


def _sm4_ecb(dati: bytes) -> bytes:
    riempimento = 16 - len(dati) % 16
    dati += bytes([riempimento]) * riempimento
    return b"".join(_sm4_blocco(dati[i : i + 16]) for i in range(0, len(dati), 16))


def password_p12(id_utente: str | int, uid: str) -> str:
    """La password del PKCS#12 dell'account, da ``id`` e ``uid`` del login."""
    cn = hashlib.md5(str(id_utente).encode()).hexdigest()
    impasto = f"{cn}{cn[::2]}{uid[1::2]}".encode()
    digest = hashlib.sha256(impasto).digest()
    return base64.b64encode(_sm4_ecb(digest)[:12]).decode()[:15]


# --- il PIN dell'auto -----------------------------------------------------------


def chiave_e_iv_pin(token: str | None) -> tuple[str, str]:
    """Chiave e IV di AES per il PIN, dal token della sessione."""
    if not token:
        return _AES_CHIAVE_RISERVA, _AES_IV_RISERVA
    if len(token) < 64:
        raise ValueError("Il token è troppo corto per ricavarne la chiave del PIN")
    chiave = hashlib.md5(token[:32].encode()).hexdigest()[8:24]
    iv = hashlib.md5(token[32:64].encode()).hexdigest()[8:24]
    return chiave, iv


def cifra_pin(pin: str, token: str | None) -> str:
    """Il PIN come lo vuole il cloud: AES-128-CBC, PKCS#7, base64."""
    chiave, iv = chiave_e_iv_pin(token)
    riempitore = padding.PKCS7(128).padder()
    chiaro = riempitore.update(pin.encode()) + riempitore.finalize()
    cifratore = Cipher(algorithms.AES(chiave.encode()), modes.CBC(iv.encode())).encryptor()
    return base64.b64encode(cifratore.update(chiaro) + cifratore.finalize()).decode()
