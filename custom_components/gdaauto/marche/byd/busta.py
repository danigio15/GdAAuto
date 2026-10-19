"""La «busta» esterna del cloud BYD: un AES a scatola bianca (white-box).

L'app BYD non parla con AES normale. Prima di uscire, ogni richiesta viene
cifrata da una libreria offuscata (chiamata «Bangcle») che non tiene una chiave
vera: al suo posto ci sono otto grandi tabelle di ricerca, estratte dalla
libreria nativa dell'app (``libencrypt.so``). Cifrare o decifrare un blocco
vuol dire solo leggere queste tabelle nell'ordine giusto. Il risultato va in
Base64 con una ``F`` davanti: ``{"request": "F...."}`` all'andata,
``{"response": "F...."}`` al ritorno.

GdAAuto **non contiene** le tabelle: sono materiale crittografico di BYD,
estratto dall'app, e nessuno ha una licenza per ridistribuirlo (vedi la nota in
``__init__.py``). L'algoritmo qui sotto è invece un fatto del protocollo,
riscritto da capo; legge le tabelle da un file che fornisce l'utente.

Il formato del file è quello usato dalla libreria MIT pyBYD
(``bangcle_tables.bin``), così chi ha già quel file può passarlo com'è:

- ``b"BGTB"`` (4 byte), versione ``uint16`` LE, numero di tabelle ``uint16`` LE (8);
- otto coppie ``(offset uint32 LE, lunghezza uint32 LE)``;
- i byte grezzi delle tabelle, nell'ordine dichiarato.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import struct

#: IV a zero: la busta non usa un vettore d'inizializzazione casuale.
_IV_ZERO = b"\x00" * 16

_MAGIA = b"BGTB"
_VERSIONE = 1
_QUANTE = 8
_TESTA = 4 + 2 + 2  # magia + versione + numero
_VOCE = 4 + 4  # offset + lunghezza

#: Nome e dimensione attesa di ogni tabella, nell'ordine del file.
_TABELLE: tuple[tuple[str, int], ...] = (
    ("inv_giro", 0x28000),
    ("inv_xor", 0x3C000),
    ("inv_prima", 0x1000),
    ("giro", 0x28000),
    ("xor", 0x3C000),
    ("finale", 0x1000),
    ("perm_decifra", 8),
    ("perm_cifra", 8),
)


class ErroreBusta(Exception):
    """La busta Bangcle non si apre o non si chiude."""


class ErroreTabelle(ErroreBusta):
    """Il file delle tabelle manca, è corrotto o non è nel formato atteso."""


@dataclass(frozen=True)
class Tabelle:
    """Le otto tabelle di ricerca dell'AES a scatola bianca."""

    inv_giro: bytes
    inv_xor: bytes
    inv_prima: bytes
    giro: bytes
    xor: bytes
    finale: bytes
    perm_decifra: bytes
    perm_cifra: bytes


def carica_tabelle(dati: bytes) -> Tabelle:
    """Legge il file binario ``BGTB`` e restituisce le otto tabelle."""
    if len(dati) < _TESTA + _QUANTE * _VOCE:
        raise ErroreTabelle("Il file delle tabelle è troppo corto")
    if dati[:4] != _MAGIA:
        raise ErroreTabelle(f"Firma del file sbagliata: attesa {_MAGIA!r}, trovata {dati[:4]!r}")
    (versione,) = struct.unpack_from("<H", dati, 4)
    if versione != _VERSIONE:
        raise ErroreTabelle(f"Versione del file non gestita: {versione}")
    (quante,) = struct.unpack_from("<H", dati, 6)
    if quante != _QUANTE:
        raise ErroreTabelle(f"Attese {_QUANTE} tabelle, trovate {quante}")
    pezzi: list[bytes] = []
    for i, (nome, lung_attesa) in enumerate(_TABELLE):
        offset, lung = struct.unpack_from("<II", dati, _TESTA + i * _VOCE)
        if lung != lung_attesa:
            raise ErroreTabelle(f"Tabella «{nome}»: attesi {lung_attesa} byte, trovati {lung}")
        if offset + lung > len(dati):
            raise ErroreTabelle(f"Tabella «{nome}»: i dati finiscono oltre il file")
        pezzi.append(dati[offset : offset + lung])
    return Tabelle(*pezzi)


def _in_stato(blocco: bytes) -> bytearray:
    """Mette i 16 byte del blocco nel formato di lavoro (colonna·8 + riga)."""
    stato = bytearray(32)
    for col in range(4):
        for riga in range(4):
            stato[col * 8 + riga] = blocco[col + riga * 4]
    return stato


def _da_stato(stato: bytearray) -> bytes:
    """Ricompone i 16 byte in uscita dal formato di lavoro."""
    uscita = bytearray(16)
    for col in range(4):
        for riga in range(4):
            uscita[col + riga * 4] = stato[col * 8 + riga]
    return bytes(uscita)


def _mescola(stato: bytearray, perm: bytes, tab_giro: bytes, tab_xor: bytes, giro: int) -> None:
    """Un giro dell'AES a scatola bianca: sostituzione (``giro``) e XOR (``xor``).

    Uguale per cifrare e decifrare, cambiano solo le tabelle e la permutazione.
    """
    base_col = giro * 4
    temp = bytearray(64)
    for i in range(4):
        salto = perm[i * 2]
        riga = i * 8
        blocco = i * 16
        for j in range(4):
            c = (salto + j) & 3
            indice = stato[riga + c] + (i + (base_col + c) * 4) * 256
            (valore,) = struct.unpack_from("<I", tab_giro, indice * 4)
            struct.pack_into("<I", temp, blocco + j * 4, valore)

    passo = 1
    for colonna in range(4):
        punt = colonna
        for cella in range(4):
            basso = temp[punt]
            nibble_basso = basso & 0xF
            nibble_alto = basso & 0xF0
            vicini = (temp[punt + 0x10], temp[punt + 0x20], temp[punt + 0x30])
            base_xor = cella * 0x18 + giro * 0x60
            k = passo
            for v in vicini:
                alto = (v << 4) & 0xFF
                a = nibble_basso | alto
                nibble_alto = ((nibble_alto >> 4) | ((v >> 4) << 4)) & 0xFF
                nibble_basso = tab_xor[(base_xor + (k - 1)) * 0x100 + a] & 0xF
                b = tab_xor[(base_xor + k) * 0x100 + nibble_alto]
                nibble_alto = (b & 0xF) << 4
                k += 2
            stato[cella + colonna * 8] = (nibble_alto | nibble_basso) & 0xFF
            punt += 4
        passo += 6


def _ultima_cifra(stato: bytearray, finale: bytes) -> None:
    """L'ultimo passo di cifratura: sostituzione finale con rotazione delle righe."""
    copia = bytearray(stato)
    for riga in range(4):
        for blocco, scarto in ((0, 0), (8, 1), (0x10, 2), (0x18, 3)):
            r = (scarto + riga) & 3
            stato[blocco + riga] = finale[copia[blocco + r] + r * 0x400 + (blocco // 8) * 0x100]


def _prima_decifra(stato: bytearray, inv_prima: bytes) -> None:
    """Il passo gemello in decifratura: sostituzione iniziale inversa."""
    copia = bytearray(stato)
    scarti = (3, 2, 1)  # righe 8, 0x10, 0x18 ruotate all'indietro
    for riga in range(4):
        stato[riga] = inv_prima[copia[riga] + riga * 0x400]
        for n, blocco in enumerate((8, 0x10, 0x18)):
            r = (scarti[n] + riga) & 3
            stato[blocco + riga] = inv_prima[copia[blocco + r] + r * 0x400 + (n + 1) * 0x100]


def cifra_blocco(t: Tabelle, blocco: bytes) -> bytes:
    """Cifra 16 byte con l'AES a scatola bianca."""
    stato = _in_stato(blocco)
    for giro in range(9):
        _mescola(stato, t.perm_cifra, t.giro, t.xor, giro)
    _ultima_cifra(stato, t.finale)
    return _da_stato(stato)


def decifra_blocco(t: Tabelle, blocco: bytes) -> bytes:
    """Decifra 16 byte con l'AES a scatola bianca."""
    stato = _in_stato(blocco)
    for giro in range(9, 0, -1):
        _mescola(stato, t.perm_decifra, t.inv_giro, t.inv_xor, giro)
    _prima_decifra(stato, t.inv_prima)
    return _da_stato(stato)


def _pkcs7(dati: bytes) -> bytes:
    riempi = 16 - (len(dati) % 16)
    return dati + bytes([riempi] * riempi)


def _togli_pkcs7(dati: bytes) -> bytes:
    if not dati:
        return dati
    riempi = dati[-1]
    if 0 < riempi <= 16 and len(dati) >= riempi and all(b == riempi for b in dati[-riempi:]):
        return dati[:-riempi]
    return dati


def _cifra_cbc(t: Tabelle, dati: bytes) -> bytes:
    prec = _IV_ZERO
    fuori = bytearray()
    for i in range(0, len(dati), 16):
        blocco = bytes(a ^ b for a, b in zip(dati[i : i + 16], prec, strict=False))
        prec = cifra_blocco(t, blocco)
        fuori += prec
    return bytes(fuori)


def _decifra_cbc(t: Tabelle, dati: bytes) -> bytes:
    prec = _IV_ZERO
    fuori = bytearray()
    for i in range(0, len(dati), 16):
        blocco = dati[i : i + 16]
        chiaro = decifra_blocco(t, blocco)
        fuori += bytes(a ^ b for a, b in zip(chiaro, prec, strict=False))
        prec = blocco
    return bytes(fuori)


class Busta:
    """Chiude e apre le buste Bangcle con le tabelle date."""

    def __init__(self, tabelle: Tabelle) -> None:
        self._t = tabelle

    def chiudi(self, chiaro: str | bytes) -> str:
        """Da JSON a ``F`` + Base64."""
        grezzo = chiaro.encode("utf-8") if isinstance(chiaro, str) else chiaro
        cifrato = _cifra_cbc(self._t, _pkcs7(grezzo))
        return "F" + base64.b64encode(cifrato).decode("ascii")

    def apri(self, busta: str) -> bytes:
        """Da ``F`` + Base64 ai byte del JSON."""
        testo = "".join(busta.split()).replace("-", "+").replace("_", "/")
        if not testo:
            raise ErroreBusta("Busta vuota")
        testo = testo.removeprefix("F")
        if resto := len(testo) % 4:
            testo += "=" * (4 - resto)
        try:
            cifrato = base64.b64decode(testo)
        except Exception as err:
            raise ErroreBusta(f"Base64 non valido nella busta: {err}") from err
        if not cifrato or len(cifrato) % 16 != 0:
            raise ErroreBusta(f"Lunghezza del cifrato non valida: {len(cifrato)}")
        return _togli_pkcs7(_decifra_cbc(self._t, cifrato))
