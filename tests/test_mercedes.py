"""Mercedes-Benz: il protobuf fatto a mano, i dati, il cliente contro un cloud finto, e Home Assistant.

I byte dei campioni (``mercedes_campioni.py``) sono stati scritti con la libreria
protobuf vera: se il decoder o l'encoder di GdAAuto sbagliano un numero di campo,
queste prove se ne accorgono.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import hashlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from yarl import URL

from custom_components.gdaauto.const import CONF_FONTE, CONF_MARCA, DOMAIN, FONTE_CLOUD
from custom_components.gdaauto.marche import mercedes
from custom_components.gdaauto.marche.errori import (
    ErroreApi,
    ErroreComando,
    ErroreCredenziali,
    ErrorePin,
    ErroreRete,
)
from custom_components.gdaauto.marche.mercedes import cliente as modulo_cliente, messaggi as m, protobuf as pb
from custom_components.gdaauto.marche.mercedes.cliente import ClienteMercedes, Indirizzi, comandi_di
from custom_components.gdaauto.marche.mercedes.dati import interpreta
from custom_components.gdaauto.vettura import StatoRicarica, Vettura

from . import mercedes_campioni as c

EMAIL = "Guida@Esempio.it"
PASSWORD = "giusta"
PIN = "1234"

# --- il formato wire ---------------------------------------------------------------------


def test_il_decoder_legge_i_tipi_del_filo() -> None:
    assert pb.intero(pb.per_numero(pb.messaggio([(1, -5)]))[1][0]) == -5
    assert pb.messaggio([(2, "ciao"), (1, 300)]) == bytes.fromhex("08ac02") + b"\x12\x04ciao"
    # Come in proto3: zero e testo vuoto non si scrivono, un messaggio vuoto sì.
    assert pb.messaggio([(1, 0), (2, ""), (3, False), (4, b"")]) == b"\x22\x00"
    for rotto in (b"\x08", b"\x12\x05ab", b"\x7b\x22", b"\x00\x01"):
        with pytest.raises(pb.ErroreProtobuf):
            list(pb.campi(rotto))


def test_gli_attributi_di_una_eqa_si_leggono_come_li_scrive_protobuf() -> None:
    agg = m.vep(c.VEP_EQA)
    assert agg.vin == c.VIN
    assert agg.emesso_ms == c.ISTANTE_MS
    assert len(agg.attributi) == 30
    assert agg.attributi["soc"]["valore"] == 64
    assert agg.attributi["rangeelectric"]["unita"] == {"distanza": m.KM}
    assert agg.attributi["chargingstatus"]["valore"] == 0  # lo zero di un oneof c'è
    assert agg.attributi["chargingPower"]["valore"] == 7.4
    assert agg.attributi["chargePrograms"]["programmi"][1] == {"programma": 2, "max_soc": 80}
    assert agg.attributi["chargeInlets"]["prese"] == [{"id": 1, "spina": 1, "blocco": 2}]
    assert agg.attributi["tirepressureRearRight"]["stato"] == 4
    usa = m.vep(c.VEP_EQB_USA)
    assert usa.attributi["positionLong"]["valore"] == -74.006
    assert usa.attributi["unAttributoNegativo"]["valore"] == -5


def test_i_comandi_si_scrivono_byte_per_byte_come_l_app() -> None:
    attesi = {
        c.CMD_CHIUDI: (m.CHIUDI, m.corpo_chiudi()),
        c.CMD_APRI: (m.APRI, m.corpo_apri(PIN)),
        c.CMD_CLIMA_NOW: (m.CLIMA_AVVIA, m.corpo_clima_avvia(m.CLIMA_ORA)),
        c.CMD_CLIMA_IMMEDIATE: (m.CLIMA_AVVIA, m.corpo_clima_avvia(m.CLIMA_SUBITO)),
        c.CMD_CLIMA_STOP: (m.CLIMA_FERMA, m.corpo_clima_ferma()),
        c.CMD_LIMITE_PROGRAMMA: (m.PROGRAMMA_RICARICA, m.corpo_programma_ricarica(2, 80)),
        c.CMD_LIMITE_CHARGING: (m.CONFIGURA_RICARICA, m.corpo_configura_ricarica(90)),
        c.CMD_TROVA: (m.TROVA, m.corpo_trova()),
    }
    for atteso, (numero, corpo) in attesi.items():
        assert m.comando(c.VIN, "RICHIESTA-1", numero, corpo) == atteso


def test_le_ricevute_e_gli_esiti_dei_comandi() -> None:
    assert m.ricevuta(m.push(c.PUSH_VEP)) == c.ACK_VEP_41
    assert m.ricevuta(m.push(c.STATO_FINITO)) == c.ACK_STATO_7
    attese = {
        "service_status_updates": c.ACK_SERVIZI_5,
        "user_data_update": c.ACK_UTENTE_6,
        "data_change_event": c.ACK_DCE_8,
        "vehicle_status_updates": c.ACK_VSU_9,
        "assigned_vehicles": bytes.fromhex("ba0100"),
        "apptwin_pending_command_request": bytes.fromhex("aa0100"),
        "debugMessage": None,
    }
    for tipo, ricevuta in attese.items():
        assert m.ricevuta(m.push(c.PUSH_VARIE[tipo])) == ricevuta, tipo
    (finito,) = m.esiti(m.push(c.STATO_FINITO).corpo)
    assert (finito.vin, finito.richiesta, finito.stato) == (c.VIN, "RICHIESTA-1", m.FINITO)
    (fallito,) = m.esiti(m.push(c.STATO_FALLITO).corpo)
    assert fallito.errori == [("4110", "Vehicle is in motion")]
    (pin,) = m.esiti(m.push(c.STATO_PIN).corpo)
    assert pin.tentativi_pin == 1


# --- i dati --------------------------------------------------------------------------------


def test_una_eqa_in_carica() -> None:
    s = interpreta(m.vep(c.VEP_EQA))
    assert s.batteria == 64
    assert s.autonomia_km == 281
    assert s.odometro_km == 12345
    assert s.stato_ricarica == StatoRicarica.IN_CARICA
    assert s.in_carica is True
    assert s.cavo_collegato is True
    assert s.potenza_ricarica_kw == 7.4
    assert s.limite_ricarica == 80  # il programma scelto è «casa», col suo 80 %
    assert s.chiusa is True
    assert s.portiere_aperte is False
    assert s.bagagliaio_aperto is False
    assert s.finestrini_aperti is False
    assert s.pneumatici_kpa == {"anteriore_sinistro": 250.0, "anteriore_destro": 252.5, "posteriore_sinistro": 260.0}
    assert (s.latitudine, s.longitudine, s.posizione_con_segno) == (45.4642, 9.19, True)
    assert s.quadro_acceso is False
    assert s.clima_acceso is False
    assert s.temperatura_esterna_c == 12.5
    assert s.letto == datetime.fromtimestamp(c.ISTANTE_MS / 1000, UTC)
    assert "positionLat" not in s.grezzo  # la posizione non finisce nella diagnostica
    assert s.grezzo["endofchargetime"]["display"] == "18:30"


def test_una_eqb_americana_in_miglia_psi_e_fahrenheit() -> None:
    s = interpreta(m.vep(c.VEP_EQB_USA))
    assert s.batteria == 81
    assert s.autonomia_km == 321.9
    assert s.odometro_km == 16093.4
    assert s.stato_ricarica == StatoRicarica.NON_IN_CARICA
    assert s.cavo_collegato is False
    assert s.potenza_ricarica_kw is None
    assert s.limite_ricarica == 90  # qui c'è maxSoc, senza programmi
    assert s.chiusa is False
    assert s.portiere_aperte is True
    assert s.bagagliaio_aperto is True
    assert s.finestrini_aperti is True
    assert s.pneumatici_kpa == {"anteriore_sinistro": 248.2}
    assert s.longitudine == -74.006
    assert s.quadro_acceso is True
    assert s.clima_acceso is True
    assert s.temperatura_esterna_c == 10.0


def test_un_auto_che_non_ha_ancora_mandato_niente() -> None:
    s = interpreta(m.vep(b""))
    assert s.batteria is None
    assert s.chiusa is None
    assert s.stato_ricarica is None
    assert s.pneumatici_kpa == {}
    assert s.letto is None


def test_i_comandi_vengono_da_quello_che_l_auto_sa_fare() -> None:
    def auto(*comandi: str) -> Vettura:
        return Vettura(vin=c.VIN, marca="mercedes", modello="EQA", extra={"comandi": list(comandi)})

    assert comandi_di(auto()) == set()
    assert comandi_di(auto("DOORS_LOCK", "DOORS_UNLOCK", "SIGPOS_START")) == {"chiudi", "apri", "trova"}
    # Il clima senza il comando per spegnerlo non si offre.
    assert comandi_di(auto("ZEV_PRECONDITIONING_START")) == set()
    assert comandi_di(auto("ZEV_PRECONDITIONING_START", "ZEV_PRECONDITIONING_STOP", "CHARGING_CONFIGURE")) == {
        "clima",
        "limite_ricarica",
    }


# --- il cloud finto ----------------------------------------------------------------------------

AUTO_JSON = {
    "fin": c.VIN,
    "vin": c.VIN,
    "licensePlate": "MI 123 AB",
    "isOwner": True,
    "salesRelatedInformation": {"baumuster": {"baumusterDescription": "EQA 250"}},
}
COMANDI_JSON = {
    "commands": [
        {"commandName": "DOORS_LOCK", "isAvailable": True},
        {"commandName": "DOORS_UNLOCK", "isAvailable": True},
        {"commandName": "ZEV_PRECONDITIONING_START", "isAvailable": True},
        {"commandName": "ZEV_PRECONDITIONING_STOP", "isAvailable": True},
        {"commandName": "CHARGE_PROGRAM_CONFIGURE", "isAvailable": True, "parameters": [{"parameterName": "MAX_SOC"}]},
        {"commandName": "SIGPOS_START", "isAvailable": True},
        {"commandName": "ENGINE_START", "isAvailable": False},
    ]
}


@dataclass
class FintaMercedes:
    """Il login, il BFF, i widget e il websocket di Mercedes, su 127.0.0.1."""

    password: str = PASSWORD
    risultato_login: str = "RESUME2OIDCP"
    passkey: bool = False
    rifiuta_refresh: bool = False
    guasto_stato: bool = False
    capacita_401: bool = False
    forza_versione: bool = False
    websocket_429: bool = False
    #: cosa risponde il websocket a un comando: finito, fallito, pin, accettato, silenzio, altra
    esito: str = "finito"
    token: str = ""
    refresh: str = ""
    sfide: dict[str, str] = field(default_factory=dict)
    login: int = 0
    rinnovi: int = 0
    chiamate: list[str] = field(default_factory=list)
    comandi: list[bytes] = field(default_factory=list)
    ricevute: list[bytes] = field(default_factory=list)
    versioni: list[str] = field(default_factory=list)
    _n: int = 0

    def app(self) -> web.Application:
        app = web.Application()
        r = app.router
        r.add_get("/as/authorization.oauth2", self._autorizza)
        r.add_get("/ciam/auth/login", self._pagina_login)
        r.add_post("/ciam/auth/ua", self._ua)
        r.add_post("/ciam/auth/login/user", self._utente)
        r.add_post("/ciam/auth/login/pass", self._password)
        r.add_post("/ciam/auth/disablePasskeyDemo", self._passkey)
        r.add_post("/as/RIPRENDI/resume/as/authorization.ping", self._riprendi)
        r.add_post("/as/token.oauth2", self._token)
        r.add_get("/v1/config", self._config)
        r.add_get("/lookup", self._negozio)
        r.add_get("/v2/vehicles", self._auto)
        r.add_get("/v1/vehicle/{vin}/capabilities/commands", self._comandi)
        r.add_get("/v1/vehicle/{vin}/capabilities", self._funzioni)
        r.add_get("/v1/vehicle/{vin}/vehicleattributes", self._attributi)
        r.add_get("/v2/ws", self._websocket)
        return app

    # il login
    async def _autorizza(self, r: web.Request) -> web.Response:
        assert r.cookies.get("CIAM.DEVICE") == "TELEFONO-1"
        assert "PF" not in r.cookies  # una sessione vecchia del login non deve arrivare qui
        assert r.query["client_id"] == modulo_cliente.CLIENT_ID
        self.sfide["ultima"] = r.query["code_challenge"]
        risposta = web.Response(
            status=302, headers={"Location": "/ciam/auth/login?resume=/as/RIPRENDI/resume/as/authorization.ping"}
        )
        risposta.set_cookie("PF", "SESSIONE-LOGIN")
        return risposta

    async def _pagina_login(self, r: web.Request) -> web.Response:
        assert r.query["resume"] == "/as/RIPRENDI/resume/as/authorization.ping"
        assert r.cookies.get("PF") == "SESSIONE-LOGIN"
        risposta = web.Response(text="<html>login</html>", content_type="text/html")
        risposta.set_cookie("CSRF", "C1")
        return risposta

    async def _ua(self, r: web.Request) -> web.Response:
        assert r.cookies.get("CSRF") == "C1"
        return web.json_response({})

    async def _utente(self, r: web.Request) -> web.Response:
        corpo = await r.json()
        if corpo["username"].lower() != EMAIL.lower():
            return web.json_response({"errorCode": "USER_NOT_FOUND"}, status=400)
        return web.json_response({"result": "GOTO_LOGIN_PASSWORD"})

    async def _password(self, r: web.Request) -> web.Response:
        assert r.cookies.get("PF") == "SESSIONE-LOGIN"  # i cookie del primo passo tornano indietro
        corpo = await r.json()
        if corpo["password"] != self.password:
            return web.json_response({"errorCode": "INVALID_CREDENTIALS"}, status=401)
        if self.passkey:
            return web.json_response({"passkeyDemoEnabled": True})
        return web.json_response({"result": self.risultato_login, "token": "PRELOGIN"})

    async def _passkey(self, r: web.Request) -> web.Response:
        assert (await r.json())["disablePasskeyDemo"] is True
        return web.json_response({"result": "RESUME2OIDCP", "token": "PRELOGIN"})

    async def _riprendi(self, r: web.Request) -> web.Response:
        assert (await r.post())["token"] == "PRELOGIN"
        return web.Response(status=302, headers={"Location": "rismycar://login-callback?code=CODICE"})

    def _nuovi_token(self) -> dict[str, Any]:
        self._n += 1
        self.token, self.refresh = f"A{self._n}", f"R{self._n}"
        return {"access_token": self.token, "refresh_token": self.refresh, "expires_in": 7200}

    async def _token(self, r: web.Request) -> web.Response:
        dati = await r.post()
        self.versioni.append(r.headers.get("Ris-Application-Version", ""))
        assert r.headers["X-Device-Id"] == "TELEFONO-1"
        if dati["grant_type"] == "authorization_code":
            sfida = base64.urlsafe_b64encode(hashlib.sha256(str(dati["code_verifier"]).encode()).digest())
            assert sfida.decode().rstrip("=") == self.sfide["ultima"]  # PKCE
            assert dati["code"] == "CODICE"
            self.login += 1
            return web.json_response(self._nuovi_token())
        if dati["grant_type"] == "refresh_token":
            if self.rifiuta_refresh or dati["refresh_token"] != self.refresh:
                return web.json_response({"error": "invalid_grant"}, status=400)
            self.rinnovi += 1
            return web.json_response(self._nuovi_token())
        return web.json_response({"error": "unsupported_grant_type"}, status=400)

    async def _config(self, _r: web.Request) -> web.Response:
        if self.forza_versione:
            return web.json_response(
                {"forceUpdate": {"status": "FORCE", "storeUrl": "https://apps.apple.com/app/id1519034860"}}
            )
        return web.json_response({})

    async def _negozio(self, r: web.Request) -> web.Response:
        assert r.query["id"] == "1519034860"
        return web.json_response({"results": [{"version": "1.70.0"}]})

    # le API
    def _autorizzato(self, r: web.Request) -> bool:
        self.chiamate.append(r.path)
        return r.headers.get("Authorization") == f"Bearer {self.token}"

    async def _auto(self, r: web.Request) -> web.Response:
        if not self._autorizzato(r):
            return web.json_response({"code": "401"}, status=401)
        self.versioni.append(r.headers.get("ris-application-version", ""))
        return web.json_response({"assignedVehicles": [AUTO_JSON], "fleets": []})

    async def _comandi(self, r: web.Request) -> web.Response:
        if self.capacita_401 or not self._autorizzato(r):
            return web.json_response({"code": "401"}, status=401)
        return web.json_response(COMANDI_JSON)

    async def _funzioni(self, r: web.Request) -> web.Response:
        if self.capacita_401 or not self._autorizzato(r):
            return web.json_response({"code": "401"}, status=401)
        return web.json_response({"features": {"precondNow": False}, "vehicle": {}})

    async def _attributi(self, r: web.Request) -> web.Response:
        if not self._autorizzato(r):
            return web.json_response({"code": "401"}, status=401)
        if self.guasto_stato:
            return web.Response(status=503, text="Service Unavailable")
        assert r.match_info["vin"] == c.VIN
        return web.Response(body=c.VEP_EQA, content_type="application/x-protobuf")

    # il websocket
    async def _websocket(self, r: web.Request) -> web.StreamResponse:
        if self.websocket_429:
            return web.Response(status=429)
        if r.headers.get("Authorization") != self.token or r.headers.get("OUTPUT-FORMAT") != "PROTO":
            return web.Response(status=401)
        ws = web.WebSocketResponse()
        await ws.prepare(r)
        # Appena collegati, il cloud spinge lo stato: va confermato.
        await ws.send_bytes(c.PUSH_VEP)
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.BINARY:
                continue
            if pb.per_numero(msg.data).get(3):  # ClientMessage.commandRequest
                self.comandi.append(msg.data)
                if self.esito == "silenzio":
                    continue
                await ws.send_bytes(c.STATO_IN_CODA)
                if self.esito == "altra":
                    await ws.send_bytes(c.STATO_ALTRA_RICHIESTA)
                    await ws.send_bytes(c.STATO_FINITO)
                finale = {"finito": c.STATO_FINITO, "fallito": c.STATO_FALLITO, "pin": c.STATO_PIN}
                if self.esito in finale:
                    await ws.send_bytes(finale[self.esito])
            else:
                self.ricevute.append(msg.data)
        return ws


@pytest.fixture
async def finta(socket_enabled: None) -> AsyncGenerator[tuple[FintaMercedes, Indirizzi]]:
    cloud = FintaMercedes()
    runner = web.AppRunner(cloud.app())
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    base = f"http://127.0.0.1:{runner.addresses[0][1]}"
    yield (
        cloud,
        Indirizzi(login=base, bff=base, widget=base, websocket=f"ws://{base[7:]}/v2/ws", itunes=f"{base}/lookup"),
    )
    await runner.cleanup()


@pytest.fixture
def richiesta_fissa(monkeypatch: pytest.MonkeyPatch) -> None:
    """Il request_id dei comandi come nei campioni; le attese brevi."""
    monkeypatch.setattr(modulo_cliente, "uuid", SimpleNamespace(uuid4=lambda: "RICHIESTA-1"))
    monkeypatch.setattr(modulo_cliente, "_ATTESA_COMANDO", 0.5)


def _cliente(
    s: aiohttp.ClientSession,
    indirizzi: Indirizzi,
    salvati: list[dict[str, Any]] | None = None,
    token: dict[str, Any] | None = None,
    password: str = PASSWORD,
    regione: str = "europa",
) -> ClienteMercedes:
    return ClienteMercedes(
        s,
        EMAIL,
        password,
        regione,
        token,
        "TELEFONO-1",
        salvati.append if salvati is not None else None,
        indirizzi=indirizzi,
    )


async def test_dal_login_allo_stato(finta) -> None:
    cloud, indirizzi = finta
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        cli = _cliente(s, indirizzi, salvati)
        await cli.accedi()
        (v,) = await cli.vetture()
        stato = await cli.stato(v)
    assert cloud.login == 1
    assert salvati[-1]["access_token"] == "A1" and salvati[-1]["refresh_token"] == "R1"
    assert (v.vin, v.modello, v.titolo, v.targa, v.condivisa) == (c.VIN, "EQA 250", "EQA 250", "MI 123 AB", False)
    assert comandi_di(v) == {"chiudi", "apri", "trova", "clima", "limite_ricarica"}
    assert v.extra["clima_ora"] is False
    assert stato.batteria == 64
    assert stato.limite_ricarica == 80


async def test_un_vecchio_cookie_del_login_non_disturba(finta) -> None:
    cloud, indirizzi = finta
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as s:
        s.cookie_jar.update_cookies({"PF": "VECCHIA"}, URL(indirizzi.login))
        await _cliente(s, indirizzi).accedi()
    assert cloud.login == 1


async def test_le_passkey_si_rifiutano_e_si_continua(finta) -> None:
    cloud, indirizzi = finta
    cloud.passkey = True
    async with aiohttp.ClientSession() as s:
        await _cliente(s, indirizzi).accedi()
    assert cloud.login == 1


async def test_password_sbagliata_email_sconosciuta_e_due_passaggi(finta) -> None:
    cloud, indirizzi = finta
    async with aiohttp.ClientSession() as s:
        with pytest.raises(ErroreCredenziali, match="rifiuta"):
            await _cliente(s, indirizzi, password="sbagliata").accedi()
        sconosciuto = ClienteMercedes(s, "altro@esempio.it", PASSWORD, dispositivo="TELEFONO-1", indirizzi=indirizzi)
        with pytest.raises(ErroreCredenziali, match="email"):
            await sconosciuto.accedi()
        cloud.risultato_login = "GOTO_LOGIN_OTP"
        with pytest.raises(ErroreCredenziali, match="due passaggi"):
            await _cliente(s, indirizzi).accedi()
        cloud.risultato_login = "GOTO_LOGIN_LEGAL_TEXTS"
        with pytest.raises(ErroreCredenziali, match="condizioni"):
            await _cliente(s, indirizzi).accedi()


async def test_il_token_scaduto_si_rinnova_e_si_salva(finta) -> None:
    cloud, indirizzi = finta
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        cli = _cliente(s, indirizzi, salvati)
        await cli.accedi()
        cli.token["scade"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        await cli.vetture()
    assert (cloud.login, cloud.rinnovi) == (1, 1)
    assert salvati[-1]["refresh_token"] == "R2"


async def test_se_il_refresh_non_vale_piu_si_rientra_con_la_password(finta) -> None:
    cloud, indirizzi = finta
    salvati: list[dict[str, Any]] = []
    scaduto = {"access_token": "VECCHIO", "refresh_token": "MORTO", "scade": datetime.now(UTC).isoformat()}
    async with aiohttp.ClientSession() as s:
        cli = _cliente(s, indirizzi, salvati, token=scaduto)
        await cli.vetture()
    assert cloud.login == 1
    assert salvati[-1]["access_token"] == "A1"


async def test_un_401_rinnova_e_riprova_una_volta(finta) -> None:
    cloud, indirizzi = finta
    async with aiohttp.ClientSession() as s:
        cli = _cliente(s, indirizzi)
        await cli.accedi()
        cloud.token = "CAMBIATO-DAL-CLOUD"  # il token in mano al cliente non vale più
        assert len(await cli.vetture()) == 1
    assert cloud.rinnovi == 1


async def test_un_guasto_non_chiede_le_credenziali(finta) -> None:
    cloud, indirizzi = finta
    async with aiohttp.ClientSession() as s:
        cli = _cliente(s, indirizzi)
        (v,) = await cli.vetture()
        cloud.guasto_stato = True
        with pytest.raises(ErroreRete):
            await cli.stato(v)
        spento = _cliente(s, Indirizzi(login="http://127.0.0.1:9", bff="", widget="", websocket=""))
        with pytest.raises(ErroreRete):
            await spento.accedi()


async def test_se_le_capacita_non_si_leggono_l_auto_si_legge_soltanto(finta) -> None:
    cloud, indirizzi = finta
    cloud.capacita_401 = True
    async with aiohttp.ClientSession() as s:
        (v,) = await _cliente(s, indirizzi).vetture()
    assert comandi_di(v) == set()
    assert cloud.rinnovi == 0  # un 401 lì non è un token scaduto


async def test_se_mercedes_chiede_l_app_nuova_si_prende_la_versione(finta) -> None:
    cloud, indirizzi = finta
    cloud.forza_versione = True
    async with aiohttp.ClientSession() as s:
        cli = _cliente(s, indirizzi)
        await cli.vetture()
    assert cloud.versioni == ["1.70.0", "1.70.0"]


async def test_chiudere_conferma_i_messaggi_e_aspetta_l_esito(finta, richiesta_fissa) -> None:
    cloud, indirizzi = finta
    async with aiohttp.ClientSession() as s:
        cli = _cliente(s, indirizzi)
        (v,) = await cli.vetture()
        await cli.chiudi(v, None)
    assert cloud.comandi == [c.CMD_CHIUDI]
    assert cloud.ricevute == [c.ACK_VEP_41, c.ACK_STATO_6, c.ACK_STATO_7]


async def test_tutti_i_comandi(finta, richiesta_fissa) -> None:
    cloud, indirizzi = finta
    async with aiohttp.ClientSession() as s:
        cli = _cliente(s, indirizzi)
        (v,) = await cli.vetture()
        with pytest.raises(ErrorePin):
            await cli.apri(v, None)
        assert cloud.comandi == []  # senza PIN non si apre nemmeno il websocket
        await cli.apri(v, PIN)
        await cli.trova(v, None)
        await cli.clima(v, True, None)
        await cli.clima(v, False, None)
        await cli.stato(v)  # il programma scelto è il 2
        await cli.limite_ricarica(v, 78, None)
        v.extra["clima_ora"] = True
        v.extra["comandi"] = [*v.extra["comandi"], "CHARGING_CONFIGURE"]
        await cli.clima(v, True, None)
        await cli.limite_ricarica(v, 90, None)
    assert cloud.comandi == [
        c.CMD_APRI,
        c.CMD_TROVA,
        c.CMD_CLIMA_IMMEDIATE,
        c.CMD_CLIMA_STOP,
        c.CMD_LIMITE_PROGRAMMA,
        c.CMD_CLIMA_NOW,
        c.CMD_LIMITE_CHARGING,
    ]


async def test_i_comandi_rifiutati(finta, richiesta_fissa) -> None:
    cloud, indirizzi = finta
    async with aiohttp.ClientSession() as s:
        cli = _cliente(s, indirizzi)
        (v,) = await cli.vetture()
        cloud.esito = "fallito"
        with pytest.raises(ErroreComando, match="Vehicle is in motion"):
            await cli.chiudi(v, None)
        cloud.esito = "pin"
        with pytest.raises(ErrorePin):
            await cli.apri(v, "0000")
        cloud.esito = "silenzio"
        with pytest.raises(ErroreRete, match="non ha risposto"):
            await cli.chiudi(v, None)
        # Preso in carico ma senza esito finale: si dà per buono, lo dirà la prossima lettura.
        cloud.esito = "accettato"
        await cli.chiudi(v, None)
        # L'esito di un'altra richiesta non conta.
        cloud.esito = "altra"
        await cli.chiudi(v, None)


async def test_il_websocket_rinnova_il_token_o_dice_che_e_bloccato(finta, richiesta_fissa) -> None:
    cloud, indirizzi = finta
    async with aiohttp.ClientSession() as s:
        cli = _cliente(s, indirizzi)
        (v,) = await cli.vetture()
        cloud.token = "CAMBIATO-DAL-CLOUD"
        await cli.chiudi(v, None)
        assert cloud.rinnovi == 1
        cloud.websocket_429 = True
        with pytest.raises(ErroreRete, match="bloccato"):
            await cli.chiudi(v, None)


async def test_una_regione_sconosciuta() -> None:
    with pytest.raises(ErroreApi):
        ClienteMercedes(None, EMAIL, PASSWORD, "cina")  # type: ignore[arg-type]


# --- in Home Assistant ---------------------------------------------------------------------------

EQA = Vettura(
    vin=c.VIN,
    marca="mercedes",
    modello="EQA 250",
    nome="EQA 250",
    targa="MI 123 AB",
    extra={"comandi": ["DOORS_LOCK", "DOORS_UNLOCK", "ZEV_PRECONDITIONING_START", "ZEV_PRECONDITIONING_STOP"]},
)
TOKEN = {"access_token": "A1", "refresh_token": "R1", "scade": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}


async def test_configurare_una_mercedes(hass: HomeAssistant) -> None:
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "mercedes"})
    assert r["step_id"] == "credenziali"
    assert {str(k) for k in r["data_schema"].schema} == {"email", "password", "regione"}

    with patch.object(ClienteMercedes, "accedi", AsyncMock(side_effect=ErroreCredenziali("no"))):
        r = await hass.config_entries.flow.async_configure(
            r["flow_id"], {"email": EMAIL, "password": "sbagliata", "regione": "europa"}
        )
    assert r["errors"] == {"base": "credenziali"}

    async def entra(self: ClienteMercedes) -> None:
        self.token = dict(TOKEN)

    with (
        patch.object(ClienteMercedes, "accedi", entra),
        patch.object(ClienteMercedes, "vetture", AsyncMock(return_value=[EQA])),
        patch.object(ClienteMercedes, "stato", AsyncMock(return_value=interpreta(m.vep(c.VEP_EQA)))),
    ):
        r = await hass.config_entries.flow.async_configure(
            r["flow_id"], {"email": f" {EMAIL} ", "password": PASSWORD, "regione": "europa"}
        )
        await hass.async_block_till_done()
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["title"] == f"Mercedes-Benz · {EMAIL}"
    assert r["result"].unique_id == f"mercedes_{EMAIL.lower()}"
    assert r["data"]["marca"] == "mercedes"
    assert r["data"]["email"] == EMAIL
    assert r["data"]["token"] == TOKEN
    assert r["data"]["dispositivo"]
    assert hass.states.get("sensor.eqa_250_batteria").state == "64"
    assert hass.states.get("lock.eqa_250_serratura") is not None


async def test_il_token_che_ruota_si_salva_senza_ricaricare(hass: HomeAssistant) -> None:
    voce = MockConfigEntry(
        domain=DOMAIN,
        title=f"Mercedes-Benz · {EMAIL}",
        unique_id=f"mercedes_{EMAIL.lower()}",
        data={
            CONF_FONTE: FONTE_CLOUD,
            CONF_MARCA: "mercedes",
            "email": EMAIL,
            "password": PASSWORD,
            "regione": "europa",
            "dispositivo": "TELEFONO-1",
            "token": TOKEN,
        },
    )
    voce.add_to_hass(hass)
    with (
        patch.object(ClienteMercedes, "vetture", AsyncMock(return_value=[EQA])),
        patch.object(ClienteMercedes, "stato", AsyncMock(return_value=interpreta(m.vep(c.VEP_EQA)))),
    ):
        assert await hass.config_entries.async_setup(voce.entry_id)
        await hass.async_block_till_done()
        prima = voce.runtime_data
        cliente = prima.conto.cliente
        assert cliente.dispositivo == "TELEFONO-1"
        cliente._salva({**TOKEN, "access_token": "A2", "refresh_token": "R2"})
        await hass.async_block_till_done()
    assert voce.data["token"]["refresh_token"] == "R2"
    assert voce.runtime_data is prima
    # Le opzioni chiedono il PIN e hanno i comandi.
    r = await hass.config_entries.options.async_init(voce.entry_id)
    chiavi = {str(k) for k in r["data_schema"].schema}
    assert {"pin", "comandi_attivi"} <= chiavi
    assert mercedes.INTERVALLO_MINUTI >= 10
