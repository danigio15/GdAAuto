"""L'auto, qualunque marca sia.

Questo è il contratto di GdAAuto: ogni marca legge il suo cloud a modo suo e
consegna uno ``StatoVettura``. Da qui in su — i sensori, GdAHome, gdanav —
nessuno sa più se è una Leapmotor, una BYD o una Tesla.

``None`` vuol sempre dire «questa marca non lo dice», non «zero».
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Any


class StatoRicarica(IntEnum):
    NON_IN_CARICA = 0
    IN_CARICA = 1
    COMPLETATA = 2
    ERRORE = 3
    PROGRAMMATA = 4
    RIGENERAZIONE = 5
    IN_PAUSA = 6

    @property
    def nome(self) -> str:
        return self.name.lower()


class Marcia(IntEnum):
    PARCHEGGIO = 0
    AVANTI = 1
    FOLLE = 2
    RETROMARCIA = 3

    @property
    def nome(self) -> str:
        return self.name.lower()


@dataclass
class StatoVettura:
    """Quello che sappiamo dell'auto adesso. ``None`` è «il modello non lo dice»."""

    batteria: float | None = None
    autonomia_km: float | None = None
    odometro_km: float | None = None
    velocita_kmh: float | None = None
    marcia: Marcia | None = None
    stato_ricarica: StatoRicarica | None = None
    cavo_collegato: bool | None = None
    tensione_v: float | None = None
    corrente_a: float | None = None
    tempo_ricarica_min: int | None = None
    energia_residua_kwh: float | None = None
    temperatura_batteria_c: float | None = None
    temperatura_interna_c: float | None = None
    temperatura_esterna_c: float | None = None
    limite_ricarica: int | None = None
    clima_acceso: bool | None = None
    temperatura_clima_c: float | None = None
    chiusa: bool | None = None
    portiere_aperte: bool | None = None
    bagagliaio_aperto: bool | None = None
    finestrini_aperti: bool | None = None
    pneumatici_kpa: dict[str, float] = field(default_factory=dict)
    quadro_acceso: bool | None = None
    sentinella: bool | None = None
    latitudine: float | None = None
    longitudine: float | None = None
    posizione_con_segno: bool = False
    letto: datetime | None = None
    #: quando la marca dice già la potenza di ricarica, o quella in marcia
    potenza_ricarica_diretta_kw: float | None = None
    potenza_diretta_kw: float | None = None
    #: l'auto dorme: i dati sono quelli di quando si è addormentata
    in_sonno: bool | None = None
    #: l'autonomia di omologazione (WLTP o CLTC) con la carica di adesso
    autonomia_nominale_km: float | None = None
    #: ibride con range extender: il serbatoio e le autonomie a benzina e totale
    carburante_percento: float | None = None
    autonomia_carburante_km: float | None = None
    autonomia_totale_km: float | None = None
    #: il consumo medio delle ultime settimane, per chi pianifica un viaggio
    consumo_medio_kwh_100km: float | None = None
    ricarica_rapida: bool | None = None
    #: l'auto sta dando corrente fuori (Vehicle-to-Load)
    v2l_attivo: bool | None = None
    batteria_in_riscaldamento: bool | None = None
    #: almeno una gomma in allarme (pressione o sensore)
    pneumatici_allarme: bool | None = None
    freno_stazionamento: bool | None = None
    antifurto: bool | None = None
    sbrinamento: bool | None = None
    #: la ricarica programmata: accesa, e dalle/alle «HH:MM»
    ricarica_programmata: bool | None = None
    ricarica_ora_inizio: str | None = None
    ricarica_ora_fine: str | None = None
    #: la potenza del riscaldatore (abitacolo e batteria), in W
    potenza_riscaldatore_w: float | None = None
    #: l'energia di oggi, divisa per dove è andata: guida, clima, il resto
    energia_oggi_guida_kwh: float | None = None
    energia_oggi_clima_kwh: float | None = None
    energia_oggi_altro_kwh: float | None = None
    #: gli ultimi sette giorni: km fatti ed energia usata per farli
    km_7_giorni: float | None = None
    energia_7_giorni_kwh: float | None = None
    #: l'ultima ricarica: quanta energia, AC o DC, quando
    ultima_ricarica_kwh: float | None = None
    ultima_ricarica_tipo: str | None = None
    ultima_ricarica_inizio: datetime | None = None
    ultima_ricarica_fine: datetime | None = None
    grezzo: dict[str, Any] = field(default_factory=dict)

    # --- quello che si ricava --------------------------------------------

    @property
    def in_marcia(self) -> bool:
        """Si muove, o è in marcia: i dati vanno chiesti più spesso."""
        if self.velocita_kmh and self.velocita_kmh > 0:
            return True
        return self.marcia in (Marcia.AVANTI, Marcia.RETROMARCIA)

    @property
    def in_carica(self) -> bool | None:
        if self.stato_ricarica is None:
            return None
        return self.stato_ricarica == StatoRicarica.IN_CARICA and not self.in_marcia

    @property
    def potenza_batteria_kw(self) -> float | None:
        """Positiva quando l'auto consuma, negativa quando carica."""
        if self.tensione_v is None or self.corrente_a is None:
            return None
        return round(self.tensione_v * self.corrente_a / 1000, 3)

    @property
    def potenza_ricarica_kw(self) -> float | None:
        if self.potenza_ricarica_diretta_kw is not None:
            return self.potenza_ricarica_diretta_kw
        p = self.potenza_batteria_kw
        if p is None:
            return None
        return round(-p, 3) if p < 0 and self.in_carica else 0.0

    @property
    def potenza_kw(self) -> float | None:
        """La potenza che l'auto sta usando per muoversi (0 da ferma)."""
        if self.potenza_diretta_kw is not None:
            return max(self.potenza_diretta_kw, 0.0)
        p = self.potenza_batteria_kw
        if p is None:
            return None
        return p if p > 0 else 0.0


@dataclass
class Vettura:
    """L'anagrafica dell'auto: chi è, di che marca, e quello che si sa adesso."""

    vin: str
    marca: str
    modello: str
    nome: str = ""
    anno: int | None = None
    targa: str = ""
    colore: str = ""
    condivisa: bool = False
    #: quello che serve solo alla marca (id interni, diritti, percorsi)
    extra: dict[str, Any] = field(default_factory=dict)
    stato: StatoVettura | None = None

    @property
    def titolo(self) -> str:
        return self.nome or f"{self.marca.capitalize()} {self.modello}"


class MemoriaEmisfero:
    """Il cloud a volte manda la posizione senza segno. Chi ha visto una volta
    un ovest o un sud se lo ricorda, e lo rimette alle letture dopo."""

    def __init__(self) -> None:
        self._segni: dict[str, tuple[int, int]] = {}

    def correggi(self, vin: str, stato: StatoVettura) -> None:
        if stato.latitudine is None or stato.longitudine is None:
            return
        if stato.posizione_con_segno or stato.latitudine < 0 or stato.longitudine < 0:
            self._segni[vin] = (1 if stato.latitudine >= 0 else -1, 1 if stato.longitudine >= 0 else -1)
            return
        lat, lon = self._segni.get(vin, (1, 1))
        stato.latitudine = abs(stato.latitudine) * lat
        stato.longitudine = abs(stato.longitudine) * lon
