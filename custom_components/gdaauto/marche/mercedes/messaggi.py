"""I messaggi protobuf del cloud Mercedes, campo per campo.

I numeri dei campi vengono dai ``.proto`` dell'app Mercedes come li pubblica
ReneNulschDE/mbapi2020 (licenza MIT, ``proto/*_pb2.py``): qui sono riscritti
a mano, solo quelli che GdAAuto usa. Tre famiglie:

- ``VEPUpdate``: lo stato dell'auto, una mappa «nome dell'attributo → valore»
  (``soc``, ``rangeelectric``, ``doorlockstatusvehicle``…). È quello che
  restituisce il REST ``vehicleattributes``, e che il websocket spinge;
- ``ClientMessage``: quello che si manda al websocket, cioè i comandi e le
  ricevute («ack») dei messaggi ricevuti;
- ``PushMessage``: quello che il websocket manda, tra cui l'esito dei comandi.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import protobuf as pb

# --- VEPUpdate e VehicleAttributeStatus ---------------------------------------------

_VEP_VIN = 2
_VEP_EMESSO_MS = 14
_VEP_ATTRIBUTI = 11  # map<string, VehicleAttributeStatus>

_A_TS = 1
_A_TS_MS = 10
_A_STATO = 3
_A_DISPLAY = 11
_A_INT = 4
_A_BOOL = 5
_A_TESTO = 6
_A_DOUBLE = 7
_A_NIL = 8
_A_PROGRAMMI = 31  # ChargeProgramsValue
_A_PRESE = 85  # ChargeInlets

#: Le unità (oneof display_unit): numero del campo → nome corto.
_A_UNITA = {
    12: "combustibile",
    13: "gas",
    14: "consumo_elettrico",
    16: "temperatura",
    17: "pressione",
    18: "rapporto",
    19: "ora",
    25: "velocita",
    26: "distanza",
}

# I valori delle unità che servono (VehicleAttributeStatus.*Unit).
KM, MIGLIA = 1, 2
KPA, BAR, PSI = 1, 2, 3
CELSIUS, FAHRENHEIT = 1, 2

# Lo stato di un attributo: 0 valido; 3 «non ricevuto» e 4 «non disponibile»
# l'app li mostra come sconosciuti (mbapi2020 fa lo stesso).
STATI_SENZA_VALORE = (3, 4)

# ChargeInletsEntry.ChargeInletsCouplerState
SPINA_SCONOSCIUTA, SPINA_INSERITA, SPINA_INSERITA_IN_AUTO, SPINA_STACCATA, SPINA_GUASTA = range(5)


def _programmi(dati: bytes) -> list[dict[str, int]]:
    """ChargeProgramsValue: 1 = ripetuto ChargeProgramParameters (1 programma, 2 max_soc)."""
    elenco = []
    for voce in pb.per_numero(dati).get(1, []):
        c = pb.per_numero(pb.annidato(voce))
        elenco.append(
            {
                "programma": pb.intero(c[1][-1]) if 1 in c else 0,
                "max_soc": pb.intero(c[2][-1]) if 2 in c else 0,
            }
        )
    return elenco


def _prese(dati: bytes) -> list[dict[str, int]]:
    """ChargeInlets: 1 = ripetuto ChargeInletsEntry (1 id, 2 coupler_state, 3 lock_state)."""
    elenco = []
    for voce in pb.per_numero(dati).get(1, []):
        c = pb.per_numero(pb.annidato(voce))
        elenco.append(
            {
                "id": pb.intero(c[1][-1]) if 1 in c else 0,
                "spina": pb.intero(c[2][-1]) if 2 in c else SPINA_SCONOSCIUTA,
                "blocco": pb.intero(c[3][-1]) if 3 in c else 0,
            }
        )
    return elenco


def attributo(dati: bytes) -> dict[str, Any]:
    """Un VehicleAttributeStatus in un dizionario semplice.

    Chiavi: ``valore`` (int, bool, str o float, se c'è), ``display``,
    ``stato``, ``ts`` (secondi), ``ts_ms``, ``nil``, ``unita`` e, per i due
    attributi strutturati che servono, ``programmi`` e ``prese``.
    """
    a: dict[str, Any] = {"stato": 0, "unita": {}}
    for numero, _tipo, v in pb.campi(dati):
        if numero == _A_TS:
            a["ts"] = pb.intero(v)
        elif numero == _A_TS_MS:
            a["ts_ms"] = pb.intero(v)
        elif numero == _A_STATO:
            a["stato"] = pb.intero(v)
        elif numero == _A_DISPLAY:
            a["display"] = pb.testo(v)
        elif numero == _A_INT:
            a["valore"] = pb.intero(v)
        elif numero == _A_BOOL:
            a["valore"] = pb.booleano(v)
        elif numero == _A_TESTO:
            a["valore"] = pb.testo(v)
        elif numero == _A_DOUBLE:
            a["valore"] = pb.doppio(v)
        elif numero == _A_NIL:
            a["nil"] = pb.booleano(v)
        elif numero in _A_UNITA:
            a["unita"][_A_UNITA[numero]] = pb.intero(v)
        elif numero == _A_PROGRAMMI:
            a["programmi"] = _programmi(pb.annidato(v))
        elif numero == _A_PRESE:
            a["prese"] = _prese(pb.annidato(v))
        # Gli altri (profili settimanali, allarmi, VSU…) a GdAAuto non servono.
    return a


def _voce_mappa(dati: bytes) -> tuple[int | bytes | None, bytes]:
    """Una voce di una map<…>: 1 = chiave, 2 = valore."""
    chiave: int | bytes | None = None
    valore = b""
    for numero, _tipo, v in pb.campi(dati):
        if numero == 1:
            chiave = v
        elif numero == 2:
            valore = pb.annidato(v)
    return chiave, valore


@dataclass
class Aggiornamento:
    """Un VEPUpdate: l'auto e i suoi attributi per nome."""

    vin: str = ""
    emesso_ms: int | None = None
    attributi: dict[str, dict[str, Any]] = field(default_factory=dict)


def vep(dati: bytes) -> Aggiornamento:
    agg = Aggiornamento()
    for numero, _tipo, v in pb.campi(dati):
        if numero == _VEP_VIN:
            agg.vin = pb.testo(v)
        elif numero == _VEP_EMESSO_MS:
            agg.emesso_ms = pb.intero(v)
        elif numero == _VEP_ATTRIBUTI:
            chiave, valore = _voce_mappa(pb.annidato(v))
            if isinstance(chiave, bytes):
                agg.attributi[pb.testo(chiave)] = attributo(valore)
    return agg


# --- ClientMessage: i comandi -----------------------------------------------------------

_CM_COMANDO = 3  # ClientMessage.commandRequest
_CR_VIN = 1
_CR_RICHIESTA = 7

# CommandRequest: il numero di ogni comando nell'oneof «command».
CHIUDI = 5  # DoorsLock
APRI = 6  # DoorsUnlock
CLIMA_AVVIA = 21  # ZEVPreconditioningStart
CLIMA_FERMA = 22  # ZEVPreconditioningStop
TROVA = 35  # SigPosStart
PROGRAMMA_RICARICA = 34  # ChargeProgramConfigure
CONFIGURA_RICARICA = 72  # ChargingConfigure

# ZEVPreconditioningType
CLIMA_SUBITO = 1  # «immediate»
CLIMA_ORA = 3  # «now», per le auto che hanno la funzione precondNow


def comando(vin: str, richiesta: str, numero: int, corpo: bytes) -> bytes:
    """ClientMessage{commandRequest{vin, request_id, <comando>}}."""
    richiesta_comando = pb.messaggio([(_CR_VIN, vin), (_CR_RICHIESTA, richiesta), (numero, corpo)])
    return pb.messaggio([(_CM_COMANDO, richiesta_comando)])


def corpo_chiudi() -> bytes:
    return b""  # DoorsLock senza porte: tutte


def corpo_apri(pin: str) -> bytes:
    return pb.messaggio([(1, pin)])  # DoorsUnlock.pin


def corpo_clima_avvia(tipo: int) -> bytes:
    return pb.messaggio([(2, tipo)])  # departure_time (1) a zero: subito


def corpo_clima_ferma() -> bytes:
    return pb.messaggio([(2, CLIMA_ORA)])


def corpo_trova() -> bytes:
    # SigPosStart: solo luci (tipo 0), anabbaglianti (light_type 1), otto secondi.
    return pb.messaggio([(3, 1), (4, 8), (5, 0)])


def _int32(valore: int) -> bytes:
    return pb.messaggio([(1, valore)])  # google.protobuf.Int32Value


def corpo_programma_ricarica(programma: int, max_soc: int) -> bytes:
    return pb.messaggio([(1, programma), (2, _int32(max_soc))])


def corpo_configura_ricarica(max_soc: int) -> bytes:
    return pb.messaggio([(5, _int32(max_soc))])


# --- PushMessage: quello che arriva dal websocket ------------------------------------------

#: PushMessage, campo dell'oneof → (campo della ricevuta in ClientMessage, campo del numero di sequenza).
_RICEVUTE: dict[int, tuple[int, int]] = {
    2: (22, 2),  # vepUpdates → acknowledge_vep_updates_by_vin
    9: (13, 1),  # service_status_updates → acknowledge_service_status_update
    10: (10, 1),  # user_data_update
    11: (11, 1),  # user_picture_update
    12: (12, 1),  # user_pin_update
    15: (17, 1),  # vehicle_updated
    16: (18, 1),  # preferred_dealer_change
    17: (19, 1),  # apptwin_command_status_updates_by_vin
    21: (26, 3),  # data_change_event
    24: (28, 1),  # vehicle_status_updates
}
#: Quelli che si confermano con un messaggio vuoto.
_RICEVUTE_VUOTE = {
    18: 21,  # apptwin_pending_command_request → apptwin_pending_commands_response
    19: 23,  # assigned_vehicles → acknowledge_assigned_vehicles
}
PUSH_VEP = 2
PUSH_ESITI = 17
_TIPI_PUSH = {1, 2, 3, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 24}


@dataclass
class Push:
    tipo: int = 0
    corpo: bytes = b""


def push(dati: bytes) -> Push:
    for numero, tipo, v in pb.campi(dati):
        if numero in _TIPI_PUSH and tipo == pb.LUNGHEZZA:
            return Push(numero, pb.annidato(v))
    return Push()


def ricevuta(p: Push) -> bytes | None:
    """La ricevuta da rimandare, se quel messaggio ne vuole una."""
    if p.tipo in _RICEVUTE_VUOTE:
        return pb.messaggio([(_RICEVUTE_VUOTE[p.tipo], b"")])
    if p.tipo not in _RICEVUTE:
        return None
    campo, campo_sequenza = _RICEVUTE[p.tipo]
    valori = pb.per_numero(p.corpo).get(campo_sequenza)
    sequenza = pb.intero(valori[-1]) if valori else 0
    return pb.messaggio([(campo, pb.messaggio([(1, sequenza)]))])


# VehicleAPI.CommandState
IN_CORSO = {1, 2, 3, 4, 7, 8, 9, 10, 11, 12}
FINITO = 5
FALLITO = 6


@dataclass
class Esito:
    """Un AppTwinCommandStatus: a che punto è un comando."""

    vin: str
    richiesta: str
    stato: int
    errori: list[tuple[str, str]] = field(default_factory=list)
    tentativi_pin: int = 0
    blocco_secondi: int = 0


def _esito(vin: str, dati: bytes) -> Esito:
    e = Esito(vin=vin, richiesta="", stato=0)
    for numero, _tipo, v in pb.campi(dati):
        if numero == 2:
            e.richiesta = pb.testo(v)
        elif numero == 8:
            e.stato = pb.intero(v)
        elif numero == 6:
            e.tentativi_pin = pb.intero(v)
        elif numero == 5:
            e.blocco_secondi = pb.intero(v)
        elif numero == 4:
            c = pb.per_numero(pb.annidato(v))
            codice = pb.testo(c[1][-1]) if 1 in c else ""
            spiegazione = pb.testo(c[2][-1]) if 2 in c else ""
            e.errori.append((codice, spiegazione))
    return e


def esiti(corpo: bytes) -> list[Esito]:
    """AppTwinCommandStatusUpdatesByVIN → per VIN → per processo → AppTwinCommandStatus."""
    trovati = []
    for numero, _tipo, v in pb.campi(corpo):
        if numero != 2:
            continue
        chiave, per_pid = _voce_mappa(pb.annidato(v))
        vin = pb.testo(chiave) if isinstance(chiave, bytes) else ""
        for numero_pid, _t, voce in pb.campi(per_pid):
            if numero_pid == 2:
                _pid, stato = _voce_mappa(pb.annidato(voce))
                trovati.append(_esito(vin, stato))
    return trovati
