"""La configurazione: da dove arriva l'auto, e come entrarci.

Il primo passo è un menu: una voce per ogni marca che GdAAuto sa leggere da
sola, più «È già in Home Assistant» per tutte le altre. Ogni marca entra a
modo suo:

- **Leapmotor**: email, password e il certificato dell'app;
- **Tesla**: l'OAuth ufficiale, con le credenziali applicative di Home
  Assistant (è per questo che il flusso eredita quello OAuth2);
- **Stellantis**: l'app del marchio e il paese, poi il login nel browser e il
  codice che il sito restituisce, incollato qui.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
from pathlib import Path
import shutil
from typing import Any
import uuid

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import (
    config_entry_oauth2_flow,
    device_registry as dr,
    entity_registry as er,
    selector,
)
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
from .marche import MARCHE, marca, stellantis, tesla
from .marche.errori import ErroreCertificato, ErroreCredenziali, ErroreMarca

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


class GdaautoConfigFlow(config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN):
    VERSION = 1
    DOMAIN = DOMAIN

    def __init__(self) -> None:
        super().__init__()
        self._marca: str | None = None
        self._dispositivo: str | None = None
        self._proposta: dict[str, Any] = {}
        self._stellantis: dict[str, str] = {}

    @property
    def logger(self) -> logging.Logger:
        return logging.getLogger(__name__)

    @property
    def extra_authorize_data(self) -> dict[str, Any]:
        """Quello che Tesla vuole nel link del login (solo Tesla usa l'OAuth di Home Assistant)."""
        return {"scope": " ".join(tesla.SCOPE), "prompt": "login"}

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
        except ErroreMarca:
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

    async def async_step_reauth(self, dati: Mapping[str, Any]) -> ConfigFlowResult:
        if dati.get(CONF_MARCA) == tesla.ID:
            return await self.async_step_reauth_tesla()
        if dati.get(CONF_MARCA) == stellantis.ID:
            self._stellantis = {
                stellantis.CONF_APP: dati[stellantis.CONF_APP],
                stellantis.CONF_PAESE: dati[stellantis.CONF_PAESE],
            }
            return await self.async_step_stellantis_codice()
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

    # --- Tesla, con l'OAuth di Home Assistant ----------------------------------

    async def async_step_tesla(self, _user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Le credenziali dell'app sviluppatore le tiene Home Assistant: se non ci
        sono ancora, è Home Assistant stesso a dire dove metterle."""
        return await self.async_step_pick_implementation()

    async def async_step_reauth_tesla(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(step_id="reauth_tesla")
        return await self.async_step_pick_implementation()

    async def async_oauth_create_entry(self, data: dict[str, Any]) -> ConfigFlowResult:
        token = str(data["token"]["access_token"])
        chi = tesla.dal_token(token).get("sub")
        if not chi:
            return self.async_abort(reason="oauth_error")
        await self.async_set_unique_id(f"tesla_{chi}")
        if self.source == SOURCE_REAUTH:
            self._abort_if_unique_id_mismatch(reason="account_diverso")
            return self.async_update_reload_and_abort(self._get_reauth_entry(), data_updates=data)
        self._abort_if_unique_id_configured()

        async def fisso() -> str:
            return token

        cliente = tesla.ClienteTesla(async_get_clientsession(self.hass), fisso, tesla.regione(token))
        try:
            vetture = await cliente.vetture()
        except ErroreCredenziali:
            return self.async_abort(reason="oauth_unauthorized")
        except ErroreMarca:
            return self.async_abort(reason="rete")
        if not vetture:
            return self.async_abort(reason="nessuna_auto")
        nomi = ", ".join(v.titolo for v in vetture)
        return self.async_create_entry(
            title=f"Tesla · {nomi}", data={**data, CONF_FONTE: FONTE_CLOUD, CONF_MARCA: tesla.ID}
        )

    # --- Stellantis, col codice del login incollato --------------------------------

    async def async_step_stellantis(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._stellantis = {stellantis.CONF_APP: user_input[stellantis.CONF_APP]}
            return await self.async_step_stellantis_paese()
        schema = vol.Schema(
            {
                vol.Required(stellantis.CONF_APP): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=list(stellantis.APP),
                        translation_key="app_stellantis",
                        mode=selector.SelectSelectorMode.LIST,
                    )
                )
            }
        )
        return self.async_show_form(step_id="stellantis", data_schema=schema)

    async def async_step_stellantis_paese(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._stellantis[stellantis.CONF_PAESE] = user_input[stellantis.CONF_PAESE]
            return await self.async_step_stellantis_codice()
        paesi = sorted(stellantis.APP[self._stellantis[stellantis.CONF_APP]]["paesi"])
        predefinito = "IT" if "IT" in paesi else paesi[0]
        schema = vol.Schema(
            {
                vol.Required(stellantis.CONF_PAESE, default=predefinito): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=paesi, mode=selector.SelectSelectorMode.DROPDOWN)
                )
            }
        )
        return self.async_show_form(step_id="stellantis_paese", data_schema=schema)

    async def async_step_stellantis_codice(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        app = self._stellantis[stellantis.CONF_APP]
        paese = self._stellantis[stellantis.CONF_PAESE]
        errori: dict[str, str] = {}
        if user_input is not None:
            codice = stellantis.codice_da(user_input["codice"])
            cliente = stellantis.ClienteStellantis(async_get_clientsession(self.hass), app, paese, {})
            try:
                token = await cliente.scambia_codice(codice)
                vetture = await cliente.vetture()
            except ErroreCredenziali:
                errori["base"] = "codice"
            except ErroreMarca:
                errori["base"] = "rete"
            else:
                if not vetture:
                    errori["base"] = "nessuna_auto"
                else:
                    dati = {
                        CONF_FONTE: FONTE_CLOUD,
                        CONF_MARCA: stellantis.ID,
                        stellantis.CONF_APP: app,
                        stellantis.CONF_PAESE: paese,
                        stellantis.CONF_TOKEN: token,
                    }
                    await self.async_set_unique_id(f"stellantis_{min(v.vin for v in vetture)}")
                    if self.source == SOURCE_REAUTH:
                        return self.async_update_reload_and_abort(self._get_reauth_entry(), data_updates=dati)
                    self._abort_if_unique_id_configured()
                    titolo = f"{stellantis.MARCHI.get(app, 'Stellantis')} · {len(vetture)} auto"
                    return self.async_create_entry(title=titolo, data=dati)
        schema = vol.Schema({vol.Required("codice"): selector.TextSelector()})
        return self.async_show_form(
            step_id="stellantis_codice",
            data_schema=schema,
            errors=errori,
            description_placeholders={"link": stellantis.indirizzo_login(app, paese)},
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
        comandi = True
        if cloud:
            modulo = marca(self.config_entry.data[CONF_MARCA])
            if getattr(modulo, "VUOLE_PIN", False):
                campi[vol.Optional(CONF_PIN)] = selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                )
            if modulo is tesla:
                campi[vol.Optional(tesla.CONF_PROXY)] = selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.URL)
                )
                campi[vol.Required(tesla.CONF_PROXY_VERIFICA, default=True)] = selector.BooleanSelector()
            # Una marca senza comandi (Stellantis, per ora) non ha niente da accendere.
            comandi = modulo is not stellantis
            predefinito = getattr(modulo, "INTERVALLO_MINUTI", INTERVALLO_PREDEFINITO)
            campi[vol.Required(CONF_INTERVALLO, default=predefinito)] = selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=60, step=1, unit_of_measurement="min")
            )
            campi[vol.Required(CONF_INTERVALLO_IN_MOTO, default=INTERVALLO_IN_MOTO_PREDEFINITO)] = (
                selector.NumberSelector(
                    selector.NumberSelectorConfig(min=30, max=600, step=10, unit_of_measurement="s")
                )
            )
            campi[vol.Required(CONF_RISPARMIO, default=True)] = selector.BooleanSelector()
        if comandi:
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
