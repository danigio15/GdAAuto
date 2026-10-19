"""Rivian: l'interpretazione di ``vehicleState``, il cliente contro un cloud
GraphQL finto su 127.0.0.1 e la configurazione in Home Assistant.

I payload sono quelli dei test di bretterer/rivian-python-client (``rivian_campioni``).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.gdaauto.const import DOMAIN
from custom_components.gdaauto.marche import rivian
from custom_components.gdaauto.marche.catalogo import PER_ID, Marchio
from custom_components.gdaauto.marche.errori import (
    CodiceInviato,
    CodiceSbagliato,
    ErroreApi,
    ErroreCertificato,
    ErroreComando,
    ErroreCredenziali,
    ErroreRete,
)
from custom_components.gdaauto.marche.rivian import chiave, cliente as modulo_cliente
from custom_components.gdaauto.marche.rivian.cliente import ClienteRivian, comandi_di, entra
from custom_components.gdaauto.marche.rivian.dati import interpreta, ultima_ricarica, vetture_da
from custom_components.gdaauto.vettura import Marcia, StatoRicarica, Vettura

from . import rivian_campioni as c

# --- le letture --------------------------------------------------------------------


def test_una_r1t_in_carica() -> None:
    s = interpreta(c.STATO)
    assert s.batteria == pytest.approx(53.4, abs=0.01)
    assert s.autonomia_km == 266
    assert s.odometro_km == 8928.8  # da metri
    assert s.limite_ricarica == 85
    assert s.capacita_batteria_kwh == 131.7
    assert s.stato_ricarica == StatoRicarica.IN_CARICA
    assert s.cavo_collegato is True
    assert s.tempo_ricarica_min == 95
    assert s.temperatura_interna_c == 21
    assert s.temperatura_clima_c == 20
    assert s.clima_acceso is False
    assert s.sbrinamento is False
    # Tutte le serrature (porte, frunk, cassoni, tonneau) chiuse; il portellone «signal_not_available» non conta.
    assert s.chiusa is True
    assert (s.portiere_aperte, s.bagagliaio_aperto, s.finestrini_aperti) == (False, False, False)
    assert s.pneumatici_allarme is False
    assert (s.in_sonno, s.quadro_acceso, s.marcia) == (False, False, Marcia.PARCHEGGIO)
    assert (s.latitudine, s.longitudine, s.posizione_con_segno) == (42.3601866, -71.0589682, True)
    assert s.velocita_kmh == 0
    assert s.letto == datetime(2022, 10, 26, 20, 7, 4, 559000, tzinfo=UTC)
    assert s.grezzo["gearStatus"]["value"] == "park"


def test_una_r1t_aperta_che_dorme() -> None:
    s = interpreta(c.STATO_APERTA)
    assert s.chiusa is False
    assert (s.portiere_aperte, s.bagagliaio_aperto, s.finestrini_aperti) == (True, True, True)
    assert s.pneumatici_allarme is True
    assert s.in_sonno is True
    assert s.stato_ricarica == StatoRicarica.NON_IN_CARICA
    assert s.cavo_collegato is False
    assert s.tempo_ricarica_min is None
    assert s.clima_acceso is True
    assert s.temperatura_clima_c is None  # 63,5 è «HI», non gradi
    assert s.marcia == Marcia.AVANTI
    assert s.velocita_kmh == 90.0  # da m/s


def test_gli_stati_della_ricarica() -> None:
    def stato(valore: str) -> StatoRicarica | None:
        return interpreta({"chargerState": {"timeStamp": "", "value": valore}}).stato_ricarica

    assert stato("charging_complete") == StatoRicarica.COMPLETATA
    assert stato("charging_scheduled") == StatoRicarica.PROGRAMMATA
    assert stato("smart_charging_paused") == StatoRicarica.IN_PAUSA
    assert stato("charging_station_error") == StatoRicarica.ERRORE
    assert stato("qualcosa_di_nuovo") is None
    assert interpreta({}).batteria is None


def test_le_auto_e_la_chiave_del_telefono() -> None:
    pubblica = chiave.pubblica(chiave.carica(c.CHIAVE_PRIVATA))
    (auto,) = vetture_da(c.utente(pubblica)["data"]["currentUser"], pubblica)
    assert (auto.vin, auto.modello, auto.nome, auto.anno, auto.condivisa) == (c.VIN, "R1T", "Rivian R1T", 2022, False)
    assert auto.extra["funzioni"] == ["CHARG_CMD", "CHARG_SLIDER", "PET_MODE"]
    assert (auto.extra["telefono"], auto.extra["identita"]) == ("vasPhoneId", "identityId")
    assert comandi_di(auto) == {"chiudi", "apri", "trova", "clima", "ricarica", "limite_ricarica"}
    # Senza chiave (o con un'altra chiave) l'auto si legge e basta.
    (sola,) = vetture_da(c.utente(pubblica)["data"]["currentUser"], "")
    assert comandi_di(sola) == set()
    (altra,) = vetture_da(c.utente("04abcd")["data"]["currentUser"], pubblica)
    assert comandi_di(altra) == set()


def test_la_firma_dei_comandi() -> None:
    privata = chiave.carica(c.CHIAVE_PRIVATA)
    assert chiave.firma("WAKE_VEHICLE", "1707000000", privata, c.CHIAVE_AUTO) == c.HMAC_SVEGLIA
    # Anche in PEM nudo, come si incolla da un file.
    import base64

    pem = base64.b64decode(c.CHIAVE_PRIVATA).decode()
    assert chiave.firma("WAKE_VEHICLE", "1707000000", chiave.carica(pem), c.CHIAVE_AUTO) == c.HMAC_SVEGLIA
    with pytest.raises(ErroreCertificato):
        chiave.carica("non è una chiave")


def test_l_ultima_ricarica() -> None:
    campi = ultima_ricarica(c.RICARICHE["data"]["getCompletedSessionSummaries"], c.ID_AUTO)
    assert campi == {
        "ultima_ricarica_kwh": 40.12,
        "ultima_ricarica_tipo": "ac",
        "ultima_ricarica_inizio": datetime(2024, 3, 3, 22, 0, tzinfo=UTC),
        "ultima_ricarica_fine": datetime(2024, 3, 4, 5, 0, tzinfo=UTC),
    }
    assert ultima_ricarica(None, c.ID_AUTO) == {}


# --- il cliente contro un cloud finto ----------------------------------------------


@dataclass
class FintaRivian:
    password: str = "giusta"
    otp: bool = False
    codice: str = "123456"
    #: le sessioni dell'app ancora buone (una nuova a ogni createCsrfToken)
    sessioni_buone: set[str] = field(default_factory=set)
    utente_valido: bool = True
    rotto: bool = False
    quota: bool = False
    chiave_pubblica: str = "publicKey"
    stato: dict[str, Any] = field(default_factory=lambda: dict(c.STATO))
    chiamate: list[str] = field(default_factory=list)
    comandi: list[dict[str, Any]] = field(default_factory=list)
    rifiuta_comandi: bool = False

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/api/gql/gateway/graphql", self._gateway)
        app.router.add_post("/api/gql/chrg/user/graphql", self._ricariche)
        return app

    async def _gateway(self, r: web.Request) -> web.Response:
        corpo = await r.json()
        op = corpo["operationName"]
        self.chiamate.append(op)
        assert r.headers["Apollographql-Client-Name"] == "com.rivian.ios.consumer-apollo-ios"
        assert r.headers["dc-cid"].startswith("m-ios-")
        if self.rotto:
            return web.Response(status=502, text="<html>Bad Gateway</html>")
        if op == "CreateCSRFToken":
            n = len(self.sessioni_buone) + 1
            self.sessioni_buone.add(f"app-{n}")
            risposta = json.loads(json.dumps(c.CSRF))
            risposta["data"]["createCsrfToken"]["appSessionToken"] = f"app-{n}"
            risposta["data"]["createCsrfToken"]["csrfToken"] = f"csrf-{n}"
            return web.json_response(risposta)
        if op == "Login":
            assert r.headers["A-Sess"] in self.sessioni_buone and r.headers["Csrf-Token"].startswith("csrf-")
            if corpo["variables"]["password"] != self.password:
                return web.json_response(c.errore("BAD_CURRENT_PASSWORD"))
            return web.json_response(c.LOGIN_OTP if self.otp else c.LOGIN)
        if op == "LoginWithOTP":
            assert corpo["variables"]["otpToken"] == "token"
            if corpo["variables"]["otpCode"] != self.codice:
                return web.json_response(c.errore("BAD_USER_INPUT", "INVALID_OTP"))
            return web.json_response(c.LOGIN_CON_CODICE)
        # Da qui in poi serve la sessione dell'utente, e una sessione dell'app buona.
        if r.headers.get("A-Sess") not in self.sessioni_buone or not self.utente_valido:
            return web.json_response(c.errore("UNAUTHENTICATED"), status=401)
        assert r.headers["U-Sess"] == "valid_user_session_token"
        if self.quota:
            return web.json_response(c.errore("RATE_LIMIT"))
        if op == "getUserInfo":
            return web.json_response(c.utente(self.chiave_pubblica))
        if op == "GetVehicleState":
            assert corpo["variables"] == {"vehicleID": c.ID_AUTO}
            assert "vehicleMileage { timeStamp value }" in corpo["query"]
            assert "gnssLocation { latitude longitude timeStamp }" in corpo["query"]
            return web.json_response({"data": {"vehicleState": self.stato}})
        if op == "sendVehicleCommand":
            assert r.headers["Csrf-Token"].startswith("csrf-")
            attributi = corpo["variables"]["attrs"]
            self.comandi.append(attributi)
            if self.rifiuta_comandi:
                return web.json_response(c.errore("BAD_REQUEST_ERROR", "INVALID_HMAC"))
            if attributi["command"] == "WAKE_VEHICLE":
                self.stato = {**self.stato, "powerState": {"timeStamp": "", "value": "ready"}}
            return web.json_response(
                {"data": {"sendVehicleCommand": {"id": f"cmd-{len(self.comandi)}", "command": "x", "state": 0}}}
            )
        return web.json_response(c.errore("BAD_REQUEST_ERROR"), status=400)

    async def _ricariche(self, r: web.Request) -> web.Response:
        corpo = await r.json()
        self.chiamate.append(corpo["operationName"])
        if r.headers.get("A-Sess") not in self.sessioni_buone:
            return web.json_response(c.errore("UNAUTHENTICATED"), status=401)
        return web.json_response(c.RICARICHE)


@pytest.fixture
async def cloud(socket_enabled: None) -> AsyncGenerator[tuple[FintaRivian, str]]:
    finta = FintaRivian()
    runner = web.AppRunner(finta.app())
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    modulo_cliente._IN_SOSPESO.clear()
    yield finta, f"http://127.0.0.1:{runner.addresses[0][1]}/api/gql"
    await runner.cleanup()


async def test_login_auto_stato_e_storico(cloud) -> None:
    finta, base = cloud
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        token = await entra(s, "Mario@Esempio.com", "giusta", base=base)
        assert token["u_sess"] == "valid_user_session_token"
        assert token["refresh_token"] == "valid_refresh_token"
        cliente = ClienteRivian(s, token, salvati.append, base_url=base)
        await cliente.accedi()
        (auto,) = await cliente.vetture()
        assert auto.vin == c.VIN and auto.extra["id"] == c.ID_AUTO
        assert comandi_di(auto) == set()  # nessuna chiave del telefono
        stato = await cliente.stato(auto)
        assert stato.batteria == pytest.approx(53.4, abs=0.01)
        assert stato.ultima_ricarica_kwh == 40.12
        await cliente.stato(auto)
    # Una sessione dell'app nuova all'avvio, salvata col token; le ricariche una volta sola.
    assert salvati[-1]["a_sess"] == "app-2"
    assert finta.chiamate.count("getCompletedSessionSummaries") == 1
    assert finta.chiamate.count("GetVehicleState") == 2


async def test_la_verifica_per_sms(cloud) -> None:
    finta, base = cloud
    finta.otp = True
    async with aiohttp.ClientSession() as s:
        with pytest.raises(CodiceInviato, match="SMS"):
            await entra(s, "mario@esempio.com", "giusta", base=base)
        with pytest.raises(CodiceSbagliato):
            await entra(s, "mario@esempio.com", "giusta", codice="000000", base=base)
        token = await entra(s, "Mario@Esempio.com", "giusta", codice=" 123456 ", base=base)
    assert token["u_sess"] == "valid_user_session_token"
    assert finta.chiamate.count("Login") == 1  # il codice conclude il login di prima


async def test_password_sbagliata_sessione_scaduta_e_guasti(cloud) -> None:
    finta, base = cloud
    async with aiohttp.ClientSession() as s:
        with pytest.raises(ErroreCredenziali, match="BAD_CURRENT_PASSWORD"):
            await entra(s, "mario@esempio.com", "sbagliata", base=base)
        token = await entra(s, "mario@esempio.com", "giusta", base=base)
        cliente = ClienteRivian(s, token, base_url=base)
        await cliente.accedi()
        (auto,) = await cliente.vetture()
        # La sessione dell'app scade: se ne chiede una nuova e si riprova, senza chiedere di rientrare.
        finta.sessioni_buone.clear()
        assert (await cliente.stato(auto)).batteria is not None
        # La sessione dell'utente non vale più: questa volta sì, bisogna rientrare.
        finta.utente_valido = False
        with pytest.raises(ErroreCredenziali):
            await cliente.vetture()
        finta.utente_valido = True
        # Un guasto (502) non chiede di rientrare.
        finta.rotto = True
        with pytest.raises(ErroreRete):
            await cliente.vetture()
        finta.rotto = False
        # «Troppe richieste»: per un quarto d'ora si tiene l'ultima lettura.
        prima = await cliente.stato(auto)
        cliente._storici.clear()
        finta.quota = True
        with pytest.raises(ErroreApi, match="richieste"):
            await cliente.stato(auto)
        chiamate = len(finta.chiamate)
        assert await cliente.stato(auto) is prima
        assert len(finta.chiamate) == chiamate
    async with aiohttp.ClientSession() as s:
        with pytest.raises(ErroreRete):
            await entra(s, "mario@esempio.com", "giusta", base="http://127.0.0.1:9/api/gql")


async def test_i_comandi_firmati(cloud, monkeypatch) -> None:
    finta, base = cloud
    monkeypatch.setattr(modulo_cliente, "PASSO_SVEGLIA", 0.01)
    privata = chiave.carica(c.CHIAVE_PRIVATA)
    finta.chiave_pubblica = chiave.pubblica(privata)
    async with aiohttp.ClientSession() as s:
        token = await entra(s, "mario@esempio.com", "giusta", base=base)
        cliente = ClienteRivian(s, token, base_url=base, chiave_telefono=c.CHIAVE_PRIVATA)
        await cliente.accedi()
        (auto,) = await cliente.vetture()
        assert comandi_di(auto) == {"chiudi", "apri", "trova", "clima", "ricarica", "limite_ricarica"}
        # L'auto dorme: prima si sveglia, poi chiude.
        finta.stato = dict(c.STATO_APERTA)
        await cliente.stato(auto)
        await cliente.chiudi(auto, None)
        await cliente.clima(auto, True, None, 35)
        await cliente.clima(auto, False, None)
        await cliente.ricarica(auto, True, None)
        await cliente.limite_ricarica(auto, 40, None)
        await cliente.trova(auto, None)
        finta.rifiuta_comandi = True
        with pytest.raises(ErroreComando, match="INVALID_HMAC"):
            await cliente.apri(auto, None)
    eseguiti = [(x["command"], x.get("params")) for x in finta.comandi]
    assert eseguiti == [
        ("WAKE_VEHICLE", None),
        ("LOCK_ALL_CLOSURES_FEEDBACK", None),
        ("VEHICLE_CABIN_PRECONDITION_ENABLE", None),
        ("CABIN_PRECONDITIONING_SET_TEMP", {"HVAC_set_temp": "29"}),
        ("VEHICLE_CABIN_PRECONDITION_DISABLE", None),
        ("START_CHARGING", None),
        ("CHARGING_LIMITS", {"SOC_limit": 50}),
        ("HONK_AND_FLASH_LIGHTS", None),
        ("UNLOCK_ALL_CLOSURES", None),
    ]
    primo = finta.comandi[0]
    assert (primo["vasPhoneId"], primo["deviceId"], primo["vehicleId"]) == ("vasPhoneId", "identityId", c.ID_AUTO)
    assert primo["hmac"] == chiave.firma("WAKE_VEHICLE", primo["timestamp"], privata, c.CHIAVE_AUTO)


async def test_senza_chiave_non_si_comanda(cloud) -> None:
    _, base = cloud
    async with aiohttp.ClientSession() as s:
        token = await entra(s, "mario@esempio.com", "giusta", base=base)
        cliente = ClienteRivian(s, token, base_url=base)
        (auto,) = await cliente.vetture()
        with pytest.raises(ErroreComando, match="telefono"):
            await cliente.chiudi(auto, None)


# --- dentro Home Assistant ------------------------------------------------------------

R1T = Vettura(vin=c.VIN, marca="rivian", modello="R1T", nome="Rivian R1T", anno=2022, extra={"id": c.ID_AUTO})
MARCHIO = Marchio(
    "rivian", "Rivian", "rivian", {}, "Rivian (HACS)", "https://github.com/bretterer/home-assistant-rivian"
)


async def test_configurare_una_rivian(hass: HomeAssistant) -> None:
    # Il catalogo, per ora, manda Rivian a Home Assistant: qui lo si punta alla marca nativa.
    with patch.dict(PER_ID, {"rivian": MARCHIO}):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "rivian"})
        assert r["step_id"] == "credenziali"
        assert {str(k) for k in r["data_schema"].schema} == {"email", "password", "codice", "chiave_app"}
        token = {"u_sess": "U", "access_token": "A", "refresh_token": "R", "csrf": "C", "a_sess": "S"}
        entra_finto = AsyncMock(side_effect=[CodiceInviato("sms"), token])
        with (
            patch.object(rivian, "entra", entra_finto),
            patch.object(rivian.ClienteRivian, "accedi", AsyncMock()),
            patch.object(rivian.ClienteRivian, "vetture", AsyncMock(return_value=[R1T])),
            patch.object(rivian.ClienteRivian, "stato", AsyncMock(return_value=interpreta(c.STATO))),
        ):
            dati = {"email": "Mario@Esempio.com", "password": "segreta"}
            r = await hass.config_entries.flow.async_configure(r["flow_id"], dati)
            assert r["errors"] == {"base": "codice_inviato"}
            r = await hass.config_entries.flow.async_configure(r["flow_id"], {**dati, "codice": "123456"})
            await hass.async_block_till_done()
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["title"] == "Rivian · Mario@Esempio.com"
    assert r["result"].unique_id == "rivian_mario@esempio.com"
    assert r["data"]["marca"] == "rivian"
    assert r["data"]["token"] == token
    assert "segreta" not in json.dumps(r["data"])
    assert entra_finto.await_args.kwargs["codice"] == "123456"
    assert hass.states.get("sensor.rivian_r1t_batteria").state == "53.400002"
    voce = r["result"]
    assert voce.runtime_data.auto[0].comandi_possibili == set()
    # Il token nuovo aggiorna la voce senza ricaricarla.
    prima = voce.runtime_data
    voce.runtime_data.conto.cliente._salva({**token, "a_sess": "S2"})
    await hass.async_block_till_done()
    assert voce.data["token"]["a_sess"] == "S2"
    assert voce.runtime_data is prima


async def test_una_chiave_che_non_si_apre(hass: HomeAssistant) -> None:
    with pytest.raises(ErroreCertificato):
        await rivian.prova_accesso(hass, {"email": "a@b.c", "password": "x", "chiave_app": "rotta"})
