"""Porsche: le misure del cloud, il cliente contro un cloud finto, il login dal browser e Home Assistant."""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
import hashlib
import json
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
from custom_components.gdaauto.marche import MARCHE, porsche
from custom_components.gdaauto.marche.base import ACCESSO_CODICE_BROWSER, Dispositivo
from custom_components.gdaauto.marche.catalogo import PER_ID
from custom_components.gdaauto.marche.errori import (
    ErroreApi,
    ErroreComando,
    ErroreCredenziali,
    ErrorePin,
    ErroreRete,
)
from custom_components.gdaauto.marche.porsche import cliente as modulo_cliente
from custom_components.gdaauto.marche.porsche.cliente import (
    CLIENT_ID,
    REDIRECT_URI,
    ClientePorsche,
    codice_da,
    comandi_di,
    hash_pin,
    indirizzo_browser,
)
from custom_components.gdaauto.marche.porsche.dati import (
    capacita_comandi,
    consumo_medio,
    interpreta,
    limite_ricarica,
    misure,
    stato_ricarica,
    vettura_da,
)
from custom_components.gdaauto.vettura import StatoRicarica, Vettura

from .porsche_campioni import (
    CAPACITA_CAYENNE,
    CAPACITA_TAYCAN,
    CAYENNE_FERMA,
    ELENCO,
    MACAN,
    MACAN_COMPLETATA,
    TAYCAN_IN_CARICA,
    VIAGGI,
    VIN_CAYENNE,
    VIN_TAYCAN,
)

EMAIL = "Ferdinand@Esempio.it"
PIN = "1234"
SFIDA_PIN = "A1B2C3D4E5F60718"
ADESSO = datetime(2026, 10, 18, 10, 0, tzinfo=UTC)


def _taycan(**extra: Any) -> Vettura:
    v = vettura_da(ELENCO[0])
    assert v is not None
    v.extra.update(extra)
    return v


# --- l'interpretazione ------------------------------------------------------------


def test_una_taycan_in_carica() -> None:
    s = interpreta(TAYCAN_IN_CARICA, _taycan(), adesso=ADESSO)
    assert s.batteria == 63
    assert s.autonomia_km == 251
    assert s.odometro_km == 13247
    assert s.stato_ricarica == StatoRicarica.IN_CARICA
    assert s.in_carica is True
    assert s.cavo_collegato is True
    assert s.potenza_ricarica_kw == 10.9
    # Finisce alle 14:30 di Stoccarda, cioè le 12:30 UTC: fra due ore e mezza.
    assert s.tempo_ricarica_min == 150
    # Il profilo attivo dice 80 %.
    assert s.limite_ricarica == 80
    assert s.clima_acceso is False
    assert s.temperatura_clima_c == 20.0
    assert s.chiusa is True
    assert (s.portiere_aperte, s.bagagliaio_aperto, s.finestrini_aperti) == (False, False, False)
    assert s.pneumatici_kpa == {
        "anteriore_sinistro": 260.0,
        "anteriore_destro": 260.0,
        "posteriore_sinistro": 240.0,
        "posteriore_destro": 290.0,
    }
    # Mezzo bar sotto: allarme, come nell'app.
    assert s.pneumatici_allarme is True
    assert (s.latitudine, s.longitudine, s.posizione_con_segno) == (48.834323, 9.152218, True)
    assert s.freno_stazionamento is True
    assert s.letto == datetime(2026, 10, 18, 9, 59, 30, tzinfo=UTC)
    # Una elettrica non ha serbatoio, e la misura spenta non c'è.
    assert s.carburante_percento is None
    assert "FUEL_LEVEL" not in s.grezzo


def test_una_cayenne_e_hybrid_ferma_senza_cavo() -> None:
    v = vettura_da(ELENCO[1])
    s = interpreta(CAYENNE_FERMA, v, adesso=ADESSO)
    assert s.batteria == 100
    # Nelle ibride l'autonomia è quella elettrica; RANGE è il totale.
    assert s.autonomia_km == 72
    assert s.autonomia_totale_km == 610
    assert s.carburante_percento == 54
    assert s.stato_ricarica == StatoRicarica.NON_IN_CARICA
    assert s.cavo_collegato is False
    assert s.potenza_ricarica_kw == 0.0
    assert s.tempo_ricarica_min is None
    # Ricarica immediata senza limite: fino al 100 %.
    assert s.limite_ricarica == 100
    assert s.clima_acceso is True
    assert s.temperatura_clima_c == 22.0
    assert s.chiusa is False
    assert s.portiere_aperte is True
    assert s.bagagliaio_aperto is False
    # Il tetto apribile conta come un finestrino: ci entra la pioggia.
    assert s.finestrini_aperti is True
    # Lisbona è a ovest: il segno resta.
    assert (s.latitudine, s.longitudine) == (38.722252, -9.139337)
    # Le gomme spente dalla privacy non si sanno.
    assert s.pneumatici_kpa == {}
    assert s.pneumatici_allarme is None
    assert s.letto == datetime(2026, 10, 17, 18, 2, tzinfo=UTC)


def test_una_macan_electric_con_le_partenze() -> None:
    v = vettura_da(MACAN)
    s = interpreta(MACAN_COMPLETATA, v)
    assert (s.batteria, s.autonomia_km) == (85, 447)
    assert s.stato_ricarica == StatoRicarica.COMPLETATA
    assert s.cavo_collegato is True
    assert s.limite_ricarica == 85
    assert s.potenza_ricarica_kw == 0.0
    assert s.chiusa is None
    assert s.odometro_km is None


def test_un_auto_a_benzina_non_ha_batteria_di_trazione() -> None:
    v = Vettura(vin="X", marca="porsche", modello="911", extra={"motore": "COMBUSTION"})
    risposta = {
        "measurements": [
            {"key": "BATTERY_LEVEL", "status": {"isEnabled": True}, "value": {"percent": 88}},
            {"key": "FUEL_LEVEL", "status": {"isEnabled": True}, "value": {"percent": 40}},
            {"key": "RANGE", "status": {"isEnabled": True}, "value": {"kilometers": 380}},
        ]
    }
    s = interpreta(risposta, v)
    assert s.batteria is None
    assert s.autonomia_km is None
    assert (s.carburante_percento, s.autonomia_totale_km) == (40, 380)
    assert comandi_di(v) == {"chiudi", "apri", "trova"}


def test_gli_stati_della_ricarica_e_il_limite() -> None:
    assert stato_ricarica("CHARGING") == StatoRicarica.IN_CARICA
    assert stato_ricarica("instant_charging") == StatoRicarica.IN_CARICA
    assert stato_ricarica("CHARGING_COMPLETED") == StatoRicarica.COMPLETATA
    assert stato_ricarica("CHARGING_ERROR") == StatoRicarica.ERRORE
    assert stato_ricarica("NOT_CHARGING") == StatoRicarica.NON_IN_CARICA
    assert stato_ricarica("SCONOSCIUTO") is None
    assert stato_ricarica(None) is None
    # Le auto nuove lo dicono direttamente.
    assert limite_ricarica({"CHARGING_SUMMARY": {"targetSoC": 90, "mode": "PROFILE"}}) == 90
    assert limite_ricarica({}) is None


def test_l_anagrafica_e_i_comandi() -> None:
    taycan = vettura_da(ELENCO[0])
    cayenne = vettura_da(ELENCO[1])
    assert taycan is not None and cayenne is not None
    assert (taycan.vin, taycan.marca, taycan.modello, taycan.titolo, taycan.anno) == (
        VIN_TAYCAN,
        "porsche",
        "Taycan 4S",
        "La Blu",
        2023,
    )
    assert taycan.extra["motore"] == "BEV"
    assert taycan.condivisa is False
    assert cayenne.condivisa is True
    assert cayenne.titolo == "Cayenne E-Hybrid"
    assert vettura_da({"modelName": "Taycan"}) is None

    accesi, clima = capacita_comandi(CAPACITA_TAYCAN)
    assert "CHARGING_SETTINGS_EDIT" not in accesi
    assert clima == {"minTemperature": 288.15, "maxTemperature": 303.15}
    taycan.extra["comandi"] = accesi
    assert comandi_di(taycan) == {"chiudi", "apri", "trova", "clima", "ricarica", "limite_ricarica"}
    taycan.extra["remoto"] = False
    assert comandi_di(taycan) == set()


def test_il_consumo_dalle_statistiche_e_le_misure_spente() -> None:
    assert consumo_medio(VIAGGI) == 23.4
    assert consumo_medio({"measurements": []}) is None
    assert misure({"measurements": [{"key": "X", "status": {"isEnabled": False}, "value": {"a": 1}}]}) == {}


def test_il_contratto() -> None:
    assert MARCHE["porsche"] is porsche
    assert porsche.ACCESSO == ACCESSO_CODICE_BROWSER
    assert porsche.CAMPI == ()
    assert porsche.VUOLE_PIN is True
    assert porsche.INTERVALLO_MINUTI == 15
    assert callable(porsche.avvia_browser) and callable(porsche.completa_browser)
    # Nessun login col modulo HTML di Porsche: niente password nel cliente.
    assert not hasattr(modulo_cliente.ClientePorsche, "_login")


def test_il_pin_e_il_codice_incollato() -> None:
    atteso = hashlib.sha512(bytes.fromhex(PIN + SFIDA_PIN)).hexdigest().upper()
    assert hash_pin(PIN, SFIDA_PIN) == atteso
    with pytest.raises(ErrorePin):
        hash_pin("12a4x", SFIDA_PIN)
    assert codice_da(f"{REDIRECT_URI}?code=ABC&state=S1") == ("ABC", "S1")
    assert codice_da("code=ABC&state=S1") == ("ABC", "S1")
    assert codice_da("  ABC  ") == ("ABC", None)
    with pytest.raises(ErroreCredenziali, match="access_denied"):
        codice_da(f"{REDIRECT_URI}?error=access_denied&state=S1")
    with pytest.raises(ErroreCredenziali):
        codice_da("non è un codice")


def test_il_link_del_browser() -> None:
    link = indirizzo_browser("verificatore-di-prova", "S1")
    q = parse_qs(urlparse(link).query)
    assert link.startswith("https://identity.porsche.com/authorize?")
    assert q["client_id"] == [CLIENT_ID]
    assert q["redirect_uri"] == [REDIRECT_URI]
    assert q["code_challenge_method"] == ["S256"]
    assert q["code_challenge"] == [_sfida("verificatore-di-prova")]
    assert "offline_access" in q["scope"][0]
    assert q["audience"] == ["https://api.porsche.com"]


# --- il cloud finto -------------------------------------------------------------------


def _sfida(verificatore: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verificatore.encode()).digest()).decode().rstrip("=")


def _jwt(contenuto: dict[str, Any]) -> str:
    parte = base64.urlsafe_b64encode(json.dumps(contenuto).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJSUzI1NiJ9.{parte}.firma"


@dataclass
class FintoPorsche:
    """I token di Porsche ID e l'API dell'app, quanto basta per le prove."""

    sfida: str = ""
    guasto_api: bool = False
    rifiuta_trova: bool = False
    validi: set[str] = field(default_factory=set)
    ruotati: int = 0
    scambi: int = 0
    letture: list[str] = field(default_factory=list)
    comandi: list[dict[str, Any]] = field(default_factory=list)
    esiti: dict[str, int] = field(default_factory=dict)

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/oauth/token", self._token)
        app.router.add_get("/app/connect/v1/vehicles", self._elenco)
        app.router.add_get("/app/connect/v1/vehicles/{vin}", self._auto)
        app.router.add_post("/app/connect/v1/vehicles/{vin}/commands", self._comando)
        app.router.add_get("/app/connect/v1/vehicles/{vin}/commands/{id}", self._esito)
        return app

    async def _token(self, r: web.Request) -> web.Response:
        modulo = await r.post()
        assert modulo["client_id"] == CLIENT_ID
        assert r.headers["X-Client-ID"]
        if modulo["grant_type"] == "authorization_code":
            if modulo["code"] != "CODICE" or _sfida(str(modulo["code_verifier"])) != self.sfida:
                return web.json_response({"error": "invalid_grant"}, status=403)
            assert modulo["redirect_uri"] == REDIRECT_URI
            self.scambi += 1
            self.validi = {f"L{self.scambi}"}
            return web.json_response(
                {
                    "access_token": f"L{self.scambi}",
                    "refresh_token": "R1",
                    "id_token": _jwt({"email": EMAIL, "sub": "auth0|pid-42"}),
                    "expires_in": 3600,
                    "token_type": "Bearer",
                }
            )
        if modulo["grant_type"] == "refresh_token":
            if modulo["refresh_token"] != f"R{self.ruotati + 1}":
                return web.json_response({"error": "invalid_grant", "error_description": "revoked"}, status=403)
            self.ruotati += 1
            nuovo = f"A{self.ruotati}"
            self.validi = {nuovo}
            return web.json_response(
                {"access_token": nuovo, "refresh_token": f"R{self.ruotati + 1}", "expires_in": 3600}
            )
        return web.json_response({"error": "unsupported_grant_type"}, status=400)

    def _non_autorizzato(self, r: web.Request) -> bool:
        return r.headers.get("Authorization", "").removeprefix("Bearer ") not in self.validi

    async def _elenco(self, r: web.Request) -> web.Response:
        if self.guasto_api:
            return web.Response(status=503, text="Service Unavailable")
        self.letture.append("elenco")
        if self._non_autorizzato(r):
            return web.json_response({"message": "Unauthorized"}, status=401)
        return web.json_response(ELENCO)

    async def _auto(self, r: web.Request) -> web.Response:
        if self.guasto_api:
            return web.Response(status=503, text="Service Unavailable")
        if self._non_autorizzato(r):
            return web.json_response({"message": "Unauthorized"}, status=401)
        assert "wakeUpJob" not in r.query, "leggere non deve svegliare l'auto"
        vin = r.match_info["vin"]
        if "cf" in r.query:
            self.letture.append(f"comandi {vin}")
            return web.json_response(CAPACITA_TAYCAN if vin == VIN_TAYCAN else CAPACITA_CAYENNE)
        mf = r.query.getall("mf")
        if "TRIP_STATISTICS_LONG_TERM" in mf:
            self.letture.append(f"viaggi {vin}")
            return web.json_response(VIAGGI)
        assert "BATTERY_LEVEL" in mf and "MDK_PAIRING_PASSWORD" not in mf
        self.letture.append(f"stato {vin}")
        return web.json_response(TAYCAN_IN_CARICA if vin == VIN_TAYCAN else CAYENNE_FERMA)

    async def _comando(self, r: web.Request) -> web.Response:
        if self._non_autorizzato(r):
            return web.json_response({"message": "Unauthorized"}, status=401)
        corpo = await r.json()
        self.comandi.append(corpo)
        if corpo["key"] == "SPIN_CHALLENGE":
            return web.json_response({"data": {"challenge": SFIDA_PIN}})
        if corpo["key"] == "UNLOCK" and corpo["payload"]["spin"]["hash"] != hash_pin(PIN, SFIDA_PIN):
            return web.json_response({"message": "Invalid PIN"}, status=403)
        id_ = f"cmd-{len(self.comandi)}"
        self.esiti[id_] = 0
        return web.json_response({"status": {"id": id_, "result": "ACCEPTED"}})

    async def _esito(self, r: web.Request) -> web.Response:
        id_ = r.match_info["id"]
        self.esiti[id_] += 1
        if self.esiti[id_] < 2:
            return web.json_response({"status": {"id": id_, "result": "ACCEPTED"}})
        ultimo = self.comandi[int(id_.removeprefix("cmd-")) - 1]
        if self.rifiuta_trova and ultimo["key"] == "HONK_FLASH":
            return web.json_response({"status": {"id": id_, "result": "ERROR", "errorType": "VEHICLE_OFFLINE"}})
        return web.json_response({"status": {"id": id_, "result": "PERFORMED"}})


@pytest.fixture
async def cloud(socket_enabled: None) -> AsyncGenerator[tuple[FintoPorsche, str]]:
    finto = FintoPorsche()
    runner = web.AppRunner(finto.app())
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    with patch.object(modulo_cliente, "_ESITO_OGNI_S", 0.01):
        yield finto, f"http://127.0.0.1:{runner.addresses[0][1]}"
    await runner.cleanup()


def _cliente(
    s: aiohttp.ClientSession, url: str, token: dict[str, Any] | None = None, salvati: list | None = None
) -> ClientePorsche:
    return ClientePorsche(s, token, salvati.append if salvati is not None else None, identita=url, api=f"{url}/app")


def _token(access: str, refresh: str, tra: timedelta) -> dict[str, Any]:
    return {"access_token": access, "refresh_token": refresh, "scade": (datetime.now(UTC) + tra).isoformat()}


async def test_dal_codice_allo_stato(cloud) -> None:
    finto, url = cloud
    finto.sfida = _sfida("verificatore")
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, salvati=salvati)
        await c.scambia_codice("CODICE", "verificatore")
        assert c.chi == {"email": EMAIL, "sub": "auth0|pid-42"}
        vetture = await c.vetture()
        assert [v.vin for v in vetture] == [VIN_TAYCAN, VIN_CAYENNE]
        taycan, cayenne = vetture
        assert comandi_di(taycan) == {"chiudi", "apri", "trova", "clima", "ricarica", "limite_ricarica"}
        assert taycan.extra["clima"]["maxTemperature"] == 303.15
        # L'utente secondario non ha i servizi remoti: niente comandi.
        assert comandi_di(cayenne) == set()
        stato = await c.stato(taycan)
        await c.stato(taycan)
    assert salvati[-1]["access_token"] == "L1"
    assert stato.batteria == 63
    assert stato.consumo_medio_kwh_100km == 23.4
    # Le statistiche dei viaggi una volta sola, non a ogni lettura.
    assert finto.letture.count(f"viaggi {VIN_TAYCAN}") == 1
    assert finto.letture.count(f"stato {VIN_TAYCAN}") == 2


async def test_un_codice_sbagliato_e_una_credenziale(cloud) -> None:
    finto, url = cloud
    finto.sfida = _sfida("verificatore")
    async with aiohttp.ClientSession() as s:
        with pytest.raises(ErroreCredenziali):
            await _cliente(s, url).scambia_codice("CODICE", "un-altro-verificatore")
    assert finto.scambi == 0


async def test_il_token_che_scade_si_rinnova_e_si_salva(cloud) -> None:
    finto, url = cloud
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, _token("VECCHIO", "R1", timedelta(minutes=2)), salvati)
        assert len(await c.vetture()) == 2
    assert finto.ruotati == 1
    assert salvati[-1]["access_token"] == "A1"
    assert salvati[-1]["refresh_token"] == "R2"


async def test_un_refresh_revocato_chiede_di_rientrare(cloud) -> None:
    _finto, url = cloud
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, _token("VECCHIO", "R-REVOCATO", timedelta(minutes=-1)))
        with pytest.raises(ErroreCredenziali, match="browser"):
            await c.vetture()
        with pytest.raises(ErroreCredenziali):
            await _cliente(s, url, {}).accedi()


async def test_un_token_rifiutato_si_rinnova_e_si_riprova_una_volta(cloud) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, _token("REVOCATO", "R1", timedelta(hours=1)))
        assert len(await c.vetture()) == 2
    assert finto.ruotati == 1
    assert finto.letture[:2] == ["elenco", "elenco"]


async def test_un_guasto_non_e_una_credenziale(cloud) -> None:
    finto, url = cloud
    finto.guasto_api = True
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, _token("L1", "R1", timedelta(hours=1)))
        finto.validi = {"L1"}
        with pytest.raises(ErroreRete):
            await c.vetture()
        with pytest.raises(ErroreRete):
            await c.stato(_taycan())


async def test_un_cloud_spento(socket_enabled: None) -> None:
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, "http://127.0.0.1:9", _token("L1", "R1", timedelta(hours=1)))
        with pytest.raises(ErroreRete):
            await c.vetture()
        with pytest.raises(ErroreRete):
            await _cliente(s, "http://127.0.0.1:9").scambia_codice("CODICE", "v")


async def test_un_errore_dell_api(cloud) -> None:
    finto, url = cloud
    finto.validi = {"L1"}
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, _token("L1", "R1", timedelta(hours=1)))
        with pytest.raises(ErroreApi) as preso:
            await c._chiedi("prova", "GET", "/connect/v1/nessuno")
    assert preso.value.http == 404


async def test_i_comandi(cloud) -> None:
    finto, url = cloud
    finto.validi = {"L1"}
    v = _taycan(comandi=capacita_comandi(CAPACITA_TAYCAN)[0], clima=capacita_comandi(CAPACITA_TAYCAN)[1])
    v.stato = interpreta(TAYCAN_IN_CARICA, v)
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, _token("L1", "R1", timedelta(hours=1)))
        await c.chiudi(v, None)
        await c.apri(v, PIN)
        await c.trova(v, None)
        await c.clima(v, True, None, 35)
        await c.clima(v, False, None)
        await c.ricarica(v, True, None)
        await c.limite_ricarica(v, 90, None)
    chiavi = [cmd["key"] for cmd in finto.comandi]
    assert chiavi == [
        "LOCK",
        "SPIN_CHALLENGE",
        "UNLOCK",
        "HONK_FLASH",
        "REMOTE_CLIMATIZER_START",
        "REMOTE_CLIMATIZER_STOP",
        "DIRECT_CHARGING_START",
        "CHARGING_PROFILES_EDIT",
    ]
    per_chiave = {cmd["key"]: cmd["payload"] for cmd in finto.comandi}
    assert per_chiave["UNLOCK"]["spin"] == {"challenge": SFIDA_PIN, "hash": hash_pin(PIN, SFIDA_PIN)}
    assert per_chiave["HONK_FLASH"]["mode"] == "HONK_AND_FLASH"
    # 35 °C è troppo: il massimo dell'auto, 30 °C, in kelvin.
    assert per_chiave["REMOTE_CLIMATIZER_START"]["targetTemperature"] == 303.15
    # Si cambia solo il profilo attivo.
    assert [p["minSoc"] for p in per_chiave["CHARGING_PROFILES_EDIT"]["list"]] == [90, 60]
    # Ogni comando aspetta l'esito.
    assert all(n == 2 for n in finto.esiti.values())


async def test_il_limite_della_macan_va_nelle_impostazioni(cloud) -> None:
    finto, url = cloud
    finto.validi = {"L1"}
    v = vettura_da(MACAN)
    v.stato = interpreta(MACAN_COMPLETATA, v)
    async with aiohttp.ClientSession() as s:
        await _cliente(s, url, _token("L1", "R1", timedelta(hours=1))).limite_ricarica(v, 10, None)
    assert finto.comandi == [{"key": "CHARGING_SETTINGS_EDIT", "payload": {"targetSoc": 25, "spin": None}}]


async def test_il_pin_e_il_comando_rifiutato(cloud) -> None:
    finto, url = cloud
    finto.validi = {"L1"}
    finto.rifiuta_trova = True
    v = _taycan()
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, _token("L1", "R1", timedelta(hours=1)))
        with pytest.raises(ErrorePin):
            await c.apri(v, None)
        with pytest.raises(ErrorePin):
            await c.apri(v, "9999")
        with pytest.raises(ErroreComando, match="VEHICLE_OFFLINE"):
            await c.trova(v, None)
    assert [cmd["key"] for cmd in finto.comandi] == ["SPIN_CHALLENGE", "UNLOCK", "HONK_FLASH"]


# --- il login dal browser ----------------------------------------------------------------


async def test_avvia_e_completa_il_browser(hass: HomeAssistant, cloud) -> None:
    finto, url = cloud
    with patch.object(modulo_cliente, "IDENTITA", url), patch.object(modulo_cliente, "API", f"{url}/app"):
        d = await porsche.avvia_browser(hass, {})
        assert d.codice_utente == ""
        assert d.link.startswith(f"{url}/authorize?")
        q = parse_qs(urlparse(d.link).query)
        assert q["state"] == [d.stato["state"]]
        finto.sfida = q["code_challenge"][0]
        with pytest.raises(ErroreCredenziali, match="altro login"):
            await porsche.completa_browser(hass, {}, d, f"{REDIRECT_URI}?code=CODICE&state=ALTRO")
        accesso = await porsche.completa_browser(hass, {}, d, f"{REDIRECT_URI}?code=CODICE&state={d.stato['state']}")
        # Anche il solo codice va bene.
        di_nuovo = await porsche.completa_browser(hass, {}, d, "CODICE")
    assert accesso.unique_id == "porsche_ferdinand@esempio.it" == di_nuovo.unique_id
    assert accesso.titolo == f"Porsche · {EMAIL}"
    assert accesso.dati["token"]["refresh_token"] == "R1"
    assert accesso.dati["email"] == EMAIL
    assert "password" not in accesso.dati
    assert [v.vin for v in accesso.vetture] == [VIN_TAYCAN, VIN_CAYENNE]


async def test_un_codice_vecchio_si_dice(hass: HomeAssistant, cloud) -> None:
    finto, url = cloud
    finto.sfida = "un-altra-sfida"
    d = Dispositivo(link="", codice_utente="", stato={"verificatore": "v", "state": "S"})
    with patch.object(modulo_cliente, "IDENTITA", url), pytest.raises(ErroreCredenziali):
        await porsche.completa_browser(hass, {}, d, "CODICE")


# --- dentro Home Assistant -------------------------------------------------------------


@pytest.fixture
def catalogo():
    """Il catalogo punta Porsche alla marca nativa (se non lo fa già)."""
    if PER_ID["porsche"].nativo == "porsche":
        yield
        return
    with patch.dict(PER_ID, {"porsche": replace(PER_ID["porsche"], nativo="porsche")}):
        yield


async def test_configurare_porsche_dal_catalogo(hass: HomeAssistant, cloud, catalogo) -> None:
    finto, url = cloud
    with patch.object(modulo_cliente, "IDENTITA", url), patch.object(modulo_cliente, "API", f"{url}/app"):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "porsche"})
        # Niente email né password: si va subito al browser.
        assert r["step_id"] == "browser"
        link = r["description_placeholders"]["link"]
        q = parse_qs(urlparse(link).query)
        finto.sfida = q["code_challenge"][0]
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"codice": "SBAGLIATO"})
        assert r["errors"] == {"base": "codice"}
        r = await hass.config_entries.flow.async_configure(
            r["flow_id"], {"codice": f"{REDIRECT_URI}?code=CODICE&state={q['state'][0]}"}
        )
        await hass.async_block_till_done()
        assert r["type"] is FlowResultType.CREATE_ENTRY
        assert r["title"] == f"Porsche · {EMAIL}"
        assert r["result"].unique_id == "porsche_ferdinand@esempio.it"
        assert r["data"][CONF_MARCA] == "porsche"
        assert r["data"]["token"]["access_token"] == "L1"
        assert hass.states.get("sensor.la_blu_batteria").state == "63"
        assert hass.states.get("sensor.cayenne_e_hybrid_batteria").state == "100"
        assert hass.states.get("lock.la_blu_serratura").state == "locked"
        # La Cayenne dell'utente secondario non si comanda.
        assert hass.states.get("lock.cayenne_e_hybrid_serratura") is None

        await hass.services.async_call("lock", "lock", {"entity_id": "lock.la_blu_serratura"}, blocking=True)
        assert finto.comandi[-1] == {"key": "LOCK", "payload": {"spin": None}}
        await hass.config_entries.async_unload(r["result"].entry_id)
    assert finto.scambi == 1  # il setup riusa il token del login


async def test_il_token_rinnovato_finisce_nella_voce(hass: HomeAssistant, cloud, catalogo) -> None:
    finto, url = cloud
    voce = MockConfigEntry(
        domain=DOMAIN,
        title=f"Porsche · {EMAIL}",
        unique_id="porsche_ferdinand@esempio.it",
        data={
            CONF_FONTE: FONTE_CLOUD,
            CONF_MARCA: "porsche",
            "marchio_catalogo": "porsche",
            "email": EMAIL,
            "token": _token("VECCHIO", "R1", timedelta(minutes=-5)),
        },
    )
    voce.add_to_hass(hass)
    with patch.object(modulo_cliente, "IDENTITA", url), patch.object(modulo_cliente, "API", f"{url}/app"):
        assert await hass.config_entries.async_setup(voce.entry_id)
        await hass.async_block_till_done()
        assert voce.state is ConfigEntryState.LOADED
        assert voce.data["token"]["refresh_token"] == "R2"
        assert hass.states.get("sensor.la_blu_batteria").state == "63"
        auto = voce.runtime_data.auto[0]
        assert auto._normale == timedelta(minutes=15)
        # In carica la si legge più spesso, ma con calma.
        assert auto.update_interval == timedelta(seconds=300)
        await hass.config_entries.async_unload(voce.entry_id)
    assert (finto.ruotati, finto.scambi) == (1, 0)


async def test_un_refresh_revocato_chiede_il_reauth(hass: HomeAssistant, cloud, catalogo) -> None:
    _finto, url = cloud
    voce = MockConfigEntry(
        domain=DOMAIN,
        unique_id="porsche_ferdinand@esempio.it",
        data={
            CONF_FONTE: FONTE_CLOUD,
            CONF_MARCA: "porsche",
            "marchio_catalogo": "porsche",
            "token": _token("VECCHIO", "R-REVOCATO", timedelta(minutes=-5)),
        },
    )
    voce.add_to_hass(hass)
    with patch.object(modulo_cliente, "IDENTITA", url), patch.object(modulo_cliente, "API", f"{url}/app"):
        assert not await hass.config_entries.async_setup(voce.entry_id)
        await hass.async_block_till_done()
    assert voce.state is ConfigEntryState.SETUP_ERROR
    flussi = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == config_entries.SOURCE_REAUTH for f in flussi)
    assert next(f for f in flussi if f["context"]["source"] == config_entries.SOURCE_REAUTH)["step_id"] == "browser"
