"""Tesla e Stellantis dentro Home Assistant: la configurazione e le entità."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
import json
import time
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.components.application_credentials import ClientCredential, async_import_client_credential
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gdaauto.const import CONF_FONTE, CONF_MARCA, DOMAIN, FONTE_CLOUD
from custom_components.gdaauto.marche import stellantis, tesla
from custom_components.gdaauto.marche.stellantis.dati import interpreta as interpreta_stellantis
from custom_components.gdaauto.marche.tesla.dati import interpreta as interpreta_tesla
from custom_components.gdaauto.vettura import StatoVettura, Vettura

from .test_stellantis import STATO as STATO_STELLANTIS, VIN as VIN_STELLANTIS
from .test_tesla import VEHICLE_DATA, VIN as VIN_TESLA


def jwt(**corpo: Any) -> str:
    return "x." + base64.urlsafe_b64encode(json.dumps(corpo).encode()).decode().rstrip("=") + ".y"


TOKEN_TESLA = jwt(sub="utente-tesla-1", ou_code="EU")
SAETTA = Vettura(vin=VIN_TESLA, marca="tesla", modello="Model 3", nome="Saetta")


@pytest.fixture
async def credenziali(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "application_credentials", {})
    await async_import_client_credential(hass, DOMAIN, ClientCredential("CLIENT", "SEGRETO"), DOMAIN)


# --- Tesla --------------------------------------------------------------------------


async def test_configurare_tesla_con_l_oauth(
    hass: HomeAssistant, hass_client_no_auth, aioclient_mock, current_request_with_host, credenziali
) -> None:
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "tesla"})
    assert r["type"] is FlowResultType.EXTERNAL_STEP
    assert r["url"].startswith(tesla.AUTORIZZA)
    assert "vehicle_device_data" in r["url"]
    assert "client_id=CLIENT" in r["url"]

    stato = config_entry_oauth2_flow._encode_jwt(
        hass, {"flow_id": r["flow_id"], "redirect_uri": "https://example.com/auth/external/callback"}
    )
    cliente = await hass_client_no_auth()
    risposta = await cliente.get(f"/auth/external/callback?code=CODICE&state={stato}")
    assert risposta.status == 200
    aioclient_mock.post(
        tesla.TOKEN,
        json={"access_token": TOKEN_TESLA, "refresh_token": "R", "token_type": "Bearer", "expires_in": 3600},
    )
    with (
        patch.object(tesla.ClienteTesla, "vetture", AsyncMock(return_value=[SAETTA])),
        patch.object(tesla.ClienteTesla, "stato", AsyncMock(return_value=interpreta_tesla(VEHICLE_DATA))),
    ):
        r = await hass.config_entries.flow.async_configure(r["flow_id"])
        await hass.async_block_till_done()
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["title"] == "Tesla · Saetta"
    assert r["data"][CONF_MARCA] == "tesla"
    assert r["result"].unique_id == "tesla_utente-tesla-1"
    assert hass.states.get("sensor.saetta_batteria").state == "64"
    assert hass.states.get("lock.saetta_serratura").state == "locked"


def _voce_tesla(**opzioni: Any) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Tesla · Saetta",
        unique_id="tesla_utente-tesla-1",
        data={
            CONF_FONTE: FONTE_CLOUD,
            CONF_MARCA: "tesla",
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": TOKEN_TESLA,
                "refresh_token": "R",
                "expires_at": time.time() + 3600,
                "token_type": "Bearer",
            },
        },
        options=opzioni,
    )


async def test_una_tesla_che_dorme_all_avvio(hass: HomeAssistant, credenziali) -> None:
    letture = [StatoVettura(in_sonno=True), interpreta_tesla(VEHICLE_DATA)]
    voce = _voce_tesla()
    voce.add_to_hass(hass)
    with (
        patch.object(tesla.ClienteTesla, "vetture", AsyncMock(return_value=[SAETTA])),
        patch.object(tesla.ClienteTesla, "stato", AsyncMock(side_effect=letture)),
    ):
        assert await hass.config_entries.async_setup(voce.entry_id)
        await hass.async_block_till_done()
        assert voce.state is ConfigEntryState.LOADED
        assert hass.states.get("binary_sensor.saetta_in_sonno").state == "on"
        # Dormendo non si sa niente: niente entità sempre «sconosciute».
        assert hass.states.get("sensor.saetta_batteria") is None
        assert hass.states.get("lock.saetta_serratura") is None

        await voce.runtime_data.auto[0].async_refresh()
        await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.saetta_in_sonno").state == "off"
    assert hass.states.get("sensor.saetta_batteria").state == "64"
    assert hass.states.get("lock.saetta_serratura").state == "locked"
    assert hass.states.get("number.saetta_limite_ricarica").state == "80"


async def test_le_opzioni_di_tesla_hanno_il_proxy(hass: HomeAssistant, credenziali) -> None:
    voce = _voce_tesla()
    voce.add_to_hass(hass)
    with (
        patch.object(tesla.ClienteTesla, "vetture", AsyncMock(return_value=[SAETTA])),
        patch.object(tesla.ClienteTesla, "stato", AsyncMock(return_value=interpreta_tesla(VEHICLE_DATA))),
    ):
        assert await hass.config_entries.async_setup(voce.entry_id)
        await hass.async_block_till_done()
        r = await hass.config_entries.options.async_init(voce.entry_id)
        nomi = {str(k) for k in r["data_schema"].schema}
        assert {"proxy", "proxy_verifica", "intervallo_minuti", "comandi_attivi"} <= nomi
        assert "pin" not in nomi
        r = await hass.config_entries.options.async_configure(
            r["flow_id"],
            {
                "proxy": "https://192.168.1.20:4443",
                "proxy_verifica": False,
                "intervallo_minuti": 10,
                "intervallo_in_moto_secondi": 60,
                "risparmio": True,
                "comandi_attivi": ["clima"],
            },
        )
        await hass.async_block_till_done()
    cliente = voce.runtime_data.conto.cliente
    assert cliente.proxy == "https://192.168.1.20:4443"
    # Da ferma ogni 10 minuti; questa è in carica, e la si legge ogni minuto.
    auto = voce.runtime_data.auto[0]
    assert auto._normale == timedelta(minutes=10)
    assert auto.update_interval == timedelta(seconds=60)


# --- Stellantis ---------------------------------------------------------------------

E208 = Vettura(vin=VIN_STELLANTIS, marca="stellantis", modello="Peugeot", nome="Peugeot", extra={"id": "abc"})
STATO_E208 = interpreta_stellantis(STATO_STELLANTIS)
TOKEN_STELLANTIS = {
    "access_token": "A1",
    "refresh_token": "R1",
    "scade": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
}


async def test_configurare_stellantis_col_codice(hass: HomeAssistant) -> None:
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    # Peugeot sa già qual è la sua app: si passa subito al paese.
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "peugeot"})
    assert r["step_id"] == "stellantis_paese"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"paese": "IT"})
    assert r["step_id"] == "stellantis_codice"
    assert r["description_placeholders"]["link"].startswith("https://idpcvs.peugeot.com/am/oauth2/authorize")

    scambia = AsyncMock(return_value=TOKEN_STELLANTIS)
    with (
        patch.object(stellantis.ClienteStellantis, "scambia_codice", scambia),
        patch.object(stellantis.ClienteStellantis, "vetture", AsyncMock(return_value=[E208])),
        patch.object(stellantis.ClienteStellantis, "stato", AsyncMock(return_value=STATO_E208)),
    ):
        r = await hass.config_entries.flow.async_configure(
            r["flow_id"], {"codice": "mymap://oauth2redirect/it?code=CODICE"}
        )
        await hass.async_block_till_done()
    scambia.assert_awaited_once_with("CODICE")
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["title"] == "Peugeot · 1 auto"
    assert r["data"]["token"] == TOKEN_STELLANTIS
    assert hass.states.get("sensor.peugeot_batteria").state == "57"
    assert hass.states.get("binary_sensor.peugeot_bagagliaio").state == "on"
    # Per ora Stellantis si legge soltanto.
    assert hass.states.get("lock.peugeot_serratura") is None
    assert hass.states.get("binary_sensor.peugeot_serratura").state == "off"


async def test_il_token_che_ruota_non_ricarica_l_integrazione(hass: HomeAssistant) -> None:
    voce = MockConfigEntry(
        domain=DOMAIN,
        title="Peugeot · 1 auto",
        unique_id=f"stellantis_{VIN_STELLANTIS}",
        data={
            CONF_FONTE: FONTE_CLOUD,
            CONF_MARCA: "stellantis",
            "app": "MyPeugeot",
            "paese": "IT",
            "token": TOKEN_STELLANTIS,
        },
    )
    voce.add_to_hass(hass)
    with (
        patch.object(stellantis.ClienteStellantis, "vetture", AsyncMock(return_value=[E208])),
        patch.object(stellantis.ClienteStellantis, "stato", AsyncMock(return_value=STATO_E208)),
    ):
        assert await hass.config_entries.async_setup(voce.entry_id)
        await hass.async_block_till_done()
        dati_prima = voce.runtime_data
        cliente = dati_prima.conto.cliente
        cliente._salva({"access_token": "A2", "refresh_token": "R2", "scade": TOKEN_STELLANTIS["scade"]})
        await hass.async_block_till_done()
    assert voce.data["token"]["refresh_token"] == "R2"
    assert voce.runtime_data is dati_prima  # non si è ricaricata
    # Le opzioni senza comandi: Stellantis non ne ha.
    r = await hass.config_entries.options.async_init(voce.entry_id)
    assert "comandi_attivi" not in {str(k) for k in r["data_schema"].schema}


async def test_la_diagnostica_toglie_i_token(hass: HomeAssistant, credenziali) -> None:
    from custom_components.gdaauto.diagnostics import async_get_config_entry_diagnostics

    voce = _voce_tesla(proxy="https://192.168.1.20:4443")
    voce.add_to_hass(hass)
    with (
        patch.object(tesla.ClienteTesla, "vetture", AsyncMock(return_value=[SAETTA])),
        patch.object(tesla.ClienteTesla, "stato", AsyncMock(return_value=interpreta_tesla(VEHICLE_DATA))),
    ):
        assert await hass.config_entries.async_setup(voce.entry_id)
        await hass.async_block_till_done()
        testo = str(await async_get_config_entry_diagnostics(hass, voce))
    for segreto in (TOKEN_TESLA, "192.168.1.20", VIN_TESLA, "41.9028"):
        assert segreto not in testo
