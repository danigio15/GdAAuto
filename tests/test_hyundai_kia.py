"""Hyundai, Kia e Genesis: le firme, lo stato dei payload veri, il cliente contro un cloud finto e il flusso in HA."""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import aiohttp
from aiohttp import web
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gdaauto.const import CONF_FONTE, CONF_MARCA, CONF_MARCHIO, DOMAIN, FONTE_CLOUD
from custom_components.gdaauto.marche import MARCHE
from custom_components.gdaauto.marche.catalogo import PER_ID
from custom_components.gdaauto.marche.errori import ErroreComando, ErroreCredenziali, ErrorePin, ErroreRete
from custom_components.gdaauto.marche.hyundai_kia import CAMPI, VUOLE_PIN, ClienteHyundaiKia, marchi
from custom_components.gdaauto.marche.hyundai_kia.cliente import (
    _gradi_vecchi,
    cifra_password,
    comandi_di,
    lingua_da,
    vettura_da_json,
)
from custom_components.gdaauto.marche.hyundai_kia.dati import (
    data_cloud,
    interpreta_ccs2,
    interpreta_vecchio,
    limite_rapida,
    posizione_parcheggio,
)
from custom_components.gdaauto.marche.hyundai_kia.firma import (
    FUSO_CLOUD,
    firma_rifiutata,
    fuso_orario,
    impronta,
    intestazioni_firmate,
    timbro,
)
from custom_components.gdaauto.vettura import StatoRicarica, Vettura

CAMPIONI = Path(__file__).parent / "hyundai_kia_campioni"
EV3 = json.loads((CAMPIONI / "ev3_ccs2.json").read_text())
EV6 = json.loads((CAMPIONI / "ev6_vecchio.json").read_text())

VIN_EV3 = "KNAEV3FINTO000001"
VIN_EV6 = "KNAEV6FINTO000002"
ELENCO = {
    "retCode": "S",
    "resCode": "0000",
    "resMsg": {
        "vehicles": [
            {
                "vehicleId": "id-ev3",
                "nickname": "Treno",
                "vehicleName": "EV3",
                "regDate": "2025-03-01 10:00:00.000",
                "vin": VIN_EV3,
                "type": "EV",
                "ccuCCS2ProtocolSupport": 1,
            },
            {
                "vehicleId": "id-ev6",
                "nickname": "",
                "vehicleName": "EV6",
                "regDate": "2023-01-01",
                "vin": VIN_EV6,
                "type": "EV",
                "ccuCCS2ProtocolSupport": 0,
            },
            {"vehicleId": "id-sportage", "nickname": "Benza", "vehicleName": "Sportage", "vin": "X", "type": "GN"},
        ]
    },
}


# --- il contratto e il catalogo ---------------------------------------------------------


def test_il_contratto() -> None:
    assert "hyundai_kia" in MARCHE
    assert VUOLE_PIN is True
    assert [c.nome for c in CAMPI] == ["email", "password", "pin"]
    for marchio in ("kia", "hyundai", "genesis"):
        assert PER_ID[marchio].nativo == "hyundai_kia"
        assert PER_ID[marchio].preimpostati == {"marchio": marchio}
        assert marchio in marchi.MARCHI


# --- le firme ---------------------------------------------------------------------------


def test_l_impronta_coi_vettori_veri() -> None:
    """I due vettori che il cloud ha accettato davvero, dal progetto di riferimento (MIT)."""
    kia = {
        "client-id": "com.kia.oneapp.eu",
        "client-name": "KiaEU",
        "client-version": "1.4.0",
        "client-os-code": "android",
        "client-os-version": "14",
        "X-Timestamp": "1700000000123",
    }
    attesa = "0a3ceccc83823ec0d2c5d39a15dbf722c813e6da48800a0156a7a8459db63337"
    assert impronta(kia, marchi.MARCHI["kia"].chiavi_impronta) == attesa
    # Gli spazi ASCII del corpo non contano, gli altri sì (come Java).
    assert impronta(kia, marchi.MARCHI["kia"].chiavi_impronta, " \t\r\n") == attesa
    assert impronta(kia, marchi.MARCHI["kia"].chiavi_impronta, " ") != attesa

    hyundai = {
        "client-id": "com.hyundai.oneapp.eu",
        "client-name": "HyundaiEU",
        "client-version": "1.4.0",
        "client-os-code": "android",
        "client-os-version": "14",
        "client-device-id": "dev-1",
        "locale": "PL",
        "timezone": "+02:00",
        "X-Timestamp": "1792000084084",
    }
    attesa = "df04e3dadd52288ce5b280b368af261272556b5360adbc90a817a167526e2946"
    assert impronta(hyundai, marchi.MARCHI["hyundai"].chiavi_impronta) == attesa


def test_le_intestazioni_firmate() -> None:
    adesso = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    h = intestazioni_firmate(marchi.MARCHI["hyundai"], "dev-1", "it", '{"a": 1}', adesso)
    assert h["client-name"] == "HyundaiEU"
    assert h["X-Timestamp"] == str(int(adesso.timestamp() * 1000))
    assert h["timezone"] == "+01:00"  # Berlino d'inverno
    assert h["App-Request-Id"].endswith("_dev1_AOS")
    assert h["X-Fingerprint"] == impronta(h, marchi.MARCHI["hyundai"].chiavi_impronta, '{"a":1}')
    assert fuso_orario(datetime(2026, 7, 1, tzinfo=UTC)) == "+02:00"


def test_il_timbro_cambia_ogni_secondo() -> None:
    kia = marchi.MARCHI["kia"]
    t = datetime(2026, 1, 1, tzinfo=UTC)
    grezzo = base64.b64decode(timbro(kia, t))
    chiaro = bytes(a ^ b for a, b in zip(kia.cfb, grezzo, strict=False)).decode()
    assert chiaro == f"{kia.app_id}:{int(t.timestamp())}"
    assert timbro(kia, t) != timbro(kia, t + timedelta(seconds=1))


def test_la_firma_rifiutata() -> None:
    for codice in (9035, 9036, 9037):
        assert firma_rifiutata(400, json.dumps({"code": codice}))
    assert not firma_rifiutata(400, '{"code": 9013}')
    assert not firma_rifiutata(500, '{"code": 9035}')
    assert not firma_rifiutata(400, "<html>")


def test_la_password_cifrata_si_apre_con_la_chiave_privata() -> None:
    privata = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numeri = privata.public_key().public_numbers()

    def b64(n: int) -> str:
        return base64.urlsafe_b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big")).decode().rstrip("=")

    cifrata = cifra_password("segreta", b64(numeri.n), b64(numeri.e))
    assert privata.decrypt(bytes.fromhex(cifrata), padding.PKCS1v15()) == b"segreta"


# --- l'interpretazione ------------------------------------------------------------------


def test_una_ev3_ccs2_carica_al_limite() -> None:
    s = interpreta_ccs2(EV3)
    assert s.batteria == 80
    assert s.autonomia_km == 450
    assert s.odometro_km == 1774.1
    assert s.limite_ricarica == 80
    assert s.cavo_collegato is True
    assert s.stato_ricarica == StatoRicarica.COMPLETATA  # collegata, e già al limite
    assert s.energia_residua_kwh == 61.21
    assert s.chiusa is True
    assert s.portiere_aperte is False
    assert s.quadro_acceso is False
    # La posizione del campione è stata tolta: non se ne inventa una.
    assert s.latitudine is None
    assert s.letto == datetime(2025, 4, 20, 20, 27, 23, tzinfo=UTC)
    assert limite_rapida(EV3) == 100  # in continua


def test_una_ev6_col_formato_vecchio() -> None:
    s = interpreta_vecchio(EV6)
    assert s.batteria == 76
    assert s.autonomia_km == 310
    assert s.odometro_km == 15234
    assert s.limite_ricarica == 100  # quello in alternata
    assert limite_rapida(EV6) == 80  # quello in continua
    assert s.cavo_collegato is False
    assert s.stato_ricarica == StatoRicarica.NON_IN_CARICA
    assert s.chiusa is True
    assert s.temperatura_clima_c == 22  # «10H»
    assert (s.latitudine, s.longitudine) == (52.52, 13.405)
    # L'ora vecchia è di Berlino: le 12 d'estate sono le 10 UTC.
    assert s.letto == datetime(2024, 7, 20, 10, 0, tzinfo=UTC)


def test_una_ccs2_in_carica_in_miglia() -> None:
    stato = {
        "Green": {
            "BatteryManagement": {"BatteryRemain": {"Ratio": 41}},
            "ChargingInformation": {
                "ConnectorFastening": {"State": 1},
                "Charging": {"RemainTime": 95},
                "TargetSoC": {"Standard": 90},
            },
            "Electric": {"SmartGrid": {"RealTimePower": 10.5}},
        },
        "Drivetrain": {"FuelSystem": {"DTE": {"Total": 100, "Unit": 3}}, "Odometer": 1000},
        "Chassis": {"Axle": {"Tire": {"PressureUnit": 1}, "Row1": {"Left": {"Tire": {"Pressure": 50}}}}},
        "Location": {"GeoCoord": {"Latitude": 51.5, "Longitude": -0.12}},
        "Cabin": {"HVAC": {"Row1": {"Driver": {"Temperature": {"Value": "OFF"}, "Blower": {"SpeedLevel": 0}}}}},
    }
    s = interpreta_ccs2(stato)
    assert s.stato_ricarica == StatoRicarica.IN_CARICA
    assert s.tempo_ricarica_min == 95
    assert s.potenza_ricarica_diretta_kw == 10.5
    assert s.autonomia_km == 160.9
    assert s.pneumatici_kpa == {"anteriore_sinistro": 250.0}
    assert s.clima_acceso is False and s.temperatura_clima_c is None
    assert (s.latitudine, s.longitudine) == (51.5, -0.12)


def test_le_date_e_il_parcheggio() -> None:
    assert data_cloud("20240915140000", FUSO_CLOUD) == datetime(2024, 9, 15, 12, 0, tzinfo=UTC)
    assert data_cloud("Tue, 24 Jun 2025 16:18:10 GMT", UTC) == datetime(2025, 6, 24, 16, 18, 10, tzinfo=UTC)
    assert data_cloud("", UTC) is None and data_cloud("2024", UTC) is None
    assert posizione_parcheggio({"coord": {"lat": 45.1, "lon": 7.6}}) == (45.1, 7.6)
    assert posizione_parcheggio({"gpsDetail": {"coord": {"lat": 45.1, "lon": 7.6}}}) == (45.1, 7.6)
    assert posizione_parcheggio({"coord": {"lat": 0, "lon": 0}}) is None


def test_l_anagrafica_e_i_comandi() -> None:
    voci = ELENCO["resMsg"]["vehicles"]
    ev3 = vettura_da_json(voci[0], "kia")
    assert ev3 is not None
    assert (ev3.vin, ev3.marca, ev3.modello, ev3.nome, ev3.anno) == (VIN_EV3, "Kia", "EV3", "Treno", 2025)
    assert ev3.extra == {"id": "id-ev3", "tipo": "EV", "ccs2": 1}
    ev6 = vettura_da_json(voci[1], "kia")
    assert ev6 is not None and ev6.nome == "EV6"
    assert vettura_da_json(voci[2], "kia") is None  # a benzina: non è per GdAAuto
    assert "trova" in comandi_di(ev3)
    assert "trova" not in comandi_di(ev6)
    assert {"chiudi", "clima", "ricarica", "limite_ricarica", "destinazione"} <= comandi_di(ev6)


def test_lingua_e_gradi() -> None:
    assert lingua_da("it-IT") == "it"
    assert lingua_da("ja") == "en"
    assert _gradi_vecchi(22) == "10H"
    assert _gradi_vecchi(14) == "00H"
    assert _gradi_vecchi(40) == "23H"


# --- il cloud finto ---------------------------------------------------------------------


@dataclass
class FintoCloud:
    """IDPConnect, la OneApp e il cloud delle auto, tutti su 127.0.0.1."""

    password: str = "giusta"
    pin: str = "1234"
    chiave: rsa.RSAPrivateKey = field(default_factory=lambda: rsa.generate_private_key(65537, 2048))
    #: la firma della OneApp non passa più: si va sulla strada vecchia
    firma_vecchia: bool = False
    condizioni: bool = False
    registrazione_guasta: bool = False
    accessi: int = 0
    rinnovi: int = 0
    ccs_validi: set[str] = field(default_factory=set)
    refresh_validi: set[str] = field(default_factory=set)
    dispositivi: set[str] = field(default_factory=set)
    chiamate: list[str] = field(default_factory=list)
    comandi: list[dict[str, Any]] = field(default_factory=list)
    rifiuta_comando: str | None = None
    login_guasto: bool = False

    def app(self) -> web.Application:
        app = web.Application()
        r = app.router
        r.add_get("/auth/api/v2/user/oauth2/authorize", self._autorizza)
        r.add_get("/auth/login", self._pagina)
        r.add_get("/auth/api/v1/accounts/certs", self._certificati)
        r.add_post("/auth/account/signin", self._entra)
        r.add_post("/auth/v1/token", self._token)
        r.add_post("/auth/v1/token-exchange", self._scambio)
        r.add_post("/auth/v2/token-refresh", self._rinnovo)
        r.add_post("/domain/api/v1/auth/token", self._token)
        r.add_post("/domain/api/v1/auth/token-exchange", self._scambio)
        r.add_post("/domain/api/v2/auth/token-refresh", self._rinnovo)
        r.add_post("/api/v1/spa/notifications/register", self._registra)
        r.add_put("/api/v1/user/pin", self._pin)
        r.add_route("*", "/api/{versione}/spa/vehicles{resto:.*}", self._auto)
        return app

    # --- il login ---------------------------------------------------------------------

    async def _autorizza(self, r: web.Request) -> web.Response:
        assert r.query["client_id"] == marchi.MARCHI["kia"].oneapp_client_id
        risposta = web.HTTPFound("/auth/login")
        risposta.set_cookie("SESSIONE", "S-1")
        raise risposta

    async def _pagina(self, r: web.Request) -> web.Response:
        assert r.cookies.get("SESSIONE") == "S-1"  # il cookie del rimando arriva
        return web.Response(text="<html>login</html>")

    async def _certificati(self, _r: web.Request) -> web.Response:
        numeri = self.chiave.public_key().public_numbers()

        def b64(n: int) -> str:
            return base64.urlsafe_b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big")).decode().rstrip("=")

        return web.json_response({"retValue": {"kid": "K1", "n": b64(numeri.n), "e": b64(numeri.e)}})

    async def _entra(self, r: web.Request) -> web.Response:
        assert r.cookies.get("SESSIONE") == "S-1"
        if self.login_guasto:
            return web.Response(status=503, text="<html>down</html>")
        f = await r.post()
        assert f["kid"] == "K1" and f["encryptedPassword"] == "true"
        password = self.chiave.decrypt(bytes.fromhex(str(f["password"])), padding.PKCS1v15()).decode()
        if f["username"] != "a@b.it" or password != self.password:
            return web.Response(status=302, headers={"Location": "https://x/auth?error=bad&error_description=no"})
        if self.condizioni:
            return web.Response(status=302, headers={"Location": "https://x/web/v1/user/authorization?x=1"})
        self.accessi += 1
        return web.Response(status=302, headers={"Location": f"{f['redirect_uri']}?code=CODICE-{self.accessi}"})

    def _firma_ok(self, r: web.Request) -> web.Response | None:
        if r.path.startswith("/domain/"):
            return None
        if self.firma_vecchia:
            return web.json_response({"code": 9036, "message": "bad fingerprint"}, status=400)
        h = dict(r.headers)
        attesa = impronta(h, marchi.MARCHI["kia"].chiavi_impronta, r["corpo"])
        if h.get("X-Fingerprint") != attesa:
            return web.json_response({"code": 9036, "message": "bad fingerprint"}, status=400)
        return None

    def _rilascia(self, n: int) -> dict[str, str]:
        self.refresh_validi.add(f"RT-{n}")
        return {
            "accessToken": f"CCI-{n}",
            "refreshToken": f"RT-{n}",
            "nonCcsToken": f"NCT-{n}",
            "exchangeableAccessToken": f"XAT-{n}",
            "idToken": f"ID-{n}",
            "expiresIn": "3599",
        }

    async def _token(self, r: web.Request) -> web.Response:
        r["corpo"] = await r.text()
        if (errore := self._firma_ok(r)) is not None:
            return errore
        if not r.query.get("code", "").startswith("CODICE-"):
            return web.json_response({"code": 9013, "message": "bad code"}, status=400)
        return web.json_response(self._rilascia(self.accessi))

    async def _scambio(self, r: web.Request) -> web.Response:
        r["corpo"] = await r.text()
        if (errore := self._firma_ok(r)) is not None:
            return errore
        assert r.query["serviceType"] == "CCS"
        cci = r.headers["authorization"].removeprefix("Bearer ")
        assert r.headers["Authentication"].startswith("NCT-")
        token = f"CCS-{cci}"
        self.ccs_validi.add(token)
        return web.json_response({"accessToken": token, "expiresTime": 86400})

    async def _rinnovo(self, r: web.Request) -> web.Response:
        r["corpo"] = await r.text()
        if (errore := self._firma_ok(r)) is not None:
            return errore
        corpo = json.loads(r["corpo"])
        if corpo["refreshToken"] not in self.refresh_validi:
            return web.json_response({"code": 9010, "message": "invalid refresh"}, status=401)
        self.rinnovi += 1
        return web.json_response(self._rilascia(100 + self.rinnovi))

    # --- il cloud delle auto ------------------------------------------------------------

    async def _registra(self, r: web.Request) -> web.Response:
        assert r.headers["ccsp-service-id"] == marchi.MARCHI["kia"].service_id
        assert r.headers["Stamp"]
        if self.registrazione_guasta:
            return web.Response(status=503)
        dispositivo = f"DEV-{len(self.dispositivi) + 1}"
        self.dispositivi.add(dispositivo)
        return web.json_response({"retCode": "S", "resCode": "0000", "resMsg": {"deviceId": dispositivo}})

    def _autorizzato(self, r: web.Request) -> bool:
        return r.headers.get("Authorization", "").removeprefix("Bearer ") in self.ccs_validi

    async def _pin(self, r: web.Request) -> web.Response:
        if not self._autorizzato(r):
            return web.json_response({"error": "Key not authorized: Token is expired"}, status=401)
        corpo = await r.json()
        if corpo["pin"] != self.pin:
            return web.json_response({"errCode": "4003", "errMsg": "PIN errato"}, status=400)
        return web.json_response({"controlToken": "CT-1", "expiresTime": 600})

    async def _auto(self, r: web.Request) -> web.Response:
        resto = r.match_info["resto"]
        self.chiamate.append(f"{r.method} {r.match_info['versione']}{resto}")
        controllo = r.headers.get("Authorization") == "Bearer CT-1"
        if not controllo and not self._autorizzato(r):
            return web.json_response({"retCode": "F", "resCode": "7501", "resMsg": "token expired"}, status=401)
        if r.headers.get("ccsp-device-id") not in self.dispositivi and not self.registrazione_guasta:
            return web.json_response({"retCode": "F", "resCode": "4002", "resMsg": "invalid deviceId"}, status=400)

        def ok(res: Any = None) -> web.Response:
            return web.json_response({"retCode": "S", "resCode": "0000", "resMsg": res, "msgId": "M-1"})

        if r.method == "GET":
            if resto == "":
                return ok(ELENCO["resMsg"])
            if resto == "/id-ev3/ccs2/carstatus/latest":
                assert r.headers["Ccuccs2protocolsupport"] == "1"
                return ok({"state": {"Vehicle": EV3}})
            if resto == "/id-ev6/status/latest":
                assert r.headers["Ccuccs2protocolsupport"] == "0"
                return ok({"vehicleStatusInfo": EV6})
            if resto == "/id-ev3/location/park":
                return ok({"coord": {"lat": 45.07, "lon": 7.68}, "time": "20250420200000"})
            if resto == "/id-ev6/location/park":
                return web.json_response({"retCode": "F", "resCode": "5921", "resMsg": "No Data Found"})
        if r.method == "POST":
            corpo = await r.json()
            if self.rifiuta_comando:
                return web.json_response({"retCode": "F", "resCode": self.rifiuta_comando, "resMsg": "no"})
            self.comandi.append({"percorso": f"{r.match_info['versione']}{resto}", "controllo": controllo, **corpo})
            return ok()
        return web.json_response({"retCode": "F", "resCode": "9999", "resMsg": f"nessuna strada per {resto}"})


@pytest.fixture
async def cloud(socket_enabled: None) -> AsyncGenerator[FintoCloud]:
    finto = FintoCloud()

    runner = web.AppRunner(finto.app())
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    url = f"http://127.0.0.1:{runner.addresses[0][1]}"
    kia = marchi.MARCHI["kia"]
    finta = replace(kia, api=url, login=url, cci=url, oneapp_redirect=f"{url}/redirect")
    with patch.dict(marchi.MARCHI, {"kia": finta}):
        yield finto
    await runner.cleanup()


def _cliente(
    s: aiohttp.ClientSession,
    salvati: list[dict[str, Any]] | None = None,
    *,
    password: str = "giusta",
    sessione: dict[str, Any] | None = None,
    pin: str | None = "1234",
) -> ClienteHyundaiKia:
    return ClienteHyundaiKia(
        s,
        email="a@b.it",
        password=password,
        marchio="kia",
        lingua="it",
        pin=pin,
        sessione=sessione,
        salva=salvati.append if salvati is not None else None,
    )


async def test_dal_login_allo_stato(cloud: FintoCloud) -> None:
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, salvati)
        await c.accedi()
        assert c.sessione["device_id"] == "DEV-1"
        assert c.sessione["access_token"] == "Bearer CCS-CCI-1"
        assert c.sessione["cci"]["refreshToken"] == "RT-1"
        assert salvati[-1] == c.sessione
        vetture = await c.vetture()
        assert [v.vin for v in vetture] == [VIN_EV3, VIN_EV6]
        ev3 = await c.stato(vetture[0])
        assert ev3.batteria == 80
        # La posizione del parcheggio vale più di quella (tolta) dentro lo stato.
        assert (ev3.latitudine, ev3.longitudine) == (45.07, 7.68)
        ev6 = await c.stato(vetture[1])
        assert ev6.batteria == 76
        assert (ev6.latitudine, ev6.longitudine) == (52.52, 13.405)  # niente parcheggio: resta quella dello stato
    assert cloud.accessi == 1
    assert cloud.comandi == []


async def test_il_token_salvato_non_rifa_il_login(cloud: FintoCloud) -> None:
    async with aiohttp.ClientSession() as s:
        c = _cliente(s)
        await c.accedi()
        sessione = dict(c.sessione)
        c2 = _cliente(s, sessione=sessione, password="")
        assert len(await c2.vetture()) == 2
    assert cloud.accessi == 1 and cloud.rinnovi == 0


async def test_il_token_scaduto_si_rinnova_senza_password(cloud: FintoCloud) -> None:
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        c = _cliente(s)
        await c.accedi()
        scaduta = {**c.sessione, "scade": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()}
        c2 = _cliente(s, salvati, sessione=scaduta, password="")
        await c2.vetture()
        assert c2.sessione["cci"]["refreshToken"] == "RT-101"
        assert c2.sessione["access_token"] == "Bearer CCS-CCI-101"
        assert salvati[-1]["cci"]["refreshToken"] == "RT-101"
    assert cloud.accessi == 1 and cloud.rinnovi == 1


async def test_il_cloud_che_dimentica_il_token_si_rinnova(cloud: FintoCloud) -> None:
    async with aiohttp.ClientSession() as s:
        c = _cliente(s)
        await c.accedi()
        cloud.ccs_validi.clear()  # il cloud non accetta più il token, anche se non è scaduto
        assert len(await c.vetture()) == 2
    assert cloud.rinnovi == 1


async def test_il_rinnovo_rifiutato_rientra_con_la_password(cloud: FintoCloud) -> None:
    async with aiohttp.ClientSession() as s:
        c = _cliente(s)
        await c.accedi()
        cloud.refresh_validi.clear()
        c.sessione["scade"] = ""
        await c.vetture()
    assert cloud.accessi == 2


async def test_la_strada_vecchia_se_la_firma_non_passa(cloud: FintoCloud) -> None:
    cloud.firma_vecchia = True
    async with aiohttp.ClientSession() as s:
        c = _cliente(s)
        await c.accedi()
        assert c.sessione["access_token"] == "Bearer CCS-CCI-1"


async def test_la_password_sbagliata_e_le_condizioni(cloud: FintoCloud) -> None:
    async with aiohttp.ClientSession() as s:
        with pytest.raises(ErroreCredenziali, match="rifiuta"):
            await _cliente(s, password="sbagliata").accedi()
        cloud.condizioni = True
        with pytest.raises(ErroreCredenziali, match="condizioni"):
            await _cliente(s).accedi()


async def test_senza_registrazione_si_entra_lo_stesso(cloud: FintoCloud) -> None:
    cloud.registrazione_guasta = True
    async with aiohttp.ClientSession() as s:
        c = _cliente(s)
        await c.accedi()
        assert len(c.sessione["device_id"]) == 36  # un uuid a caso


async def test_il_dispositivo_dimenticato_si_registra_di_nuovo(cloud: FintoCloud) -> None:
    async with aiohttp.ClientSession() as s:
        c = _cliente(s)
        await c.accedi()
        cloud.dispositivi.clear()
        assert len(await c.vetture()) == 2
        assert c.sessione["device_id"] == "DEV-1"  # il primo di nuovo: l'elenco era vuoto


async def test_i_guasti_non_chiedono_di_rientrare(cloud: FintoCloud) -> None:
    async with aiohttp.ClientSession() as s:
        cloud.login_guasto = True
        with pytest.raises(ErroreRete, match="guasto"):
            await _cliente(s).accedi()
        spento = replace(marchi.MARCHI["kia"], api="http://127.0.0.1:9", login="http://127.0.0.1:9")
        with patch.dict(marchi.MARCHI, {"kia": spento}), pytest.raises(ErroreRete, match="non risponde"):
            await _cliente(s).accedi()


async def test_i_comandi_ccs2_col_pin(cloud: FintoCloud) -> None:
    async with aiohttp.ClientSession() as s:
        c = _cliente(s)
        ev3, _ev6 = await c.vetture()
        await c.stato(ev3)
        await c.chiudi(ev3, None)
        await c.apri(ev3, "1234")
        await c.trova(ev3, None)
        await c.clima(ev3, True, None, 21)
        await c.clima(ev3, False, None)
        await c.ricarica(ev3, True, None)
        await c.limite_ricarica(ev3, 87, None)
        await c.destinazione(ev3, "Casa", 45.0, 7.6, "Via Roma 1")
    percorsi = [(x["percorso"], x["controllo"]) for x in cloud.comandi]
    assert percorsi == [
        ("v2/id-ev3/ccs2/control/door", True),
        ("v2/id-ev3/ccs2/control/door", True),
        ("v2/id-ev3/ccs2/control/hornlight", True),
        ("v2/id-ev3/ccs2/control/temperature", True),
        ("v2/id-ev3/ccs2/control/temperature", True),
        ("v2/id-ev3/ccs2/control/charge", True),
        ("v1/id-ev3/charge/target", False),
        ("v2/id-ev3/location/routes", True),
    ]
    assert [cloud.comandi[0]["command"], cloud.comandi[1]["command"]] == ["close", "open"]
    clima = cloud.comandi[3]
    assert (clima["command"], clima["hvacTemp"], clima["drvSeatLoc"]) == ("start", 21, "L")
    assert cloud.comandi[4] == {"percorso": "v2/id-ev3/ccs2/control/temperature", "controllo": True, "command": "stop"}
    # 87 % diventa 90: il cloud vuole le decine. Quello in continua resta com'era.
    assert cloud.comandi[6]["targetSOClist"] == [
        {"plugType": 0, "targetSOClevel": 100},
        {"plugType": 1, "targetSOClevel": 90},
    ]
    punto = cloud.comandi[7]["poiInfoList"][0]
    assert (punto["name"], punto["coord"]["lat"], punto["addr"]) == ("Casa", 45.0, "Via Roma 1")


async def test_i_comandi_delle_auto_vecchie_senza_pin(cloud: FintoCloud) -> None:
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, pin=None)
        _ev3, ev6 = await c.vetture()
        await c.chiudi(ev6, None)
        await c.clima(ev6, True, None, 22)
        await c.ricarica(ev6, False, None)
        with pytest.raises(ErroreComando, match="clacson"):
            await c.trova(ev6, None)
    assert [x["percorso"] for x in cloud.comandi] == [
        "v1/id-ev6/control/door",
        "v1/id-ev6/control/temperature",
        "v1/id-ev6/control/charge",
    ]
    assert cloud.comandi[0]["action"] == "close" and cloud.comandi[0]["deviceId"] == "DEV-1"
    assert cloud.comandi[1]["tempCode"] == "10H"
    assert cloud.comandi[2]["action"] == "stop"


async def test_il_pin_mancante_o_sbagliato(cloud: FintoCloud) -> None:
    async with aiohttp.ClientSession() as s:
        ev3 = (await _cliente(s).vetture())[0]
        with pytest.raises(ErrorePin, match="opzioni"):
            await _cliente(s, pin=None).chiudi(ev3, None)
        with pytest.raises(ErrorePin, match="non accetta"):
            await _cliente(s, pin="0000").chiudi(ev3, None)
    assert cloud.comandi == []


async def test_un_comando_rifiutato_dice_perche(cloud: FintoCloud) -> None:
    cloud.rifiuta_comando = "5031"
    async with aiohttp.ClientSession() as s:
        c = _cliente(s)
        ev3 = (await c.vetture())[0]
        with pytest.raises(ErroreComando, match="non sono disponibili"):
            await c.ricarica(ev3, True, None)


async def test_la_guida_a_destra() -> None:
    async with aiohttp.ClientSession() as s:
        c = _cliente(s)
    v = Vettura(vin="UK", marca="Kia", modello="EV3", extra={"id": "x", "ccs2": 1})
    c._grezzi["UK"] = {"Drivetrain": {"FuelSystem": {"DTE": {"Unit": 3}}}}
    assert c._guida(v) == "R"
    c._grezzi["UK"] = EV3
    assert c._guida(v) == "L"


# --- dentro Home Assistant --------------------------------------------------------------


async def test_configurare_una_kia(hass: HomeAssistant, cloud: FintoCloud) -> None:
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "kia"})
    assert r["step_id"] == "credenziali"
    # Il marchio arriva dal catalogo: non si chiede.
    assert {str(k) for k in r["data_schema"].schema} == {"email", "password", "pin"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"email": "a@b.it", "password": "sbagliata"})
    assert r["errors"] == {"base": "credenziali"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"email": "a@b.it", "password": "giusta"})
    await hass.async_block_till_done()
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["title"] == "Kia · a@b.it"
    assert r["result"].unique_id == "hyundai_kia_kia_a@b.it"
    assert r["data"][CONF_MARCA] == "hyundai_kia"
    assert r["data"][CONF_MARCHIO] == "kia"
    assert r["data"]["marchio"] == "kia"
    assert r["data"]["token"]["access_token"].startswith("Bearer CCS-")
    assert hass.states.get("sensor.treno_batteria").state == "80"
    assert hass.states.get("lock.treno_serratura").state == "locked"
    assert hass.states.get("sensor.ev6_batteria").state == "76"
    voce = r["result"]
    assert voce.runtime_data.auto[0]._normale.total_seconds() == 600
    await hass.config_entries.async_unload(voce.entry_id)
    await hass.async_block_till_done()


async def test_il_token_che_cambia_non_ricarica_l_integrazione(hass: HomeAssistant, cloud: FintoCloud) -> None:
    voce = MockConfigEntry(
        domain=DOMAIN,
        title="Kia · a@b.it",
        unique_id="hyundai_kia_kia_a@b.it",
        data={
            CONF_FONTE: FONTE_CLOUD,
            CONF_MARCA: "hyundai_kia",
            CONF_MARCHIO: "kia",
            "marchio": "kia",
            "email": "a@b.it",
            "password": "giusta",
            "token": {"device_id": "DEV-X", "access_token": "Bearer VECCHIO", "scade": ""},
        },
        options={"pin": "1234"},
    )
    voce.add_to_hass(hass)
    assert await hass.config_entries.async_setup(voce.entry_id)
    await hass.async_block_till_done()
    dati_prima = voce.runtime_data
    # Il token salvato non valeva: si è rientrati, e il nuovo è nella voce.
    assert voce.data["token"]["access_token"] == "Bearer CCS-CCI-1"
    assert hass.states.get("sensor.treno_batteria").state == "80"
    assert voce.runtime_data is dati_prima
    # La diagnostica toglie anche la posizione dentro i dati grezzi («lat», «Latitude»…).
    from custom_components.gdaauto.diagnostics import async_get_config_entry_diagnostics

    testo = str(await async_get_config_entry_diagnostics(hass, voce))
    for segreto in ("45.07", "52.52", "13.405", VIN_EV3, "a@b.it", "giusta", "CCS-", "RT-", "1234"):
        assert segreto not in testo, segreto
    await hass.config_entries.async_unload(voce.entry_id)
    await hass.async_block_till_done()
