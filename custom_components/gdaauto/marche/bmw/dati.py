"""Dai «descrittori» di BMW CarData allo stato di GdAAuto.

CarData non ha una risposta fissa: si sceglie un elenco di descrittori del
catalogo telematico di BMW (``vehicle.drivetrain.electricEngine.charging.level``
e così via), lo si mette in un «container», e il cloud risponde con un valore
per ognuno: ``{"value": "57", "unit": "%", "timestamp": "2026-…Z"}``. I valori
arrivano come testo, a volte in miglia o in bar, e gli stati con le parole di
BMW (``CHARGINGACTIVE``, ``SECURED``, ``OPEN``…). Qui diventano uno
``StatoVettura`` in km, kW e kPa.

I nomi dei descrittori sono quelli del catalogo pubblico di BMW (Telematics
Data Catalogue); la scelta e le parole possibili sono state controllate sui
progetti JustChr/BavarianData (MIT) e kvanbiesen/bmw-cardata-ha (BSD): vedi
RINGRAZIAMENTI.md.
"""

from __future__ import annotations

from datetime import datetime
import json
import re
from typing import Any

from ...vettura import StatoRicarica, StatoVettura

MIGLIO_KM = 1.609344
PSI_KPA = 6.894757

# --- i descrittori -------------------------------------------------------------

D_SOC = "vehicle.drivetrain.batteryManagement.header"
D_SOC_PREVISTO = "vehicle.drivetrain.electricEngine.charging.level"
D_SOC_VIAGGIO = "vehicle.trip.segment.end.drivetrain.batteryManagement.hvSoc"
D_AUTONOMIA = "vehicle.drivetrain.electricEngine.remainingElectricRange"
D_ODOMETRO = "vehicle.vehicle.travelledDistance"
D_RICARICA = "vehicle.drivetrain.electricEngine.charging.status"
D_RICARICA_HV = "vehicle.drivetrain.electricEngine.charging.hvStatus"
D_SPINA = "vehicle.powertrain.tractionBattery.charging.port.anyPosition.isPlugged"
D_PRESA = "vehicle.body.chargingPort.status"
D_POTENZA = "vehicle.powertrain.electric.battery.charging.power"
D_VOLT_AC = "vehicle.drivetrain.electricEngine.charging.acVoltage"
D_AMPERE_AC = "vehicle.drivetrain.electricEngine.charging.acAmpere"
D_FASI = "vehicle.drivetrain.electricEngine.charging.phaseNumber"
D_AL_PIENO = "vehicle.drivetrain.electricEngine.charging.timeToFullyCharged"
D_RESTANTE = "vehicle.drivetrain.electricEngine.charging.timeRemaining"
D_LIMITE = "vehicle.powertrain.electric.battery.stateOfCharge.target"
D_SERRATURA = "vehicle.cabin.door.lock.status"
D_PORTE = "vehicle.cabin.door.status"
D_PORTIERE = tuple(f"vehicle.cabin.door.row{r}.{lato}.isOpen" for r in (1, 2) for lato in ("driver", "passenger"))
D_BAGAGLIAIO = ("vehicle.body.trunk.isOpen", "vehicle.body.trunk.door.isOpen")
D_FINESTRINI = tuple(f"vehicle.cabin.window.row{r}.{lato}.status" for r in (1, 2) for lato in ("driver", "passenger"))
D_TETTO = "vehicle.cabin.sunroof.overallStatus"
D_LAT = "vehicle.cabin.infotainment.navigation.currentLocation.latitude"
D_LON = "vehicle.cabin.infotainment.navigation.currentLocation.longitude"
D_QUADRO = "vehicle.drivetrain.engine.isActive"

_RUOTE = {
    "anteriore_sinistro": "vehicle.chassis.axle.row1.wheel.left.tire.pressure",
    "anteriore_destro": "vehicle.chassis.axle.row1.wheel.right.tire.pressure",
    "posteriore_sinistro": "vehicle.chassis.axle.row2.wheel.left.tire.pressure",
    "posteriore_destro": "vehicle.chassis.axle.row2.wheel.right.tire.pressure",
}

#: Quelli che BavarianData tiene già in un container che funziona: se BMW
#: rifiuta il container intero (succede, con un descrittore che non serve più),
#: si riparte da questi.
DESCRITTORI_RIDOTTI: tuple[str, ...] = (
    D_SOC,
    D_SOC_PREVISTO,
    D_AUTONOMIA,
    D_RICARICA,
    D_RICARICA_HV,
    D_SPINA,
    D_POTENZA,
    D_VOLT_AC,
    D_AMPERE_AC,
    D_FASI,
    D_AL_PIENO,
    D_RESTANTE,
    D_LIMITE,
    D_SERRATURA,
    D_ODOMETRO,
)

#: Tutto quello che GdAAuto chiede, in un container solo: una chiamata, tutto.
DESCRITTORI: tuple[str, ...] = (
    *DESCRITTORI_RIDOTTI,
    D_SOC_VIAGGIO,
    D_PRESA,
    D_PORTE,
    *D_PORTIERE,
    *D_BAGAGLIAIO,
    *D_FINESTRINI,
    D_TETTO,
    D_LAT,
    D_LON,
    D_QUADRO,
    *_RUOTE.values(),
)

# --- le parole di BMW ----------------------------------------------------------------

_RICARICHE = {
    "NOCHARGING": StatoRicarica.NON_IN_CARICA,
    "INITIALIZATION": StatoRicarica.IN_CARICA,
    "CHARGINGACTIVE": StatoRicarica.IN_CARICA,
    "CHARGINGPAUSED": StatoRicarica.IN_PAUSA,
    "CHARGINGENDED": StatoRicarica.COMPLETATA,
    "CHARGINGERROR": StatoRicarica.ERRORE,
    "CHARGINGINTERRUPTED": StatoRicarica.IN_PAUSA,
    "CHARGINGDISRUPTED": StatoRicarica.ERRORE,
}
_RICARICHE_HV = {
    "CHARGING": StatoRicarica.IN_CARICA,
    "NOTCHARGING": StatoRicarica.NON_IN_CARICA,
    "WAITINGFORCHARGING": StatoRicarica.PROGRAMMATA,
    "FINISHEDFULLYCHARGED": StatoRicarica.COMPLETATA,
    "FINISHEDNOTFULL": StatoRicarica.COMPLETATA,
    "ERROR": StatoRicarica.ERRORE,
}
_CHIUSA = {"LOCKED", "SECURED"}
_APERTA = {"UNLOCKED", "SELECTIVELOCKED"}
_APERTO = {"OPEN", "INTERMEDIATE", "OPENTILT", "INTERMEDIATETILT", "TRUE", "ISTRUE"}
_CHIUSO = {"CLOSED", "FALSE", "ISFALSE"}
_VERO = {"TRUE", "ISTRUE", "1", "YES", "ON", "CONNECTED"}
_FALSO = {"FALSE", "ISFALSE", "0", "NO", "OFF", "DISCONNECTED"}


def parola(valore: Any) -> str | None:
    """La parola di BMW, senza maiuscole, trattini né il prefisso ``ASN_``:
    ``selective-Locked``, ``SELECTIVE_LOCKED`` e ``ASN_isTrue`` si capiscono uguali."""
    if isinstance(valore, bool):
        return "TRUE" if valore else "FALSE"
    if not isinstance(valore, str | int | float):
        return None
    testo = str(valore).strip().upper()
    testo = testo.removeprefix("ASN_")
    return re.sub(r"[^A-Z0-9]", "", testo) or None


def valori(risposta: Any) -> dict[str, dict[str, Any]]:
    """Le voci della risposta di ``telematicData``, per descrittore.

    Il cloud risponde ``{"telematicData": {descrittore: {value, unit, timestamp}}}``;
    la documentazione mostra anche voci con ``name``: vanno bene tutte e due.
    """
    if isinstance(risposta, dict) and "telematicData" in risposta:
        risposta = risposta["telematicData"]
    voci: dict[str, dict[str, Any]] = {}
    if isinstance(risposta, dict):
        for nome, voce in risposta.items():
            if isinstance(voce, dict):
                voci[str(nome)] = voce
    elif isinstance(risposta, list):
        for voce in risposta:
            if isinstance(voce, dict) and voce.get("name"):
                voci[str(voce["name"])] = voce
    return voci


class _Lettore:
    """Legge le voci una per una, con le unità di misura."""

    def __init__(self, voci: dict[str, dict[str, Any]]) -> None:
        self.voci = voci

    def grezzo(self, d: str) -> Any:
        voce = self.voci.get(d)
        return voce.get("value") if voce else None

    def unita(self, d: str) -> str:
        voce = self.voci.get(d) or {}
        return str(voce.get("unit") or "").strip().lower()

    def numero(self, d: str) -> float | None:
        v = self.grezzo(d)
        if v is None or isinstance(v, bool):
            return None
        try:
            n = float(str(v).strip().replace(",", "."))
        except ValueError:
            return None
        return n if n == n else None  # NaN no

    def intero(self, d: str) -> int | None:
        n = self.numero(d)
        return round(n) if n is not None else None

    def km(self, d: str) -> float | None:
        n = self.numero(d)
        if n is None:
            return None
        if self.unita(d) in ("mi", "mile", "miles"):
            n *= MIGLIO_KM
        return round(n, 1)

    def kpa(self, d: str) -> float | None:
        n = self.numero(d)
        if n is None or n <= 0:
            return None
        unita = self.unita(d)
        if unita == "bar":
            n *= 100
        elif unita == "psi":
            n *= PSI_KPA
        return round(n, 1)

    def parola(self, d: str) -> str | None:
        return parola(self.grezzo(d))

    def vero(self, d: str) -> bool | None:
        p = self.parola(d)
        if p in _VERO:
            return True
        if p in _FALSO:
            return False
        return None

    def aperti(self, *descrittori: str) -> bool | None:
        """Aperto se uno è aperto; chiuso se tutti quelli che si sanno sono chiusi."""
        noti = []
        for d in descrittori:
            p = self.parola(d)
            if p in _APERTO:
                return True
            if p in _CHIUSO:
                noti.append(False)
        return False if noti else None

    def primo(self, *descrittori: str) -> float | None:
        for d in descrittori:
            if (n := self.numero(d)) is not None:
                return n
        return None

    def letto(self) -> datetime | None:
        quando = []
        for voce in self.voci.values():
            t = voce.get("timestamp")
            if not isinstance(t, str) or not t:
                continue
            try:
                quando.append(datetime.fromisoformat(t))
            except ValueError:
                continue
        quando = [q for q in quando if q.tzinfo is not None]
        return max(quando) if quando else None


def _chiusa(r: _Lettore) -> bool | None:
    p = r.parola(D_SERRATURA)
    if p in _CHIUSA:
        return True
    if p in _APERTA:
        return False
    # Lo stato «porte» a volte arriva come oggetto: {"allDoorsLocked": "ASN_isTrue", …}.
    porte = r.grezzo(D_PORTE)
    if isinstance(porte, str) and porte.strip().startswith("{"):
        try:
            porte = json.loads(porte)
        except ValueError:
            porte = None
    if isinstance(porte, dict):
        tutte = parola(porte.get("allDoorsLocked"))
        if tutte in _VERO:
            return True
        if tutte in _FALSO:
            return False
        porte = porte.get("newDoorStatus")
    p = parola(porte)
    if p in _CHIUSA:
        return True
    if p in _APERTA:
        return False
    return None


def _ricarica(r: _Lettore, s: StatoVettura) -> None:
    s.stato_ricarica = _RICARICHE.get(r.parola(D_RICARICA) or "") or _RICARICHE_HV.get(r.parola(D_RICARICA_HV) or "")

    spina = r.vero(D_SPINA)
    if spina is None:
        spina = r.vero(D_PRESA)
    s.cavo_collegato = spina
    if spina is False and s.stato_ricarica in (
        StatoRicarica.COMPLETATA,
        StatoRicarica.IN_PAUSA,
        StatoRicarica.PROGRAMMATA,
        StatoRicarica.IN_CARICA,
    ):
        # Senza cavo la carica è finita, anche se l'ultimo stato dice ancora com'era andata.
        s.stato_ricarica = StatoRicarica.NON_IN_CARICA
    if s.cavo_collegato is None and s.stato_ricarica == StatoRicarica.IN_CARICA:
        s.cavo_collegato = True

    s.limite_ricarica = r.intero(D_LIMITE)

    if s.stato_ricarica == StatoRicarica.IN_CARICA:
        watt = r.numero(D_POTENZA)
        if watt is not None:
            kw = watt / 1000 if r.unita(D_POTENZA) in ("w", "") else watt
            s.potenza_ricarica_diretta_kw = round(abs(kw), 2)
        else:
            volt, ampere = r.numero(D_VOLT_AC), r.numero(D_AMPERE_AC)
            if volt is not None and ampere is not None:
                fasi = r.numero(D_FASI) or 1
                s.potenza_ricarica_diretta_kw = round(volt * ampere * fasi / 1000, 2)
        minuti = r.primo(D_AL_PIENO, D_RESTANTE)
        s.tempo_ricarica_min = round(minuti) if minuti is not None else None
    elif s.stato_ricarica is not None:
        s.potenza_ricarica_diretta_kw = 0.0


def interpreta(risposta: Any) -> StatoVettura:
    """``risposta`` è quella di ``GET /customers/vehicles/{vin}/telematicData``."""
    voci = valori(risposta)
    r = _Lettore(voci)
    # La posizione resta fuori dal grezzo: la diagnostica la toglie solo dai suoi campi.
    s = StatoVettura(grezzo={d: v.get("value") for d, v in voci.items() if d not in (D_LAT, D_LON)})

    s.batteria = r.primo(D_SOC, D_SOC_PREVISTO, D_SOC_VIAGGIO)
    s.autonomia_km = r.km(D_AUTONOMIA)
    s.odometro_km = r.km(D_ODOMETRO)
    _ricarica(r, s)

    s.chiusa = _chiusa(r)
    s.portiere_aperte = r.aperti(*D_PORTIERE)
    s.bagagliaio_aperto = r.aperti(*D_BAGAGLIAIO)
    s.finestrini_aperti = r.aperti(*D_FINESTRINI, D_TETTO)
    s.quadro_acceso = r.vero(D_QUADRO)

    lat, lon = r.numero(D_LAT), r.numero(D_LON)
    if lat is not None and lon is not None and (lat, lon) != (0.0, 0.0):
        s.latitudine, s.longitudine = lat, lon
        s.posizione_con_segno = True

    for ruota, d in _RUOTE.items():
        if (kpa := r.kpa(d)) is not None:
            s.pneumatici_kpa[ruota] = kpa

    s.letto = r.letto()
    return s
