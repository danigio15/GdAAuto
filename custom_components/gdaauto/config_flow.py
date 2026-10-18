"""La configurazione: quale marchio, e come entrarci.

Il primo passo è l'elenco di tutti i marchi di auto elettriche
(``marche/catalogo.py``). Se GdAAuto sa leggere quel marchio dal suo cloud,
parte la configurazione della sua marca:

- **campi** (Leapmotor e le altre): un modulo con le credenziali;
- **dispositivo**: il login su un altro schermo, con un link e un codice;
- **oauth_ha** (Tesla): l'OAuth di Home Assistant e le credenziali
  applicative, ed è per questo che il flusso eredita quello OAuth2;
- **codice_browser** (Stellantis): il login nel browser e il codice incollato.

Se no, GdAAuto dice quale integrazione di Home Assistant installare, e poi
legge l'auto da lì.
"""

from __future__ import annotations

from collections.abc import Mapping
import contextlib
import logging
from types import ModuleType
from typing import Any

from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    config_entry_oauth2_flow,
    device_registry as dr,
    entity_registry as er,
    selector,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from . import fonte_ha as f, pem
from .const import (
    COMANDI_ATTIVI_PREDEFINITI,
    COMANDO_APRI,
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
    CONF_MARCHIO,
    CONF_NOME,
    CONF_PIN,
    CONF_RISPARMIO,
    DOMAIN,
    FONTE_CLOUD,
    FONTE_HA,
    INTERVALLO_IN_MOTO_PREDEFINITO,
    INTERVALLO_MINIMO,
    INTERVALLO_PREDEFINITO,
)
from .marche import MARCHE, marca, stellantis, tesla
from .marche.base import (
    ACCESSO_CAMPI,
    ACCESSO_CODICE_BROWSER,
    ACCESSO_DISPOSITIVO,
    ACCESSO_OAUTH_HA,
    Accesso,
    Campo,
    Dispositivo,
    InAttesa,
)
from .marche.catalogo import ALTRO, MARCHI, PER_ID, Marchio
from .marche.errori import (
    CodiceInviato,
    CodiceSbagliato,
    ErroreCertificato,
    ErroreCredenziali,
    ErroreMarca,
    ErrorePin,
)

CONF_SCELTA = "marchio_scelto"


#: Il campo di un file caricato si chiama come il campo del testo, più questo.
_FILE = "_file"


def _selettore(c: Campo) -> Any:
    if c.scelte:
        return selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[selector.SelectOptionDict(value=v, label=e) for v, e in c.scelte],
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )
    if c.lungo:
        return selector.TextSelector(selector.TextSelectorConfig(multiline=True))
    if c.segreto:
        return selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD))
    if c.nome == "email":
        return selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.EMAIL))
    return selector.TextSelector()


def _schema(campi: tuple[Campo, ...], salta: Mapping[str, Any], reauth: bool = False) -> vol.Schema:
    schema: dict[Any, Any] = {}
    for c in campi:
        if c.nome in salta:
            continue
        # Nel reauth i segreti si riscrivono; il resto, se non cambia, resta.
        obbligatorio = c.obbligatorio and (c.segreto or not reauth)
        chiave = vol.Required(c.nome) if obbligatorio else vol.Optional(c.nome)
        if c.predefinito is not None and not reauth:
            chiave = (
                vol.Required(c.nome, default=c.predefinito)
                if obbligatorio
                else vol.Optional(c.nome, default=c.predefinito)
            )
        schema[chiave] = _selettore(c)
        if c.carica:
            # Da un telefono o da un tablet un file si carica meglio di quanto si incolli.
            schema[vol.Optional(c.nome + _FILE)] = selector.FileSelector(selector.FileSelectorConfig(accept=c.carica))
    return vol.Schema(schema)


def _leggi_caricato(hass: HomeAssistant, id_file: str) -> bytes:
    """Il contenuto di un file caricato nel modulo. Bloccante: va nell'executor."""
    with process_uploaded_file(hass, id_file) as percorso:
        return percorso.read_bytes()


async def _con_i_file(hass: HomeAssistant, campi: tuple[Campo, ...], dati: dict[str, Any]) -> dict[str, Any]:
    """Mette i file caricati al posto del loro id, in PEM; un file vince sul testo incollato.

    Un PKCS#12 porta certificato e chiave insieme: riempie anche l'altro campo,
    se per quello non è stato caricato un file suo.
    """
    caricati = {c.nome: dati.pop(c.nome + _FILE, None) for c in campi if c.carica}
    for nome, id_file in caricati.items():
        if not id_file:
            continue
        try:
            grezzo = await hass.async_add_executor_job(_leggi_caricato, hass, id_file)
            dati[nome] = pem.in_pem(grezzo, pem.tipo_di(nome))
            if b"-----BEGIN" in grezzo:
                continue
            with contextlib.suppress(pem.FormatoSconosciuto):
                cert, chiave = pem.da_pkcs12(grezzo)
                for altro in (a for a, f in caricati.items() if not f):
                    if contenuto := chiave if pem.tipo_di(altro) == pem.CHIAVE else cert:
                        dati[altro] = contenuto
        except (OSError, ValueError) as err:
            raise FileIllegibile(str(err)) from err
    return dati


class FileIllegibile(Exception):
    """Il file caricato non c'è più, è vuoto, o non è testo."""


def _errore(err: Exception) -> str:
    if isinstance(err, CodiceInviato):
        return "codice_inviato"
    if isinstance(err, CodiceSbagliato):
        return "codice_sbagliato"
    if isinstance(err, ErroreCredenziali):
        return "credenziali"
    if isinstance(err, ErroreCertificato):
        return "certificato"
    if isinstance(err, ErrorePin):
        return "pin"
    return "rete"


class GdaautoConfigFlow(config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN):
    VERSION = 1
    DOMAIN = DOMAIN

    def __init__(self) -> None:
        super().__init__()
        self._marchio: Marchio | None = None
        self._dati: dict[str, Any] = {}
        self._dispositivo_login: Dispositivo | None = None
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

    # --- il marchio --------------------------------------------------------------

    def _modulo(self) -> ModuleType:
        assert self._marchio is not None and self._marchio.nativo is not None
        return MARCHE[self._marchio.nativo]

    @property
    def _nome_marchio(self) -> str:
        return self._marchio.nome if self._marchio else ""

    def _nota(self, modulo: ModuleType) -> str:
        nota = getattr(modulo, "NOTA", {}) or {}
        return nota.get(self.hass.config.language) or nota.get("en") or nota.get("it") or ""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            scelta = user_input[CONF_SCELTA]
            self._marchio = PER_ID.get(scelta)
            if self._marchio is None or self._marchio.nativo not in MARCHE:
                return await self.async_step_home_assistant()
            return await self._avvia_marca()
        schema = vol.Schema(
            {
                vol.Required(CONF_SCELTA): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[*PER_ID, ALTRO],
                        translation_key="marchio",
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        sort=False,
                    )
                )
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def _avvia_marca(self) -> ConfigFlowResult:
        modulo = self._modulo()
        assert self._marchio is not None
        self._dati = dict(self._marchio.preimpostati)
        accesso = modulo.ACCESSO
        if accesso in (ACCESSO_CAMPI, ACCESSO_DISPOSITIVO):
            return await self.async_step_credenziali()
        if accesso == ACCESSO_OAUTH_HA:
            return await self.async_step_pick_implementation()
        if accesso == ACCESSO_CODICE_BROWSER:
            self._stellantis = {k: v for k, v in self._dati.items() if k == stellantis.CONF_APP}
            if stellantis.CONF_APP in self._stellantis:
                return await self.async_step_stellantis_paese()
            return await self.async_step_stellantis()
        raise ValueError(f"Accesso sconosciuto: {accesso}")

    def _crea_o_aggiorna(self, accesso: Accesso) -> ConfigFlowResult:
        modulo = self._modulo()
        assert self._marchio is not None
        dati = {
            **accesso.dati,
            CONF_FONTE: FONTE_CLOUD,
            CONF_MARCA: modulo.ID,
            CONF_MARCHIO: self._marchio.id,
        }
        # Il PIN chiesto nella configurazione va nelle opzioni, dove si cambia poi.
        pin = dati.pop(CONF_PIN, None)
        if self.source == SOURCE_REAUTH:
            voce = self._get_reauth_entry()
            opzioni = {**voce.options, CONF_PIN: pin} if pin else voce.options
            return self.async_update_reload_and_abort(voce, data_updates=dati, options=opzioni)
        return self.async_create_entry(title=accesso.titolo, data=dati, options={CONF_PIN: pin} if pin else {})

    # --- le credenziali, per le marche «campi» e «dispositivo» ------------------------

    async def async_step_credenziali(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        modulo = self._modulo()
        reauth = self.source == SOURCE_REAUTH
        errori: dict[str, str] = {}
        if user_input is not None:
            dati = {**self._dati, **{k: v for k, v in user_input.items() if v not in (None, "")}}
            try:
                dati = await _con_i_file(self.hass, modulo.CAMPI, dati)
                if modulo.ACCESSO == ACCESSO_DISPOSITIVO:
                    self._dati = dati
                    self._dispositivo_login = await modulo.avvia_dispositivo(self.hass, dati)
                    return await self.async_step_conferma_dispositivo()
                accesso = await modulo.prova_accesso(self.hass, dati)
            except FileIllegibile:
                errori["base"] = "file"
            except ErroreMarca as err:
                errori["base"] = _errore(err)
            else:
                if not accesso.vetture:
                    errori["base"] = "nessuna_auto"
                else:
                    return await self._fine_accesso(accesso)
        schema = _schema(modulo.CAMPI, self._marchio.preimpostati if self._marchio else {}, reauth)
        segreti = {c.nome for c in modulo.CAMPI if c.segreto}
        suggeriti = {k: v for k, v in (user_input or {}).items() if not k.endswith(_FILE)} or {
            k: v for k, v in self._dati.items() if k in schema.schema and k not in segreti
        }
        return self.async_show_form(
            step_id="credenziali",
            data_schema=self.add_suggested_values_to_schema(schema, suggeriti),
            errors=errori,
            description_placeholders={"marchio": self._nome_marchio, "nota": self._nota(modulo)},
        )

    async def _fine_accesso(self, accesso: Accesso) -> ConfigFlowResult:
        await self.async_set_unique_id(accesso.unique_id)
        if self.source == SOURCE_REAUTH:
            self._abort_if_unique_id_mismatch(reason="account_diverso")
        else:
            self._abort_if_unique_id_configured()
        return self._crea_o_aggiorna(accesso)

    async def async_step_conferma_dispositivo(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        modulo = self._modulo()
        assert self._dispositivo_login is not None
        errori: dict[str, str] = {}
        if user_input is not None:
            try:
                accesso = await modulo.completa_dispositivo(self.hass, self._dati, self._dispositivo_login)
            except InAttesa:
                errori["base"] = "in_attesa"
            except ErroreMarca as err:
                errori["base"] = _errore(err)
            else:
                if not accesso.vetture:
                    errori["base"] = "nessuna_auto"
                else:
                    return await self._fine_accesso(accesso)
        return self.async_show_form(
            step_id="conferma_dispositivo",
            errors=errori,
            description_placeholders={
                "marchio": self._nome_marchio,
                "link": self._dispositivo_login.link,
                "codice": self._dispositivo_login.codice_utente,
            },
        )

    # --- il reauth -------------------------------------------------------------------

    async def async_step_reauth(self, dati: Mapping[str, Any]) -> ConfigFlowResult:
        self._marchio = PER_ID.get(dati.get(CONF_MARCHIO, "")) or PER_ID.get(dati.get(CONF_MARCA, ""))
        if self._marchio is None or self._marchio.nativo != dati.get(CONF_MARCA):
            # Una voce di prima del catalogo: si cerca un marchio che usi quella marca.
            self._marchio = next((m for m in MARCHI if m.nativo == dati.get(CONF_MARCA)), None)
        if self._marchio is None:
            return self.async_abort(reason="rete")
        modulo = self._modulo()
        if modulo.ACCESSO == ACCESSO_OAUTH_HA:
            return await self.async_step_reauth_tesla()
        if modulo.ACCESSO == ACCESSO_CODICE_BROWSER:
            self._stellantis = {
                stellantis.CONF_APP: dati[stellantis.CONF_APP],
                stellantis.CONF_PAESE: dati[stellantis.CONF_PAESE],
            }
            return await self.async_step_stellantis_codice()
        # Si tiene tutto: nel modulo i segreti non si mostrano, e quello che non
        # si riscrive resta com'era.
        self._dati = dict(dati)
        return await self.async_step_credenziali()

    # --- Tesla, con l'OAuth di Home Assistant -------------------------------------------

    async def async_step_reauth_tesla(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(step_id="reauth_tesla")
        return await self.async_step_pick_implementation()

    async def async_oauth_create_entry(self, data: dict[str, Any]) -> ConfigFlowResult:
        token = str(data["token"]["access_token"])
        chi = tesla.dal_token(token).get("sub")
        if not chi:
            return self.async_abort(reason="oauth_error")
        if self._marchio is None:
            self._marchio = PER_ID["tesla"]
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
            title=f"Tesla · {nomi}",
            data={**data, CONF_FONTE: FONTE_CLOUD, CONF_MARCA: tesla.ID, CONF_MARCHIO: "tesla"},
        )

    # --- Stellantis, col codice del login incollato -------------------------------------

    async def async_step_stellantis(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        # Le chiavi delle traduzioni vanno minuscole: «mypeugeot» è MyPeugeot.
        app_per_chiave = {app.lower(): app for app in stellantis.APP}
        if user_input is not None:
            self._stellantis = {stellantis.CONF_APP: app_per_chiave[user_input[stellantis.CONF_APP]]}
            return await self.async_step_stellantis_paese()
        schema = vol.Schema(
            {
                vol.Required(stellantis.CONF_APP): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=list(app_per_chiave),
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
        return self.async_show_form(
            step_id="stellantis_paese", data_schema=schema, description_placeholders={"marchio": self._nome_marchio}
        )

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
                    if self._marchio is None:
                        self._marchio = next(m for m in MARCHI if m.preimpostati.get(stellantis.CONF_APP) == app)
                    dati = {
                        stellantis.CONF_APP: app,
                        stellantis.CONF_PAESE: paese,
                        stellantis.CONF_TOKEN: token,
                    }
                    titolo = f"{stellantis.MARCHI.get(app, 'Stellantis')} · {len(vetture)} auto"
                    accesso = Accesso(dati, vetture, f"stellantis_{min(v.vin for v in vetture)}", titolo)
                    await self.async_set_unique_id(accesso.unique_id)
                    if self.source != SOURCE_REAUTH:
                        self._abort_if_unique_id_configured()
                    return self._crea_o_aggiorna(accesso)
        schema = vol.Schema({vol.Required("codice"): selector.TextSelector()})
        return self.async_show_form(
            step_id="stellantis_codice",
            data_schema=schema,
            errors=errori,
            description_placeholders={"link": stellantis.indirizzo_login(app, paese)},
        )

    # --- un'auto che c'è già in Home Assistant ------------------------------------------

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
        m = self._marchio
        return self.async_show_form(
            step_id="home_assistant",
            data_schema=schema,
            errors=errori,
            description_placeholders={
                "marchio": m.nome if m else "",
                "integrazione": (m.ha if m and m.ha else "-"),
                "link": (m.link if m and m.link else "https://www.home-assistant.io/integrations/"),
            },
        )

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
                dati = {
                    CONF_FONTE: FONTE_HA,
                    CONF_DISPOSITIVO: self._dispositivo,
                    CONF_NOME: user_input[CONF_NOME],
                    CONF_MAPPA: mappa,
                }
                if self._marchio is not None:
                    dati[CONF_MARCHIO] = self._marchio.id
                return self.async_create_entry(title=user_input[CONF_NOME], data=dati)
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
            comandi = not getattr(modulo, "SENZA_COMANDI", False)
            predefinito = getattr(modulo, "INTERVALLO_MINUTI", INTERVALLO_PREDEFINITO)
            in_moto = getattr(modulo, "INTERVALLO_IN_MOTO_SECONDI", INTERVALLO_IN_MOTO_PREDEFINITO)
            campi[vol.Required(CONF_INTERVALLO, default=predefinito)] = selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=180, step=1, unit_of_measurement="min")
            )
            minimo = getattr(modulo, "INTERVALLO_MINIMO_SECONDI", INTERVALLO_MINIMO.total_seconds())
            campi[vol.Required(CONF_INTERVALLO_IN_MOTO, default=in_moto)] = selector.NumberSelector(
                selector.NumberSelectorConfig(min=minimo, max=3600, step=5, unit_of_measurement="s")
            )
            risparmio = getattr(modulo, "RISPARMIO_PREDEFINITO", True)
            campi[vol.Required(CONF_RISPARMIO, default=risparmio)] = selector.BooleanSelector()
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


__all__ = ["GdaautoConfigFlow"]
