"""BMW CarData: i descrittori, il login col codice, il cliente contro un cloud finto, e il flusso di HA."""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import functools
import hashlib
import json
from typing import Any
from unittest.mock import patch

import aiohttp
from aiohttp import web
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.gdaauto.const import DOMAIN
from custom_components.gdaauto.marche import MARCHE, bmw
from custom_components.gdaauto.marche.base import InAttesa
from custom_components.gdaauto.marche.bmw import cliente as modulo_cliente
from custom_components.gdaauto.marche.bmw.cliente import (
    QUOTA_GIORNALIERA,
    ClienteBmw,
    Quota,
    chi_e,
    chiedi_codice,
    nuovo_verifier,
    scambia_codice,
)
from custom_components.gdaauto.marche.bmw.dati import DESCRITTORI, DESCRITTORI_RIDOTTI, interpreta
from custom_components.gdaauto.marche.catalogo import PER_ID
from custom_components.gdaauto.marche.errori import ErroreApi, ErroreCredenziali, ErroreRete
from custom_components.gdaauto.vettura import StatoRicarica, Vettura

from .bmw_campioni import (
    BASIC,
    CLIENT_ID,
    COUNTRYMAN_FERMA,
    FORMA_CON_NOME,
    GCID,
    I4_IN_CARICA,
    MAPPINGS,
    VIN,
    VIN_MINI,
)

# --- i descrittori ----------------------------------------------------------------------


def test_una_i4_in_carica() -> None:
    s = interpreta(I4_IN_CARICA)
    assert s.batteria == 57  # il «header», non la previsione
    assert s.autonomia_km == 287
    assert s.odometro_km == 23456
    assert s.stato_ricarica == StatoRicarica.IN_CARICA
    assert s.in_carica is True
    assert s.cavo_collegato is True
    assert s.potenza_ricarica_kw == 10.85
    assert s.tempo_ricarica_min == 135
    assert s.limite_ricarica == 80
    assert s.chiusa is True
    assert s.portiere_aperte is False
    assert s.bagagliaio_aperto is False
    assert s.finestrini_aperti is True
    assert (s.latitudine, s.longitudine) == (48.177567, 11.556123)
    assert s.posizione_con_segno is True
    assert s.pneumatici_kpa == {
        "anteriore_sinistro": 250,
        "anteriore_destro": 252,
        "posteriore_sinistro": 270,
        "posteriore_destro": 268,
    }
    assert s.letto == datetime(2026, 10, 18, 9, 55, 3, tzinfo=UTC)
    assert s.grezzo["vehicle.cabin.door.lock.status"] == "SECURED"
    assert "48.177567" not in str(s.grezzo)


def test_una_countryman_in_miglia_e_bar_col_cavo_staccato() -> None:
    s = interpreta(COUNTRYMAN_FERMA)
    assert s.batteria == 91
    assert s.autonomia_km == 241.4
    assert s.odometro_km == 16093.4
    # La carica è finita e il cavo non c'è più: non è in carica.
    assert s.stato_ricarica == StatoRicarica.NON_IN_CARICA
    assert s.cavo_collegato is False
    assert s.potenza_ricarica_kw == 0.0
    assert s.tempo_ricarica_min is None
    assert s.limite_ricarica == 90
    assert s.chiusa is False  # chiusa solo in parte vuol dire aperta
    assert s.portiere_aperte is True
    assert s.bagagliaio_aperto is None  # non lo dice
    assert s.finestrini_aperti is True  # il tetto è a vasistas
    assert s.quadro_acceso is False
    assert s.pneumatici_kpa == {"anteriore_sinistro": 250}
    assert s.latitudine is None


def test_la_forma_con_name_e_lo_stato_porte_come_oggetto() -> None:
    s = interpreta(FORMA_CON_NOME)
    assert s.stato_ricarica == StatoRicarica.PROGRAMMATA
    assert s.cavo_collegato is True
    assert s.chiusa is True


def test_una_risposta_vuota_non_dice_niente() -> None:
    s = interpreta({"telematicData": {"vehicle.vehicle.travelledDistance": {"value": None}}})
    assert (s.batteria, s.odometro_km, s.stato_ricarica, s.chiusa, s.letto) == (None, None, None, None, None)
    assert interpreta("non json").batteria is None


def test_il_container_ridotto_sta_in_quello_intero() -> None:
    assert set(DESCRITTORI_RIDOTTI) < set(DESCRITTORI)
    assert len(DESCRITTORI) == len(set(DESCRITTORI))
    assert all(d.startswith("vehicle.") for d in DESCRITTORI)


def test_pkce_e_l_account_dall_id_token() -> None:
    verifier, sfida = nuovo_verifier()
    assert 43 <= len(verifier) <= 128
    attesa = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert sfida == attesa
    corpo = base64.urlsafe_b64encode(json.dumps({"sub": "Mario.Rossi@Example.IT"}).encode()).decode().rstrip("=")
    assert chi_e({"id_token": f"x.{corpo}.y", "gcid": GCID}) == {"gcid": GCID, "email": "mario.rossi@example.it"}
    assert chi_e({"id_token": "rotto"}) == {}


def test_la_quota_delle_24_ore() -> None:
    ora = [1000.0]
    q = Quota(lambda: ora[0], limite=2)
    q.prendi("a")
    q.prendi("b")
    with pytest.raises(ErroreRete):
        q.prendi("c")
    ora[0] += 24 * 3600
    assert q.disponibile() and q.usate == 0
    q.esaurita()
    assert not q.disponibile()


# --- il cloud finto -----------------------------------------------------------------------


def _id_token(email: str) -> str:
    corpo = base64.urlsafe_b64encode(json.dumps({"sub": email, "gcid": GCID}).encode()).decode().rstrip("=")
    return f"intestazione.{corpo}.firma"


@dataclass
class FintoBmw:
    confermato: bool = False
    negato: bool = False
    guasto_login: bool = False
    validi: set[str] = field(default_factory=lambda: {"A1"})
    ruotati: int = 0
    sfida: str = ""
    containers: dict[str, dict[str, Any]] = field(default_factory=dict)
    rifiutati: set[str] = field(default_factory=set)  # container che BMW non serve (CU-400)
    stati: dict[str, Any] = field(default_factory=lambda: {VIN: I4_IN_CARICA, VIN_MINI: COUNTRYMAN_FERMA})
    vietati: set[str] = field(default_factory=set)  # auto che l'account non può leggere (CU-104)
    quota_finita: bool = False
    guasto_api: bool = False
    chiamate: list[str] = field(default_factory=list)

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/gcdm/oauth/device/code", self._codice)
        app.router.add_post("/gcdm/oauth/token", self._token)
        app.router.add_route("*", "/customers/{resto:.*}", self._api)
        return app

    async def _codice(self, r: web.Request) -> web.Response:
        m = await r.post()
        if m["client_id"] != CLIENT_ID:
            return web.json_response({"error": "invalid_client", "error_description": "unknown client"}, status=401)
        assert m["response_type"] == "device_code"
        assert "cardata:api:read" in str(m["scope"]).split()
        assert m["code_challenge_method"] == "S256"
        self.sfida = str(m["code_challenge"])
        return web.json_response(
            {
                "user_code": "sDzZXXmD",
                "device_code": "DC1",
                "interval": 5,
                "verification_uri": "https://customer.bmwgroup.com/oneid/link",
                "verification_uri_complete": "https://customer.bmwgroup.com/oneid/link?user_code=sDzZXXmD",
                "expires_in": 300,
            }
        )

    async def _token(self, r: web.Request) -> web.Response:
        if self.guasto_login:
            return web.Response(status=503, text="Service Unavailable")
        m = await r.post()
        assert m["client_id"] == CLIENT_ID
        if m["grant_type"] == "urn:ietf:params:oauth:grant-type:device_code":
            if self.negato:
                return web.json_response({"error": "access_denied"}, status=403)
            if not self.confermato:
                return web.json_response({"error": "authorization_pending"}, status=403)
            impronta = base64.urlsafe_b64encode(hashlib.sha256(str(m["code_verifier"]).encode()).digest())
            if m["device_code"] != "DC1" or impronta.rstrip(b"=").decode() != self.sfida:
                return web.json_response({"error": "invalid_grant"}, status=400)
            return web.json_response(
                {
                    "access_token": "A1",
                    "token_type": "Bearer",
                    "expires_in": 3599,
                    "refresh_token": "R1",
                    "id_token": _id_token("Mario.Rossi@example.it"),
                    "gcid": GCID,
                    "scope": "authenticate_user openid cardata:api:read cardata:streaming:read",
                }
            )
        if m["grant_type"] == "refresh_token":
            if m["refresh_token"] != f"R{self.ruotati + 1}":
                return web.json_response({"error": "invalid_grant", "error_description": "expired"}, status=400)
            self.ruotati += 1
            nuovo = f"A{self.ruotati + 1}"
            self.validi = {nuovo}
            return web.json_response(
                {
                    "access_token": nuovo,
                    "refresh_token": f"R{self.ruotati + 1}",
                    "id_token": "ID2",
                    "expires_in": 3599,
                    "gcid": GCID,
                }
            )
        return web.json_response({"error": "unsupported_grant_type"}, status=400)

    def _errore(self, codice: str, http: int) -> web.Response:
        return web.json_response({"exveErrorId": codice, "exveErrorMsg": "errore", "exveNote": ""}, status=http)

    async def _api(self, r: web.Request) -> web.Response:
        resto = r.match_info["resto"]
        self.chiamate.append(f"{r.method} {resto}")
        assert r.headers["x-version"] == "v1"
        if r.headers.get("Authorization", "").removeprefix("Bearer ") not in self.validi:
            return self._errore("CU-102", 401)
        if self.quota_finita:
            return self._errore("CU-429", 429)
        if self.guasto_api:
            return self._errore("CU-503", 503)
        if resto == "vehicles/mappings":
            return web.json_response(MAPPINGS)
        if resto.endswith("/basicData"):
            return web.json_response(BASIC[resto.split("/")[1]])
        if resto == "containers" and r.method == "GET":
            return web.json_response(
                {
                    "containers": [
                        {k: v for k, v in c.items() if k != "technicalDescriptors"} for c in self.containers.values()
                    ]
                }
            )
        if resto == "containers" and r.method == "POST":
            corpo = await r.json()
            assert corpo["technicalDescriptors"]
            nuovo = f"c{len(self.containers) + 1:012d}"
            self.containers[nuovo] = {**corpo, "containerId": nuovo, "state": "ACTIVE"}
            return web.json_response(self.containers[nuovo], status=201)
        if resto.endswith("/telematicData"):
            vin = resto.split("/")[1]
            if vin in self.vietati:
                return self._errore("CU-104", 403)
            container = r.query["containerId"]
            if container not in self.containers:
                return self._errore("CU-105", 403)
            if container in self.rifiutati:
                return self._errore("CU-400", 400)
            return web.json_response(self.stati[vin])
        return self._errore("CU-400", 404)


@pytest.fixture
async def cloud(socket_enabled: None) -> AsyncGenerator[tuple[FintoBmw, str]]:
    finto = FintoBmw()
    runner = web.AppRunner(finto.app())
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    yield finto, f"http://127.0.0.1:{runner.addresses[0][1]}"
    await runner.cleanup()


def _token(access: str = "A1", refresh: str = "R1", fra: timedelta = timedelta(hours=1)) -> dict[str, Any]:
    return {"access_token": access, "refresh_token": refresh, "scade": (datetime.now(UTC) + fra).isoformat()}


class Orologio:
    def __init__(self) -> None:
        self.ora = 1_000_000.0

    def __call__(self) -> float:
        return self.ora


# --- il login ------------------------------------------------------------------------------


async def test_il_codice_si_aspetta_poi_si_scambia(cloud) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        stato = await chiedi_codice(s, CLIENT_ID, auth=url)
        assert stato["link"].endswith("user_code=sDzZXXmD")
        assert stato["codice"] == "sDzZXXmD"
        assert stato["intervallo"] == 5
        with pytest.raises(InAttesa):
            await scambia_codice(s, CLIENT_ID, stato, auth=url)
        finto.confermato = True
        token = await scambia_codice(s, CLIENT_ID, stato, auth=url)
    assert token["access_token"] == "A1"
    assert token["refresh_token"] == "R1"
    assert token["gcid"] == GCID
    assert chi_e(token)["email"] == "mario.rossi@example.it"


async def test_negato_e_attesa_per_un_po_poi_e_un_rifiuto(cloud) -> None:
    finto, url = cloud
    finto.negato = True
    orologio = Orologio()
    async with aiohttp.ClientSession() as s:
        stato = await chiedi_codice(s, CLIENT_ID, auth=url)
        with pytest.raises(InAttesa):
            await scambia_codice(s, CLIENT_ID, stato, auth=url, adesso=orologio)
        orologio.ora += 600
        with pytest.raises(ErroreCredenziali):
            await scambia_codice(s, CLIENT_ID, stato, auth=url, adesso=orologio)


async def test_client_id_sbagliato_e_login_guasto(cloud) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        with pytest.raises(ErroreCredenziali):
            await chiedi_codice(s, "sbagliato", auth=url)
        stato = await chiedi_codice(s, CLIENT_ID, auth=url)
        finto.guasto_login = True
        with pytest.raises(ErroreRete):
            await scambia_codice(s, CLIENT_ID, stato, auth=url)


async def test_un_cloud_che_non_risponde_e_rete(socket_enabled: None) -> None:
    async with aiohttp.ClientSession() as s:
        with pytest.raises(ErroreRete):
            await chiedi_codice(s, CLIENT_ID, auth="http://127.0.0.1:9")


# --- il cliente ------------------------------------------------------------------------------


def _cliente(s: aiohttp.ClientSession, url: str, salvati: list[dict[str, Any]], **altro: Any) -> ClienteBmw:
    altro.setdefault("adesso", Orologio())
    token = altro.pop("token", None) or _token()
    return ClienteBmw(s, CLIENT_ID, token, salvati.append, auth=url, api=url, **altro)


async def test_le_auto_il_container_e_lo_stato(cloud) -> None:
    finto, url = cloud
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, salvati)
        i4, mini = await c.leggi_vetture()  # la condivisa non si legge
        assert (i4.vin, i4.modello, i4.titolo, i4.anno) == (VIN, "i4 eDrive40", "BMW i4 eDrive40", 2024)
        assert (mini.titolo, mini.extra["marchio"]) == ("MINI Countryman E", "MINI")
        stato = await c.stato(i4)
        assert stato.batteria == 57
        assert (await c.stato(mini)).autonomia_km == 241.4
    (container,) = finto.containers.values()
    assert container["name"] == "GdAAuto"
    assert container["technicalDescriptors"] == list(DESCRITTORI)
    assert salvati == [{"container_id": container["containerId"], "container_ridotto": False}]

    # Un altro accesso (un reauth) ritrova lo stesso container invece di farne un altro.
    async with aiohttp.ClientSession() as s:
        c2 = _cliente(s, url, [])
        assert await c2.prepara_container() == container["containerId"]
    assert len(finto.containers) == 1


async def test_le_auto_salvate_non_costano_chiamate(cloud) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, [], vetture_note=[{"vin": VIN, "modello": "i4 eDrive40", "nome": "BMW i4 eDrive40"}])
        (v,) = await c.vetture()
    assert v.titolo == "BMW i4 eDrive40"
    assert finto.chiamate == []


async def test_il_token_scaduto_si_rinnova_e_si_salva(cloud) -> None:
    finto, url = cloud
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, salvati, token=_token(fra=-timedelta(minutes=1)))
        await c.leggi_vetture()
    assert finto.ruotati == 1
    token = salvati[0]["token"]
    assert (token["access_token"], token["refresh_token"], token["id_token"]) == ("A2", "R2", "ID2")


async def test_un_401_rinnova_e_riprova_una_volta(cloud) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, [], token=_token(access="VECCHIO"))
        assert len(await c.leggi_vetture()) == 2
    assert finto.ruotati == 1


async def test_il_refresh_rifiutato_chiede_di_rientrare(cloud) -> None:
    _, url = cloud
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, [], token=_token(refresh="SCADUTO", fra=-timedelta(minutes=1)))
        with pytest.raises(ErroreCredenziali):
            await c.accedi()


async def test_i_guasti_sono_rete_non_credenziali(cloud) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        finto.guasto_login = True
        c = _cliente(s, url, [], token=_token(fra=-timedelta(minutes=1)))
        with pytest.raises(ErroreRete):
            await c.accedi()
        finto.guasto_login = False
        finto.guasto_api = True
        with pytest.raises(ErroreRete):
            await _cliente(s, url, []).leggi_vetture()
        with pytest.raises(ErroreRete):
            await _cliente(s, "http://127.0.0.1:9", []).leggi_vetture()


async def test_la_quota_distanzia_le_letture(cloud) -> None:
    finto, url = cloud
    orologio = Orologio()
    i4 = Vettura(vin=VIN, marca="bmw", modello="i4")
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, [], adesso=orologio, vetture_note=[{"vin": VIN}])
        primo = await c.stato(i4)
        letture = finto.chiamate.count(f"GET vehicles/{VIN}/telematicData")
        # Dieci minuti dopo: troppo presto, vale l'ultima lettura.
        orologio.ora += 600
        assert await c.stato(i4) is primo
        assert finto.chiamate.count(f"GET vehicles/{VIN}/telematicData") == letture
        # Mezz'ora dopo la prima si rilegge.
        orologio.ora += 1200
        assert await c.stato(i4) is not primo
        # BMW dice che la quota è finita: si tiene l'ultima lettura e ci si ferma.
        finto.quota_finita = True
        orologio.ora += 1800
        terzo = c._ultimi[VIN][1]
        assert await c.stato(i4) is terzo
        assert not c.quota.disponibile()
        # Senza una lettura da dare, è un guasto (non un problema di credenziali).
        with pytest.raises(ErroreRete):
            await c.stato(Vettura(vin=VIN_MINI, marca="bmw", modello="Countryman"))


async def test_la_quota_non_si_supera_mai(cloud) -> None:
    _, url = cloud
    orologio = Orologio()
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, [], adesso=orologio)
        for _ in range(QUOTA_GIORNALIERA):
            c.quota.prendi("prova")
        with pytest.raises(ErroreRete):
            await c.leggi_vetture()


async def test_il_container_cancellato_si_rifa(cloud) -> None:
    finto, url = cloud
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, salvati, container_id="c0000000000XX")
        stato = await c.stato(Vettura(vin=VIN, marca="bmw", modello="i4"))
    assert stato.batteria == 57
    assert salvati[-1]["container_id"] in finto.containers


async def test_un_container_rifiutato_passa_a_quello_ridotto(cloud) -> None:
    finto, url = cloud
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, salvati)
        finto.rifiutati.add(await c.prepara_container())
        stato = await c.stato(Vettura(vin=VIN, marca="bmw", modello="i4"))
    assert stato.batteria == 57
    assert salvati[-1]["container_ridotto"] is True
    ridotto = finto.containers[salvati[-1]["container_id"]]
    assert ridotto["technicalDescriptors"] == list(DESCRITTORI_RIDOTTI)


async def test_un_auto_non_permessa_e_un_errore_dell_api(cloud) -> None:
    finto, url = cloud
    finto.vietati.add(VIN)
    async with aiohttp.ClientSession() as s:
        with pytest.raises(ErroreApi) as err:
            await _cliente(s, url, []).stato(Vettura(vin=VIN, marca="bmw", modello="i4"))
    assert err.value.codice == "CU-104"


def test_senza_comandi() -> None:
    assert bmw.SENZA_COMANDI is True
    assert bmw.comandi_di(Vettura(vin=VIN, marca="bmw", modello="i4")) == set()
    assert bmw.INTERVALLO_MINUTI >= 30
    assert bmw.INTERVALLO_IN_MOTO_SECONDI == bmw.INTERVALLO_MINUTI * 60
    assert [c.nome for c in bmw.CAMPI] == ["client_id"]
    assert MARCHE["bmw"] is bmw
    assert PER_ID["bmw"].nativo == PER_ID["mini"].nativo == "bmw"


# --- il flusso di Home Assistant ------------------------------------------------------------


@pytest.fixture
def verso_il_finto(cloud):
    _, url = cloud
    with (
        patch.object(bmw, "chiedi_codice", functools.partial(chiedi_codice, auth=url)),
        patch.object(bmw, "scambia_codice", functools.partial(scambia_codice, auth=url)),
        patch.object(bmw, "ClienteBmw", functools.partial(ClienteBmw, auth=url, api=url)),
    ):
        yield cloud


async def test_configurare_una_bmw_col_codice(hass: HomeAssistant, verso_il_finto) -> None:
    finto, _ = verso_il_finto
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "bmw"})
    assert r["step_id"] == "credenziali"
    assert {str(k) for k in r["data_schema"].schema} == {"client_id"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"client_id": "sbagliato"})
    assert r["errors"] == {"base": "credenziali"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"client_id": f" {CLIENT_ID} "})
    assert r["step_id"] == "conferma_dispositivo"
    assert r["description_placeholders"]["codice"] == "sDzZXXmD"
    assert r["description_placeholders"]["link"].startswith("https://customer.bmwgroup.com/oneid/link")
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    assert r["errors"] == {"base": "in_attesa"}
    finto.confermato = True
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    await hass.async_block_till_done()
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["title"] == "BMW · mario.rossi@example.it"
    voce = r["result"]
    assert voce.unique_id == f"bmw_{GCID}"
    assert r["data"]["marchio"] == "bmw"
    assert r["data"]["client_id"] == CLIENT_ID
    assert r["data"]["container_id"] in finto.containers
    assert [v["vin"] for v in r["data"]["vetture"]] == [VIN, VIN_MINI]

    assert hass.states.get("sensor.bmw_i4_edrive40_batteria").state == "57"
    assert hass.states.get("sensor.mini_countryman_e_batteria").state == "91"
    assert hass.states.get("lock.bmw_i4_edrive40_serratura") is None

    # All'avvio si leggono solo gli stati: l'elenco delle auto e il container sono salvati.
    prima = len(finto.chiamate)
    assert await hass.config_entries.async_reload(voce.entry_id)
    await hass.async_block_till_done()
    assert all(c.endswith("/telematicData") for c in finto.chiamate[prima:])

    # Il token che ruota si salva senza ricaricare.
    dati_prima = voce.runtime_data
    dati_prima.conto.cliente._salva({"token": {**voce.data["token"], "refresh_token": "R9"}})
    await hass.async_block_till_done()
    assert voce.data["token"]["refresh_token"] == "R9"
    assert voce.runtime_data is dati_prima

    # Le opzioni: niente comandi.
    r = await hass.config_entries.options.async_init(voce.entry_id)
    assert "comandi_attivi" not in {str(k) for k in r["data_schema"].schema}


async def test_un_mini_entra_dal_suo_marchio(hass: HomeAssistant, verso_il_finto) -> None:
    finto, _ = verso_il_finto
    finto.confermato = True
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "mini"})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"client_id": CLIENT_ID})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    await hass.async_block_till_done()
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["title"].startswith("MINI · ")
    assert r["data"]["marchio"] == "mini"


def test_il_modulo_non_importa_home_assistant() -> None:
    import ast
    from pathlib import Path

    for nome in ("cliente.py", "dati.py"):
        albero = ast.parse((Path(modulo_cliente.__file__).parent / nome).read_text())
        for nodo in ast.walk(albero):
            if isinstance(nodo, ast.ImportFrom | ast.Import):
                moduli = [nodo.module or ""] if isinstance(nodo, ast.ImportFrom) else [a.name for a in nodo.names]
                assert not any(m.startswith("homeassistant") for m in moduli)


async def test_le_opzioni_di_bmw_si_salvano_coi_valori_predefiniti(hass) -> None:
    """Il ritmo di BMW (30 minuti anche in marcia) deve stare nei limiti delle opzioni."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.gdaauto.config_flow import GdaautoOptionsFlow
    from custom_components.gdaauto.const import DOMAIN

    voce = MockConfigEntry(domain=DOMAIN, data={"fonte": "cloud", "marca": "bmw"})
    voce.add_to_hass(hass)
    flusso = GdaautoOptionsFlow()
    flusso.hass = hass
    flusso.handler = voce.entry_id
    r = await flusso.async_step_init()
    schema = r["data_schema"]
    valori = schema({})
    assert valori["intervallo_in_moto_secondi"] == 1800
    assert valori["intervallo_minuti"] == 30
