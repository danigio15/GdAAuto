"""GdAAuto dentro Home Assistant: le due fonti, le entità, i comandi."""

from __future__ import annotations

from collections.abc import Generator
from datetime import timedelta
from typing import Any
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.gdaauto.const import (
    CONF_COMANDI_ATTIVI,
    CONF_DISPOSITIVO,
    CONF_FONTE,
    CONF_INTERVALLO_IN_MOTO,
    CONF_MAPPA,
    CONF_MARCA,
    CONF_NOME,
    CONF_PIN,
    CONF_RISPARMIO,
    DOMAIN,
    FONTE_CLOUD,
    FONTE_HA,
)
from custom_components.gdaauto.marche.errori import ErroreCertificato

from .finto_cliente import FintoCliente
from .test_segnali import C10, T03

T03_VIN = "LTEST000000000T03"
B10_VIN = "LTEST000000000B10"


@pytest.fixture
def cliente() -> Generator[FintoCliente]:
    finto = FintoCliente(
        elenco=[
            {"vin": T03_VIN, "carType": "T03", "nickName": "Bianchina", "rightList": "110,120,170,180"},
            {"vin": B10_VIN, "carType": "B10", "nickName": "Grande"},
        ],
        stati={T03_VIN: dict(T03), B10_VIN: dict(C10)},
    )
    with patch("custom_components.gdaauto.marche.leapmotor.crea", return_value=finto):
        yield finto


def _voce_cloud(**opzioni: Any) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Leapmotor · auto@esempio.it",
        unique_id="leapmotor_auto@esempio.it",
        data={
            CONF_FONTE: FONTE_CLOUD,
            CONF_MARCA: "leapmotor",
            "email": "auto@esempio.it",
            "password": "segreta",
            "cert_app": "-----BEGIN CERTIFICATE-----",
            "chiave_app": "-----BEGIN PRIVATE KEY-----",
        },
        options=opzioni,
    )


async def _installa(hass: HomeAssistant, voce: MockConfigEntry) -> MockConfigEntry:
    voce.add_to_hass(hass)
    assert await hass.config_entries.async_setup(voce.entry_id)
    await hass.async_block_till_done()
    return voce


# --- dal cloud -----------------------------------------------------------------


async def test_le_auto_del_cloud_hanno_le_entita_di_gdaauto(hass: HomeAssistant, cliente: FintoCliente) -> None:
    await _installa(hass, _voce_cloud(**{CONF_PIN: "1234"}))

    assert hass.states.get("sensor.bianchina_batteria").state == "85"
    assert hass.states.get("sensor.bianchina_autonomia").state == "300"
    assert hass.states.get("sensor.bianchina_odometro").state == "15000"
    assert hass.states.get("binary_sensor.bianchina_cavo_collegato").state == "off"
    assert hass.states.get("binary_sensor.bianchina_in_carica").state == "off"
    assert hass.states.get("lock.bianchina_serratura").state == "locked"
    assert hass.states.get("switch.bianchina_clima").state == "off"
    assert hass.states.get("device_tracker.bianchina_posizione").attributes["latitude"] == 45.123
    # La T03 non accetta il limite da remoto e non ha il comando di ricarica.
    assert hass.states.get("number.bianchina_limite_ricarica") is None
    assert hass.states.get("switch.bianchina_comando_ricarica") is None
    # Né la temperatura della batteria: niente sensore sempre «sconosciuto».
    assert hass.states.get("sensor.bianchina_temperatura_batteria") is None

    # La B10 in carica a 11 kW, col limite al 90%.
    assert hass.states.get("sensor.grande_batteria").state == "65"
    assert hass.states.get("binary_sensor.grande_in_carica").state == "on"
    assert hass.states.get("sensor.grande_potenza_ricarica").state == "11.047"
    assert hass.states.get("sensor.grande_stato_ricarica").state == "in_carica"
    assert hass.states.get("number.grande_limite_ricarica").state == "90"
    assert hass.states.get("switch.grande_comando_ricarica").state == "on"

    voce = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, T03_VIN)})
    assert voce is not None
    assert (voce.manufacturer, voce.model, voce.name) == ("Leapmotor", "T03", "Bianchina")


async def test_un_sensore_nasce_quando_arriva_il_suo_numero(hass: HomeAssistant, cliente: FintoCliente) -> None:
    voce = await _installa(hass, _voce_cloud())
    assert hass.states.get("sensor.bianchina_temperatura_batteria") is None
    cliente.stati[T03_VIN]["minBatteryTemp"] = 21
    auto = voce.runtime_data.auto[0]
    await auto.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.bianchina_temperatura_batteria").state == "21"


async def test_si_legge_spesso_solo_quando_serve(hass: HomeAssistant, cliente: FintoCliente) -> None:
    voce = await _installa(hass, _voce_cloud())
    t03, b10 = voce.runtime_data.auto
    # Leggere il cloud Leapmotor non costa niente all'auto: anche chiusa e senza cavo, ogni minuto.
    assert t03.update_interval == timedelta(minutes=1)
    # In carica: ogni 15 secondi, perché GdAHome e gdanav la vedano salire.
    assert b10.update_interval == timedelta(seconds=15)


async def test_il_risparmio_si_accende_a_mano(hass: HomeAssistant, cliente: FintoCliente) -> None:
    voce = await _installa(hass, _voce_cloud(**{CONF_RISPARMIO: True, CONF_INTERVALLO_IN_MOTO: 5}))
    t03, b10 = voce.runtime_data.auto
    # Chiusa, ferma e senza cavo: si risparmia.
    assert t03.update_interval == timedelta(minutes=15)
    # Meno di 10 secondi non si scende, anche se le opzioni lo chiedono.
    assert b10.update_interval == timedelta(seconds=10)


async def test_aprire_da_lontano_e_spento_finche_non_lo_accendi(hass: HomeAssistant, cliente: FintoCliente) -> None:
    await _installa(hass, _voce_cloud(**{CONF_PIN: "1234"}))
    await hass.services.async_call("lock", "lock", {"entity_id": "lock.bianchina_serratura"}, blocking=True)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call("lock", "unlock", {"entity_id": "lock.bianchina_serratura"}, blocking=True)
    assert cliente.chiamate == [("chiudi", (T03_VIN,))]


async def test_col_permesso_si_apre(hass: HomeAssistant, cliente: FintoCliente) -> None:
    await _installa(hass, _voce_cloud(**{CONF_PIN: "1234", CONF_COMANDI_ATTIVI: ["apri", "clima"]}))
    await hass.services.async_call("lock", "unlock", {"entity_id": "lock.bianchina_serratura"}, blocking=True)
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.bianchina_clima"}, blocking=True)
    assert cliente.chiamate == [("apri", (T03_VIN,)), ("clima", (T03_VIN, True, 22))]
    # «Trova» è spento nelle opzioni: niente pulsante.
    assert hass.states.get("button.bianchina_trova") is None


async def test_senza_pin_il_comando_lo_dice(hass: HomeAssistant, cliente: FintoCliente) -> None:
    await _installa(hass, _voce_cloud())
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call("button", "press", {"entity_id": "button.bianchina_trova"}, blocking=True)


async def test_la_destinazione_a_un_auto_sola(hass: HomeAssistant, cliente: FintoCliente) -> None:
    await _installa(hass, _voce_cloud())
    voce = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, B10_VIN)})
    await hass.services.async_call(
        DOMAIN,
        "invia_destinazione",
        {"dispositivo": voce.id, "nome": "Casa", "latitudine": 41.9, "longitudine": 12.5},
        blocking=True,
    )
    assert cliente.chiamate == [("destinazione", (B10_VIN, "Casa", 41.9, 12.5))]


async def test_aggiorna_chiede_una_lettura_sola(hass: HomeAssistant, cliente: FintoCliente) -> None:
    assert await async_setup_component(hass, "homeassistant", {})
    await _installa(hass, _voce_cloud())
    prima = cliente.letture
    # Tante entità che chiedono insieme (come fa gdanav in viaggio).
    await hass.services.async_call(
        "homeassistant",
        "update_entity",
        {"entity_id": ["sensor.bianchina_batteria", "sensor.bianchina_autonomia", "sensor.bianchina_odometro"]},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert cliente.letture == prima + 1


async def test_la_password_cambiata_chiede_di_rientrare(hass: HomeAssistant, cliente: FintoCliente) -> None:
    cliente.password_giusta = False
    voce = _voce_cloud()
    voce.add_to_hass(hass)
    await hass.config_entries.async_setup(voce.entry_id)
    await hass.async_block_till_done()
    assert voce.state is ConfigEntryState.SETUP_ERROR
    (flusso,) = hass.config_entries.flow.async_progress()
    assert flusso["context"]["source"] == config_entries.SOURCE_REAUTH


async def test_la_diagnostica_non_ha_segreti(hass: HomeAssistant, cliente: FintoCliente) -> None:
    from custom_components.gdaauto.diagnostics import async_get_config_entry_diagnostics

    voce = await _installa(hass, _voce_cloud(**{CONF_PIN: "1234"}))
    testo = str(await async_get_config_entry_diagnostics(hass, voce))
    for segreto in ("segreta", "1234", T03_VIN, "auto@esempio.it", "45.123", "BEGIN CERTIFICATE"):
        assert segreto not in testo


# --- da un'integrazione che c'è già ------------------------------------------------


@pytest.fixture
def tesla(hass: HomeAssistant) -> str:
    """Una Tesla come la mette Home Assistant, con le miglia."""
    altra = MockConfigEntry(domain="tesla_fleet")
    altra.add_to_hass(hass)
    dispositivo = dr.async_get(hass).async_get_or_create(
        config_entry_id=altra.entry_id,
        identifiers={("tesla_fleet", "5YJ")},
        manufacturer="Tesla",
        model="Model Y",
        name="Model Y",
    )
    registro = er.async_get(hass)
    for entity_id, nome, unita, classe, stato, extra in (
        ("sensor.model_y_battery_level", "Battery level", "%", "battery", "64", {}),
        ("sensor.model_y_battery_range", "Battery range", "mi", "distance", "200", {}),
        ("sensor.model_y_charging", "Charging", None, "enum", "charging", {}),
        ("sensor.model_y_charger_power", "Charger power", "kW", "power", "7.4", {}),
        ("sensor.model_y_odometer", "Odometer", "mi", "distance", "10000", {}),
        ("binary_sensor.model_y_charge_cable", "Charge cable", None, "connectivity", "on", {}),
        ("lock.model_y_lock", "Lock", None, None, "locked", {}),
        ("number.model_y_charge_limit", "Charge limit", "%", "battery", "80", {}),
        ("device_tracker.model_y_location", "Location", None, None, "home", {"latitude": 45.0, "longitude": 9.0}),
    ):
        dominio, oggetto = entity_id.split(".")
        registro.async_get_or_create(
            dominio,
            "tesla_fleet",
            oggetto,
            suggested_object_id=oggetto,
            device_id=dispositivo.id,
            original_name=nome,
            original_device_class=classe,
            unit_of_measurement=unita,
        )
        attributi = {**extra}
        if unita:
            attributi["unit_of_measurement"] = unita
        if classe:
            attributi["device_class"] = classe
        hass.states.async_set(entity_id, stato, attributi)
    return dispositivo.id


MAPPA_TESLA = {
    "batteria": "sensor.model_y_battery_level",
    "autonomia": "sensor.model_y_battery_range",
    "in_carica": "sensor.model_y_charging",
    "potenza_ricarica": "sensor.model_y_charger_power",
    "odometro": "sensor.model_y_odometer",
    "cavo": "binary_sensor.model_y_charge_cable",
    "serratura": "lock.model_y_lock",
    "limite": "number.model_y_charge_limit",
    "posizione": "device_tracker.model_y_location",
}


async def test_una_tesla_diventa_un_auto_gdaauto(hass: HomeAssistant, tesla: str) -> None:
    voce = MockConfigEntry(
        domain=DOMAIN,
        title="Model Y",
        unique_id=f"ha_{tesla}",
        data={CONF_FONTE: FONTE_HA, CONF_DISPOSITIVO: tesla, CONF_NOME: "Model Y", CONF_MAPPA: MAPPA_TESLA},
    )
    await _installa(hass, voce)

    assert hass.states.get("sensor.model_y_batteria").state == "64"
    # Le miglia diventano chilometri.
    assert float(hass.states.get("sensor.model_y_autonomia").state) == pytest.approx(321.869, rel=1e-3)
    assert hass.states.get("binary_sensor.model_y_in_carica").state == "on"
    assert hass.states.get("binary_sensor.model_y_cavo_collegato").state == "on"
    assert hass.states.get("sensor.model_y_potenza_ricarica").state == "7.4"
    assert hass.states.get("number.model_y_limite_ricarica").state == "80"
    assert hass.states.get("lock.model_y_serratura").state == "locked"

    # La Tesla cambia, GdAAuto la segue.
    hass.states.async_set("sensor.model_y_battery_level", "65", {"unit_of_measurement": "%", "device_class": "battery"})
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done()
    assert hass.states.get("sensor.model_y_batteria").state == "65"

    # I comandi passano per le entità della Tesla.
    # (I servizi si intercettano: così si vede a chi arrivano, ma vuol dire
    # che il comando si dà all'auto di GdAAuto direttamente.)
    auto = voce.runtime_data.auto[0]
    chiusure = async_mock_service(hass, "lock", "lock")
    limiti = async_mock_service(hass, "number", "set_value")
    await auto.async_comanda("chiudi")
    await auto.async_comanda("limite_ricarica", percento=90)
    assert chiusure[0].data["entity_id"] == "lock.model_y_lock"
    assert limiti[0].data == {"entity_id": "number.model_y_charge_limit", "value": 90}


# --- la configurazione ------------------------------------------------------------


async def test_configurare_un_auto_che_c_e_gia(hass: HomeAssistant, tesla: str) -> None:
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert r["type"] is FlowResultType.FORM
    assert r["step_id"] == "user"
    # Un marchio fuori dal catalogo: si prendono le entità di un'altra integrazione.
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "altro"})
    assert r["step_id"] == "home_assistant"
    assert r["description_placeholders"]["marchio"] == ""
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {CONF_DISPOSITIVO: tesla})
    assert r["step_id"] == "entita"
    assert r["description_placeholders"]["trovate"] == "9"
    proposti = {
        str(k): k.description["suggested_value"]
        for k in r["data_schema"].schema
        if k.description and "suggested_value" in k.description
    }
    assert proposti["batteria"] == "sensor.model_y_battery_level"
    assert proposti["nome"] == "Model Y"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {CONF_NOME: "Model Y", **MAPPA_TESLA})
    await hass.async_block_till_done()
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["data"][CONF_MAPPA] == MAPPA_TESLA


async def test_configurare_leapmotor(hass: HomeAssistant, cliente: FintoCliente) -> None:
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "leapmotor"})
    assert r["step_id"] == "credenziali"
    assert r["description_placeholders"]["marchio"] == "Leapmotor"
    assert "dedicat" in r["description_placeholders"]["nota"]
    dati = {"email": "auto@esempio.it", "password": "x"}
    # Senza certificato si prova a scaricare quello pubblico; qui il repository non risponde.
    with patch(
        "custom_components.gdaauto.marche.leapmotor.scarica_pubblici",
        side_effect=ErroreCertificato("non si scarica"),
    ):
        r = await hass.config_entries.flow.async_configure(r["flow_id"], dati)
    assert r["errors"] == {"base": "certificato"}

    dati |= {"cert_app": "CERT", "chiave_app": "CHIAVE"}
    cliente.password_giusta = False
    r = await hass.config_entries.flow.async_configure(r["flow_id"], dati)
    assert r["errors"] == {"base": "credenziali"}

    cliente.password_giusta = True
    # Il PIN si chiede qui, e uno sbagliato si dice subito.
    assert "pin" in {str(k) for k in r["data_schema"].schema}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {**dati, "pin": "0000"})
    assert r["errors"] == {"base": "pin"}

    r = await hass.config_entries.flow.async_configure(r["flow_id"], {**dati, "pin": "1234"})
    await hass.async_block_till_done()
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["data"][CONF_MARCA] == "leapmotor"
    assert r["data"]["marchio_catalogo"] == "leapmotor"
    assert r["title"] == "Leapmotor · auto@esempio.it"
    # Il PIN va nelle opzioni, dove si cambia poi; nei dati non resta.
    assert "pin" not in r["data"]
    assert r["result"].options == {CONF_PIN: "1234"}
    await hass.services.async_call("lock", "lock", {"entity_id": "lock.bianchina_serratura"}, blocking=True)
    assert ("chiudi", (T03_VIN,)) in cliente.chiamate


async def test_le_opzioni_del_cloud(hass: HomeAssistant, cliente: FintoCliente) -> None:
    voce = await _installa(hass, _voce_cloud())
    r = await hass.config_entries.options.async_init(voce.entry_id)
    nomi = {str(k) for k in r["data_schema"].schema}
    assert {"pin", "intervallo_minuti", "intervallo_in_moto_secondi", "risparmio", "comandi_attivi"} <= nomi
    r = await hass.config_entries.options.async_configure(
        r["flow_id"],
        {
            "pin": "1234",
            "intervallo_minuti": 10,
            "intervallo_in_moto_secondi": 30,
            "risparmio": False,
            "comandi_attivi": ["clima"],
        },
    )
    await hass.async_block_till_done()
    assert voce.options["pin"] == "1234"
    t03, b10 = voce.runtime_data.auto
    assert t03.update_interval == timedelta(minutes=10)
    assert b10.update_interval == timedelta(seconds=30)


async def test_si_spegne_pulito(hass: HomeAssistant, cliente: FintoCliente) -> None:
    voce = await _installa(hass, _voce_cloud())
    assert await hass.config_entries.async_unload(voce.entry_id)
    await hass.async_block_till_done()
    assert voce.state is ConfigEntryState.NOT_LOADED


async def test_il_reauth_di_leapmotor_chiede_solo_la_password(hass: HomeAssistant, cliente: FintoCliente) -> None:
    cliente.password_giusta = False
    voce = _voce_cloud()
    voce.add_to_hass(hass)
    await hass.config_entries.async_setup(voce.entry_id)
    await hass.async_block_till_done()
    (flusso,) = hass.config_entries.flow.async_progress()
    assert flusso["step_id"] == "credenziali"
    cliente.password_giusta = True
    r = await hass.config_entries.flow.async_configure(flusso["flow_id"], {"password": "nuova"})
    await hass.async_block_till_done()
    assert r["type"] is FlowResultType.ABORT
    assert r["reason"] == "reauth_successful"
    assert voce.data["password"] == "nuova"
    assert voce.data["email"] == "auto@esempio.it"


def test_ogni_marchio_del_catalogo_ha_una_strada() -> None:
    from custom_components.gdaauto.marche import MARCHE
    from custom_components.gdaauto.marche.catalogo import MARCHI

    for m in MARCHI:
        # O lo legge GdAAuto (o lo leggerà), o c'è scritto cosa usare.
        assert m.ha, m.id
        if m.nativo in MARCHE:
            assert MARCHE[m.nativo].ACCESSO in ("campi", "dispositivo", "oauth_ha", "codice_browser")
