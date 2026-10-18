"""GdAAuto: tutte le auto elettriche in Home Assistant, con la stessa faccia.

Due strade per entrare:

- **la marca**: GdAAuto parla col cloud del costruttore (Leapmotor oggi);
- **Home Assistant**: l'auto ha già la sua integrazione (Tesla, Stellantis,
  Kia, BMW, Renault…) e GdAAuto la legge da lì.

In tutti e due i casi l'auto diventa un dispositivo GdAAuto con le stesse
entità, che la plancia di GdAHome e l'integrazione gdanav riconoscono da sole.
"""

from __future__ import annotations

from dataclasses import dataclass
import shutil
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType
import voluptuous as vol

from .auto import Auto, AutoCloud, AutoHA, Conto
from .const import (
    COMANDO_DESTINAZIONE,
    CONF_DISPOSITIVO,
    CONF_FONTE,
    CONF_MAPPA,
    CONF_MARCA,
    CONF_NOME,
    DOMAIN,
    FONTE_HA,
    SERVIZIO_AGGIORNA,
    SERVIZIO_DESTINAZIONE,
)
from .marche import marca
from .marche.errori import ErroreCertificato, ErroreCredenziali, ErroreMarca
from .vettura import Vettura

PIATTAFORME = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.DEVICE_TRACKER,
    Platform.LOCK,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class Dati:
    auto: list[Auto]
    conto: Conto | None = None
    #: le opzioni con cui è partita: si ricarica solo se cambiano quelle
    opzioni: dict[str, Any] | None = None


type GdaautoConfigEntry = ConfigEntry[Dati]


def cartella_certificati(hass: HomeAssistant, chiave: str) -> str:
    """Dove stanno i certificati di un account: dentro .storage, non nella config."""
    return hass.config.path(".storage", DOMAIN, chiave)


async def async_setup(hass: HomeAssistant, _config: ConfigType) -> bool:
    async def destinazione(call: ServiceCall) -> None:
        for a in _auto_scelte(hass, call):
            await a.async_comanda(
                COMANDO_DESTINAZIONE,
                nome=call.data["nome"],
                latitudine=call.data["latitudine"],
                longitudine=call.data["longitudine"],
                indirizzo=call.data.get("indirizzo", ""),
            )

    async def aggiorna(call: ServiceCall) -> None:
        for a in _auto_scelte(hass, call):
            await a.async_request_refresh()

    dispositivo = vol.Optional("dispositivo")
    hass.services.async_register(
        DOMAIN,
        SERVIZIO_DESTINAZIONE,
        destinazione,
        schema=vol.Schema(
            {
                dispositivo: cv.string,
                vol.Required("nome"): cv.string,
                vol.Required("latitudine"): cv.latitude,
                vol.Required("longitudine"): cv.longitude,
                vol.Optional("indirizzo"): cv.string,
            }
        ),
    )
    hass.services.async_register(DOMAIN, SERVIZIO_AGGIORNA, aggiorna, schema=vol.Schema({dispositivo: cv.string}))
    return True


def _auto_scelte(hass: HomeAssistant, call: ServiceCall) -> list[Auto]:
    tutte: list[Auto] = [a for e in hass.config_entries.async_loaded_entries(DOMAIN) for a in e.runtime_data.auto]
    scelto = call.data.get("dispositivo")
    if scelto:
        voce = dr.async_get(hass).async_get(scelto)
        vin = next((i[1] for i in voce.identifiers if i[0] == DOMAIN), None) if voce else None
        tutte = [a for a in tutte if a.vettura.vin == vin]
    if not tutte:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="nessuna_auto")
    return tutte


async def async_setup_entry(hass: HomeAssistant, entry: GdaautoConfigEntry) -> bool:
    if entry.data.get(CONF_FONTE) == FONTE_HA:
        entry.runtime_data = await _avvia_ha(hass, entry)
    else:
        entry.runtime_data = await _avvia_cloud(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PIATTAFORME)
    entry.async_on_unload(entry.add_update_listener(_opzioni_cambiate))
    return True


async def _avvia_cloud(hass: HomeAssistant, entry: GdaautoConfigEntry) -> Dati:
    modulo = marca(entry.data[CONF_MARCA])
    cliente = await modulo.crea_da_voce(hass, entry, cartella_certificati(hass, entry.entry_id))
    try:
        await cliente.accedi()
        vetture: list[Vettura] = await cliente.vetture()
        conto = Conto(cliente=cliente, vetture=vetture)
        auto: list[Auto] = [AutoCloud(hass, entry, conto, v, modulo) for v in vetture]
        for a in auto:
            await a.async_config_entry_first_refresh()
    except (ErroreCredenziali, ErroreCertificato) as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except ErroreMarca as err:
        raise ConfigEntryNotReady(str(err)) from err
    return Dati(auto=auto, conto=conto, opzioni=dict(entry.options))


async def _avvia_ha(hass: HomeAssistant, entry: GdaautoConfigEntry) -> Dati:
    device_id = entry.data[CONF_DISPOSITIVO]
    voce = dr.async_get(hass).async_get(device_id)
    vettura = Vettura(
        vin=f"ha_{device_id}",
        marca=(voce.manufacturer if voce and voce.manufacturer else "Home Assistant"),
        modello=(voce.model if voce and voce.model else ""),
        nome=entry.data.get(CONF_NOME) or (voce.name_by_user or voce.name if voce else "") or "",
        extra={"dispositivo": device_id},
    )
    a = AutoHA(hass, entry, vettura, dict(entry.data.get(CONF_MAPPA) or {}))
    a.async_set_updated_data(a.leggi())
    await a.async_ascolta()
    return Dati(auto=[a], opzioni=dict(entry.options))


async def async_unload_entry(hass: HomeAssistant, entry: GdaautoConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PIATTAFORME)
    if ok:
        for a in entry.runtime_data.auto:
            await a.async_shutdown()
    return ok


async def async_remove_entry(hass: HomeAssistant, entry: GdaautoConfigEntry) -> None:
    """Tolta l'auto, via anche i suoi certificati dal disco."""
    await hass.async_add_executor_job(shutil.rmtree, cartella_certificati(hass, entry.entry_id), True)


async def _opzioni_cambiate(hass: HomeAssistant, entry: GdaautoConfigEntry) -> None:
    # Anche un token nuovo aggiorna la voce: quello non è un motivo per ricaricare.
    if dict(entry.options) != entry.runtime_data.opzioni:
        await hass.config_entries.async_reload(entry.entry_id)


def chiavi_da_redigere() -> set[str]:
    return {
        "email",
        "password",
        "cert_app",
        "chiave_app",
        "pin",
        "vin",
        "targa",
        "latitudine",
        "longitudine",
        "token",
        "access_token",
        "refresh_token",
        "proxy",
        "client_id",
        "client_secret",
        "api_key",
        "codice",
    }


__all__ = ["Dati", "GdaautoConfigEntry", "cartella_certificati", "chiavi_da_redigere"]
