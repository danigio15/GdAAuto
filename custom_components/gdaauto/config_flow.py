"""La configurazione: da dove arriva l'auto, e come entrarci.

Il primo passo è un menu: una voce per ogni marca che GdAAuto sa leggere da
sola, più «È già in Home Assistant» per tutte le altre.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import shutil
from typing import Any
import uuid

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr, entity_registry as er, selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from . import fonte_ha as f
from .const import (
    COMANDI_ATTIVI_PREDEFINITI,
    COMANDO_APRI,
    COMANDO_CHIUDI,
    COMANDO_CLIMA,
    COMANDO_DESTINAZIONE,
    COMANDO_LIMITE,
    COMANDO_RICARICA,
    COMANDO_TROVA,
    CONF_COMANDI_ATTIVI,
    CONF_DISPOSITIVO,
    CONF_FONTE,
    CONF_INTERVALLO,
    CONF_INTERVALLO_IN_MOTO,
    CONF_MAPPA,
    CONF_MARCA,
    CONF_NOME,
    CONF_PIN,
    CONF_RISPARMIO,
    DOMAIN,
    FONTE_CLOUD,
    FONTE_HA,
    INTERVALLO_IN_MOTO_PREDEFINITO,
    INTERVALLO_PREDEFINITO,
)
from .marche import MARCHE, marca
from .marche.leapmotor.errori import ErroreCertificato, ErroreCredenziali, ErroreLeapmotor

# Chi preferisce un file al copia-incolla mette i due PEM qui, in /config.
CARTELLA_PEM = "gdaauto"
FILE_CERT = "app_cert.pem"
FILE_CHIAVE = "app_key.pem"


def _campo(c: dict[str, Any]) -> tuple[Any, Any]:
    chiave = vol.Required(c["nome"]) if c.get("obbligatorio") else vol.Optional(c["nome"])
    if c.get("lungo"):
        sel = selector.TextSelector(selector.TextSelectorConfig(multiline=True))
    elif c.get("segreto"):
        sel = selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD))
    elif c["nome"] == "email":
        sel = selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.EMAIL))
    else:
        sel = selector.TextSelector()
    return chiave, sel


class GdaautoConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._marca: str | None = None
        self._dispositivo: str | None = None
        self._proposta: dict[str, Any] = {}

    # --- il menu ------------------------------------------------------------

    async def async_step_user(self, _user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=[*MARCHE, "home_assistant"])

    # --- una marca, dal suo cloud ----------------------------------------

    async def async_step_leapmotor(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._passo_marca("leapmotor", user_input)

    def _schema_marca(self, id_marca: str) -> vol.Schema:
        return vol.Schema(dict(_campo(c) for c in marca(id_marca).CAMPI))

    def _pem_da_file(self, dati: dict[str, Any]) -> dict[str, Any]:
        """Se i PEM non sono stati incollati, si cercano in /config/gdaauto."""
        cartella = Path(self.hass.config.path(CARTELLA_PEM))
        for campo, nome in (("cert_app", FILE_CERT), ("chiave_app", FILE_CHIAVE)):
            if not (dati.get(campo) or "").strip() and (cartella / nome).is_file():
                dati[campo] = (cartella / nome).read_text()
        return dati

    async def _prova(self, id_marca: str, dati: dict[str, Any]) -> tuple[dict[str, str], int]:
        """Entra davvero e conta le auto. Torna gli errori per il modulo."""
        cartella = self.hass.config.path(".storage", DOMAIN, f"prova-{uuid.uuid4().hex}")
        sessione = async_get_clientsession(self.hass)
        cliente = marca(id_marca).crea(sessione, dati, cartella, self.hass.config.language)
        try:
            await cliente.accedi()
            vetture = await cliente.vetture()
        except ErroreCredenziali:
            return {"base": "credenziali"}, 0
        except ErroreCertificato:
            return {"base": "certificato"}, 0
        except ErroreLeapmotor:
            return {"base": "rete"}, 0
        finally:
            await self.hass.async_add_executor_job(shutil.rmtree, cartella, True)
        if not vetture:
            return {"base": "nessuna_auto"}, 0
        return {}, len(vetture)

    async def _passo_marca(self, id_marca: str, user_input: dict[str, Any] | None) -> ConfigFlowResult:
        errori: dict[str, str] = {}
        if user_input is not None:
            dati = await self.hass.async_add_executor_job(self._pem_da_file, dict(user_input))
            if not (dati.get("cert_app") or "").strip() or not (dati.get("chiave_app") or "").strip():
                errori["base"] = "certificato_mancante"
            else:
                errori, _quante = await self._prova(id_marca, dati)
            if not errori:
                await self.async_set_unique_id(f"{id_marca}_{dati['email'].strip().lower()}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"{marca(id_marca).NOME} · {dati['email']}",
                    data={**dati, CONF_FONTE: FONTE_CLOUD, CONF_MARCA: id_marca},
                )
        schema = self.add_suggested_values_to_schema(self._schema_marca(id_marca), user_input or {})
        return self.async_show_form(
            step_id=id_marca,
            data_schema=schema,
            errors=errori,
            description_placeholders={"cartella": f"/config/{CARTELLA_PEM}"},
        )

    async def async_step_reauth(self, _dati: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        voce = self._get_reauth_entry()
        id_marca = voce.data[CONF_MARCA]
        errori: dict[str, str] = {}
        if user_input is not None:
            dati = {**voce.data, **{k: v for k, v in user_input.items() if v}}
            dati = await self.hass.async_add_executor_job(self._pem_da_file, dati)
            errori, _ = await self._prova(id_marca, dati)
            if not errori:
                return self.async_update_reload_and_abort(voce, data=dati)
        schema = vol.Schema(
            {
                vol.Required("password"): _campo({"nome": "password", "segreto": True})[1],
                vol.Optional("cert_app"): _campo({"nome": "cert_app", "lungo": True})[1],
                vol.Optional("chiave_app"): _campo({"nome": "chiave_app", "lungo": True})[1],
            }
        )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errori,
            description_placeholders={"email": voce.data.get("email", "")},
        )

    # --- un'auto che c'è già in Home Assistant -----------------------------

    async def async_step_home_assistant(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errori: dict[str, str] = {}
        if user_input is not None:
            device_id = user_input[CONF_DISPOSITIVO]
            voce = dr.async_get(self.hass).async_get(device_id)
            if voce is None:
                errori["base"] = "dispositivo"
            elif any(i[0] == DOMAIN for i in voce.identifiers):
                errori["base"] = "gia_gdaauto"
            else:
                await self.async_set_unique_id(f"ha_{device_id}")
                self._abort_if_unique_id_configured()
                self._dispositivo = device_id
                self._proposta = self._dal_dispositivo(device_id)
                self._proposta[CONF_NOME] = voce.name_by_user or voce.name or ""
                return await self.async_step_entita()
        schema = vol.Schema({vol.Required(CONF_DISPOSITIVO): selector.DeviceSelector()})
        return self.async_show_form(step_id="home_assistant", data_schema=schema, errors=errori)

    def _dal_dispositivo(self, device_id: str) -> dict[str, Any]:
        registro = er.async_get(self.hass)
        entita = []
        for voce in er.async_entries_for_device(registro, device_id):
            if voce.disabled:
                continue
            stato = self.hass.states.get(voce.entity_id)
            attributi = stato.attributes if stato else {}
            entita.append(
                f.Entita(
                    voce.entity_id,
                    nome=voce.name or voce.original_name or (stato.name if stato else ""),
                    unita=attributi.get("unit_of_measurement") or voce.unit_of_measurement,
                    classe=attributi.get("device_class") or voce.device_class or voce.original_device_class,
                    categoria=voce.entity_category.value if voce.entity_category else None,
                )
            )
        return dict(f.proponi(entita))

    async def async_step_entita(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errori: dict[str, str] = {}
        if user_input is not None:
            mappa = {c: user_input[c] for c in f.CASELLE if user_input.get(c)}
            if f.BATTERIA not in mappa:
                errori[f.BATTERIA] = "batteria"
            else:
                assert self._dispositivo is not None
                return self.async_create_entry(
                    title=user_input[CONF_NOME],
                    data={
                        CONF_FONTE: FONTE_HA,
                        CONF_DISPOSITIVO: self._dispositivo,
                        CONF_NOME: user_input[CONF_NOME],
                        CONF_MAPPA: mappa,
                    },
                )
        campi: dict[Any, Any] = {vol.Required(CONF_NOME): selector.TextSelector()}
        for casella in f.CASELLE:
            campi[vol.Optional(casella)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain=list(f.DOMINI[casella]))
            )
        trovate = len([k for k in self._proposta if k != CONF_NOME])
        return self.async_show_form(
            step_id="entita",
            data_schema=self.add_suggested_values_to_schema(vol.Schema(campi), user_input or self._proposta),
            errors=errori,
            description_placeholders={"trovate": str(trovate)},
        )

    @staticmethod
    @callback
    def async_get_options_flow(_entry: ConfigEntry) -> OptionsFlow:
        return GdaautoOptionsFlow()


_TUTTI_I_COMANDI = [
    COMANDO_TROVA,
    COMANDO_CLIMA,
    COMANDO_RICARICA,
    COMANDO_LIMITE,
    COMANDO_DESTINAZIONE,
    COMANDO_APRI,
]


class GdaautoOptionsFlow(OptionsFlow):
    """Il PIN, il ritmo delle letture e quali comandi sono accesi."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        cloud = self.config_entry.data.get(CONF_FONTE) != FONTE_HA
        campi: dict[Any, Any] = {}
        if cloud:
            modulo = marca(self.config_entry.data[CONF_MARCA])
            if getattr(modulo, "VUOLE_PIN", False):
                campi[vol.Optional(CONF_PIN)] = selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                )
            campi[vol.Required(CONF_INTERVALLO, default=INTERVALLO_PREDEFINITO)] = selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=60, step=1, unit_of_measurement="min")
            )
            campi[vol.Required(CONF_INTERVALLO_IN_MOTO, default=INTERVALLO_IN_MOTO_PREDEFINITO)] = (
                selector.NumberSelector(
                    selector.NumberSelectorConfig(min=30, max=600, step=10, unit_of_measurement="s")
                )
            )
            campi[vol.Required(CONF_RISPARMIO, default=True)] = selector.BooleanSelector()
        campi[vol.Required(CONF_COMANDI_ATTIVI, default=COMANDI_ATTIVI_PREDEFINITI)] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=_TUTTI_I_COMANDI,
                multiple=True,
                translation_key=CONF_COMANDI_ATTIVI,
                mode=selector.SelectSelectorMode.LIST,
            )
        )
        schema = self.add_suggested_values_to_schema(vol.Schema(campi), self.config_entry.options)
        return self.async_show_form(step_id="init", data_schema=schema)


__all__ = ["COMANDO_CHIUDI", "GdaautoConfigFlow"]
