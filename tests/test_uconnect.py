"""Uconnect: lo stato dei payload veri, la firma AWS, il cliente contro un cloud finto e il flusso in HA."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
import json
import re
import time
from typing import Any
from unittest.mock import patch

import aiohttp
from aiohttp import web
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gdaauto.const import CONF_FONTE, CONF_MARCA, CONF_MARCHIO, DOMAIN, FONTE_CLOUD
from custom_components.gdaauto.marche import MARCHE
from custom_components.gdaauto.marche.catalogo import PER_ID
from custom_components.gdaauto.marche.errori import (
    ErroreApi,
    ErroreComando,
    ErroreCredenziali,
    ErrorePin,
    ErroreRete,
)
from custom_components.gdaauto.marche.uconnect import (
    CAMPI,
    INTERVALLO_MINUTI,
    VUOLE_PIN,
    ClienteUconnect,
    cliente as modulo_cliente,
)
from custom_components.gdaauto.marche.uconnect.cliente import comandi_di, vettura_da_json
from custom_components.gdaauto.marche.uconnect.dati import interpreta, km, kpa, minuti_alla_fine
from custom_components.gdaauto.marche.uconnect.firma import firma, query_canonica
from custom_components.gdaauto.marche.uconnect.regioni import Configurazione, configurazione
from custom_components.gdaauto.vettura import StatoRicarica

from .uconnect_campioni import ELENCO, POSIZIONE, REMOTO, STATO_4XE, STATO_500E, UID, VIN_500E

# --- il contratto e il catalogo ---------------------------------------------------------


def test_il_contratto() -> None:
    assert "uconnect" in MARCHE
    assert VUOLE_PIN is True
    assert INTERVALLO_MINUTI >= 10
    assert [c.nome for c in CAMPI] == ["email", "password", "regione", "pin"]
    regione = next(c for c in CAMPI if c.nome == "regione")
    assert regione.predefinito == "eu"
    assert {v for v, _ in regione.scelte} == {"eu", "us", "asia"}
    for marchio in ("fiat", "abarth", "jeep", "alfa_romeo", "lancia", "maserati"):
        assert PER_ID[marchio].nativo == "uconnect"


def test_marchi_e_regioni() -> None:
    fiat = configurazione("fiat", "eu")
    assert fiat.login == "https://loginmyuconnect.fiat.com"
    assert fiat.api == "https://channels.sdpr-01.fcagcv.com"
    assert fiat.regione_aws == "eu-west-1"
    assert fiat.cognito == "https://cognito-identity.eu-west-1.amazonaws.com/"
    assert fiat.codici_marchio == ("FIAT",)
    assert configurazione("jeep", "eu").codici_marchio == ("REST",)
    assert configurazione("alfaromeo", "eu").login == "https://login.alfaromeo.com"
    us = configurazione("jeep", "us")
    assert (us.api, us.regione_aws, us.codici_marchio) == ("https://channels.sdpr-02.fcagcv.com", "us-east-1", ("ALL",))
    # Lancia entra da Fiat, e prova più codici di marchio.
    lancia = configurazione("lancia", "eu")
    assert lancia.login == fiat.login
    assert lancia.codici_marchio[0] == "LANCIA"
    # Una regione che non c'è ripiega sull'Europa.
    assert configurazione("maserati", "marte") == configurazione("maserati", "eu")


# --- l'interpretazione ------------------------------------------------------------------


def test_una_500e_in_carica_alla_wallbox() -> None:
    s = interpreta(STATO_500E, POSIZIONE, REMOTO)
    assert s.batteria == 71
    assert s.autonomia_km == 195
    assert s.odometro_km == 12345
    assert s.stato_ricarica == StatoRicarica.IN_CARICA
    assert s.in_carica is True
    assert s.cavo_collegato is True
    # Collegata in AC (LEVEL_2): il tempo è quello della wallbox, non quello della presa.
    assert s.tempo_ricarica_min == 135
    assert s.pneumatici_kpa == {"anteriore_sinistro": 250, "anteriore_destro": 248, "posteriore_sinistro": 240}
    assert s.quadro_acceso is False
    assert s.chiusa is True
    assert s.finestrini_aperti is True
    assert (s.latitudine, s.longitudine) == (45.0703, 7.6869)
    assert s.posizione_con_segno is True
    assert s.letto == datetime(2026, 10, 18, 10, 0, tzinfo=UTC)
    assert s.grezzo["stato"] is STATO_500E


def test_una_jeep_4xe_americana_in_miglia_e_psi() -> None:
    s = interpreta(STATO_4XE)
    assert s.batteria == 38
    assert s.autonomia_km == 20.9  # 13 mi di autonomia elettrica
    assert s.odometro_km == 16093.4
    assert s.stato_ricarica == StatoRicarica.NON_IN_CARICA
    assert s.cavo_collegato is False
    assert s.tempo_ricarica_min is None
    assert s.quadro_acceso is True
    assert s.pneumatici_kpa["anteriore_sinistro"] == 248.2
    assert "posteriore_destro" not in s.pneumatici_kpa  # 0 psi è «non lo so»
    # Senza serrature né posizione non si inventa niente.
    assert s.chiusa is None and s.latitudine is None


def test_il_tempo_del_caricatore_collegato() -> None:
    tempi = {"timeToFullyChargeL1": 900, "timeToFullyChargeL2": 120, "timeToFullyChargeL3": 30}
    assert minuti_alla_fine({**tempi, "chargingLevel": "LEVEL_1"}) == 900
    assert minuti_alla_fine({**tempi, "chargingLevel": "LEVEL_3"}) == 30
    # Il caricatore detto non ha un tempo: niente, non quello di un altro.
    assert minuti_alla_fine({**tempi, "timeToFullyChargeL3": -1, "chargingLevel": "LEVEL_3"}) is None
    # Senza caricatore detto, il più breve fra quelli validi.
    assert minuti_alla_fine({**tempi, "chargingLevel": "DEFAULT"}) == 30
    assert minuti_alla_fine({"timeToFullyChargeL1": -1}) is None


def test_le_unita() -> None:
    assert km("100", "mi") == 160.9
    assert km("null", "km") is None
    assert kpa("2.5", "bar") == 250
    assert kpa(30, "psi") == 206.8


def test_lo_stato_vuoto_e_quello_parziale() -> None:
    s = interpreta({})
    assert s.batteria is None and s.stato_ricarica is None and s.letto is None
    # Una ricarica completata col cavo ancora attaccato.
    s = interpreta({"evInfo": {"battery": {"chargingStatus": "CHARGE_COMPLETE", "plugInStatus": "true"}}})
    assert s.stato_ricarica == StatoRicarica.COMPLETATA
    assert s.cavo_collegato is True


def test_l_anagrafica_e_i_comandi() -> None:
    v = vettura_da_json(ELENCO["vehicles"][0])
    assert (v.vin, v.marca, v.modello, v.nome, v.anno, v.colore) == (
        VIN_500E,
        "uconnect",
        "Nuova 500",
        "Cinquino",
        2022,
        "BLUE",
    )
    # HBLF è spento, REON non è possibile: restano chiudi, clima e ricarica.
    assert comandi_di(v) == {"chiudi", "clima", "ricarica"}
    senza_nome = vettura_da_json({"vin": "X", "make": "ALFA ROMEO", "modelDescription": "Junior Elettrica"})
    assert senza_nome.nome == "Alfa Romeo Junior Elettrica"
    # Senza l'elenco dei servizi decide il cloud.
    assert comandi_di(senza_nome) == {"chiudi", "trova", "clima", "ricarica"}


# --- la firma AWS -----------------------------------------------------------------------

# I valori registrati da evcc (vehicle/fiat/sigv4_test.go, MIT) con l'SDK ufficiale di Amazon.
_INTESTAZIONI_EVCC = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "X-Clientapp-Version": "1.0",
    "ClientrequestId": "abcdefghijklmnop",
    "X-Api-Key": "qLYupk65UU1tw2Ih1cJhs4izijgRDbir2UFHA3Je",
    "X-Originator-Type": "web",
    "locale": "de_de",
}


@pytest.mark.parametrize(
    ("metodo", "url", "corpo", "firmate", "attesa"),
    [
        (
            "GET",
            "https://channels.sdpr-01.fcagcv.com/v4/accounts/0123456789abcdef/vehicles?stage=ALL",
            "",
            "accept;clientrequestid;content-type;host;locale;x-amz-date;x-amz-security-token;x-api-key;"
            "x-clientapp-version;x-originator-type",
            "f1bcde2296b3473715e6886a63c30da9bc0c7b4edc56b323fedfe339d0e5557f",
        ),
        (
            "POST",
            "https://channels.sdpr-01.fcagcv.com/v1/accounts/0123456789abcdef/vehicles/ZFA1234567890/remote",
            '{"command":"RON","pin":"c2VjcmV0"}',
            "accept;clientrequestid;content-length;content-type;host;locale;x-amz-date;x-amz-security-token;"
            "x-api-key;x-clientapp-version;x-originator-type",
            "3f1e0e55fede45545998ac824b6eee14145c6e8222c74c11f7c46caa55ffce57",
        ),
        (
            "POST",
            "https://mfa.fcl-01.fcagcv.com/v1/accounts/0123456789abcdef/ignite/pin/authenticate",
            '{"pin":"c2VjcmV0"}',
            "accept;clientrequestid;content-length;content-type;host;locale;x-amz-date;x-amz-security-token;"
            "x-api-key;x-clientapp-version;x-originator-type",
            "c0a44f05b8573f8415cf4838a42bc0b81703d22f402809cd790f2cd095afada7",
        ),
    ],
)
def test_la_firma_come_l_sdk_di_amazon(metodo: str, url: str, corpo: str, firmate: str, attesa: str) -> None:
    h = firma(
        metodo,
        url,
        _INTESTAZIONI_EVCC,
        corpo.encode() or None,
        chiave_accesso="ASIAEXAMPLE",
        segreto="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        token_sessione="IQoJb3JpZ2luX2VjEXAMPLE",
        regione="eu-west-1",
        ora=datetime(2026, 10, 15, 13, 45, tzinfo=UTC),
    )
    assert h["X-Amz-Date"] == "20261015T134500Z"
    assert h["X-Amz-Security-Token"] == "IQoJb3JpZ2luX2VjEXAMPLE"
    assert h["Authorization"] == (
        "AWS4-HMAC-SHA256 Credential=ASIAEXAMPLE/20261015/eu-west-1/execute-api/aws4_request, "
        f"SignedHeaders={firmate}, Signature={attesa}"
    )


def test_la_query_canonica() -> None:
    assert query_canonica("stage=ALL&brand=FIAT&sdp=ALL") == "brand=FIAT&sdp=ALL&stage=ALL"
    assert query_canonica("a=x+y&b=%2F") == "a=x%20y&b=%2F"
    assert query_canonica("") == ""


# --- il cloud finto ---------------------------------------------------------------------

_AUTH = re.compile(r"Credential=([^/]+)/(\d{8})/([^/]+)/([^/]+)/aws4_request, SignedHeaders=([^,]+), Signature=(\w+)")


@dataclass
class FintoUconnect:
    """Gigya, il token Cognito, Cognito e i canali, tutti su 127.0.0.1."""

    password: str = "giusta"
    pin: str = "1234"
    login_token_validi: set[str] = field(default_factory=lambda: {"LT-1"})
    accessi_con_password: int = 0
    jwt_rilasciati: int = 0
    credenziali: int = 0
    #: AK-n → segreto: le credenziali AWS rilasciate
    segreti: dict[str, str] = field(default_factory=dict)
    scadute: set[str] = field(default_factory=set)
    chiamate: list[str] = field(default_factory=list)
    comandi: list[dict[str, Any]] = field(default_factory=list)
    gigya_guasto: bool = False
    canali_guasti: bool = False
    stato_v3_manca: bool = False
    v1_carica_vietata: bool = False
    esito: str = "SUCCESS"
    #: le serrature: non tutte le auto le mandano
    con_serrature: bool = False
    #: brand → elenco; None per «senza brand»
    elenchi: dict[str | None, dict[str, Any]] = field(default_factory=lambda: {"FIAT": ELENCO})

    def app(self) -> web.Application:
        app = web.Application()
        r = app.router
        r.add_get("/accounts.webSdkBootstrap", self._bootstrap)
        r.add_post("/accounts.login", self._login)
        r.add_post("/accounts.getJWT", self._jwt)
        r.add_post("/v2/cognito/identity/token", self._identita)
        r.add_post("/cognito/", self._cognito)
        r.add_post("/mfa/v1/accounts/{uid}/ignite/pin/authenticate", self._pin)
        r.add_route("*", "/{resto:v[1-4]/.*}", self._canali)
        return app

    # --- Gigya --------------------------------------------------------------------------

    async def _bootstrap(self, r: web.Request) -> web.Response:
        assert r.query["apiKey"] == "CHIAVE-LOGIN"
        risposta = web.json_response({"statusCode": 200, "errorCode": 0, "statusReason": "OK"})
        risposta.set_cookie("gmid", "GMID-1")
        return risposta

    async def _login(self, r: web.Request) -> web.Response:
        if self.gigya_guasto:
            return web.Response(status=503, text="<html>down</html>")
        f = await r.post()
        assert f["APIKey"] == "CHIAVE-LOGIN"
        assert r.cookies.get("gmid") == "GMID-1"  # il biscotto del bootstrap arriva al login
        assert "password" not in r.query  # la password va nel corpo, non nell'indirizzo
        if f["loginID"] != "a@b.it" or f["password"] != self.password:
            return web.json_response(
                {"statusCode": 403, "errorCode": 403042, "errorMessage": "Invalid LoginID", "errorDetails": "x"}
            )
        self.accessi_con_password += 1
        token = f"LT-{self.accessi_con_password + 1}"
        self.login_token_validi.add(token)
        return web.json_response(
            {"statusCode": 200, "errorCode": 0, "UID": UID, "sessionInfo": {"login_token": token, "expires_in": "1"}}
        )

    async def _jwt(self, r: web.Request) -> web.Response:
        f = await r.post()
        if f["login_token"] not in self.login_token_validi:
            return web.json_response({"statusCode": 403, "errorCode": 403005, "errorMessage": "Unauthorized user"})
        self.jwt_rilasciati += 1
        return web.json_response({"statusCode": 200, "errorCode": 0, "id_token": f"JWT-{self.jwt_rilasciati}"})

    # --- Cognito ------------------------------------------------------------------------

    async def _identita(self, r: web.Request) -> web.Response:
        assert r.headers["x-api-key"] == "CHIAVE-API"
        corpo = await r.json()
        assert corpo["gigya_token"].startswith("JWT-")
        return web.json_response({"Token": "COGNITO-" + corpo["gigya_token"], "IdentityId": "eu-west-1:id-1"})

    async def _cognito(self, r: web.Request) -> web.Response:
        assert r.headers["X-Amz-Target"] == "AWSCognitoIdentityService.GetCredentialsForIdentity"
        corpo = json.loads(await r.text())
        assert corpo["Logins"]["cognito-identity.amazonaws.com"].startswith("COGNITO-JWT-")
        self.credenziali += 1
        chiave = f"AK-{self.credenziali}"
        self.segreti[chiave] = f"SEGRETO-{self.credenziali}"
        return web.json_response(
            {
                "IdentityId": corpo["IdentityId"],
                "Credentials": {
                    "AccessKeyId": chiave,
                    "SecretKey": self.segreti[chiave],
                    "SessionToken": f"ST-{self.credenziali}",
                    "Expiration": time.time() + 3600,
                },
            }
        )

    # --- la firma, controllata da capo come farebbe Amazon ---------------------------------

    async def _firmata(self, r: web.Request) -> web.Response | None:
        m = _AUTH.search(r.headers.get("Authorization", ""))
        if not m:
            return web.json_response({"message": "Missing Authentication Token"}, status=403)
        chiave, _giorno, regione, servizio, firmate, _firma = m.groups()
        if chiave in self.scadute:
            return web.json_response(
                {"message": "The security token included in the request is expired"},
                status=403,
                headers={"x-amzn-ErrorType": "ExpiredTokenException"},
            )
        corpo = await r.read()
        nomi = firmate.split(";")
        assert {"host", "x-amz-date", "x-amz-security-token", "x-api-key"} <= set(nomi)
        intestazioni = {n: r.headers[n] for n in nomi if n not in ("host", "content-length", "x-amz-date")}
        token = intestazioni.pop("x-amz-security-token")
        assert token == f"ST-{chiave.removeprefix('AK-')}"
        rifatta = firma(
            r.method,
            f"http://{r.headers['Host']}{r.raw_path}",
            intestazioni,
            corpo or None,
            chiave_accesso=chiave,
            segreto=self.segreti[chiave],
            token_sessione=token,
            regione=regione,
            servizio=servizio,
            ora=datetime.strptime(r.headers["X-Amz-Date"], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC),
        )
        if rifatta["Authorization"] != r.headers["Authorization"]:
            return web.json_response({"message": "The request signature we calculated does not match"}, status=403)
        return None

    async def _pin(self, r: web.Request) -> web.Response:
        if (errore := await self._firmata(r)) is not None:
            return errore
        assert r.headers["x-api-key"] == "CHIAVE-PIN"
        assert r.match_info["uid"] == UID
        pin = base64.b64decode((await r.json())["pin"]).decode()
        if pin != self.pin:
            return web.json_response({"name": "INVALID_PIN", "message": "Invalid PIN"}, status=403)
        return web.json_response({"token": "PIN-TOKEN", "expiry": 120000})

    async def _canali(self, r: web.Request) -> web.Response:
        if self.canali_guasti:
            return web.Response(status=503, text="Service Unavailable")
        if (errore := await self._firmata(r)) is not None:
            return errore
        assert r.headers["x-api-key"] == "CHIAVE-API"
        resto = r.match_info["resto"]
        self.chiamate.append(resto)
        conto = f"accounts/{UID}"
        if r.method == "GET" and resto == f"v4/{conto}/vehicles":
            assert r.query["stage"] == "ALL" and r.query["sdp"] == "ALL"
            return web.json_response(self.elenchi.get(r.query.get("brand"), {"vehicles": []}))
        if resto == f"v3/{conto}/vehicles/{VIN_500E}/status/":
            if self.stato_v3_manca:
                return web.json_response({"message": "not found"}, status=404)
            return web.json_response(STATO_500E)
        if resto == f"v4/{conto}/vehicles/{VIN_500E}/status/":
            return web.json_response(STATO_500E)
        if resto == f"v1/{conto}/vehicles/{VIN_500E}/location/lastknown":
            return web.json_response(POSIZIONE)
        if resto == f"v1/{conto}/vehicles/{VIN_500E}/remote/status":
            if self.con_serrature:
                return web.json_response(REMOTO)
            return web.json_response({"message": "not supported"}, status=404)
        if r.method == "POST" and resto.endswith(("/remote", "/ev/chargenow")):
            corpo = await r.json()
            if self.v1_carica_vietata and resto.startswith("v1/") and resto.endswith("/ev/chargenow"):
                return web.json_response({"name": "FORBIDDEN", "debugMsg": "use v4"}, status=403)
            assert corpo["pinAuth"] == "PIN-TOKEN"
            self.comandi.append({"percorso": resto, **corpo})
            return web.json_response(
                {"command": corpo["command"], "correlationId": "CORR-1", "responseStatus": "pending"}
            )
        if resto == f"v1/{conto}/vehicles/{VIN_500E}/remote/CORR-1/status/":
            return web.json_response({"status": self.esito, "correlationId": "CORR-1"})
        return web.json_response({"message": f"nessuna strada per {resto}"}, status=404)


@pytest.fixture
async def cloud(socket_enabled: None) -> AsyncGenerator[tuple[FintoUconnect, Configurazione]]:
    finto = FintoUconnect()
    runner = web.AppRunner(finto.app())
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    url = f"http://127.0.0.1:{runner.addresses[0][1]}"
    config = replace(
        configurazione("fiat", "eu"),
        login=url,
        chiave_login="CHIAVE-LOGIN",
        token=f"{url}/v2/cognito/identity/token",
        api=url,
        chiave_api="CHIAVE-API",
        pin=f"{url}/mfa",
        chiave_pin="CHIAVE-PIN",
        url_cognito=f"{url}/cognito/",
    )
    yield finto, config
    await runner.cleanup()


def _cliente(
    s: aiohttp.ClientSession,
    config: Configurazione,
    salvati: list[dict[str, Any]] | None = None,
    *,
    password: str = "giusta",
    sessione: dict[str, Any] | None = None,
    pin: str | None = "1234",
) -> ClienteUconnect:
    c = ClienteUconnect(
        s,
        email="a@b.it",
        password=password,
        pin=pin,
        sessione=sessione,
        salva=salvati.append if salvati is not None else None,
        config=config,
    )
    c.attesa_esito = 0.01
    c.tempo_esito = 2
    return c


async def test_dal_login_allo_stato(cloud) -> None:
    finto, config = cloud
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, config, salvati)
        await c.accedi()
        (v,) = await c.vetture()
        stato = await c.stato(v)
    assert finto.accessi_con_password == 1
    assert salvati == [{"uid": UID, "login_token": "LT-2"}]
    assert v.titolo == "Cinquino"
    assert stato.batteria == 71
    assert stato.tempo_ricarica_min == 135
    assert stato.latitudine == 45.0703
    # Le serrature non ci sono (404): nessun errore, e nessun valore inventato.
    assert stato.chiusa is None
    # Nessun deep refresh e nessun comando per leggere.
    assert not finto.comandi
    assert not any("ev" in x.split("/") for x in finto.chiamate)


async def test_il_login_salvato_non_rimanda_la_password(cloud) -> None:
    finto, config = cloud
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, config, salvati, sessione={"uid": UID, "login_token": "LT-1"})
        await c.accedi()
        assert len(await c.vetture()) == 1
    assert finto.accessi_con_password == 0
    assert finto.jwt_rilasciati == 1
    assert salvati == []


async def test_il_login_salvato_scaduto_rientra_con_la_password(cloud) -> None:
    finto, config = cloud
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, config, salvati, sessione={"uid": UID, "login_token": "VECCHIO"})
        await c.accedi()
    assert finto.accessi_con_password == 1
    assert salvati[-1]["login_token"] == "LT-2"


async def test_le_credenziali_aws_scadute_si_rinnovano(cloud) -> None:
    finto, config = cloud
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, config)
        await c.accedi()
        finto.scadute.add("AK-1")
        assert len(await c.vetture()) == 1
    assert finto.credenziali == 2
    # La seconda volta col login_token: la password si è mandata una volta sola.
    assert finto.accessi_con_password == 1


async def test_le_credenziali_aws_in_scadenza_si_rinnovano_prima(cloud) -> None:
    finto, config = cloud
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, config)
        await c.accedi()
        assert c._credenziali is not None
        c._credenziali["scade"] = datetime.now(UTC)
        await c.vetture()
    assert finto.credenziali == 2


async def test_la_password_sbagliata(cloud) -> None:
    _, config = cloud
    async with aiohttp.ClientSession() as s:
        with pytest.raises(ErroreCredenziali):
            await _cliente(s, config, password="sbagliata").accedi()


async def test_i_guasti_non_chiedono_di_rientrare(cloud) -> None:
    finto, config = cloud
    async with aiohttp.ClientSession() as s:
        finto.gigya_guasto = True
        with pytest.raises(ErroreRete):
            await _cliente(s, config).accedi()
        finto.gigya_guasto = False
        c = _cliente(s, config)
        await c.accedi()
        finto.canali_guasti = True
        with pytest.raises(ErroreRete):
            await c.vetture()
        # E un cloud che non c'è proprio.
        spento = replace(config, login="http://127.0.0.1:9")
        with pytest.raises(ErroreRete):
            await _cliente(s, spento).accedi()


async def test_la_versione_dello_stato_si_ricorda(cloud) -> None:
    finto, config = cloud
    finto.stato_v3_manca = True
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, config)
        (v,) = await c.vetture()
        assert (await c.stato(v)).batteria == 71
        finto.chiamate.clear()
        await c.stato(v)
    assert not any(x.startswith("v3/") for x in finto.chiamate)
    assert any(x.startswith("v4/") and x.endswith("/status/") for x in finto.chiamate)


async def test_lancia_prova_piu_codici_di_marchio(cloud) -> None:
    finto, config = cloud
    finto.elenchi = {"REST": ELENCO}
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, replace(config, codici_marchio=configurazione("lancia", "eu").codici_marchio))
        assert len(await c.vetture()) == 1
        assert c._codice_marchio == "REST"
        # Nessun codice va: si prova senza.
        finto.elenchi = {None: ELENCO}
        c2 = _cliente(s, replace(config, codici_marchio=("LANCIA",)))
        assert len(await c2.vetture()) == 1


async def test_chiudere_col_pin(cloud) -> None:
    finto, config = cloud
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, config)
        (v,) = await c.vetture()
        await c.chiudi(v, None)  # il PIN della configurazione
        await c.clima(v, True, "1234")
        await c.clima(v, False, "1234")
    assert [(x["command"], x["percorso"].split("/", 1)[0]) for x in finto.comandi] == [
        ("RDL", "v1"),
        ("ROPRECOND", "v1"),
        ("ROPRECOND_OFF", "v1"),
    ]
    assert finto.comandi[0]["percorso"] == f"v1/accounts/{UID}/vehicles/{VIN_500E}/remote"


async def test_la_ricarica_ripiega_sulla_v4(cloud) -> None:
    finto, config = cloud
    finto.v1_carica_vietata = True
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, config)
        (v,) = await c.vetture()
        await c.ricarica(v, True, None)
        with pytest.raises(ErroreComando):
            await c.ricarica(v, False, None)
    assert [(x["command"], x["percorso"]) for x in finto.comandi] == [
        ("START_CHARGE", f"v4/accounts/{UID}/vehicles/{VIN_500E}/ev/chargenow")
    ]


async def test_il_pin_sbagliato_o_mancante(cloud) -> None:
    finto, config = cloud
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, config, pin=None)
        (v,) = await c.vetture()
        with pytest.raises(ErrorePin):
            await c.apri(v, None)
        with pytest.raises(ErrorePin):
            await c.apri(v, "0000")
    assert finto.comandi == []


async def test_un_comando_che_l_auto_non_esegue(cloud) -> None:
    finto, config = cloud
    finto.esito = "FAILURE"
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, config)
        (v,) = await c.vetture()
        with pytest.raises(ErroreComando):
            await c.chiudi(v, None)


async def test_un_comando_alla_volta(cloud) -> None:
    finto, config = cloud
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, config)
        (v,) = await c.vetture()
        await asyncio.gather(c.chiudi(v, None), c.apri(v, None))
    assert sorted(x["command"] for x in finto.comandi) == ["RDL", "RDU"]


async def test_un_auto_senza_dati(cloud) -> None:
    finto, config = cloud
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, config)
        (v,) = await c.vetture()
        with pytest.raises(ErroreApi):
            await c.stato(replace(v, vin="ZFA00000000000000"))
    assert finto.comandi == []


# --- dentro Home Assistant --------------------------------------------------------------


async def test_configurare_una_fiat(hass: HomeAssistant, cloud) -> None:
    finto, config = cloud
    finto.con_serrature = True
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "fiat"})
    assert r["step_id"] == "credenziali"
    # Il marchio arriva dal catalogo: non si chiede.
    assert {str(k) for k in r["data_schema"].schema} == {"email", "password", "regione", "pin"}
    with patch.object(modulo_cliente, "configurazione", lambda _m, _r: config):
        r = await hass.config_entries.flow.async_configure(
            r["flow_id"], {"email": "a@b.it", "password": "sbagliata", "regione": "eu"}
        )
        assert r["errors"] == {"base": "credenziali"}
        r = await hass.config_entries.flow.async_configure(
            r["flow_id"], {"email": "a@b.it", "password": "giusta", "regione": "eu"}
        )
        await hass.async_block_till_done()
        assert r["type"] is FlowResultType.CREATE_ENTRY
        assert r["title"] == "Fiat · a@b.it"
        assert r["result"].unique_id == "uconnect_fiat_eu_a@b.it"
        assert r["data"][CONF_MARCA] == "uconnect"
        assert r["data"][CONF_MARCHIO] == "fiat"
        assert r["data"]["marchio"] == "fiat"
        assert r["data"]["token"] == {"uid": UID, "login_token": "LT-2"}
        assert hass.states.get("sensor.cinquino_batteria").state == "71"
        assert hass.states.get("lock.cinquino_serratura").state == "locked"
        assert hass.states.get("binary_sensor.cinquino_in_carica").state == "on"
        # Il PIN si chiede nelle opzioni.
        voce = r["result"]
        o = await hass.config_entries.options.async_init(voce.entry_id)
        assert "pin" in {str(k) for k in o["data_schema"].schema}
        assert voce.runtime_data.auto[0]._normale.total_seconds() == 600
        await hass.config_entries.async_unload(voce.entry_id)
        await hass.async_block_till_done()


async def test_il_login_che_cambia_non_ricarica_l_integrazione(hass: HomeAssistant, cloud) -> None:
    finto, config = cloud
    voce = MockConfigEntry(
        domain=DOMAIN,
        title="Jeep · a@b.it",
        unique_id="uconnect_jeep_eu_a@b.it",
        data={
            CONF_FONTE: FONTE_CLOUD,
            CONF_MARCA: "uconnect",
            CONF_MARCHIO: "jeep",
            "marchio": "jeep",
            "email": "a@b.it",
            "password": "giusta",
            "regione": "eu",
            "token": {"uid": UID, "login_token": "SCADUTO"},
        },
        options={"pin": "1234"},
    )
    voce.add_to_hass(hass)
    finto.elenchi = {"FIAT": ELENCO}
    with patch.object(modulo_cliente, "configurazione", lambda _m, _r: config):
        assert await hass.config_entries.async_setup(voce.entry_id)
        await hass.async_block_till_done()
        dati_prima = voce.runtime_data
        # Il login_token salvato non valeva più: si è rientrati, e il nuovo è nella voce.
        assert voce.data["token"]["login_token"] == "LT-2"
        assert hass.states.get("sensor.cinquino_batteria").state == "71"
        voce.runtime_data.conto.cliente._salva({"uid": UID, "login_token": "LT-9"})
        await hass.async_block_till_done()
        assert voce.data["token"]["login_token"] == "LT-9"
        assert voce.runtime_data is dati_prima  # non si è ricaricata
        await hass.config_entries.async_unload(voce.entry_id)
        await hass.async_block_till_done()
