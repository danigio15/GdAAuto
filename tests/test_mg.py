"""MG iSMART: la cifratura, lo stato, il cliente contro un cloud finto, e il flusso in Home Assistant."""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import json
import time
from typing import Any
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qsl

import aiohttp
from aiohttp import web
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.gdaauto.const import DOMAIN
from custom_components.gdaauto.marche import mg
from custom_components.gdaauto.marche.errori import ErroreComando, ErroreCredenziali, ErroreRete
from custom_components.gdaauto.marche.mg import ClienteMG, cripto
from custom_components.gdaauto.marche.mg.dati import (
    codice_da_limite,
    indice_temperatura,
    interpreta,
    limite_da_codice,
    vettura_da_json,
)
from custom_components.gdaauto.vettura import StatoRicarica, Vettura

from .mg_campioni import ELENCO, RICARICA, STATO, VIN

# --- la cifratura -------------------------------------------------------------------
# I vettori sono quelli di tests/security_test.py di saic-python-client-ng.


def test_la_firma_coi_vettori_della_libreria() -> None:
    comune = {"ora_ms": "20230514123000", "tenant": "1234", "tipo": "application/json", "token": "dummy_token"}
    assert (
        cripto.stringa_verifica(percorso="/api/v1/data", contenuto='{"key": "value"}', **comune)
        == "afd4eaf98af2d964f8ea840fc144ee7bae95dbeeeb251d5e3a01371442f92eeb"
    )
    assert (
        cripto.stringa_verifica(percorso="", contenuto='{"key": "value"}', **comune)
        == "ff8cb13ebcce5958e7fbfe602716c653fd72ce78842be87b6d50dccede198735"
    )
    assert (
        cripto.stringa_verifica(percorso="/api/v1/data", contenuto="", **comune)
        == "332c85836aa9afc864282436a740eb2cc778fafd1fea74dd887c1f8de5056de0"
    )


def test_il_corpo_cifrato_torna_indietro() -> None:
    firma = {
        "percorso": "/with/path?vin=zevin",
        "ora_ms": "1700000000000",
        "tenant": "2559",
        "tipo": "application/json",
    }
    cifrato = cripto.cifra_richiesta(contenuto='{"change":"me"}', token="T", **firma)
    assert cifrato and all(c in "0123456789abcdef" for c in cifrato)
    assert cripto.decifra_richiesta(cifrato=cifrato, token="T", **firma) == '{"change":"me"}'
    risposta = cripto.cifra_risposta('{"code":0}', "1700000000123")
    assert cripto.decifra_risposta(risposta, "1700000000123", "application/json") == '{"code":0}'
    assert cripto.sha1_hex("password") == "5baa61e4c9b93f3f0682250b6cf8331b7ee68fd8"


# --- lo stato -----------------------------------------------------------------------


def test_lo_stato_di_una_mg4_in_carica() -> None:
    s = interpreta(STATO, RICARICA)
    assert s.batteria == 78.6
    assert s.autonomia_km == 330.0
    assert s.odometro_km == 13369.0
    assert s.tensione_v == 412.25
    assert s.corrente_a == -4.25
    assert s.potenza_batteria_kw == -1.752
    assert s.stato_ricarica == StatoRicarica.IN_CARICA
    assert s.in_carica is True
    assert s.potenza_ricarica_kw == 1.752
    assert s.cavo_collegato is True
    assert s.tempo_ricarica_min == 29
    assert s.energia_residua_kwh == 57.0
    assert s.limite_ricarica == 80
    assert s.temperatura_interna_c == 17
    assert s.temperatura_esterna_c == 7
    assert s.clima_acceso is True
    assert s.chiusa is True
    assert s.portiere_aperte is False
    assert s.bagagliaio_aperto is False
    assert s.finestrini_aperti is False
    assert s.pneumatici_kpa == {
        "anteriore_sinistro": 248.0,
        "anteriore_destro": 248.0,
        "posteriore_sinistro": 248.0,
        "posteriore_destro": 252.0,
    }
    assert s.quadro_acceso is False
    assert s.velocita_kmh == 0.0
    assert (s.latitudine, s.longitudine) == (45.485072, 9.160267)
    assert s.posizione_con_segno is True
    assert s.letto == datetime.fromtimestamp(1705953524, tz=UTC)
    assert s.in_sonno is False


def test_senza_i_dati_della_ricarica_e_coi_valori_non_validi() -> None:
    stato = json.loads(json.dumps(STATO))
    base = stato["basicVehicleStatus"]
    base.update(interiorTemperature=87, exteriorTemperature=-128, driverDoor=1, bootStatus=1, lockStatus=0)
    base["rearLeftTyrePressure"] = 255
    stato["gpsPosition"]["gpsStatus"] = 0
    s = interpreta(stato, None)
    # Senza il BMS: la percentuale e l'autonomia dallo stato.
    assert s.batteria == 79
    assert s.autonomia_km == 324.0
    assert s.stato_ricarica is None
    assert s.cavo_collegato is None
    assert s.limite_ricarica is None
    assert s.temperatura_interna_c is None
    assert s.temperatura_esterna_c is None
    assert s.portiere_aperte is True
    assert s.bagagliaio_aperto is True
    assert s.chiusa is False
    assert "posteriore_sinistro" not in s.pneumatici_kpa
    # Senza fix GPS niente posizione; ferma, la velocità è zero.
    assert s.latitudine is None
    assert s.velocita_kmh == 0.0


def test_gli_stati_della_ricarica() -> None:
    def stato(codice: int) -> StatoRicarica | None:
        return interpreta({}, {"chrgMgmtData": {"bmsChrgSts": codice}}).stato_ricarica

    assert stato(0) == StatoRicarica.NON_IN_CARICA
    assert stato(10) == StatoRicarica.IN_CARICA
    assert stato(2) == StatoRicarica.COMPLETATA
    assert stato(4) == StatoRicarica.ERRORE
    assert stato(9) == StatoRicarica.PROGRAMMATA
    assert interpreta({}, {"chrgMgmtData": {"bmsChrgSts": 0}}).cavo_collegato is False
    assert interpreta({}, {"chrgMgmtData": {"bmsChrgSts": 7}}).cavo_collegato is True


def test_limite_e_temperatura_nei_codici_del_cloud() -> None:
    assert [limite_da_codice(c) for c in (0, 1, 5, 7, 9)] == [None, 40, 80, 100, None]
    assert [codice_da_limite(p) for p in (30, 40, 55, 80, 84, 100)] == [1, 1, 3, 5, 5, 7]
    mg4 = vettura_da_json(ELENCO["vinList"][0])
    altra = Vettura(vin="X", marca="mg", modello="ZS EV", extra={"serie": "ZS EV"})
    assert indice_temperatura(mg4, 22) == 8
    assert indice_temperatura(altra, 22) == 8
    assert indice_temperatura(altra, 40) == 14  # 28 gradi al massimo


def test_l_auto_dall_elenco() -> None:
    v = vettura_da_json(ELENCO["vinList"][0])
    assert (v.vin, v.marca, v.modello, v.nome, v.anno) == (VIN, "mg", "MG4 EV", "MG4 EV", 2023)
    assert v.condivisa is True
    assert v.extra["serie"] == "EH32 S"
    assert mg.comandi_di(v) == {"chiudi", "trova", "clima", "ricarica", "limite_ricarica"}
    # Con BType diverso da 1 (batteria LFP) il limite da remoto non c'è.
    lfp = vettura_da_json(
        {**ELENCO["vinList"][0], "vehicleModelConfiguration": [{"itemCode": "BType", "itemValue": "0"}]}
    )
    assert "limite_ricarica" not in mg.comandi_di(lfp)


# --- il cliente contro un cloud finto -------------------------------------------------

BASE = "/api.app/v1"
TENANT = "459771"


@dataclass
class FintoMG:
    """Il cloud MG come lo vede la libreria: corpo cifrato, firma, event-id."""

    email: str = "seconda@esempio.it"
    password: str = "giusta"
    token_valido: set[str] = field(default_factory=set)
    login: int = 0
    attese: int = 1  # quante volte risponde «aspetta» prima dei dati
    chiamate: list[str] = field(default_factory=list)
    comandi: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    eventi: list[str] = field(default_factory=list)
    stato_codice: int = 0
    comando_codice: int = 0
    guasto: bool = False
    _contatori: dict[str, int] = field(default_factory=dict)

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_route("*", BASE + "/{resto:.*}", self._gestisci)
        return app

    def _risposta(self, dati: dict[str, Any], *, evento: str | None = None) -> web.Response:
        ora = str(int(time.time() * 1000))
        testo = cripto.cifra_risposta(json.dumps(dati), ora)
        h = {"APP-SEND-DATE": ora, "ORIGINAL-CONTENT-TYPE": "application/json", "APP-CONTENT-ENCRYPTED": "1"}
        if evento:
            h["event-id"] = evento
        return web.Response(text=testo, content_type="application/json", headers=h)

    async def _gestisci(self, r: web.Request) -> web.Response:
        if self.guasto:
            return web.Response(status=502, text="Bad Gateway")
        resto = r.match_info["resto"]
        self.chiamate.append(resto)
        percorso = r.path_qs.removeprefix(BASE)
        token = r.headers.get("blade-auth", "")
        tipo = r.headers["ORIGINAL-CONTENT-TYPE"]
        ora = r.headers["APP-SEND-DATE"]
        assert r.headers["tenant-id"] == TENANT
        assert r.headers["REGION"] == "eu"
        assert r.headers["User-Type"] == "app"
        assert r.headers["Content-Type"] == f"{tipo};charset=utf-8"
        firma = {"percorso": percorso, "ora_ms": ora, "tenant": TENANT, "tipo": tipo, "token": token}
        contenuto = cripto.decifra_richiesta(cifrato=await r.text(), **firma)
        assert r.headers["APP-VERIFICATION-STRING"] == cripto.stringa_verifica(contenuto=contenuto, **firma)

        if resto == "oauth/token":
            assert r.headers["Authorization"] == "Basic c3dvcmQ6c3dvcmRfc2VjcmV0"
            form = dict(parse_qsl(contenuto))
            assert form["grant_type"] == "password" and form["loginType"] == "2"
            assert form["deviceId"].endswith("###com.saicmotor.europecar")
            if form["username"] != self.email or form["password"] != cripto.sha1_hex(self.password):
                return self._risposta({"code": 2, "message": "The account or password is incorrect"})
            self.login += 1
            nuovo = f"TOKEN{self.login}"
            self.token_valido = {nuovo}
            return self._risposta(
                {"code": 0, "data": {"access_token": nuovo, "expires_in": 86400, "user_id": "u-1", "account": "a"}}
            )

        if token not in self.token_valido:
            return web.json_response({"code": 401, "message": "Unauthorized"}, status=401)
        vin = cripto.sha256_hex(VIN)
        if resto == "vehicle/list":
            return self._risposta({"code": 0, "message": "success", "data": ELENCO})
        evento = r.headers.get("event-id")
        assert evento is not None, "lo stato e i comandi vogliono l'event-id"
        self.eventi.append(evento)
        if resto == "vehicle/status":
            assert r.query["vin"] == vin and r.query["vehStatusReqType"] == "2"
            if self.stato_codice:
                return self._risposta({"code": self.stato_codice, "message": "The vehicle is not reachable"})
            return self._asincrona(resto, evento, STATO)
        if resto == "vehicle/charging/mgmtData":
            assert r.query["vin"] == vin
            return self._asincrona(resto, evento, RICARICA)
        if resto in ("vehicle/control", "vehicle/charging/control", "vehicle/charging/setting"):
            corpo = json.loads(contenuto)
            assert corpo["vin"] == vin
            if self.comando_codice:
                return self._risposta({"code": self.comando_codice, "message": "Vehicle not locked."})
            if evento == "0":
                self.comandi.append((resto, corpo))
            return self._asincrona(resto, evento, {"rvcReqSts": "AQ=="})
        return web.json_response({"code": 404, "message": "not found"}, status=404)

    def _asincrona(self, chiave: str, evento: str, dati: dict[str, Any]) -> web.Response:
        """La prima volta «aspetta» con un event-id nuovo; poi i dati."""
        if evento == "0":
            self._contatori[chiave] = 0
            return self._risposta({"code": 0, "message": "success"}, evento=f"EV-{chiave}")
        assert evento == f"EV-{chiave}"
        self._contatori[chiave] += 1
        if self._contatori[chiave] < self.attese:
            return self._risposta({"code": 0, "message": "success"}, evento=evento)
        return self._risposta({"code": 0, "message": "success", "data": dati})


@pytest.fixture
async def cloud(socket_enabled: None) -> AsyncGenerator[tuple[FintoMG, str]]:
    finto = FintoMG()
    runner = web.AppRunner(finto.app())
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    yield finto, f"http://127.0.0.1:{runner.addresses[0][1]}{BASE}/"
    await runner.cleanup()


def _cliente(
    s: aiohttp.ClientSession,
    url: str,
    *,
    password: str = "giusta",
    token: dict[str, Any] | None = None,
    salvati: list[dict[str, Any]] | None = None,
    pazienza: float = 5.0,
) -> ClienteMG:
    return ClienteMG(
        s,
        email="Seconda@esempio.it ".lower(),
        password=password,
        regione="eu",
        token=token,
        salva=salvati.append if salvati is not None else None,
        base_url=url,
        pazienza=pazienza,
        attesa_stato=0.01,
        attesa_comando=0.01,
    )


async def test_dal_login_allo_stato(cloud) -> None:
    finto, url = cloud
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, salvati=salvati)
        await c.accedi()
        assert c.token["access_token"] == "TOKEN1"
        (v,) = await c.vetture()
        assert (v.vin, v.nome) == (VIN, "MG4 EV")
        stato = await c.stato(v)
    assert stato.batteria == 78.6
    assert stato.limite_ricarica == 80
    assert stato.chiusa is True
    assert finto.login == 1
    assert salvati[-1]["access_token"] == "TOKEN1"
    # Ogni lettura parte con event-id 0 e poi segue quello del cloud.
    assert finto.eventi == ["0", "EV-vehicle/status", "0", "EV-vehicle/charging/mgmtData"]


async def test_un_token_salvato_non_rientra(cloud) -> None:
    finto, url = cloud
    finto.token_valido = {"SALVATO"}
    token = {"access_token": "SALVATO", "scade": (datetime.now(UTC) + timedelta(days=2)).isoformat()}
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, token=token)
        await c.accedi()
        assert len(await c.vetture()) == 1
    assert finto.login == 0


async def test_il_token_scaduto_si_rinnova_e_si_salva(cloud) -> None:
    finto, url = cloud
    salvati: list[dict[str, Any]] = []
    scaduto = {"access_token": "VECCHIO", "scade": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()}
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, token=scaduto, salvati=salvati)
        await c.vetture()
    assert finto.login == 1
    assert salvati[-1]["access_token"] == "TOKEN1"


async def test_un_401_fa_rientrare_e_riprovare_una_volta(cloud) -> None:
    finto, url = cloud
    salvati: list[dict[str, Any]] = []
    # Il token sembra valido, ma il cloud non lo conosce più (login da un'altra parte).
    valido = {"access_token": "REVOCATO", "scade": (datetime.now(UTC) + timedelta(days=2)).isoformat()}
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, token=valido, salvati=salvati)
        assert len(await c.vetture()) == 1
    assert finto.login == 1
    assert finto.chiamate == ["vehicle/list", "oauth/token", "vehicle/list"]
    assert salvati[-1]["access_token"] == "TOKEN1"


async def test_password_sbagliata_e_guasto_non_si_confondono(cloud) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        with pytest.raises(ErroreCredenziali):
            await _cliente(s, url, password="sbagliata").accedi()
        finto.guasto = True
        with pytest.raises(ErroreRete):
            await _cliente(s, url).accedi()
        with pytest.raises(ErroreRete):
            await _cliente(s, "http://127.0.0.1:1/api.app/v1/").accedi()


async def test_l_auto_che_non_risponde_tiene_gli_ultimi_dati(cloud) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url)
        (v,) = await c.vetture()
        primo = await c.stato(v)
        assert primo.in_sonno is False
        # Il codice 4: l'auto non si raggiunge.
        finto.stato_codice = 4
        dopo = await c.stato(v)
        assert dopo.in_sonno is True
        assert dopo.batteria == 78.6
        # L'auto non risponde mai entro la pazienza: stessa cosa, senza errori.
        finto.stato_codice = 0
        finto.attese = 10_000
        c._pazienza = 0.2
        assert (await c.stato(v)).in_sonno is True
        nuova = _cliente(s, url, pazienza=0.2)
        assert (await nuova.stato(v)).in_sonno is True


async def test_i_comandi(cloud) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url)
        (v,) = await c.vetture()
        await c.chiudi(v, None)
        await c.apri(v, None)
        await c.trova(v, None)
        await c.clima(v, True, None, 22)
        await c.clima(v, False, None)
        await c.ricarica(v, True, None)
        await c.limite_ricarica(v, 90, None)
    vin = cripto.sha256_hex(VIN)

    def valori(corpo: dict[str, Any]) -> dict[int, bytes]:
        return {p["paramId"]: base64.b64decode(p["paramValue"]) for p in corpo["rvcParams"] or []}

    (chiudi, apri, trova, clima_on, clima_off, ricarica, limite) = finto.comandi
    assert chiudi == ("vehicle/control", {"rvcParams": None, "rvcReqType": "1", "vin": vin})
    assert apri[1]["rvcReqType"] == "2" and valori(apri[1])[7] == b"\x03"
    assert trova[1]["rvcReqType"] == "0" and valori(trova[1]) == {1: b"\x01", 2: b"\x01", 3: b"\x01", 255: bytes(4)}
    assert clima_on[1]["rvcReqType"] == "6" and valori(clima_on[1]) == {19: b"\x02", 20: b"\x08", 255: bytes(4)}
    assert valori(clima_off[1]) == {19: b"\x00", 22: b"\x00", 255: bytes(4)}
    assert ricarica == (
        "vehicle/charging/control",
        {"chrgCtrlReq": 1, "tboxEleccLckCtrlReq": 0, "tboxV2XReq": 0, "vin": vin},
    )
    assert limite == (
        "vehicle/charging/setting",
        {"altngChrgCrntReq": 0, "onBdChrgTrgtSOCReq": 6, "tboxV2XSpSOCReq": 0, "vin": vin},
    )


async def test_un_comando_rifiutato_dice_perche(cloud) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url)
        (v,) = await c.vetture()
        finto.comando_codice = 8
        with pytest.raises(ErroreComando, match="Vehicle not locked"):
            await c.clima(v, True, None)
        finto.comando_codice = 0
        finto.attese = 10_000
        c._pazienza = 0.2
        with pytest.raises(ErroreComando, match="in tempo"):
            await c.ricarica(v, False, None)


# --- dentro Home Assistant -----------------------------------------------------------


async def test_configurare_una_mg_con_email_e_password(hass: HomeAssistant) -> None:
    v = vettura_da_json(ELENCO["vinList"][0])
    stato = interpreta(STATO, RICARICA)
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "mg"})
    assert r["step_id"] == "credenziali"
    assert {str(k) for k in r["data_schema"].schema} == {"email", "password", "regione"}
    assert "secondary account" in r["description_placeholders"]["nota"]
    with patch.object(ClienteMG, "accedi", AsyncMock(side_effect=ErroreCredenziali("no"))):
        r = await hass.config_entries.flow.async_configure(
            r["flow_id"], {"email": "seconda@esempio.it", "password": "no", "regione": "eu"}
        )
    assert r["errors"] == {"base": "credenziali"}
    with (
        patch.object(ClienteMG, "accedi", AsyncMock()),
        patch.object(ClienteMG, "vetture", AsyncMock(return_value=[v])),
        patch.object(ClienteMG, "stato", AsyncMock(return_value=stato)),
    ):
        r = await hass.config_entries.flow.async_configure(
            r["flow_id"], {"email": " Seconda@Esempio.it", "password": "giusta", "regione": "eu"}
        )
        await hass.async_block_till_done()
        assert r["type"] is FlowResultType.CREATE_ENTRY
        assert r["title"] == "MG · Seconda@Esempio.it"
        assert r["result"].unique_id == "mg_seconda@esempio.it"
        assert r["data"]["marca"] == "mg"
        assert r["data"]["regione"] == "eu"
        assert hass.states.get("sensor.mg4_ev_batteria").state == "78.6"
        assert hass.states.get("lock.mg4_ev_serratura").state == "locked"
        assert hass.states.get("number.mg4_ev_limite_ricarica").state == "80"
        auto = r["result"].runtime_data.auto[0]
        # Si legge piano: l'API sveglia l'auto. In carica ogni cinque minuti.
        assert auto._normale == timedelta(minutes=30)
        assert auto.update_interval == timedelta(seconds=300)


async def test_il_token_nuovo_finisce_nella_voce(hass: HomeAssistant) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    voce = MockConfigEntry(
        domain=DOMAIN,
        title="MG · seconda@esempio.it",
        unique_id="mg_seconda@esempio.it",
        data={
            "fonte": "cloud",
            "marca": "mg",
            "email": "seconda@esempio.it",
            "password": "giusta",
            "regione": "eu",
            "token": {},
        },
    )
    voce.add_to_hass(hass)
    cliente = await mg.crea_da_voce(hass, voce, "")
    assert cliente.regione == "eu"
    assert cliente.base_url == "https://gateway-mg-eu.soimt.com/api.app/v1/"
    cliente._salva({"access_token": "NUOVO", "scade": "2030-01-01T00:00:00+00:00"})
    assert voce.data["token"]["access_token"] == "NUOVO"
