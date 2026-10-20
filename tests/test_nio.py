"""NIO: le letture dell'API ufficiale, il cliente contro un cloud finto, il login dal browser e Home Assistant."""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field, replace
import hashlib
import time
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import aiohttp
from aiohttp import web
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gdaauto.const import CONF_FONTE, CONF_MARCA, DOMAIN, FONTE_CLOUD
from custom_components.gdaauto.marche import MARCHE, nio
from custom_components.gdaauto.marche.base import ACCESSO_CODICE_BROWSER, Dispositivo
from custom_components.gdaauto.marche.catalogo import PER_ID
from custom_components.gdaauto.marche.errori import ErroreApi, ErroreCredenziali, ErroreRete
from custom_components.gdaauto.marche.nio import cliente as modulo_cliente
from custom_components.gdaauto.marche.nio.cliente import (
    AMBITI,
    RITORNO,
    ClienteNio,
    comandi_di,
    leggi_codice,
    link_di_accesso,
    pkce,
)
from custom_components.gdaauto.marche.nio.dati import interpreta, unisci
from custom_components.gdaauto.vettura import StatoRicarica, Vettura

from .nio_campioni import (
    CLIMA,
    CONTAKM,
    ESTREMI,
    FINESTRINI,
    PORTE,
    POSIZIONE,
    SOC_CAMBI,
    STATO,
    VIN,
    T,
    busta,
    busta_token,
)

CLIENT_ID = "cid-123"
SEGRETO = "segreto-456"


def _tutte() -> dict[str, dict[str, Any]]:
    return {
        "soc_status": unisci(None, SOC_CAMBI),
        "vehicle_status": dict(STATO),
        "position_status": unisci(None, POSIZIONE),
        "door_status": unisci(None, PORTE),
        "window_status": unisci(None, FINESTRINI),
        "hvac_status": unisci(None, CLIMA),
        "extremum_data": unisci(None, ESTREMI),
        "odometer_report": CONTAKM[-1],
    }


def _sfida(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


# --- l'interpretazione ------------------------------------------------------------


def test_una_et5_touring_ferma_e_chiusa() -> None:
    s = interpreta(_tutte())
    assert s.batteria == 52
    # L'autonomia viene dal record di prima: il cambio dopo non l'aveva.
    assert s.autonomia_km == 270
    assert s.limite_ricarica == 90
    # «3» non è documentato: meglio niente che un'invenzione.
    assert s.stato_ricarica is None
    assert s.odometro_km == 12_345
    assert s.velocita_kmh == 0
    # La tensione del pacco (uno solo), la corrente della batteria.
    assert s.tensione_v == 359
    assert s.corrente_a == -0.5
    assert s.temperatura_esterna_c == 11.5
    # In soc_status le estreme sono zero («non misurate»): valgono quelle di extremum_data, meno 40.
    assert s.temperatura_batteria_c == 18
    assert (s.latitudine, s.longitudine, s.posizione_con_segno) == (52.3702, 4.8952, True)
    assert s.chiusa is True
    assert s.portiere_aperte is False
    assert s.bagagliaio_aperto is True
    assert s.finestrini_aperti is False
    assert s.letto is not None and int(s.letto.timestamp() * 1000) == T + 1_000
    # Il grezzo non porta la posizione precisa né i numeri di serie.
    assert "latitude" not in s.grezzo["position_status"]
    assert "btry_pak_sn" not in s.grezzo["soc_status"]["btry_paks"][0]
    assert s.grezzo["vehicle_status"]["vehl_state"] == "locked"


def test_i_numeri_impossibili_e_i_segnaposto() -> None:
    s = interpreta(
        {
            "soc_status": {"soc": 0xFFFF, "remaining_range": 0xFFFE, "max_soc": 0},
            "vehicle_status": {
                "soc": 0,
                "remaining_range": 312,
                "mileage": 0xFFFFFFFE,
                "speed": 0xFFFF,
                "vehl_totl_volt": 3_970,
                "vehl_totl_curnt": 9_880,
            },
            "odometer_report": CONTAKM[-1],
            "position_status": {"latitude": 0, "longitude": 0},
            "door_status": {"door_ajar_rear_left_status": 0},
        }
    )
    assert s.batteria is None
    # Senza l'autonomia buona di soc_status vale quella (non zero) dello stato.
    assert s.autonomia_km == 312
    assert s.limite_ricarica is None
    # I km impossibili dello stato lasciano il posto a quelli dell'assistenza.
    assert s.odometro_km == 12_300
    assert s.velocita_kmh is None
    assert s.tensione_v == 397
    # Decimi di ampere con 1000 A di scarto: 988,0 − 1000 = −12 A (in carica).
    assert s.corrente_a == -12
    assert s.latitudine is None
    assert s.portiere_aperte is True
    assert s.chiusa is None


def test_gli_stati_della_ricarica_scritti_in_chiaro() -> None:
    assert interpreta({"soc_status": {"chrg_state": "charging"}}).stato_ricarica == StatoRicarica.IN_CARICA
    assert interpreta({"soc_status": {"chrg_state": "Not_Charging"}}).stato_ricarica == StatoRicarica.NON_IN_CARICA
    assert interpreta({"soc_status": {"chrg_state": 3}}).stato_ricarica is None
    assert interpreta({"soc_status": {"soc": None}, "vehicle_status": None}).batteria is None  # type: ignore[dict-item]


def test_i_cambi_si_uniscono_senza_perdere_il_buono() -> None:
    prima = unisci(None, SOC_CAMBI)
    assert (prima["soc"], prima["remaining_range"], prima["sample_timestamp"]) == (52, 270, T)
    # Un cambio vecchio (la finestra di dieci minuti che si sovrappone) non copre quello nuovo.
    dopo = unisci(prima, [{"soc": 49, "sample_timestamp": T - 120_000}])
    assert dopo["soc"] == 52
    # Un soc impossibile non copre quello buono; uno nuovo sì.
    dopo = unisci(prima, [{"soc": 0xFFFF, "remaining_range": 268, "sample_timestamp": T + 60_000}])
    assert (dopo["soc"], dopo["remaining_range"]) == (52, 268)


def test_il_link_e_il_codice_incollato() -> None:
    verifier, sfida = pkce()
    assert 43 <= len(verifier) <= 128 and _sfida(verifier) == sfida
    link = link_di_accesso(CLIENT_ID, sfida, "STATO")
    q = parse_qs(urlparse(link).query)
    assert link.startswith("https://open-eu.nio.com/oauth2/authorize?")
    assert q["client_id"] == [CLIENT_ID]
    assert q["redirect_uri"] == [RITORNO]
    assert q["code_challenge_method"] == ["S256"]
    assert q["scope"] == [" ".join(AMBITI)]
    assert leggi_codice(f"{RITORNO}?code=ABC&state=XYZ") == ("ABC", "XYZ")
    assert leggi_codice("  ABC  ") == ("ABC", "")


def test_il_contratto() -> None:
    assert MARCHE["nio"] is nio
    assert nio.ACCESSO == ACCESSO_CODICE_BROWSER
    assert [c.nome for c in nio.CAMPI] == ["client_id", "client_secret", "vin"]
    assert nio.SENZA_COMANDI is True
    assert comandi_di(Vettura(vin=VIN, marca="nio", modello="NIO")) == set()


# --- il cliente contro un cloud finto -----------------------------------------------


@dataclass
class FintoNio:
    sfida: str = ""
    ruotati: int = 0
    scambi: int = 0
    validi: set[str] = field(default_factory=lambda: {"A0"})
    letture: list[str] = field(default_factory=list)
    negate: set[str] = field(default_factory=set)
    vuote: set[str] = field(default_factory=set)
    troppe: bool = False
    guasto_api: bool = False
    guasto_token: bool = False
    parametri: list[dict[str, str]] = field(default_factory=list)

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/api/2/oauth/token", self._token)
        app.router.add_get("/api/1/telematics/vehicles/{vin}/vehicle_status/latest", self._stato)
        app.router.add_get("/api/1/telematics/vehicles/{vin}/{risorsa}/changes", self._cambi)
        app.router.add_get("/api/1/telematics/aftersales/vehicles/{vin}/odometer_reports", self._contakm)
        return app

    async def _token(self, r: web.Request) -> web.Response:
        if self.guasto_token:
            return web.Response(status=503, text="Service Unavailable")
        auth = aiohttp.BasicAuth.decode(r.headers["Authorization"])
        if (auth.login, auth.password) != (CLIENT_ID, SEGRETO):
            return web.json_response({"result_code": "invalid_client"}, status=401)
        modulo = await r.post()
        if modulo["grant_type"] == "authorization_code":
            if modulo["code"] != "CODICE" or _sfida(str(modulo["code_verifier"])) != self.sfida:
                return web.json_response({"result_code": "invalid_grant"}, status=400)
            assert modulo["redirect_uri"] == RITORNO
            self.scambi += 1
            self.validi = {"A-LOGIN"}
            return web.json_response(busta_token("A-LOGIN", "R1"))
        if modulo["refresh_token"] != f"R{self.ruotati + 1}":
            return web.json_response({"result_code": "invalid_grant", "display_msg": "revoked"}, status=400)
        self.ruotati += 1
        nuovo = f"A{self.ruotati}"
        self.validi = {nuovo}
        return web.json_response(busta_token(nuovo, f"R{self.ruotati + 1}"))

    def _controlla(self, r: web.Request, risorsa: str) -> web.Response | None:
        if self.guasto_api:
            return web.Response(status=503, text="Service Unavailable")
        if r.headers.get("Authorization", "").removeprefix("Bearer ") not in self.validi:
            return web.json_response({"result_code": "invalid_token"}, status=401)
        assert r.match_info["vin"] == VIN
        self.letture.append(risorsa)
        self.parametri.append(dict(r.query))
        if self.troppe:
            return web.json_response(
                {"result_code": "rate_limited", "display_msg": "Too many requests"},
                status=429,
                headers={"Retry-After": "120"},
            )
        if risorsa in self.negate:
            return web.json_response({"result_code": "access_denied"}, status=403)
        if risorsa in self.vuote:
            return web.json_response({"result_code": "resource_not_found"}, status=404)
        return None

    async def _stato(self, r: web.Request) -> web.Response:
        return self._controlla(r, "vehicle_status") or web.json_response(busta(STATO))

    async def _cambi(self, r: web.Request) -> web.Response:
        risorsa = r.match_info["risorsa"]
        if errore := self._controlla(r, risorsa):
            return errore
        dati = {
            "soc_status": SOC_CAMBI,
            "position_status": POSIZIONE,
            "door_status": PORTE,
            "window_status": FINESTRINI,
            "hvac_status": CLIMA,
            "extremum_data": ESTREMI,
        }[risorsa]
        return web.json_response(busta(dati))

    async def _contakm(self, r: web.Request) -> web.Response:
        return self._controlla(r, "odometer_report") or web.json_response(busta(CONTAKM))


@pytest.fixture
async def cloud(socket_enabled: None) -> AsyncGenerator[tuple[FintoNio, str]]:
    finto = FintoNio()
    runner = web.AppRunner(finto.app())
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    with (
        patch.object(modulo_cliente, "PAUSA_S", 0.0),
        patch.dict(modulo_cliente._TURNI, clear=True),
        patch.dict(modulo_cliente._FERMI, clear=True),
    ):
        yield finto, f"http://127.0.0.1:{runner.addresses[0][1]}"
    await runner.cleanup()


def _token(access: str = "A0", refresh: str = "R1", tra: float = 3600) -> dict[str, Any]:
    return {"access_token": access, "refresh_token": refresh, "scade": time.time() + tra}


def _cliente(
    s: aiohttp.ClientSession,
    url: str,
    token: dict[str, Any] | None = None,
    salvati: list | None = None,
    memoria: dict[str, Any] | None = None,
) -> ClienteNio:
    return ClienteNio(
        s,
        CLIENT_ID,
        SEGRETO,
        VIN,
        token or _token(),
        salvati.append if salvati is not None else None,
        memoria=memoria,
        api=url,
        url_token=f"{url}/api/2/oauth/token",
    )


async def test_l_auto_e_lo_stato_a_turno(cloud) -> None:
    finto, url = cloud
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, salvati=salvati)
        await c.accedi()
        [v] = await c.vetture()
        assert (v.vin, v.marca, v.nome) == (VIN, "nio", "NIO 8901")
        st = await c.stato(v)
        # Al primo giro le frequenti tutte, dopo una a turno.
        assert finto.letture == ["vehicle_status", "soc_status", "vehicle_status", "position_status", "door_status"]
        assert (st.batteria, st.autonomia_km, st.chiusa, st.latitudine) == (52, 270, True, 52.3702)
        # La carica si chiede senza date: con le date NIO oggi dice «invalid_param».
        assert finto.parametri[1] == {}
        finto.letture.clear()
        for _ in range(5):
            await c.stato(v)
    giri = [finto.letture[i : i + 3] for i in range(0, 15, 3)]
    assert all(g[:2] == ["soc_status", "vehicle_status"] for g in giri)
    assert [g[2] for g in giri] == ["position_status", "door_status", "position_status", "door_status", "window_status"]
    # Le ultime letture buone vanno nella voce una volta sola (non cambiano), senza liste.
    memorie = [x["memoria"] for x in salvati if "memoria" in x]
    assert len(memorie) == 1
    assert memorie[0]["soc_status"]["soc"] == 52 and "btry_paks" not in memorie[0]["soc_status"]


async def test_dopo_un_riavvio_la_carica_e_quella_ricordata(cloud) -> None:
    finto, url = cloud
    finto.vuote = {"soc_status"}  # ferma da più di dieci minuti: niente cambi
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, memoria={"soc_status": {"soc": 47, "remaining_range": 250, "sample_timestamp": T}})
        [v] = await c.vetture()
        st = await c.stato(v)
    assert (st.batteria, st.autonomia_km) == (47, 250)


async def test_le_risorse_negate_si_saltano(cloud) -> None:
    finto, url = cloud
    finto.negate = {"position_status", "door_status", "window_status"}
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url)
        [v] = await c.vetture()
        st = await c.stato(v)
        assert st.batteria == 52 and st.latitudine is None and st.chiusa is None
        finto.letture.clear()
        await c.stato(v)
        await c.stato(v)
    # Le negate non si richiedono più: tocca alle lente, e anche window_status si scopre negata.
    assert finto.letture == [
        *("soc_status", "vehicle_status", "window_status"),
        *("soc_status", "vehicle_status", "hvac_status"),
    ]


async def test_il_token_che_scade_si_rinnova_e_si_salva(cloud) -> None:
    finto, url = cloud
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, token=_token("VECCHIO", "R1", tra=-60), salvati=salvati)
        [v] = await c.vetture()
        # Un token rifiutato prima della scadenza: si rinnova e si riprova una volta.
        finto.validi = {"ALTRO"}
        c.token["scade"] = time.time() + 3600
        await c.stato(v)
    assert finto.ruotati == 2
    token = [x["token"] for x in salvati if "token" in x]
    assert [t["refresh_token"] for t in token] == ["R2", "R3"]
    assert c.token["access_token"] == "A2"


async def test_un_refresh_revocato_e_una_credenziale(cloud) -> None:
    _finto, url = cloud
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, token=_token("VECCHIO", "R-REVOCATO", tra=-60))
        with pytest.raises(ErroreCredenziali):
            await c.vetture()


async def test_i_guasti_sono_rete_non_credenziali(cloud) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url)
        finto.guasto_api = True
        with pytest.raises(ErroreRete):
            await c.vetture()
        finto.guasto_api = False
        finto.guasto_token = True
        c.token["scade"] = 0
        with pytest.raises(ErroreRete):
            await c.vetture()
    async with aiohttp.ClientSession() as s:
        spento = ClienteNio(s, CLIENT_ID, SEGRETO, VIN, _token(), api="http://127.0.0.1:9")
        with pytest.raises(ErroreRete):
            await spento.vetture()


async def test_troppe_richieste_si_tiene_l_ultima_lettura(cloud) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url)
        [v] = await c.vetture()
        prima = await c.stato(v)
        finto.troppe = True
        assert await c.stato(v) is prima
        letture = len(finto.letture)
        # Fermi quanto dice Retry-After: niente richieste intanto.
        assert await c.stato(v) is prima
        assert len(finto.letture) == letture
        nuovo = _cliente(s, url)
        with pytest.raises(ErroreApi) as err:
            await nuovo.stato(v.__class__(vin=VIN, marca="nio", modello="NIO"))
        assert err.value.codice == "rate_limited"


async def test_un_auto_che_non_si_puo_leggere(cloud) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        finto.negate = {"vehicle_status"}
        with pytest.raises(ErroreApi, match="non dà i dati"):
            await _cliente(s, url).vetture()
        finto.negate = set()
        finto.vuote = {"vehicle_status"}
        with pytest.raises(ErroreApi, match="non conosce"):
            await _cliente(s, url).vetture()


# --- il login dal browser ----------------------------------------------------------------


def _indirizzi(url: str):
    return (
        patch.object(modulo_cliente, "AUTORIZZA", f"{url}/oauth2/authorize"),
        patch.object(modulo_cliente, "URL_TOKEN", f"{url}/api/2/oauth/token"),
        patch.object(modulo_cliente, "API", url),
    )


async def test_avvia_e_completa_il_browser(hass: HomeAssistant, cloud) -> None:
    finto, url = cloud
    dati = {"client_id": CLIENT_ID, "client_secret": SEGRETO, "vin": VIN.lower()}
    a, b, c = _indirizzi(url)
    with a, b, c:
        d = await nio.avvia_browser(hass, dati)
        assert d.codice_utente == ""
        assert d.link.startswith(f"{url}/oauth2/authorize?")
        q = parse_qs(urlparse(d.link).query)
        assert q["state"] == [d.stato["state"]]
        finto.sfida = q["code_challenge"][0]
        with pytest.raises(ErroreCredenziali, match="login appena aperto"):
            await nio.completa_browser(hass, dati, d, f"{RITORNO}?code=CODICE&state=ALTRO")
        with pytest.raises(ErroreCredenziali):
            await nio.completa_browser(hass, dati, d, f"{RITORNO}?code=SBAGLIATO&state={d.stato['state']}")
        accesso = await nio.completa_browser(hass, dati, d, f"{RITORNO}?code=CODICE&state={d.stato['state']}")
        # Anche il solo codice va bene.
        di_nuovo = await nio.completa_browser(hass, dati, d, "CODICE")
        with pytest.raises(ErroreCredenziali, match="17 caratteri"):
            await nio.completa_browser(hass, {**dati, "vin": "CORTA"}, d, "CODICE")
    assert accesso.unique_id == f"nio_{VIN.lower()}" == di_nuovo.unique_id
    assert accesso.titolo == f"NIO · {VIN}"
    assert accesso.dati["vin"] == VIN
    assert accesso.dati["token"]["refresh_token"] == "R1"
    assert [v.vin for v in accesso.vetture] == [VIN]


async def test_un_codice_vecchio_si_dice(hass: HomeAssistant, cloud) -> None:
    finto, url = cloud
    finto.sfida = "un-altra-sfida"
    d = Dispositivo(link="", codice_utente="", stato={"verifier": "v", "state": "S"})
    a, b, c = _indirizzi(url)
    with a, b, c, pytest.raises(ErroreCredenziali):
        await nio.completa_browser(hass, {"client_id": CLIENT_ID, "client_secret": SEGRETO, "vin": VIN}, d, "CODICE")


# --- dentro Home Assistant -------------------------------------------------------------


@pytest.fixture
def catalogo():
    """Il catalogo punta NIO alla marca nativa (se non lo fa già)."""
    if PER_ID["nio"].nativo == "nio":
        yield
        return
    with patch.dict(PER_ID, {"nio": replace(PER_ID["nio"], nativo="nio")}):
        yield


async def test_configurare_nio_dal_catalogo(hass: HomeAssistant, cloud, catalogo) -> None:
    finto, url = cloud
    a, b, c = _indirizzi(url)
    with a, b, c:
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "nio"})
        assert r["step_id"] == "credenziali"
        r = await hass.config_entries.flow.async_configure(
            r["flow_id"], {"client_id": CLIENT_ID, "client_secret": SEGRETO, "vin": VIN}
        )
        assert r["step_id"] == "browser"
        q = parse_qs(urlparse(r["description_placeholders"]["link"]).query)
        finto.sfida = q["code_challenge"][0]
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"codice": "SBAGLIATO"})
        assert r["errors"] == {"base": "codice"}
        r = await hass.config_entries.flow.async_configure(
            r["flow_id"], {"codice": f"{RITORNO}?code=CODICE&state={q['state'][0]}"}
        )
        await hass.async_block_till_done()
        assert r["type"] is FlowResultType.CREATE_ENTRY
        assert r["title"] == f"NIO · {VIN}"
        assert r["result"].unique_id == f"nio_{VIN.lower()}"
        assert r["data"][CONF_MARCA] == "nio"
        assert r["data"]["token"]["access_token"] == "A-LOGIN"
        assert hass.states.get("sensor.nio_8901_batteria").state == "52"
        assert hass.states.get("sensor.nio_8901_autonomia").state == "270"
        assert hass.states.get("lock.nio_8901_serratura") is None  # niente comandi
        # Le ultime letture buone sono già nella voce.
        assert r["result"].data["memoria"]["soc_status"]["soc"] == 52
        await hass.config_entries.async_unload(r["result"].entry_id)
    assert finto.scambi == 1  # il setup riusa il token del login


async def test_il_token_rinnovato_finisce_nella_voce(hass: HomeAssistant, cloud, catalogo) -> None:
    finto, url = cloud
    voce = MockConfigEntry(
        domain=DOMAIN,
        title=f"NIO · {VIN}",
        unique_id=f"nio_{VIN.lower()}",
        data={
            CONF_FONTE: FONTE_CLOUD,
            CONF_MARCA: "nio",
            "marchio_catalogo": "nio",
            "client_id": CLIENT_ID,
            "client_secret": SEGRETO,
            "vin": VIN,
            "token": _token("VECCHIO", "R1", tra=-60),
            "memoria": {"soc_status": {"soc": 47, "sample_timestamp": T - 600_000}},
        },
    )
    voce.add_to_hass(hass)
    finto.vuote = {"soc_status"}
    a, b, c = _indirizzi(url)
    with a, b, c:
        assert await hass.config_entries.async_setup(voce.entry_id)
        await hass.async_block_till_done()
        assert voce.state is ConfigEntryState.LOADED
        assert voce.data["token"]["refresh_token"] == "R2"
        # La carica ricordata riparte dopo il riavvio.
        assert hass.states.get("sensor.nio_8901_batteria").state == "47"
        assert voce.runtime_data.auto[0]._normale.total_seconds() == 300
        await hass.config_entries.async_unload(voce.entry_id)
    assert (finto.ruotati, finto.scambi) == (1, 0)


async def test_un_refresh_revocato_chiede_il_reauth(hass: HomeAssistant, cloud, catalogo) -> None:
    _finto, url = cloud
    voce = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"nio_{VIN.lower()}",
        data={
            CONF_FONTE: FONTE_CLOUD,
            CONF_MARCA: "nio",
            "marchio_catalogo": "nio",
            "client_id": CLIENT_ID,
            "client_secret": SEGRETO,
            "vin": VIN,
            "token": _token("VECCHIO", "R-REVOCATO", tra=-60),
        },
    )
    voce.add_to_hass(hass)
    a, b, c = _indirizzi(url)
    with a, b, c:
        assert not await hass.config_entries.async_setup(voce.entry_id)
        await hass.async_block_till_done()
    assert voce.state is ConfigEntryState.SETUP_ERROR
    flussi = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == config_entries.SOURCE_REAUTH for f in flussi)
