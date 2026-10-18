"""Volvo: le risposte delle API, il login nel browser, il cliente contro un cloud finto, e Home Assistant."""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import hashlib
import inspect
import json
from pathlib import Path
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

from custom_components.gdaauto import config_flow
from custom_components.gdaauto.const import CONF_FONTE, CONF_MARCA, DOMAIN, FONTE_CLOUD
from custom_components.gdaauto.marche import MARCHE, volvo
from custom_components.gdaauto.marche.base import ACCESSO_CODICE_BROWSER, Dispositivo
from custom_components.gdaauto.marche.catalogo import PER_ID
from custom_components.gdaauto.marche.errori import (
    CodiceSbagliato,
    ErroreApi,
    ErroreComando,
    ErroreCredenziali,
    ErroreRete,
)
from custom_components.gdaauto.marche.volvo import cliente as modulo_cliente
from custom_components.gdaauto.marche.volvo.cliente import (
    SCOPE,
    ClienteVolvo,
    Quota,
    chi_e,
    comandi_di,
    leggi_incollato,
    nuovo_verifier,
    scambia_codice,
)
from custom_components.gdaauto.marche.volvo.dati import comandi_da, interpreta, vettura_da
from custom_components.gdaauto.vettura import StatoRicarica, Vettura

from . import volvo_campioni as c

API_KEY = "chiave-vcc-di-prova"
CLIENT_ID = "gdaauto-mario"
CLIENT_SECRET = "segreto-mario"
SUB = "a1b2c3d4-volvo-id"


def _d(r: dict[str, Any]) -> dict[str, Any]:
    return r["data"]


def _letture_ex30(**altre: Any) -> dict[str, Any]:
    letture = {
        "doors": _d(c.PORTE),
        "windows": _d(c.FINESTRINI),
        "engine-status": _d(c.MOTORE),
        "odometer": _d(c.ODOMETRO),
        "statistics": _d(c.STATISTICHE_EX30),
        "command-accessibility": _d(c.DISPONIBILE),
        "energia": c.ENERGIA_EX30,
        "location": _d(c.POSIZIONE),
        "tyres": _d(c.GOMME),
        "diagnostics": _d(c.DIAGNOSTICA),
        "warnings": _d(c.AVVISI),
        "brakes": _d(c.FRENI),
        "engine": _d(c.AVVISI_MOTORE),
    }
    letture.update(altre)
    return letture


# --- l'interpretazione ------------------------------------------------------------


def test_una_ex30_carica_e_chiusa() -> None:
    ex30 = vettura_da(c.DETTAGLI[c.VIN_EX30], "volvo")
    s = interpreta(_letture_ex30(), ex30)
    assert s.batteria == 90
    # L'autonomia è quella delle statistiche, come nell'app; l'Energy API è il ripiego.
    assert s.autonomia_km == 250
    assert s.odometro_km == 30000
    assert s.capacita_batteria_kwh == 66.0
    assert s.capacita_kwh == 66.0
    assert s.consumo_medio_kwh_100km == 22.6
    assert s.stato_ricarica == StatoRicarica.COMPLETATA
    assert s.in_carica is False
    assert s.cavo_collegato is True
    assert s.ricarica_rapida is False
    # Due minuti a carica finita non sono un tempo di ricarica.
    assert s.tempo_ricarica_min is None
    assert s.limite_ricarica == 90
    # chargingPower in ERROR: non si sa, ma non sta caricando.
    assert s.potenza_ricarica_diretta_kw == 0.0
    assert s.potenza_ricarica_kw == 0.0
    assert s.chiusa is True
    assert s.portiere_aperte is False
    assert s.bagagliaio_aperto is False
    assert s.finestrini_aperti is False  # il tetto «UNSPECIFIED» non conta
    assert s.quadro_acceso is False
    assert s.pneumatici_allarme is False
    assert s.pneumatici_kpa == {}  # le API pubbliche non danno le pressioni
    assert s.in_sonno is False
    assert (s.latitudine, s.longitudine) == (57.72537482589284, 11.849843629550225)
    assert s.posizione_con_segno is True
    assert s.v2l_attivo is None
    assert s.clima_acceso is None  # le API non dicono il clima
    assert s.letto == datetime(2025, 8, 7, 14, 49, 50, tzinfo=UTC)
    # La diagnostica: i valori del cloud e gli avvisi accesi, mai la posizione.
    assert s.grezzo["doors.centralLock"] == "LOCKED"
    assert s.grezzo["diagnostics.distanceToService"] == "29000 km"
    assert s.grezzo["location.heading"] == "90"
    assert s.grezzo["avvisi"] == ["brakeLightLeftWarning", "washerFluidLevelWarning"]
    assert "chargingPower" not in str(s.grezzo)
    assert "57.725" not in str(s.grezzo)


def test_una_xc40_in_carica_in_miglia() -> None:
    letture = {"energia": c.ENERGIA_XC40, "odometer": _d(c.ODOMETRO_MIGLIA)}
    s = interpreta(letture)
    assert s.batteria == 53
    assert s.autonomia_km == 241.4  # 150 mi
    assert s.odometro_km == 16093.4  # 10 000 mi
    assert s.stato_ricarica == StatoRicarica.IN_CARICA
    assert s.in_carica is True
    assert s.cavo_collegato is True
    assert s.ricarica_rapida is False
    assert s.tempo_ricarica_min == 1440
    assert s.potenza_ricarica_kw == 1.386
    assert s.limite_ricarica == 90
    assert s.grezzo["energia.chargingCurrentLimit"] == "32 ampere"
    assert s.capacita_batteria_kwh is None  # senza l'anagrafica non si sa


def test_una_xc90_plug_in_ferma() -> None:
    xc90 = vettura_da(c.DETTAGLI[c.VIN_XC90], "volvo")
    s = interpreta({"energia": c.ENERGIA_XC90, "statistics": _d(c.STATISTICHE_XC90), "fuel": _d(c.CARBURANTE)}, xc90)
    assert s.batteria == 87.3
    assert s.autonomia_km == 43
    assert s.autonomia_carburante_km == 804
    assert s.autonomia_totale_km == 847
    assert s.consumo_medio_kwh_100km == 19.9
    assert s.stato_ricarica == StatoRicarica.NON_IN_CARICA
    assert s.cavo_collegato is False
    assert s.ricarica_rapida is False
    assert s.limite_ricarica is None  # ERROR_READING_PROPERTY
    assert s.tempo_ricarica_min is None
    assert s.potenza_ricarica_diretta_kw == 0.0
    assert s.capacita_batteria_kwh == 18.819
    assert s.grezzo["fuel.fuelAmount"] == "47.3 l"


def test_la_fuel_api_e_la_v1_quando_l_energy_api_v2_non_c_e() -> None:
    assert interpreta({"fuel": _d(c.CARBURANTE)}).batteria == 87.3
    s = interpreta({"ricarica_v1": _d(c.RICARICA_V1)})
    assert s.batteria == 58
    assert s.autonomia_km == 250
    assert s.stato_ricarica == StatoRicarica.IN_CARICA
    assert s.cavo_collegato is True
    assert s.ricarica_rapida is True
    assert s.tempo_ricarica_min == 780


def test_aperta_accesa_con_una_gomma_bassa_e_addormentata() -> None:
    s = interpreta(
        {
            "doors": _d(c.PORTE_APERTE),
            "windows": _d(c.TETTO_APERTO),
            "engine-status": _d(c.MOTORE_ACCESO),
            "tyres": _d(c.GOMMA_BASSA),
            "command-accessibility": _d(c.ADDORMENTATA),
        }
    )
    assert s.chiusa is False
    assert s.portiere_aperte is True  # «AJAR» è aperta
    assert s.bagagliaio_aperto is True
    assert s.finestrini_aperti is True  # il tetto
    assert s.quadro_acceso is True
    assert s.pneumatici_allarme is True
    assert s.in_sonno is True
    assert s.grezzo["command-accessibility.availabilityStatus.motivo"] == "POWER_SAVING_MODE"


def test_quello_che_non_si_sa_resta_none() -> None:
    s = interpreta({"tyres": _d(c.GOMME_SCONOSCIUTE), "energia": {"chargingStatus": {"status": "ERROR"}}})
    assert s.pneumatici_allarme is None
    assert s.stato_ricarica is None
    assert s.potenza_ricarica_diretta_kw is None
    s = interpreta({})
    assert (s.batteria, s.autonomia_km, s.odometro_km, s.chiusa, s.latitudine, s.letto) == (None,) * 6
    assert s.grezzo == {}


def test_l_auto_che_scarica_fuori_e_v2l() -> None:
    s = interpreta({"energia": {"chargingStatus": {"status": "OK", "value": "DISCHARGING"}}})
    assert s.v2l_attivo is True
    assert s.stato_ricarica == StatoRicarica.NON_IN_CARICA


def test_l_anagrafica_e_i_comandi() -> None:
    v = vettura_da(c.DETTAGLI[c.VIN_EX30], "volvo", comandi_da(c.comandi(c.VIN_EX30)))
    assert (v.vin, v.modello, v.nome, v.anno, v.colore) == (
        c.VIN_EX30,
        "EX30",
        "Volvo EX30",
        2024,
        "Crystal White Pearl",
    )
    assert v.extra["carburante"] == "NONE"
    assert v.extra["comandi"]["HONK_AND_FLASH"] == "honk-flash"
    assert v.extra["comandi"]["CLIMATIZATION_START"] == "climatization-start"
    assert comandi_di(v) == {"chiudi", "apri", "trova", "clima"}
    assert volvo.comandi_di(v) == comandi_di(v)
    solo_luci = vettura_da(c.DETTAGLI[c.VIN_XC40], "volvo", comandi_da(c.comandi(c.VIN_XC40, "FLASH")))
    assert comandi_di(solo_luci) == {"trova"}
    assert solo_luci.colore == "Silver Dawn"
    # Senza href il pezzo dell'indirizzo si ricava dal nome.
    assert comandi_da({"data": [{"command": "HONK_AND_FLASH"}, {"command": "LOCK_REDUCED_GUARD"}]}) == {
        "HONK_AND_FLASH": "honk-flash",
        "LOCK_REDUCED_GUARD": "lock-reduced-guard",
    }
    assert comandi_di(Vettura(vin="X", marca="volvo", modello="")) == set()


# --- il login nel browser --------------------------------------------------------------


def _jwt(corpo: dict[str, Any]) -> str:
    return "e30." + base64.urlsafe_b64encode(json.dumps(corpo).encode()).decode().rstrip("=") + ".firma"


def test_pkce_l_indirizzo_e_l_account() -> None:
    verifier, sfida = nuovo_verifier()
    assert 43 <= len(verifier) <= 128
    assert sfida == base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    link = modulo_cliente.indirizzo_login(CLIENT_ID, "https://x/cb", sfida, "S1")
    q = parse_qs(urlparse(link).query)
    assert link.startswith("https://volvoid.eu.volvocars.com/as/authorization.oauth2?")
    assert q["response_type"] == ["code"] and q["code_challenge_method"] == ["S256"]
    assert set(q["scope"][0].split()) == set(SCOPE)
    assert {"openid", "conve:lock", "conve:unlock", "conve:honk_flash", "location:read"} <= set(SCOPE)
    assert chi_e({"id_token": _jwt({"sub": SUB, "email": "Mario@Esempio.IT"})}) == {
        "sub": SUB,
        "email": "mario@esempio.it",
    }
    assert chi_e({"access_token": _jwt({"sub": SUB})}) == {"sub": SUB}
    assert chi_e({"access_token": "opaco"}) == {}


def test_si_incolla_l_indirizzo_o_il_codice() -> None:
    assert leggi_incollato("https://my.home-assistant.io/redirect/oauth?code=abc123&state=S1") == ("abc123", "S1")
    assert leggi_incollato(" http://ha.local:8123/auth/external/callback?state=S2&code=xyz ") == ("xyz", "S2")
    assert leggi_incollato("  abc123  ") == ("abc123", None)
    assert leggi_incollato("code=abc&state=S3") == ("abc", "S3")
    with pytest.raises(ErroreCredenziali, match="access_denied"):
        leggi_incollato("https://my.home-assistant.io/redirect/oauth?error=access_denied&state=S1")
    with pytest.raises(CodiceSbagliato):
        leggi_incollato("https://my.home-assistant.io/redirect/oauth")
    with pytest.raises(CodiceSbagliato):
        leggi_incollato("")


def test_la_quota_delle_24_ore() -> None:
    ora = [1000.0]
    q = Quota(lambda: ora[0], limite=2)
    q.prendi("a")
    q.prendi("b")
    with pytest.raises(ErroreRete):
        q.prendi("c")
    q.prendi("comando", comando=True)  # i comandi hanno il loro margine
    ora[0] += 24 * 3600
    assert q.usate == 0


# --- il cloud finto -----------------------------------------------------------------------


@dataclass
class FintoVolvo:
    sfida: str = ""
    validi: set[str] = field(default_factory=lambda: {"A1"})
    ruotati: int = 0
    guasto_login: bool = False
    guasto_api: bool = False
    senza_scope: str = ""  # uno scope che il login non concede
    esito: str = "COMPLETED"
    vietate: set[str] = field(default_factory=lambda: {f"{c.VIN_XC90}/location"})
    comandi_inviati: list[str] = field(default_factory=list)
    chiamate: list[str] = field(default_factory=list)
    porte: dict[str, Any] = field(default_factory=lambda: dict(c.PORTE))
    rotte_400: set[str] = field(default_factory=set)
    elenco_vietato: bool = False

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/as/token.oauth2", self._token)
        app.router.add_route("*", "/{resto:.*}", self._api)
        return app

    def _scope(self) -> str:
        return " ".join(s for s in SCOPE if s != self.senza_scope)

    async def _token(self, r: web.Request) -> web.Response:
        if self.guasto_login:
            return web.Response(status=502, text="Bad Gateway")
        atteso = "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
        if r.headers.get("Authorization") != atteso:
            return web.json_response({"error": "invalid_client", "error_description": "Invalid client"}, status=401)
        m = await r.post()
        if m["grant_type"] == "authorization_code":
            impronta = base64.urlsafe_b64encode(hashlib.sha256(str(m["code_verifier"]).encode()).digest())
            if m["code"] != "C1" or impronta.rstrip(b"=").decode() != self.sfida:
                return web.json_response(
                    {"error": "invalid_grant", "error_description": "Authorization code is invalid"}, status=400
                )
            assert m["redirect_uri"] == modulo_cliente.REDIRECT_URI
            return web.json_response(
                {
                    "access_token": "A1",
                    "refresh_token": "R1",
                    "id_token": _jwt({"sub": SUB}),
                    "token_type": "Bearer",
                    "expires_in": 1799,
                    "scope": self._scope(),
                }
            )
        if m["grant_type"] == "refresh_token":
            if m["refresh_token"] != f"R{self.ruotati + 1}":
                return web.json_response({"error": "invalid_grant", "error_description": "token expired"}, status=400)
            self.ruotati += 1
            nuovo = f"A{self.ruotati + 1}"
            self.validi = {nuovo}
            return web.json_response(
                {"access_token": nuovo, "refresh_token": f"R{self.ruotati + 1}", "expires_in": 1799}
            )
        return web.json_response({"error": "unsupported_grant_type"}, status=400)

    async def _api(self, r: web.Request) -> web.Response:
        resto = r.match_info["resto"]
        self.chiamate.append(f"{r.method} {resto}")
        if r.headers.get("vcc-api-key") != API_KEY:
            return web.json_response(
                {"statusCode": 401, "message": "Access denied due to invalid subscription key."}, status=401
            )
        if r.headers.get("Authorization", "").removeprefix("Bearer ") not in self.validi:
            return web.json_response(
                {"status": 401, "error": {"message": "Unauthorized", "description": "x"}}, status=401
            )
        if self.guasto_api:
            return web.Response(status=503, text="Service Unavailable")
        parti = resto.split("/")
        if resto == "connected-vehicle/v2/vehicles":
            if self.elenco_vietato:
                return web.json_response({"status": 403, "error": {"message": "Forbidden"}}, status=403)
            return web.json_response(c.ELENCO)
        vin = parti[3] if len(parti) > 3 else ""
        cosa = "/".join(parti[4:])
        if f"{vin}/{cosa}" in self.rotte_400:
            return web.json_response({"status": 400, "error": {"message": "Bad request"}}, status=400)
        if f"{vin}/{cosa}" in self.vietate:
            return web.json_response(
                {"status": 403, "error": {"message": "Forbidden", "description": "Not allowed"}}, status=403
            )
        if parti[0] == "connected-vehicle":
            if r.method == "POST" and cosa.startswith("commands/"):
                self.comandi_inviati.append(f"{vin[-4:]} {cosa.removeprefix('commands/')}")
                if self.esito == "422":
                    return web.json_response({"status": 422, "error": {"message": "Unprocessable"}}, status=422)
                return web.json_response({"data": {"vin": vin, "invokeStatus": self.esito, "message": "msg"}})
            risorse: dict[str, Any] = {
                "": {"data": c.DETTAGLI[vin]},
                "commands": c.comandi(vin),
                "doors": self.porte,
                "windows": c.FINESTRINI,
                "engine-status": c.MOTORE,
                "odometer": c.ODOMETRO,
                "statistics": c.STATISTICHE_XC90 if vin == c.VIN_XC90 else c.STATISTICHE_EX30,
                "command-accessibility": c.DISPONIBILE,
                "tyres": c.GOMME,
                "diagnostics": c.DIAGNOSTICA,
                "warnings": c.AVVISI,
                "brakes": c.FRENI,
                "engine": c.AVVISI_MOTORE,
                "fuel": c.CARBURANTE,
            }
            if cosa in risorse:
                return web.json_response(risorse[cosa])
        if parti[0] == "energy" and parti[1] == "v2":
            if cosa == "capabilities":
                return web.json_response(c.CAPACITA_SI)
            if cosa == "state":
                return web.json_response(
                    {c.VIN_EX30: c.ENERGIA_EX30, c.VIN_XC40: c.ENERGIA_XC40}.get(vin, c.ENERGIA_XC90)
                )
        if parti[0] == "location" and cosa == "location":
            return web.json_response(c.POSIZIONE)
        return web.json_response({"status": 404, "error": {"message": "Not found"}}, status=404)


@pytest.fixture
async def cloud(socket_enabled: None) -> AsyncGenerator[tuple[FintoVolvo, str]]:
    finto = FintoVolvo()
    runner = web.AppRunner(finto.app())
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    url = f"http://127.0.0.1:{runner.addresses[0][1]}"
    with patch.object(modulo_cliente, "AUTH", url), patch.object(modulo_cliente, "API", url):
        yield finto, url
    await runner.cleanup()


def _token(access: str = "A1", refresh: str = "R1", fra: timedelta = timedelta(minutes=30)) -> dict[str, Any]:
    return {
        "access_token": access,
        "refresh_token": refresh,
        "scade": (datetime.now(UTC) + fra).isoformat(),
        "scope": " ".join(SCOPE),
    }


class Orologio:
    def __init__(self) -> None:
        self.ora = 1_000_000.0

    def __call__(self) -> float:
        return self.ora


def _cliente(s: aiohttp.ClientSession, token: dict[str, Any] | None = None, **kw: Any) -> ClienteVolvo:
    kw.setdefault("quota", Quota())
    return ClienteVolvo(s, API_KEY, CLIENT_ID, CLIENT_SECRET, token or _token(), **kw)


# --- il cliente --------------------------------------------------------------------------


async def test_il_codice_si_scambia_coi_token(cloud) -> None:
    finto, _ = cloud
    verifier, finto.sfida = nuovo_verifier()
    async with aiohttp.ClientSession() as s:
        token = await scambia_codice(s, CLIENT_ID, CLIENT_SECRET, "C1", verifier)
        assert token["access_token"] == "A1" and token["refresh_token"] == "R1"
        assert "conve:lock" in token["scope"]
        assert chi_e(token)["sub"] == SUB
        with pytest.raises(CodiceSbagliato):
            await scambia_codice(
                s, CLIENT_ID, CLIENT_SECRET, "C1", "un-altro-verifier-lungo-abbastanza-per-pkce-000000"
            )
        with pytest.raises(ErroreCredenziali, match="client"):
            await scambia_codice(s, CLIENT_ID, "sbagliato", "C1", verifier)
        finto.guasto_login = True
        with pytest.raises(ErroreRete):
            await scambia_codice(s, CLIENT_ID, CLIENT_SECRET, "C1", verifier)


async def test_le_auto_elettriche_e_plug_in_coi_loro_comandi(cloud) -> None:
    finto, _ = cloud
    async with aiohttp.ClientSession() as s:
        vetture = await _cliente(s).vetture()
    # La S90 diesel non è un'auto per GdAAuto.
    assert [v.vin for v in vetture] == [c.VIN_EX30, c.VIN_XC40, c.VIN_XC90]
    assert [v.nome for v in vetture] == ["Volvo EX30", "Volvo XC40", "Volvo XC90"]
    assert vetture[0].extra["capacita_kwh"] == 66.0
    assert comandi_di(vetture[0]) == {"chiudi", "apri", "trova", "clima"}
    assert "GET connected-vehicle/v2/vehicles" in finto.chiamate


async def test_senza_lo_scope_il_comando_non_si_offre(cloud) -> None:
    finto, _ = cloud
    finto.senza_scope = "conve:unlock"
    token = _token()
    token["scope"] = finto._scope()
    async with aiohttp.ClientSession() as s:
        vetture = await _cliente(s, token).vetture()
    assert comandi_di(vetture[0]) == {"chiudi", "trova", "clima"}


async def test_lo_stato_di_una_ex30(cloud) -> None:
    finto, _ = cloud
    orologio = Orologio()
    async with aiohttp.ClientSession() as s:
        cl = _cliente(s, orologio=orologio)
        ex30 = (await cl.vetture())[0]
        finto.chiamate.clear()
        stato = await cl.stato(ex30)
        assert stato.batteria == 90
        assert stato.autonomia_km == 250
        assert stato.chiusa is True
        assert stato.latitudine == 57.72537482589284
        assert stato.pneumatici_allarme is False
        assert stato.capacita_kwh == 66.0
        assert stato.grezzo["avvisi"] == ["brakeLightLeftWarning", "washerFluidLevelWarning"]
        prima = len(finto.chiamate)
        # 6 della Connected Vehicle, capacità ed energia, posizione, e le 5 lente.
        assert prima == 6 + 2 + 1 + 5
        # Al giro dopo né le capacità né le lente: sono già note.
        await cl.stato(ex30)
        assert len(finto.chiamate) - prima == 6 + 1 + 1
        # Mezz'ora dopo tornano le lente.
        orologio.ora += 31 * 60
        prima = len(finto.chiamate)
        await cl.stato(ex30)
        assert len(finto.chiamate) - prima == 6 + 1 + 1 + 5


async def test_la_posizione_negata_si_salta_per_un_po(cloud) -> None:
    finto, _ = cloud
    orologio = Orologio()
    async with aiohttp.ClientSession() as s:
        cl = _cliente(s, orologio=orologio)
        xc90 = (await cl.vetture())[2]
        stato = await cl.stato(xc90)
        assert stato.latitudine is None
        assert stato.batteria == 87.3
        assert stato.autonomia_totale_km == 847
        assert stato.grezzo["fuel.fuelAmount"] == "47.3 l"
        finto.chiamate.clear()
        await cl.stato(xc90)
        assert not any(ch.startswith("GET location") for ch in finto.chiamate)
        orologio.ora += 7 * 3600
        finto.chiamate.clear()
        await cl.stato(xc90)
        assert any(ch.startswith("GET location") for ch in finto.chiamate)


async def test_il_token_scaduto_si_rinnova_e_si_salva(cloud) -> None:
    finto, _ = cloud
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        cl = _cliente(s, _token("VECCHIO", "R1", timedelta(minutes=-5)), salva=salvati.append)
        vetture = await cl.vetture()
        await cl.stato(vetture[0])
    assert finto.ruotati == 1  # una volta sola, anche con le letture in parallelo
    assert [t["refresh_token"] for t in salvati] == ["R2"]
    assert salvati[0]["access_token"] == "A2"
    assert "conve:lock" in salvati[0]["scope"]  # lo scope del login resta


async def test_un_401_rinnova_e_riprova_una_volta(cloud) -> None:
    finto, _ = cloud
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        cl = _cliente(s, _token("REVOCATO", "R1"), salva=salvati.append)
        vetture = await cl.vetture()
        assert len(vetture) == 3
        stato = await cl.stato(vetture[1])
        assert stato.in_carica is True
    assert finto.ruotati == 1 and len(salvati) == 1


async def test_il_refresh_rifiutato_e_la_chiave_sbagliata_chiedono_di_rientrare(cloud) -> None:
    _finto, _ = cloud
    async with aiohttp.ClientSession() as s:
        with pytest.raises(ErroreCredenziali, match="Volvo ID"):
            await _cliente(s, _token("VECCHIO", "R9", timedelta(minutes=-5))).accedi()
        senza_chiave = ClienteVolvo(s, "chiave-sbagliata", CLIENT_ID, CLIENT_SECRET, _token(), quota=Quota())
        with pytest.raises(ErroreCredenziali, match="chiave VCC"):
            await senza_chiave.vetture()


async def test_i_guasti_sono_rete_non_credenziali(cloud) -> None:
    finto, _ = cloud
    async with aiohttp.ClientSession() as s:
        cl = _cliente(s)
        ex30 = (await cl.vetture())[0]
        finto.guasto_api = True
        with pytest.raises(ErroreRete):
            await cl.vetture()
        with pytest.raises(ErroreRete):
            await cl.stato(ex30)
        finto.guasto_api = False
        finto.guasto_login = True
        with pytest.raises(ErroreRete):
            await _cliente(s, _token("A1", "R1", timedelta(minutes=-5))).accedi()


async def test_un_cloud_che_non_risponde_e_rete(socket_enabled: None) -> None:
    with (
        patch.object(modulo_cliente, "AUTH", "http://127.0.0.1:9"),
        patch.object(modulo_cliente, "API", "http://127.0.0.1:9"),
    ):
        async with aiohttp.ClientSession() as s:
            with pytest.raises(ErroreRete):
                await _cliente(s, _token(fra=timedelta(minutes=-1))).accedi()
            with pytest.raises(ErroreRete):
                await _cliente(s).vetture()


async def test_un_404_e_vuoto_un_400_e_un_errore_dell_api_e_l_elenco_negato_e_credenziali(cloud) -> None:
    finto, _ = cloud
    async with aiohttp.ClientSession() as s:
        cl = _cliente(s)
        assert await cl._chiedi("GET", "prova", "/sconosciuto") == {}
        finto.rotte_400.add(f"{c.VIN_EX30}/doors")
        ex30 = (await cl.vetture())[0]
        with pytest.raises(ErroreApi) as err:
            await cl._risorsa(c.VIN_EX30, "doors", f"/connected-vehicle/v2/vehicles/{c.VIN_EX30}/doors")
        assert err.value.http == 400
        # Una lettura rotta fra tante non ferma lo stato.
        assert (await cl.stato(ex30)).batteria == 90
        finto.elenco_vietato = True
        with pytest.raises(ErroreCredenziali, match="scope"):
            await cl.vetture()


async def test_i_comandi(cloud) -> None:
    finto, _ = cloud
    async with aiohttp.ClientSession() as s:
        cl = _cliente(s)
        ex30 = (await cl.vetture())[0]
        await cl.chiudi(ex30, None)
        await cl.apri(ex30, None)
        await cl.trova(ex30, None)
        await cl.clima(ex30, True, None, 21)
        await cl.clima(ex30, False, None)
        assert finto.comandi_inviati == [
            "0001 lock",
            "0001 unlock",
            "0001 honk-flash",
            "0001 climatization-start",
            "0001 climatization-stop",
        ]
        finto.esito = "VEHICLE_IN_SLEEP"
        with pytest.raises(ErroreComando, match="dorme"):
            await cl.chiudi(ex30, None)
        finto.esito = "422"
        with pytest.raises(ErroreComando, match="adesso"):
            await cl.chiudi(ex30, None)
        with pytest.raises(ErroreComando):
            await cl.ricarica(ex30, True, None)
        with pytest.raises(ErroreComando):
            await cl.limite_ricarica(ex30, 80, None)
        senza = Vettura(vin=c.VIN_EX30, marca="volvo", modello="EX30", extra={"comandi": {"FLASH": "flash"}})
        with pytest.raises(ErroreComando, match="non accetta"):
            await cl.chiudi(senza, None)
        finto.esito = "COMPLETED"
        await cl.trova(senza, None)
        assert finto.comandi_inviati[-1] == "0001 flash"


# --- il contratto ------------------------------------------------------------------------------


def test_il_contratto_di_volvo() -> None:
    assert MARCHE["volvo"] is volvo
    assert volvo.ACCESSO == ACCESSO_CODICE_BROWSER
    assert [x.nome for x in volvo.CAMPI] == ["api_key", "client_id", "client_secret"]
    assert volvo.VUOLE_PIN is False
    assert not getattr(volvo, "SENZA_COMANDI", False)
    assert 5 <= volvo.INTERVALLO_MINUTI <= 30
    assert volvo.INTERVALLO_IN_MOTO_SECONDI >= 120
    assert inspect.iscoroutinefunction(volvo.avvia_browser)
    assert inspect.iscoroutinefunction(volvo.completa_browser)
    assert set(volvo.NOTA) == {"it", "en"}
    assert "volvo" in PER_ID


def test_il_modulo_non_importa_home_assistant() -> None:
    import ast

    for nome in ("cliente.py", "dati.py"):
        albero = ast.parse((Path(modulo_cliente.__file__).parent / nome).read_text())
        for nodo in ast.walk(albero):
            if isinstance(nodo, ast.ImportFrom | ast.Import):
                moduli = [nodo.module or ""] if isinstance(nodo, ast.ImportFrom) else [a.name for a in nodo.names]
                assert not any(m.startswith("homeassistant") for m in moduli)


# --- dentro Home Assistant -------------------------------------------------------------

DATI = {"api_key": f" {API_KEY} ", "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "marchio": "volvo"}


async def _avvia(hass: HomeAssistant, finto: FintoVolvo) -> Dispositivo:
    dispositivo = await volvo.avvia_browser(hass, DATI)
    finto.sfida = parse_qs(urlparse(dispositivo.link).query)["code_challenge"][0]
    return dispositivo


async def test_il_login_nel_browser_dall_indirizzo_o_dal_codice(hass: HomeAssistant, cloud) -> None:
    finto, url = cloud
    d = await _avvia(hass, finto)
    assert d.link.startswith(f"{url}/as/authorization.oauth2?")
    q = parse_qs(urlparse(d.link).query)
    assert q["client_id"] == [CLIENT_ID]
    assert q["redirect_uri"] == [modulo_cliente.REDIRECT_URI]
    assert q["state"] == [d.stato["state"]]
    assert d.codice_utente == ""
    finale = f"https://my.home-assistant.io/redirect/oauth?code=C1&state={d.stato['state']}"
    accesso = await volvo.completa_browser(hass, DATI, d, finale)
    assert accesso.unique_id == f"volvo_{SUB}"
    assert accesso.titolo == "Volvo · EX30, XC40, XC90"
    assert [v.vin for v in accesso.vetture] == [c.VIN_EX30, c.VIN_XC40, c.VIN_XC90]
    assert accesso.dati["api_key"] == API_KEY  # senza spazi
    assert accesso.dati["token"]["refresh_token"] == "R1"
    assert "id_token" not in accesso.dati["token"]
    assert accesso.dati["marchio"] == "volvo"

    # Solo il codice va bene lo stesso; un indirizzo di un altro login no.
    d = await _avvia(hass, finto)
    assert (await volvo.completa_browser(hass, DATI, d, "C1")).unique_id == f"volvo_{SUB}"
    with pytest.raises(CodiceSbagliato):
        await volvo.completa_browser(hass, DATI, d, "https://my.home-assistant.io/redirect/oauth?code=C1&state=altro")
    with pytest.raises(CodiceSbagliato):
        await volvo.completa_browser(hass, DATI, d, "C2")
    with pytest.raises(ErroreCredenziali):
        await volvo.avvia_browser(hass, {"api_key": API_KEY})


def _voce(token: dict[str, Any]) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Volvo · EX30, XC40, XC90",
        unique_id=f"volvo_{SUB}",
        data={
            CONF_FONTE: FONTE_CLOUD,
            CONF_MARCA: "volvo",
            "marchio": "volvo",
            "api_key": API_KEY,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "token": token,
        },
    )


async def test_le_auto_in_home_assistant_col_token_che_ruota(hass: HomeAssistant, cloud) -> None:
    finto, _ = cloud
    voce = _voce(_token("VECCHIO", "R1", timedelta(minutes=-5)))
    voce.add_to_hass(hass)
    with patch.object(modulo_cliente, "_QUOTE", {}):
        assert await hass.config_entries.async_setup(voce.entry_id)
        await hass.async_block_till_done()
        assert voce.state is ConfigEntryState.LOADED
        # Il token rinnovato si salva nella voce senza ricaricarla.
        assert voce.data["token"]["refresh_token"] == "R2"
        assert finto.ruotati == 1
        assert hass.states.get("sensor.volvo_ex30_batteria").state == "90"
        assert hass.states.get("sensor.volvo_xc40_batteria").state == "53"
        assert hass.states.get("sensor.volvo_xc90_batteria").state == "87.3"
        assert hass.states.get("sensor.volvo_ex30_autonomia").state == "250"
        assert hass.states.get("sensor.volvo_ex30_odometro").state == "30000"
        assert hass.states.get("lock.volvo_ex30_serratura").state == "locked"
        assert hass.states.get("device_tracker.volvo_ex30_posizione") is not None
        auto = next(a for a in voce.runtime_data.auto if a.vettura.vin == c.VIN_XC40)
        assert auto._normale == timedelta(minutes=5)
        assert auto.update_interval == timedelta(seconds=120)  # in carica

        await hass.services.async_call("lock", "lock", {"entity_id": "lock.volvo_ex30_serratura"}, blocking=True)
        assert finto.comandi_inviati == ["0001 lock"]
        await hass.config_entries.async_unload(voce.entry_id)


async def test_il_refresh_rifiutato_porta_al_reauth(hass: HomeAssistant, cloud) -> None:
    _finto, _ = cloud
    voce = _voce(_token("VECCHIO", "R9", timedelta(minutes=-5)))
    voce.add_to_hass(hass)
    with patch.object(modulo_cliente, "_QUOTE", {}):
        assert not await hass.config_entries.async_setup(voce.entry_id)
        await hass.async_block_till_done()
    # Il refresh rifiutato chiede di rientrare (ConfigEntryAuthFailed), non di riprovare più tardi.
    assert voce.state is ConfigEntryState.SETUP_ERROR
    assert "rifiuta" in str(voce.reason)


_FLUSSO_COLLEGATO = "avvia_browser" in inspect.getsource(config_flow) and PER_ID["volvo"].nativo == "volvo"


@pytest.mark.skipif(
    not _FLUSSO_COLLEGATO, reason="il passo «codice_browser» generico e il catalogo non puntano ancora a Volvo"
)
async def test_configurare_una_volvo_dal_catalogo(hass: HomeAssistant, cloud) -> None:
    """Dal marchio del catalogo fino alle entità, qualunque sia il nome dei passi del modulo."""
    finto, _ = cloud
    with patch.object(modulo_cliente, "_QUOTE", {}):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "volvo"})
        for _ in range(5):
            if r["type"] is not FlowResultType.FORM:
                break
            campi = {str(k) for k in r["data_schema"].schema} if r.get("data_schema") else set()
            if {"api_key", "client_id", "client_secret"} <= campi:
                r = await hass.config_entries.flow.async_configure(
                    r["flow_id"], {k: v for k, v in DATI.items() if k in campi} | {"api_key": API_KEY}
                )
                continue
            link = next(
                (v for v in (r.get("description_placeholders") or {}).values() if "code_challenge" in str(v)), ""
            )
            assert link, r
            q = parse_qs(urlparse(link).query)
            finto.sfida = q["code_challenge"][0]
            incolla = next(iter(campi))
            finale = f"https://my.home-assistant.io/redirect/oauth?code=C1&state={q['state'][0]}"
            r = await hass.config_entries.flow.async_configure(r["flow_id"], {incolla: finale})
        await hass.async_block_till_done()
        assert r["type"] is FlowResultType.CREATE_ENTRY, r
        assert r["result"].unique_id == f"volvo_{SUB}"
        assert r["data"][CONF_MARCA] == "volvo"
        assert hass.states.get("sensor.volvo_ex30_batteria").state == "90"
