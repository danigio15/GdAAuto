"""Un'auto in Home Assistant: la legge, la tiene fresca, la comanda.

Ce n'è una per ogni vettura, e sono di due tipi con la stessa faccia:

- ``AutoCloud``: GdAAuto parla col cloud della marca (Leapmotor oggi). Lo
  interroga più spesso quando l'auto si muove o carica, meno quando è chiusa
  in garage, e mai più di una volta ogni trenta secondi anche se glielo
  chiedono in tanti.
- ``AutoHA``: l'auto ha già un'integrazione in Home Assistant. Non si
  interroga niente: si ascoltano le sue entità e si traduce.

Sopra, i sensori e i comandi di GdAAuto non sanno quale delle due hanno.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfLength, UnitOfPower, UnitOfSpeed
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError, ServiceValidationError
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import DistanceConverter, PowerConverter, SpeedConverter, TemperatureConverter

from . import fonte_ha as f
from .const import (
    AGGIORNA_A_MANO_OGNI,
    COMANDI_ATTIVI_PREDEFINITI,
    COMANDO_APRI,
    COMANDO_CHIUDI,
    COMANDO_CLIMA,
    COMANDO_DESTINAZIONE,
    COMANDO_LIMITE,
    COMANDO_RICARICA,
    COMANDO_TROVA,
    CONF_COMANDI_ATTIVI,
    CONF_INTERVALLO,
    CONF_INTERVALLO_IN_MOTO,
    CONF_PIN,
    CONF_RISPARMIO,
    DOMAIN,
    INTERVALLO_IN_MOTO_PREDEFINITO,
    INTERVALLO_MINIMO,
    INTERVALLO_PREDEFINITO,
    INTERVALLO_RISPARMIO,
    RILEGGI_DOPO_COMANDO,
)
from .marche.errori import ErroreCertificato, ErroreComando, ErroreCredenziali, ErroreMarca, ErrorePin
from .vettura import MemoriaEmisfero, StatoRicarica, StatoVettura, Vettura

_LOGGER = logging.getLogger(__name__)


class Auto(DataUpdateCoordinator[StatoVettura]):
    """Quello che hanno in comune le due fonti."""

    vettura: Vettura

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, vettura: Vettura, intervallo: timedelta | None) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {vettura.titolo}",
            update_interval=intervallo,
            # Tante entità che chiedono «aggiorna» insieme fanno una lettura sola.
            request_refresh_debouncer=Debouncer(
                hass, _LOGGER, cooldown=AGGIORNA_A_MANO_OGNI.total_seconds(), immediate=True
            ),
        )
        self.vettura = vettura

    # --- i comandi --------------------------------------------------------

    @property
    def comandi_possibili(self) -> set[str]:
        """Quello che l'auto sa fare, prima delle scelte dell'utente."""
        raise NotImplementedError

    @property
    def comandi(self) -> set[str]:
        """Quello che l'auto sa fare e l'utente ha acceso nelle opzioni."""
        assert self.config_entry is not None
        attivi = set(self.config_entry.options.get(CONF_COMANDI_ATTIVI, COMANDI_ATTIVI_PREDEFINITI))
        possibili = set(self.comandi_possibili)
        # «Chiudi» c'è se la marca sa chiudere; «apri» è un permesso in più.
        if COMANDO_CHIUDI in possibili:
            attivi.add(COMANDO_CHIUDI)
            if COMANDO_APRI in attivi:
                possibili.add(COMANDO_APRI)
        return possibili & attivi

    def _controlla(self, comando: str) -> None:
        if comando not in self.comandi:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="comando_spento",
                translation_placeholders={"comando": comando},
            )

    async def async_comanda(self, comando: str, **parametri: Any) -> None:
        self._controlla(comando)
        await self._esegui(comando, **parametri)

    async def _esegui(self, comando: str, **parametri: Any) -> None:
        raise NotImplementedError


# --- dal cloud della marca ---------------------------------------------------


@dataclass
class Conto:
    """Un account della marca, con il suo cliente e le sue auto."""

    cliente: Any
    vetture: list[Vettura] = field(default_factory=list)
    emisfero: MemoriaEmisfero = field(default_factory=MemoriaEmisfero)


class AutoCloud(Auto):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, conto: Conto, vettura: Vettura, modulo: Any) -> None:
        predefinito = getattr(modulo, "INTERVALLO_MINUTI", INTERVALLO_PREDEFINITO)
        self._normale = timedelta(minutes=entry.options.get(CONF_INTERVALLO, predefinito))
        in_moto = getattr(modulo, "INTERVALLO_IN_MOTO_SECONDI", INTERVALLO_IN_MOTO_PREDEFINITO)
        minimo = timedelta(seconds=getattr(modulo, "INTERVALLO_MINIMO_SECONDI", INTERVALLO_MINIMO.total_seconds()))
        self._in_moto = max(timedelta(seconds=entry.options.get(CONF_INTERVALLO_IN_MOTO, in_moto)), minimo)
        self._risparmio = entry.options.get(CONF_RISPARMIO, getattr(modulo, "RISPARMIO_PREDEFINITO", True))
        super().__init__(hass, entry, vettura, self._normale)
        self.conto = conto
        self._modulo = modulo
        self._annulla_rilettura: Callable[[], None] | None = None

    @property
    def comandi_possibili(self) -> set[str]:
        return set(self._modulo.comandi_di(self.vettura))

    async def _async_update_data(self) -> StatoVettura:
        try:
            stato = await self.conto.cliente.stato(self.vettura)
        except (ErroreCredenziali, ErroreCertificato) as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ErroreMarca as err:
            raise UpdateFailed(str(err)) from err
        self.conto.emisfero.correggi(self.vettura.vin, stato)
        self.vettura.stato = stato
        self.update_interval = self._ritmo(stato)
        return stato

    def _ritmo(self, s: StatoVettura) -> timedelta:
        """Spesso quando succede qualcosa, piano quando l'auto dorme."""
        if s.in_sonno:
            return self._normale
        if s.in_marcia or s.in_carica or s.quadro_acceso:
            return self._in_moto
        if (
            self._risparmio
            and s.chiusa is True
            and not s.cavo_collegato
            and s.stato_ricarica != StatoRicarica.PROGRAMMATA
        ):
            return max(self._normale, INTERVALLO_RISPARMIO)
        return self._normale

    @property
    def _pin(self) -> str | None:
        assert self.config_entry is not None
        # Le voci fatte prima che il PIN si chiedesse nella configurazione lo hanno nei dati.
        return self.config_entry.options.get(CONF_PIN) or self.config_entry.data.get(CONF_PIN) or None

    async def _esegui(self, comando: str, **p: Any) -> None:
        c, v, pin = self.conto.cliente, self.vettura, self._pin
        try:
            if comando == COMANDO_CHIUDI:
                await c.chiudi(v, pin)
            elif comando == COMANDO_APRI:
                await c.apri(v, pin)
            elif comando == COMANDO_TROVA:
                await c.trova(v, pin)
            elif comando == COMANDO_CLIMA:
                await c.clima(v, bool(p["acceso"]), pin, int(p.get("temperatura") or 22))
            elif comando == COMANDO_RICARICA:
                await c.ricarica(v, bool(p["avvia"]), pin)
            elif comando == COMANDO_LIMITE:
                await c.limite_ricarica(v, int(p["percento"]), pin)
            elif comando == COMANDO_DESTINAZIONE:
                await c.destinazione(v, p["nome"], p["latitudine"], p["longitudine"], p.get("indirizzo") or "")
            else:
                raise ServiceValidationError(f"Comando sconosciuto: {comando}")
        except ErrorePin as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="pin", translation_placeholders={"errore": str(err)}
            ) from err
        except ErroreCredenziali as err:
            assert self.config_entry is not None
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(f"{self.vettura.titolo}: {err}") from err
        except ErroreComando as err:
            raise HomeAssistantError(str(err)) from err
        except ErroreMarca as err:
            raise HomeAssistantError(f"{self.vettura.titolo}: {err}") from err
        self._rileggi_fra_poco()

    @callback
    def _rileggi_fra_poco(self) -> None:
        """Il cloud sa del comando qualche secondo dopo l'auto."""
        if self._annulla_rilettura:
            self._annulla_rilettura()

        async def rileggi(_ora: Any) -> None:
            self._annulla_rilettura = None
            await self.async_refresh()

        self._annulla_rilettura = async_call_later(self.hass, RILEGGI_DOPO_COMANDO, rileggi)

    async def async_shutdown(self) -> None:
        if self._annulla_rilettura:
            self._annulla_rilettura()
            self._annulla_rilettura = None
        await super().async_shutdown()


# --- da un'integrazione che c'è già -----------------------------------------


def _numero(stato: State | None) -> float | None:
    if stato is None or stato.state in (STATE_UNKNOWN, STATE_UNAVAILABLE, ""):
        return None
    try:
        return float(stato.state)
    except ValueError:
        return None


def _converti(stato: State | None, convertitore: Any, verso: str) -> float | None:
    valore = _numero(stato)
    if valore is None or stato is None:
        return None
    unita = stato.attributes.get("unit_of_measurement")
    if not unita or unita == verso:
        return valore
    try:
        return round(convertitore.convert(valore, unita, verso), 3)
    except HomeAssistantError:
        return valore


def _vero(stato: State | None, *si: str) -> bool | None:
    if stato is None or stato.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
        return None
    return stato.state.lower() in {STATE_ON, "true", *si}


# Gli stati testuali della ricarica che le integrazioni usano davvero.
_IN_CARICA = {"charging", "in_carica", "in carica", "laden", "lädt", "en charge", "charge_in_progress", "on"}
_COMPLETATA = {"complete", "completed", "full", "finished", "charged", "completata", "fertig", "terminee"}
_IN_ATTESA = {"scheduled", "waiting", "programmata", "pending", "plugged_waiting", "waiting_for_charging", "starting"}
_CAVO_SI = {"plugged", "connected", "plugged_in", "collegato", "on", "true", "yes", "b", "c"}


class AutoHA(Auto):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, vettura: Vettura, mappa: dict[str, str]) -> None:
        super().__init__(hass, entry, vettura, None)
        self.mappa = {k: v for k, v in mappa.items() if v}
        self._annulla_attesa: Callable[[], None] | None = None

    def _get(self, casella: str) -> State | None:
        entity_id = self.mappa.get(casella)
        return self.hass.states.get(entity_id) if entity_id else None

    def _dominio(self, casella: str) -> str | None:
        entity_id = self.mappa.get(casella)
        return entity_id.split(".", 1)[0] if entity_id else None

    @property
    def comandi_possibili(self) -> set[str]:
        c: set[str] = set()
        if self._dominio(f.SERRATURA) == "lock":
            c.add(COMANDO_CHIUDI)
        if self._dominio(f.CLIMA):
            c.add(COMANDO_CLIMA)
        if self._dominio(f.INTERRUTTORE_RICARICA):
            c.add(COMANDO_RICARICA)
        if self._dominio(f.LIMITE) in ("number", "select"):
            c.add(COMANDO_LIMITE)
        if self._dominio(f.TROVA):
            c.add(COMANDO_TROVA)
        return c

    async def async_ascolta(self) -> None:
        """Si ascoltano le entità dell'auto; più cambi insieme fanno una lettura."""
        assert self.config_entry is not None
        self.config_entry.async_on_unload(
            async_track_state_change_event(self.hass, list(self.mappa.values()), self._cambiata)
        )

    @callback
    def _cambiata(self, _event: Event[EventStateChangedData]) -> None:
        if self._annulla_attesa is None:
            self._annulla_attesa = async_call_later(self.hass, 1.0, self._ricostruisci)

    @callback
    def _ricostruisci(self, _ora: Any) -> None:
        self._annulla_attesa = None
        self.async_set_updated_data(self.leggi())

    async def _async_update_data(self) -> StatoVettura:
        # «Aggiorna» da GdAAuto o da gdanav: si fa rileggere l'integrazione vera.
        if self.data is not None and self.mappa:
            try:
                async with asyncio.timeout(20):
                    await self.hass.services.async_call(
                        "homeassistant", "update_entity", {"entity_id": list(self.mappa.values())}, blocking=True
                    )
            except (HomeAssistantError, TimeoutError) as err:
                _LOGGER.debug("Rilettura di %s non riuscita: %s", self.vettura.titolo, err)
        return self.leggi()

    def leggi(self) -> StatoVettura:
        s = StatoVettura()
        batteria = self._get(f.BATTERIA)
        s.batteria = _numero(batteria)
        s.autonomia_km = _converti(self._get(f.AUTONOMIA), DistanceConverter, UnitOfLength.KILOMETERS)
        s.odometro_km = _converti(self._get(f.ODOMETRO), DistanceConverter, UnitOfLength.KILOMETERS)
        s.velocita_kmh = _converti(self._get(f.VELOCITA), SpeedConverter, UnitOfSpeed.KILOMETERS_PER_HOUR)
        potenza = _converti(self._get(f.POTENZA_RICARICA), PowerConverter, UnitOfPower.KILO_WATT)
        s.temperatura_batteria_c = _converti(self._get(f.TEMPERATURA_BATTERIA), TemperatureConverter, "°C")
        s.temperatura_interna_c = _converti(self._get(f.TEMPERATURA_INTERNA), TemperatureConverter, "°C")
        s.temperatura_esterna_c = _converti(self._get(f.TEMPERATURA_ESTERNA), TemperatureConverter, "°C")
        s.tempo_ricarica_min = self._minuti_alla_fine()

        limite = self._get(f.LIMITE)
        if (n := _numero(limite)) is not None:
            s.limite_ricarica = int(n)
        elif limite is not None and limite.state.rstrip("%").strip().isdigit():
            s.limite_ricarica = int(limite.state.rstrip("%").strip())

        # La ricarica: un binario, o uno stato testuale da leggere.
        carica = self._get(f.IN_CARICA) or self._get(f.INTERRUTTORE_RICARICA)
        if carica is not None and carica.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            testo = carica.state.lower()
            if carica.domain in ("binary_sensor", "switch"):
                s.stato_ricarica = StatoRicarica.IN_CARICA if testo == STATE_ON else StatoRicarica.NON_IN_CARICA
            elif testo in _IN_CARICA:
                s.stato_ricarica = StatoRicarica.IN_CARICA
            elif testo in _COMPLETATA:
                s.stato_ricarica = StatoRicarica.COMPLETATA
            elif testo in _IN_ATTESA:
                s.stato_ricarica = StatoRicarica.PROGRAMMATA
            else:
                s.stato_ricarica = StatoRicarica.NON_IN_CARICA
        cavo = self._get(f.CAVO)
        if cavo is not None and cavo.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            s.cavo_collegato = cavo.state.lower() in _CAVO_SI
        elif s.stato_ricarica is not None and s.stato_ricarica != StatoRicarica.NON_IN_CARICA:
            s.cavo_collegato = True
        if potenza is not None:
            # La potenza la dice già l'integrazione: la si tiene com'è.
            s.potenza_ricarica_diretta_kw = potenza

        serratura = self._get(f.SERRATURA)
        if serratura is not None and serratura.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            if serratura.domain == "lock":
                s.chiusa = serratura.state == "locked"
            else:  # binary_sensor di classe lock: «on» vuol dire aperta
                s.chiusa = serratura.state != STATE_ON
        clima = self._get(f.CLIMA)
        if clima is not None and clima.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            s.clima_acceso = clima.state not in ("off", "idle")
            if clima.domain == "climate":
                s.temperatura_clima_c = clima.attributes.get("temperature")

        posizione = self._get(f.POSIZIONE)
        if posizione is not None:
            s.latitudine = posizione.attributes.get("latitude")
            s.longitudine = posizione.attributes.get("longitude")
            s.posizione_con_segno = True

        if batteria is not None:
            s.letto = getattr(batteria, "last_reported", None) or batteria.last_updated
        else:
            s.letto = dt_util.utcnow()
        s.grezzo = {casella: (st.state if (st := self._get(casella)) else None) for casella in self.mappa}
        self.vettura.stato = s
        return s

    def _minuti_alla_fine(self) -> int | None:
        """Quanto manca alla fine della ricarica: un numero, o un'ora di fine."""
        tempo = self._get(f.TEMPO_RICARICA)
        if tempo is None or tempo.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None
        if tempo.attributes.get("device_class") == "timestamp":
            fine = dt_util.parse_datetime(tempo.state)
            if fine is None:
                return None
            return max(0, round((fine - dt_util.utcnow()).total_seconds() / 60))
        n = _numero(tempo)
        if n is None:
            return None
        unita = tempo.attributes.get("unit_of_measurement")
        return round(n * 60 if unita == "h" else n / 60 if unita == "s" else n)

    async def _esegui(self, comando: str, **p: Any) -> None:
        chiama = self.hass.services.async_call
        try:
            if comando in (COMANDO_CHIUDI, COMANDO_APRI):
                servizio = "lock" if comando == COMANDO_CHIUDI else "unlock"
                await chiama("lock", servizio, {"entity_id": self.mappa[f.SERRATURA]}, blocking=True)
            elif comando == COMANDO_CLIMA:
                entity_id = self.mappa[f.CLIMA]
                dominio = entity_id.split(".", 1)[0]
                await chiama(dominio, "turn_on" if p["acceso"] else "turn_off", {"entity_id": entity_id}, blocking=True)
                if p["acceso"] and dominio == "climate" and p.get("temperatura"):
                    await chiama(
                        "climate",
                        "set_temperature",
                        {"entity_id": entity_id, "temperature": p["temperatura"]},
                        blocking=True,
                    )
            elif comando == COMANDO_RICARICA:
                await chiama(
                    "switch",
                    "turn_on" if p["avvia"] else "turn_off",
                    {"entity_id": self.mappa[f.INTERRUTTORE_RICARICA]},
                    blocking=True,
                )
            elif comando == COMANDO_LIMITE:
                await self._imposta_limite(int(p["percento"]))
            elif comando == COMANDO_TROVA:
                await chiama("button", "press", {"entity_id": self.mappa[f.TROVA]}, blocking=True)
            elif comando == COMANDO_DESTINAZIONE:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="comando_spento",
                    translation_placeholders={"comando": comando},
                )
        except KeyError as err:
            raise ServiceValidationError(f"Manca l'entità per {comando}") from err

    async def _imposta_limite(self, percento: int) -> None:
        entity_id = self.mappa[f.LIMITE]
        if entity_id.startswith("number."):
            await self.hass.services.async_call(
                "number", "set_value", {"entity_id": entity_id, "value": percento}, blocking=True
            )
            return
        # Una tendina: si sceglie la voce più vicina.
        stato = self.hass.states.get(entity_id)
        opzioni = list(stato.attributes.get("options") or []) if stato else []
        numeriche = [(o, int(o.rstrip("%").strip())) for o in opzioni if str(o).rstrip("%").strip().isdigit()]
        if not numeriche:
            raise ServiceValidationError(f"{entity_id} non ha valori in percentuale")
        scelta = min(numeriche, key=lambda o: abs(o[1] - percento))[0]
        await self.hass.services.async_call(
            "select", "select_option", {"entity_id": entity_id, "option": scelta}, blocking=True
        )

    async def async_shutdown(self) -> None:
        if self._annulla_attesa:
            self._annulla_attesa()
            self._annulla_attesa = None
        await super().async_shutdown()
