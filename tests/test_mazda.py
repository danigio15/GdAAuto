"""La Mazda 6e: lo stato, il cloud finto (login, codice per email, token, comandi firmati) e Home Assistant."""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.gdaauto.const import DOMAIN
from custom_components.gdaauto.marche import mazda
from custom_components.gdaauto.marche.catalogo import PER_ID, Marchio
from custom_components.gdaauto.marche.errori import (
    CodiceInviato,
    CodiceSbagliato,
    ErroreCertificato,
    ErroreComando,
    ErroreCredenziali,
    ErrorePin,
    ErroreRete,
)
from custom_components.gdaauto.marche.mazda import cliente as modulo_cliente, cripto
from custom_components.gdaauto.marche.mazda.cliente import ClienteMazda, comandi_di, entra
from custom_components.gdaauto.marche.mazda.dati import interpreta, vetture_da
from custom_components.gdaauto.vettura import Marcia, StatoRicarica, Vettura

from . import mazda_campioni as c

#: La chiave del cloud finto: la pubblica va al cliente, la privata decifra le credenziali.
_SERVER = rsa.generate_private_key(public_exponent=65537, key_size=2048)
CHIAVE_SERVER = base64.b64encode(
    _SERVER.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
).decode()


def _decifra(testo: str) -> str:
    grezzo = base64.b64decode(testo)
    return b"".join(
        _SERVER.decrypt(grezzo[i : i + 256], padding.PKCS1v15()) for i in range(0, len(grezzo), 256)
    ).decode()


# --- lo stato ---------------------------------------------------------------------


def test_una_6e_in_carica() -> None:
    s = interpreta(c.STATO_IN_CARICA)
    assert (s.batteria, s.autonomia_km, s.odometro_km) == (64, 312, 8421.5)
    assert s.stato_ricarica == StatoRicarica.IN_CARICA and s.in_carica
    assert s.cavo_collegato is True and s.ricarica_rapida is False
    assert s.tempo_ricarica_min == 185 and s.limite_ricarica == 90
    assert s.temperatura_interna_c == 19.5 and s.temperatura_clima_c == 21.5
    assert s.clima_acceso is False and s.sbrinamento is False
    assert s.chiusa is True and s.portiere_aperte is False and s.bagagliaio_aperto is False
    assert s.finestrini_aperti is True
    assert s.pneumatici_kpa == {
        "anteriore_sinistro": 250,
        "anteriore_destro": 252,
        "posteriore_sinistro": 248,
        "posteriore_destro": 249,
    }
    assert (s.latitudine, s.longitudine) == (45.464211, 9.191383)
    assert s.marcia == Marcia.PARCHEGGIO and s.quadro_acceso is False and s.in_sonno is False
    assert s.velocita_kmh == 0
    assert s.letto == datetime(2025, 10, 20, 12, 0, tzinfo=UTC)


def test_una_6e_in_marcia_aperta() -> None:
    s = interpreta(c.STATO_IN_MARCIA)
    assert s.velocita_kmh == 87.5 and s.marcia == Marcia.AVANTI and s.in_marcia and s.quadro_acceso
    assert s.stato_ricarica == StatoRicarica.NON_IN_CARICA and s.cavo_collegato is False
    assert s.tempo_ricarica_min is None  # 8191 = «non si sa»
    assert s.temperatura_interna_c is None and s.temperatura_clima_c == 21
    assert s.clima_acceso is True and s.sbrinamento is True
    assert s.chiusa is False and s.portiere_aperte is True and s.bagagliaio_aperto is True
    assert s.finestrini_aperti is False
    assert s.pneumatici_kpa == {} and s.latitudine is None


def test_uno_stato_vuoto_o_rotto_non_si_rompe() -> None:
    for vuoto in (None, {}, {"vehicleStatus": None, "charge": "x", "door": {"doors": None}}):
        s = interpreta(vuoto)
        assert s.batteria is None and s.chiusa is None and s.portiere_aperte is None


def test_le_auto() -> None:
    (auto,) = vetture_da(c.AUTO)
    assert (auto.vin, auto.marca, auto.modello, auto.nome, auto.targa) == (
        c.VIN,
        "mazda",
        "Mazda 6e",
        "La mia 6e",
        "GD 123 AA",
    )
    assert auto.extra["id"] == c.ID_AUTO
    senza_nome = vetture_da([{**c.AUTO[0], "carName": None}, {"vin": "X"}, "rotto"])
    assert [a.nome for a in senza_nome] == ["Mazda 6e"]


def test_i_comandi_secondo_le_funzioni() -> None:
    auto = Vettura(vin=c.VIN, marca="mazda", modello="6e", extra={"funzioni": ["ACSW"]})
    assert comandi_di(auto) == {"chiudi", "apri", "clima"}
    auto.extra["funzioni"] = []
    assert comandi_di(auto) == {"chiudi", "apri", "trova", "clima", "limite_ricarica"}


def test_la_cifratura_e_la_firma() -> None:
    lungo = "a" * 244 + "ø" + "🔑" * 70  # più di un blocco RSA
    assert _decifra(cripto.cifra(lungo, CHIAVE_SERVER)) == lungo
    pubblica, privata = cripto.nuova_coppia()
    chiave = serialization.load_der_public_key(base64.b64decode(pubblica))
    campi = {"vehicleId": "7", "open": False, "rcToken": "rc", "seriralNo": "s", "command": "x"}
    assert cripto.testo_da_firmare(campi, frozenset({"command"})) == "open=false&rcToken=rc&seriralNo=s&vehicleId=7"
    chiave.verify(
        base64.b64decode(cripto.firma(campi, privata, frozenset({"command"}))),
        b"open=false&rcToken=rc&seriralNo=s&vehicleId=7",
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    seriale = base64.b64encode(chiave.encrypt(b"serial-1", padding.PKCS1v15())).decode()
    assert cripto.decifra_seriale(seriale, privata) == "serial-1"
    with pytest.raises(ErroreCertificato):
        cripto.carica_pubblica("rotta")


# --- il cloud finto ------------------------------------------------------------------


@dataclass
class FintaMazda:
    password: str = "giusta"
    pin: str = "123456"
    codice: str = "654321"
    #: i «telefoni» che Mazda conosce già: per gli altri vuole il codice per email
    telefoni: set[str] = field(default_factory=set)
    token: str = "tok-1"
    rinnovi: int = 0
    rinnovo_rifiutato: bool = False
    rotto: bool = False
    rifiuta_comandi: bool = False
    chiavi_comandi: dict[str, Any] = field(default_factory=dict)
    seriali: list[str] = field(default_factory=list)
    comandi: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    chiamate: list[str] = field(default_factory=list)
    attese: int = 0

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/cma-app-gw/{percorso:.*}", self._gestisci)
        return app

    async def _gestisci(self, r: web.Request) -> web.Response:
        percorso = r.match_info["percorso"]
        corpo = await r.json()
        self.chiamate.append(percorso)
        assert r.headers["appid"] == "cma" and r.headers["user-agent"].startswith("overseas/")
        if self.rotto:
            return web.Response(status=502, text="<html>Bad Gateway</html>")
        telefono = r.headers.get("deviceid", "")
        if percorso == "cma-app-auth/api/login/email-pass-in/v2":
            if _decifra(corpo["email"]).lower() != "mario@esempio.com" or _decifra(corpo["password"]) != self.password:
                return web.json_response(c.no("APP_1_1_01_002", "account or password error"))
            self.chiavi_comandi[telefono] = serialization.load_der_public_key(base64.b64decode(corpo["pubKey"]))
            nuovo = telefono not in self.telefoni
            return web.json_response(c.ok({"token": self.token, "refreshToken": "ref-1", "emailVerify": nuovo}))
        if percorso == "cma-app-user/api/send-email/device-login/send":
            assert r.headers["authorization"] == self.token and _decifra(corpo["email"]).lower() == "mario@esempio.com"
            return web.json_response(c.ok(True))
        if percorso == "cma-app-user/api/login-device/email-verify":
            if corpo["authCode"] != self.codice:
                return web.json_response(c.no("APP_1_2_00_010", "verification code error"))
            self.telefoni.add(telefono)
            return web.json_response(c.ok(True))
        if percorso == "cma-app-auth/api/auth/refresh-token":
            self.rinnovi += 1
            if self.rinnovo_rifiutato or corpo["refreshToken"] != "ref-1":
                return web.json_response(c.no("APP_1_1_02_005", "refresh token invalid"))
            self.token = f"tok-{self.rinnovi + 1}"
            return web.json_response(c.ok({"token": self.token, "refreshToken": "ref-1"}))
        # Da qui in poi serve il token buono.
        if r.headers.get("authorization") != self.token:
            return web.json_response(c.no("APP_1_1_02_004", "token expired"))
        if percorso == "cma-app-user/api/vehicle/vehicles":
            return web.json_response(c.ok([]))  # come alcuni account: l'elenco vero è all'altro indirizzo
        if percorso == "cma-app-user/api/car/vehicles":
            return web.json_response(c.ok(c.AUTO))
        if percorso == "cma-app-user/api/vehicle/function-config":
            assert corpo == {"vehicleId": c.ID_AUTO}
            return web.json_response(c.ok(c.FUNZIONI))
        if percorso == "cma-app-car-condition/api/vehicle/condition/v2":
            assert corpo["vehicleId"] == c.ID_AUTO and corpo["vechileCriteria"]["charge"] == "1"
            return web.json_response(c.ok(c.STATO_IN_CARICA))
        if percorso == "cma-app-car-control/api/security-code/check-code":
            if _decifra(corpo["safeCode"]) != self.pin:
                return web.json_response(c.no("APP_3_1_00_011", "safe code error"))
            return web.json_response(c.ok({"rcToken": "rc-1"}))
        if percorso == "cma-app-car-control/api/serial-no/get":
            seriale = f"serial-{len(self.seriali) + 1}-tipo{corpo['type']}"
            self.seriali.append(seriale)
            cifrato = self.chiavi_comandi[telefono].encrypt(seriale.encode(), padding.PKCS1v15())
            return web.json_response(c.ok(base64.encodebytes(cifrato).decode()))
        if percorso == "cma-app-car-control/api/control/control-result":
            assert corpo["vehicleId"] == str(c.ID_AUTO)
            self.attese += 1
            if self.attese % 2:
                return web.json_response(c.ok({"resultCode": -100, "errorMsg": "processing"}))
            esito = 2001 if self.rifiuta_comandi else 0
            return web.json_response(c.ok({"resultCode": esito, "errorMsg": "vehicle not responding"}))
        if percorso.startswith("cma-app-car-control/api/"):
            firma = base64.b64decode(corpo.pop("sign"))
            salta = frozenset({"command", "rcToken"}) if "command" in corpo else frozenset()
            self.chiavi_comandi[telefono].verify(
                firma, cripto.testo_da_firmare(corpo, salta).encode(), padding.PKCS1v15(), hashes.SHA256()
            )
            assert corpo["seriralNo"] == self.seriali[-1]
            self.comandi.append((percorso.removeprefix("cma-app-car-control/api/"), corpo))
            return web.json_response(c.ok({"commandId": f"cmd-{len(self.comandi)}"}))
        return web.json_response({"error": "not found"}, status=404)


@pytest.fixture
async def cloud(socket_enabled: None, monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[tuple[FintaMazda, str]]:
    monkeypatch.setattr(modulo_cliente, "PASSO_ESITO", 0)
    finta = FintaMazda()
    runner = web.AppRunner(finta.app())
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    modulo_cliente._IN_SOSPESO.clear()
    yield finta, f"http://127.0.0.1:{runner.addresses[0][1]}/cma-app-gw"
    await runner.cleanup()


async def _entra(s: aiohttp.ClientSession, base: str, **kw: Any) -> dict[str, str]:
    return await entra(
        s, "Mario@Esempio.com", kw.pop("password", "giusta"), chiave_server=CHIAVE_SERVER, base=base, **kw
    )


async def test_login_codice_per_email_auto_e_stato(cloud) -> None:
    finta, base = cloud
    async with aiohttp.ClientSession() as s:
        with pytest.raises(CodiceInviato, match="email"):
            await _entra(s, base)
        with pytest.raises(CodiceSbagliato):
            await _entra(s, base, codice="000000")
        token = await _entra(s, base, codice=" 654321 ")
        assert (token["token"], token["refresh"]) == ("tok-1", "ref-1")
        assert token["dispositivo"] in finta.telefoni and token["chiave_comandi"]
        assert finta.chiamate.count("cma-app-auth/api/login/email-pass-in/v2") == 1  # il codice conclude quel login
        # Lo stesso «telefono» la volta dopo non chiede il codice.
        di_nuovo = await _entra(s, base, dispositivo=token["dispositivo"])
        assert di_nuovo["dispositivo"] == token["dispositivo"]

        cliente = ClienteMazda(s, token, chiave_server=CHIAVE_SERVER, base_url=base)
        await cliente.accedi()
        (auto,) = await cliente.vetture()
        assert auto.vin == c.VIN and auto.extra["id"] == c.ID_AUTO
        assert set(auto.extra["funzioni"]) >= {"ACSW", "FlashHonk", "BatteryMaxSoc"}
        assert comandi_di(auto) == {"chiudi", "apri", "trova", "clima", "limite_ricarica"}
        stato = await cliente.stato(auto)
        assert stato.batteria == 64 and stato.limite_ricarica == 90


async def test_token_scaduto_password_sbagliata_e_guasti(cloud) -> None:
    finta, base = cloud
    salvati: list[dict[str, Any]] = []
    finta.telefoni.add("telefono-1")
    async with aiohttp.ClientSession() as s:
        with pytest.raises(ErroreCredenziali, match="password"):
            await _entra(s, base, password="sbagliata", dispositivo="telefono-1")
        token = await _entra(s, base, dispositivo="telefono-1")
        cliente = ClienteMazda(s, token, chiave_server=CHIAVE_SERVER, base_url=base, salva=salvati.append)
        (auto,) = await cliente.vetture()
        # Il token scade: si rinnova, si salva, e si riprova senza chiedere di rientrare.
        finta.token = "tok-scaduto-sul-server"
        finta.rinnovi = 0
        assert (await cliente.stato(auto)).batteria == 64
        assert salvati[-1]["token"] == "tok-2" and cliente.token["token"] == "tok-2"
        assert finta.rinnovi == 1
        # Il rinnovo non va più: bisogna rientrare.
        finta.token = "tok-ancora-diverso"
        finta.rinnovo_rifiutato = True
        with pytest.raises(ErroreCredenziali, match="rientrare"):
            await cliente.stato(auto)
        # Un guasto (502) non chiede di rientrare.
        finta.rotto = True
        with pytest.raises(ErroreRete):
            await cliente.stato(auto)
    async with aiohttp.ClientSession() as s:
        with pytest.raises(ErroreRete):
            await _entra(s, "http://127.0.0.1:9/cma-app-gw")


async def test_i_comandi_firmati(cloud) -> None:
    finta, base = cloud
    finta.telefoni.add("telefono-1")
    async with aiohttp.ClientSession() as s:
        token = await _entra(s, base, dispositivo="telefono-1")
        cliente = ClienteMazda(s, token, chiave_server=CHIAVE_SERVER, base_url=base)
        (auto,) = await cliente.vetture()
        await cliente.chiudi(auto, "123456")
        await cliente.trova(auto, None)
        await cliente.clima(auto, True, None, 35)
        await cliente.limite_ricarica(auto, 40, None)
        with pytest.raises(ErrorePin, match="opzioni"):
            await cliente.apri(auto, None)
        with pytest.raises(ErrorePin, match="non accetta"):
            await cliente.apri(auto, "000000")
        finta.rifiuta_comandi = True
        with pytest.raises(ErroreComando, match="2001"):
            await cliente.apri(auto, "123456")
    eseguiti = [(percorso, {k: v for k, v in corpo.items() if k != "seriralNo"}) for percorso, corpo in finta.comandi]
    assert eseguiti == [
        ("control/doors", {"open": False, "rcToken": "rc-1", "vehicleId": str(c.ID_AUTO)}),
        ("control/flashing-honking", {"type": 1, "vehicleId": str(c.ID_AUTO)}),
        ("control/air-conditioner", {"enabled": True, "targetTemp": 300, "runTime": 15, "vehicleId": str(c.ID_AUTO)}),
        (
            "charge/percentage",
            {"chargePercentageMax": 60, "command": "charge_max", "rcToken": "", "vehicleId": str(c.ID_AUTO)},
        ),
        ("control/doors", {"open": True, "rcToken": "rc-1", "vehicleId": str(c.ID_AUTO)}),
    ]
    # Il limite usa il numero di serie di tipo 2, gli altri di tipo 1.
    assert [x["seriralNo"].rsplit("-", 1)[-1] for _, x in finta.comandi] == [
        "tipo1",
        "tipo1",
        "tipo1",
        "tipo2",
        "tipo1",
    ]


# --- la chiave del cloud, scaricata --------------------------------------------------


def _file_con_la_chiave() -> bytes:
    return f"SERVER_PUBLIC_KEY = '{CHIAVE_SERVER}'\n\ndef encrypt_credential(): ...\n".encode()


def test_la_chiave_nel_file() -> None:
    contenuto = _file_con_la_chiave()
    with pytest.raises(ErroreCertificato, match="atteso"):
        mazda.chiave_dal_file(contenuto)
    with patch.object(mazda, "SHA256_FILE", hashlib.sha256(contenuto).hexdigest()):
        assert mazda.chiave_dal_file(contenuto) == CHIAVE_SERVER


async def test_la_chiave_si_scarica_da_sola(hass: HomeAssistant, aioclient_mock, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(hass.config, "config_dir", str(tmp_path))
    contenuto = _file_con_la_chiave()
    aioclient_mock.get(mazda.URL_CHIAVE, content=contenuto)
    with pytest.raises(ErroreCertificato):
        await mazda.chiave_cloud(hass)
    assert not (tmp_path / "gdaauto" / mazda.NOME_FILE).exists()

    monkeypatch.setattr(mazda, "SHA256_FILE", hashlib.sha256(contenuto).hexdigest())
    assert await mazda.chiave_cloud(hass) == CHIAVE_SERVER
    assert (tmp_path / "gdaauto" / mazda.NOME_FILE).read_text().strip() == CHIAVE_SERVER
    # La volta dopo c'è già: non si riscarica.
    assert await mazda.chiave_cloud(hass) == CHIAVE_SERVER
    assert aioclient_mock.call_count == 2


# --- dentro Home Assistant ------------------------------------------------------------

SEI_E = Vettura(vin=c.VIN, marca="mazda", modello="Mazda 6e", nome="La mia 6e", extra={"id": c.ID_AUTO})
MARCHIO = Marchio("mazda", "Mazda", "mazda", {}, "nessuna", "https://example.invalid")


async def test_configurare_una_6e(hass: HomeAssistant) -> None:
    # Il catalogo, per ora, non ha una marca nativa per Mazda: qui la si punta a questa.
    with patch.dict(PER_ID, {"mazda": MARCHIO}):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "mazda"})
        assert r["step_id"] == "credenziali"
        assert {str(k) for k in r["data_schema"].schema} == {"email", "password", "codice", "pin"}
        token = {"token": "T", "refresh": "R", "dispositivo": "D", "chiave_comandi": "K"}
        entra_finto = AsyncMock(side_effect=[CodiceInviato("email"), token])
        with (
            patch.object(mazda, "chiave_cloud", AsyncMock(return_value=CHIAVE_SERVER)),
            patch.object(mazda, "entra", entra_finto),
            patch.object(mazda.ClienteMazda, "vetture", AsyncMock(return_value=[SEI_E])),
            patch.object(mazda.ClienteMazda, "stato", AsyncMock(return_value=interpreta(c.STATO_IN_CARICA))),
        ):
            dati = {"email": "Mario@Esempio.com", "password": "segreta", "pin": "123456"}
            r = await hass.config_entries.flow.async_configure(r["flow_id"], dati)
            assert r["errors"] == {"base": "codice_inviato"}
            r = await hass.config_entries.flow.async_configure(r["flow_id"], {**dati, "codice": "654321"})
            await hass.async_block_till_done()
            assert r["type"] is FlowResultType.CREATE_ENTRY
            assert r["title"] == "Mazda · Mario@Esempio.com"
            assert r["result"].unique_id == "mazda_mario@esempio.com"
            assert r["data"]["marca"] == "mazda" and r["data"]["token"] == token
            assert "segreta" not in json.dumps(r["data"]) and "codice" not in r["data"]
            assert r["result"].options["pin"] == "123456"
            assert entra_finto.await_args.kwargs["codice"] == "654321"
            assert hass.states.get("sensor.la_mia_6e_batteria").state == "64"
            assert hass.states.get("binary_sensor.la_mia_6e_cavo_collegato").state == "on"
            voce = r["result"]
            # Il token nuovo aggiorna la voce senza ricaricarla.
            prima = voce.runtime_data
            voce.runtime_data.conto.cliente._salva({**token, "token": "T2"})
            await hass.async_block_till_done()
            assert voce.data["token"]["token"] == "T2"
            assert voce.runtime_data is prima
