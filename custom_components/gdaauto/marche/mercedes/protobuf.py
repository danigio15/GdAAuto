"""Il formato «wire» di protobuf, quanto basta per Mercedes.

Il cloud Mercedes parla protobuf, ma la libreria ``protobuf`` non c'è di
sicuro in Home Assistant e GdAAuto non aggiunge dipendenze. Il formato sul
filo però è semplice: ogni campo è un numero col suo «tipo di filo» e il
valore. Qui ci sono solo i quattro tipi di filo che servono:

- 0, varint: interi e booleani, sette bit per byte;
- 1, 64 bit: i ``double`` (posizione, potenza, pressioni);
- 2, lunghezza e byte: testi, messaggi annidati, voci delle mappe;
- 5, 32 bit: i ``float`` (non li usa nessuno, ma si saltano bene).

Quello che i campi *vogliono dire* (quale numero è il VIN, quale la
batteria) sta in ``messaggi.py``: qui non si sa niente di auto.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
import struct

VARINT = 0
FISSO64 = 1
LUNGHEZZA = 2
FISSO32 = 5

_DUE_ALLA_64 = 1 << 64
_DUE_ALLA_63 = 1 << 63


class ErroreProtobuf(ValueError):
    """I byte non sono un messaggio protobuf che si sappia leggere."""


def _varint(dati: bytes, i: int) -> tuple[int, int]:
    valore = 0
    spostamento = 0
    while True:
        if i >= len(dati):
            raise ErroreProtobuf("varint troncato")
        b = dati[i]
        i += 1
        valore |= (b & 0x7F) << spostamento
        if not b & 0x80:
            return valore, i
        spostamento += 7
        if spostamento >= 70:
            raise ErroreProtobuf("varint troppo lungo")


def campi(dati: bytes) -> Iterator[tuple[int, int, int | bytes]]:
    """Uno dopo l'altro: (numero del campo, tipo di filo, valore).

    Il valore è un intero per i varint, i byte grezzi per tutto il resto.
    """
    i = 0
    while i < len(dati):
        chiave, i = _varint(dati, i)
        numero, tipo = chiave >> 3, chiave & 7
        if numero == 0:
            raise ErroreProtobuf("campo numero 0")
        if tipo == VARINT:
            valore, i = _varint(dati, i)
            yield numero, tipo, valore
        elif tipo == FISSO64:
            if i + 8 > len(dati):
                raise ErroreProtobuf("64 bit troncati")
            yield numero, tipo, dati[i : i + 8]
            i += 8
        elif tipo == LUNGHEZZA:
            lunghezza, i = _varint(dati, i)
            if i + lunghezza > len(dati):
                raise ErroreProtobuf("campo più lungo del messaggio")
            yield numero, tipo, dati[i : i + lunghezza]
            i += lunghezza
        elif tipo == FISSO32:
            if i + 4 > len(dati):
                raise ErroreProtobuf("32 bit troncati")
            yield numero, tipo, dati[i : i + 4]
            i += 4
        else:
            # I gruppi (3 e 4) non li usa più nessuno: se arrivano, non è il nostro messaggio.
            raise ErroreProtobuf(f"tipo di filo {tipo} non previsto")


def per_numero(dati: bytes) -> dict[int, list[int | bytes]]:
    """Tutti i valori di ogni campo, nell'ordine in cui arrivano."""
    trovati: dict[int, list[int | bytes]] = {}
    for numero, _tipo, valore in campi(dati):
        trovati.setdefault(numero, []).append(valore)
    return trovati


# --- dai valori grezzi ai tipi ----------------------------------------------------


def intero(valore: int | bytes) -> int:
    """int32/int64: i negativi arrivano in complemento a due su 64 bit."""
    if not isinstance(valore, int):
        raise ErroreProtobuf("atteso un varint")
    return valore - _DUE_ALLA_64 if valore >= _DUE_ALLA_63 else valore


def booleano(valore: int | bytes) -> bool:
    return bool(intero(valore))


def doppio(valore: int | bytes) -> float:
    if not isinstance(valore, bytes) or len(valore) != 8:
        raise ErroreProtobuf("atteso un double")
    return float(struct.unpack("<d", valore)[0])


def testo(valore: int | bytes) -> str:
    if not isinstance(valore, bytes):
        raise ErroreProtobuf("atteso un testo")
    return valore.decode("utf-8", errors="replace")


def annidato(valore: int | bytes) -> bytes:
    if not isinstance(valore, bytes):
        raise ErroreProtobuf("atteso un messaggio annidato")
    return valore


# --- scrivere ---------------------------------------------------------------------


def _scrivi_varint(n: int) -> bytes:
    if n < 0:
        n += _DUE_ALLA_64
    fuori = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            fuori.append(b | 0x80)
        else:
            fuori.append(b)
            return bytes(fuori)


def _chiave(numero: int, tipo: int) -> bytes:
    return _scrivi_varint(numero << 3 | tipo)


Valore = int | bool | str | bytes | None


def messaggio(voci: Iterable[tuple[int, Valore]]) -> bytes:
    """Un messaggio dai suoi campi, in ordine di numero come fa protobuf.

    Interi e booleani diventano varint; testi e ``bytes`` (messaggi annidati
    già scritti) diventano lunghezza e byte. Come in proto3, un intero a zero,
    un falso o un testo vuoto non si scrivono; un messaggio annidato vuoto
    (``b""``) sì, perché dice «questo comando, senza parametri».
    """
    fuori = bytearray()
    for numero, valore in sorted(voci, key=lambda v: v[0]):
        if valore is None:
            continue
        if isinstance(valore, bool | int):
            if not valore:
                continue
            fuori += _chiave(numero, VARINT) + _scrivi_varint(int(valore))
        elif isinstance(valore, str):
            if not valore:
                continue
            b = valore.encode()
            fuori += _chiave(numero, LUNGHEZZA) + _scrivi_varint(len(b)) + b
        else:
            fuori += _chiave(numero, LUNGHEZZA) + _scrivi_varint(len(valore)) + valore
    return bytes(fuori)
