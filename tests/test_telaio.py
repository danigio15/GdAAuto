"""Il telaio delle marche: una marca finta entra dal catalogo come una vera.

È la prova che una marca nuova, scritta da sola nella sua cartella, funziona
con la configurazione, le entità e il reauth senza toccare altro.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.gdaauto.const import DOMAIN
from custom_components.gdaauto.marche import MARCHE
from custom_components.gdaauto.marche.base import (
    ACCESSO_CAMPI,
    ACCESSO_DISPOSITIVO,
    Accesso,
    Campo,
    Dispositivo,
    InAttesa,
)
from custom_components.gdaauto.marche.catalogo import PER_ID, Marchio
from custom_components.gdaauto.marche.errori import ErroreCredenziali
from custom_components.gdaauto.vettura import StatoVettura, Vettura

AUTO = Vettura(vin="FINTA0000000001", marca="finta", modello="Uno", nome="Finta")


class ClienteFinto:
    async def accedi(self) -> None:
        return None

    async def vetture(self) -> list[Vettura]:
        return [AUTO]

    async def stato(self, _v: Vettura) -> StatoVettura:
        return StatoVettura(batteria=42, autonomia_km=150, chiusa=True)


def _modulo(accesso: str) -> SimpleNamespace:
    confermato = {"si": False}

    async def prova_accesso(_hass: HomeAssistant, dati: dict[str, Any]) -> Accesso:
        if dati.get("password") != "giusta":
            raise ErroreCredenziali("no")
        assert dati["marchio"] == "finta-brand"  # il preimpostato del catalogo arriva alla marca
        return Accesso(dict(dati), [AUTO], "finta_utente", "Finta · utente")

    async def avvia_dispositivo(_hass: HomeAssistant, dati: dict[str, Any]) -> Dispositivo:
        assert dati["client_id"] == "CID"
        return Dispositivo("https://esempio.dev/attiva", "ABCD-1234", {"device_code": "DC"})

    async def completa_dispositivo(_hass: HomeAssistant, dati: dict[str, Any], d: Dispositivo) -> Accesso:
        if not confermato["si"]:
            raise InAttesa("non ancora")
        return Accesso({**dati, "token": d.stato["device_code"]}, [AUTO], "finta_cid", "Finta · CID")

    async def crea_da_voce(_hass: HomeAssistant, _entry: Any, _cartella: str) -> ClienteFinto:
        return ClienteFinto()

    campi = (Campo("email"), Campo("password", segreto=True))
    if accesso == ACCESSO_DISPOSITIVO:
        campi = (Campo("client_id"),)
    return SimpleNamespace(
        ID="finta",
        NOME="Finta",
        ACCESSO=accesso,
        CAMPI=campi,
        NOTA={"it": "nota italiana", "en": "english note"},
        VUOLE_PIN=False,
        INTERVALLO_MINUTI=5,
        comandi_di=lambda _v: set(),
        prova_accesso=prova_accesso,
        avvia_dispositivo=avvia_dispositivo,
        completa_dispositivo=completa_dispositivo,
        crea_da_voce=crea_da_voce,
        confermato=confermato,
    )


MARCHIO = Marchio("finta_brand", "Finta Brand", "finta", {"marchio": "finta-brand"}, "Finta HA", "https://esempio.dev")


async def test_una_marca_coi_campi(hass: HomeAssistant) -> None:
    modulo = _modulo(ACCESSO_CAMPI)
    with patch.dict(MARCHE, {"finta": modulo}), patch.dict(PER_ID, {"finta_brand": MARCHIO}):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "finta_brand"})
        assert r["step_id"] == "credenziali"
        assert r["description_placeholders"] == {"marchio": "Finta Brand", "nota": "english note"}
        # Il marchio preimpostato non si chiede.
        assert {str(k) for k in r["data_schema"].schema} == {"email", "password"}
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"email": "a@b.it", "password": "no"})
        assert r["errors"] == {"base": "credenziali"}
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"email": "a@b.it", "password": "giusta"})
        await hass.async_block_till_done()
        assert r["type"] is FlowResultType.CREATE_ENTRY
        assert r["data"]["marca"] == "finta"
        assert r["data"]["marchio_catalogo"] == "finta_brand"
        assert r["data"]["marchio"] == "finta-brand"
        assert hass.states.get("sensor.finta_batteria").state == "42"


async def test_una_marca_col_codice_su_un_altro_schermo(hass: HomeAssistant) -> None:
    modulo = _modulo(ACCESSO_DISPOSITIVO)
    with patch.dict(MARCHE, {"finta": modulo}), patch.dict(PER_ID, {"finta_brand": MARCHIO}):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "finta_brand"})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"client_id": "CID"})
        assert r["step_id"] == "conferma_dispositivo"
        assert r["description_placeholders"]["codice"] == "ABCD-1234"
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
        assert r["errors"] == {"base": "in_attesa"}
        modulo.confermato["si"] = True
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
        await hass.async_block_till_done()
        assert r["type"] is FlowResultType.CREATE_ENTRY
        assert r["data"]["token"] == "DC"


async def test_un_marchio_senza_marca_nativa_va_su_home_assistant(hass: HomeAssistant) -> None:
    orfano = Marchio("orfano", "Orfano", "non_ancora_scritta", {}, "Orfano HA", "https://esempio.dev/orfano")
    with patch.dict(PER_ID, {"orfano": orfano}):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "orfano"})
    assert r["step_id"] == "home_assistant"
    assert r["description_placeholders"]["integrazione"] == "Orfano HA"


async def test_un_codice_mandato_per_email_si_chiede_nel_modulo(hass: HomeAssistant) -> None:
    from custom_components.gdaauto.marche.errori import CodiceInviato

    modulo = _modulo(ACCESSO_CAMPI)

    async def con_codice(_hass: HomeAssistant, dati: dict[str, Any]) -> Accesso:
        if not dati.get("codice"):
            raise CodiceInviato("codice inviato a a***@b.it")
        return Accesso(dict(dati), [AUTO], "finta_codice", "Finta · codice")

    modulo.prova_accesso = con_codice
    modulo.CAMPI = (Campo("email"), Campo("password", segreto=True), Campo("codice", obbligatorio=False))
    with patch.dict(MARCHE, {"finta": modulo}), patch.dict(PER_ID, {"finta_brand": MARCHIO}):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "finta_brand"})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"email": "a@b.it", "password": "x"})
        assert r["errors"] == {"base": "codice_inviato"}
        r = await hass.config_entries.flow.async_configure(
            r["flow_id"], {"email": "a@b.it", "password": "x", "codice": "123456"}
        )
        await hass.async_block_till_done()
        assert r["type"] is FlowResultType.CREATE_ENTRY


async def test_una_marca_che_si_entra_dal_browser(hass: HomeAssistant) -> None:
    from custom_components.gdaauto.marche.base import ACCESSO_CODICE_BROWSER

    modulo = _modulo(ACCESSO_CODICE_BROWSER)
    aperti: list[dict[str, Any]] = []

    async def avvia_browser(_hass: HomeAssistant, dati: dict[str, Any]) -> Dispositivo:
        aperti.append(dict(dati))
        return Dispositivo(f"https://esempio.dev/login?regione={dati['regione']}", "", {"verifier": "V"})

    async def completa_browser(_hass: HomeAssistant, dati: dict[str, Any], d: Dispositivo, incollato: str) -> Accesso:
        codice = incollato.rsplit("code=", 1)[-1]
        if codice != "BUONO":
            raise ErroreCredenziali("codice scaduto")
        return Accesso({**dati, "token": d.stato["verifier"] + codice}, [AUTO], "finta_browser", "Finta · browser")

    modulo.avvia_browser, modulo.completa_browser = avvia_browser, completa_browser
    modulo.CAMPI = (Campo("regione", scelte=(("eu", "Europa"), ("us", "Stati Uniti")), predefinito="eu"),)
    with patch.dict(MARCHE, {"finta": modulo}), patch.dict(PER_ID, {"finta_brand": MARCHIO}):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "finta_brand"})
        # Prima quello che serve al link, poi il browser.
        assert r["step_id"] == "credenziali"
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"regione": "us"})
        assert r["step_id"] == "browser"
        assert r["description_placeholders"]["link"] == "https://esempio.dev/login?regione=us"
        assert r["description_placeholders"]["marchio"] == "Finta Brand"
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"codice": "app://cb?code=VECCHIO"})
        assert r["errors"] == {"base": "codice"}
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"codice": "app://cb?code=BUONO"})
        await hass.async_block_till_done()
        assert r["type"] is FlowResultType.CREATE_ENTRY
        assert r["data"]["token"] == "VBUONO"
        assert r["data"]["regione"] == "us" and r["data"]["marchio"] == "finta-brand"
        voce = r["result"]

        # Il reauth riapre il browser coi dati di prima, senza richiederli.
        r = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_REAUTH, "entry_id": voce.entry_id},
            data=dict(voce.data),
        )
        assert r["step_id"] == "browser"
        assert aperti[-1]["regione"] == "us"
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"codice": "BUONO"})
        assert r["type"] is FlowResultType.ABORT
        assert r["reason"] == "reauth_successful"
