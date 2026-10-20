"""Zeekr: lo stato, le firme, il cliente contro un cloud finto, e la configurazione in Home Assistant."""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator
import copy
from datetime import UTC, datetime, timedelta
from email.utils import formatdate
import hashlib
import hmac
import json
from typing import Any
from unittest.mock import patch

import aiohttp
from aiohttp import web
from cryptography.hazmat.primitives import padding, serialization
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.gdaauto.const import CONF_MARCA, DOMAIN
from custom_components.gdaauto.marche import zeekr
from custom_components.gdaauto.marche.catalogo import PER_ID, Marchio
from custom_components.gdaauto.marche.errori import (
    ErroreApi,
    ErroreCertificato,
    ErroreComando,
    ErroreCredenziali,
    ErroreRete,
)
from custom_components.gdaauto.marche.zeekr import cliente as modulo_cliente
from custom_components.gdaauto.marche.zeekr.cliente import ClienteZeekr
from custom_components.gdaauto.marche.zeekr.dati import (
    aggiungi_ricarica,
    interpreta,
    limite_da_soc,
    modello_di,
    piano_di_ricarica,
    sentinella_da,
    ultimi_7_giorni,
    viaggi_da,
)
from custom_components.gdaauto.marche.zeekr.firma import chiavi_da, cifra_vin, firma_centro_utenti
from custom_components.gdaauto.vettura import StatoRicarica, Vettura

from .zeekr_campioni import AUTO, LIMITE, PIANO, QRVS, STATO, STATO_COMANDI, VIN, viaggio

EMAIL = "Auto@Example.com"
PASSWORD = "segreta-123"
ACCESS_KEY = "ak-finta"
SECRET_KEY = "sk-finta"
PROD_SECRET = "prod-finto"
VIN_KEY = "0123456789abcdef"
VIN_IV = "fedcba9876543210"

_PRIVATA = rsa.generate_private_key(public_exponent=65537, key_size=2048)
CHIAVE_PUBBLICA = base64.b64encode(
    _PRIVATA.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
).decode()
#: le chiavi dell'app, come le scrive zeekr_key_extractor (finte)
SEGRETI = json.dumps(
    {
        "hmac_access_key": ACCESS_KEY,
        "hmac_secret_key": SECRET_KEY,
        "password_public_key": CHIAVE_PUBBLICA,
        "prod_secret": PROD_SECRET,
        "xchanger_sign_secret": "",
        "vin_key": VIN_KEY,
        "vin_iv": VIN_IV,
    }
)
CHIAVI = chiavi_da(SEGRETI)


def _ev(stato: dict[str, Any]) -> dict[str, Any]:
    return stato["additionalVehicleStatus"]["electricVehicleStatus"]


def _stato_in_carica(**ev: str) -> dict[str, Any]:
    stato = copy.deepcopy(STATO)
    _ev(stato).update({"chargerState": "2", "timeToFullyCharged": "95", **ev})
    return stato


# --- lo stato ---------------------------------------------------------------------------


def test_lo_stato_di_una_7x_parcheggiata() -> None:
    s = interpreta(STATO)
    assert s.batteria == 76  # chargeLevel: stateOfCharge è vuoto
    assert s.autonomia_km == 384
    assert s.odometro_km == 12873
    assert s.stato_ricarica == StatoRicarica.COMPLETATA
    assert s.cavo_collegato is True  # statusOfChargerConnection 3
    assert s.potenza_ricarica_diretta_kw == 0.0
    assert s.tempo_ricarica_min is None  # 2047: nessuna ricarica
    assert s.consumo_medio_kwh_100km == 18.7
    assert s.batteria_in_riscaldamento is False
    assert s.chiusa is True  # centralLockingStatus 1
    assert s.portiere_aperte is False
    assert s.bagagliaio_aperto is False
    assert s.freno_stazionamento is True
    assert s.finestrini_aperti is False
    assert s.temperatura_interna_c == 18.5
    assert s.temperatura_esterna_c == 12.0
    assert s.clima_acceso is False
    assert s.sbrinamento is False
    assert s.quadro_acceso is False
    assert s.velocita_kmh == 0.0
    assert s.in_sonno is False
    assert s.pneumatici_kpa == {
        "anteriore_sinistro": 262.0,
        "anteriore_destro": 265.0,
        "posteriore_sinistro": 268.0,
        "posteriore_destro": 268.0,
    }
    assert s.pneumatici_allarme is False
    assert (s.latitudine, s.longitudine) == (45.464211, 9.191383)
    assert s.posizione_con_segno is True
    assert s.letto == datetime(2025, 10, 20, 9, 0, tzinfo=UTC)


def test_una_ricarica_trifase_e_una_monofase() -> None:
    s = interpreta(_stato_in_carica(chargeIAct="16.0", chargeUAct="400.0"))
    assert s.stato_ricarica == StatoRicarica.IN_CARICA
    assert s.cavo_collegato is True
    assert s.ricarica_rapida is False
    assert s.potenza_ricarica_diretta_kw == 11.09  # √3 × 400 V × 16 A
    assert s.tempo_ricarica_min == 95
    s = interpreta(_stato_in_carica(chargeIAct="9.4", chargeUAct="222.0"))
    assert s.potenza_ricarica_diretta_kw == 2.09


def test_la_ricarica_dc_e_quella_vista_solo_dalla_potenza() -> None:
    s = interpreta(_stato_in_carica(chargerState="15", dcChargePileIAct="250", dcChargePileUAct="620"))
    assert s.ricarica_rapida is True
    assert s.potenza_ricarica_diretta_kw == 155.0
    # isCharging non conta, chargerState sconosciuto: la potenza dice che carica.
    s = interpreta(_stato_in_carica(chargerState="1", chargeIAct="10", chargeUAct="230"))
    assert s.stato_ricarica == StatoRicarica.IN_CARICA
    assert s.potenza_ricarica_diretta_kw == 2.3


def test_porte_aperte_finestrino_a_meta_e_auto_che_dorme() -> None:
    stato = copy.deepcopy(STATO)
    sicurezza = stato["additionalVehicleStatus"]["drivingSafetyStatus"]
    sicurezza.update(centralLockingStatus="0", doorOpenStatusPassengerRear="1", trunkOpenStatus="1")
    stato["additionalVehicleStatus"]["climateStatus"]["winPosDriver"] = "30"
    stato["additionalVehicleStatus"]["maintenanceStatus"]["tyrePreWarningDriver"] = "1"
    stato["basicVehicleStatus"]["usageMode"] = "0"
    stato["basicVehicleStatus"]["engineStatus"] = "engine-running"
    stato["basicVehicleStatus"]["speed"] = "57"
    s = interpreta(stato)
    assert s.chiusa is False
    assert s.portiere_aperte is True
    assert s.bagagliaio_aperto is True
    assert s.finestrini_aperti is True
    assert s.pneumatici_allarme is True
    assert s.in_sonno is True
    assert s.quadro_acceso is True
    assert s.velocita_kmh == 57


def test_un_cloud_che_manda_poco_o_null() -> None:
    s = interpreta({"additionalVehicleStatus": {"electricVehicleStatus": None, "climateStatus": {}}})
    assert s.batteria is None
    assert s.chiusa is None
    assert s.pneumatici_kpa == {}
    assert interpreta(None).batteria is None  # type: ignore[arg-type]


def test_le_letture_a_parte() -> None:
    assert limite_da_soc(LIMITE["soc"]) == 80
    assert limite_da_soc("0") is None
    assert piano_di_ricarica(PIANO) == {
        "ricarica_programmata": True,
        "ricarica_ora_inizio": "01:15",
        "ricarica_ora_fine": "06:45",
    }
    assert piano_di_ricarica({}) == {}
    assert sentinella_da(STATO_COMANDI) is True
    assert sentinella_da({"vstdModeState": "0"}) is False
    assert sentinella_da({}) is None
    s = interpreta(_stato_in_carica())
    aggiungi_ricarica(s, QRVS)
    assert s.potenza_ricarica_diretta_kw == 2.1
    ferma = interpreta(STATO)
    aggiungi_ricarica(ferma, {"chargerState": "4", "chargePower": "7.0"})
    assert ferma.potenza_ricarica_diretta_kw == 0.0  # da ferma la potenza vecchia non conta


def test_diario_e_modelli() -> None:
    ora = datetime(2025, 10, 20, 12, tzinfo=UTC)
    ms = int((ora - timedelta(days=1)).timestamp() * 1000)
    vecchio = int((ora - timedelta(days=10)).timestamp() * 1000)
    viaggi = viaggi_da({"total": 3, "data": [viaggio(ms, 20, 18.0), viaggio(ms, 10, 20.0), viaggio(vecchio, 99, 1)]})
    assert ultimi_7_giorni(viaggi, ora) == {"km_7_giorni": 30.0, "energia_7_giorni_kwh": 5.6}
    assert viaggi_da({"data": None}) == []
    assert modello_di(AUTO) == "7X"
    assert modello_di({"appModelCode": "CC1E"}) == "7GT"
    assert modello_di({"modelName": "ZEEKR 001"}) == "001"
    assert modello_di({"seriesName": "Zeekr X"}) == "X"
    assert modello_di({}) == "Zeekr"


def test_le_chiavi_dell_app() -> None:
    assert CHIAVI.access_key == ACCESS_KEY and CHIAVI.vin_iv == VIN_IV
    righe = "\n".join(
        [
            "# secrets.properties",
            f"HMAC_ACCESS_KEY={ACCESS_KEY}",
            f"HMAC_SECRET_KEY={SECRET_KEY}",
            f"PASSWORD_PUBLIC_KEY={CHIAVE_PUBBLICA}",
            f"PROD_SECRET={PROD_SECRET}",
            f"VIN_KEY={VIN_KEY}",
            f"VIN_IV={VIN_IV}",
        ]
    )
    assert chiavi_da(righe) == CHIAVI
    with pytest.raises(ErroreCertificato, match="prod_secret"):
        chiavi_da(json.dumps({**json.loads(SEGRETI), "prod_secret": ""}))
    with pytest.raises(ErroreCertificato, match="16"):
        chiavi_da(json.dumps({**json.loads(SEGRETI), "vin_iv": "corta"}))
    with pytest.raises(ErroreCertificato):
        chiavi_da(json.dumps({**json.loads(SEGRETI), "password_public_key": "non-una-chiave"}))


def test_la_firma_del_centro_utenti_e_il_vin() -> None:
    data = "Mon, 20 Oct 2025 09:00:00 GMT"
    h = firma_centro_utenti("GET", "https://x/zeekr-cuc-idaas/user/tspCode?b=2&A=1", None, "ak", "sk", data)
    atteso = base64.b64encode(
        hmac.new(b"sk", b"GET\n/zeekr-cuc-idaas/user/tspCode\nA=1&b=2\nak\n" + data.encode() + b"\n", "sha256").digest()
    ).decode()
    assert h["X-HMAC-SIGNATURE"] == atteso
    assert h["X-HMAC-DIGEST"] == base64.b64encode(hmac.new(b"sk", b"", "sha256").digest()).decode()
    assert _decifra_vin(cifra_vin(VIN, VIN_KEY, VIN_IV)) == VIN


# --- il cloud finto -------------------------------------------------------------------------


def _decifra_vin(cifrato: str) -> str:
    decifratore = Cipher(algorithms.AES(VIN_KEY.encode()), modes.CBC(VIN_IV.encode())).decryptor()
    dati = decifratore.update(base64.b64decode(cifrato)) + decifratore.finalize()
    togli = padding.PKCS7(128).unpadder()
    return (togli.update(dati) + togli.finalize()).decode()


_FIRMATE = (
    "x-app-id",
    "content-type",
    "x-api-signature-nonce",
    "x-timestamp",
    "x-api-signature-version",
    "x-project-id",
    "authorization",
    "accept-language",
    "x-vin",
    "x-device-id",
    "x-platform",
)


def _firma_tsp_attesa(r: web.Request, corpo: str) -> str:
    """La X-SIGNATURE ricostruita dal lato del server, come la descrive zeekr_ev_api."""
    righe = sorted((k.lower(), v) for k, v in r.headers.items() if k.lower() in _FIRMATE and v)
    testo = "".join(f"{k}:{v}\n" for k, v in righe)
    if r.query_string:
        testo += "&".join(sorted(r.query_string.split("&"))) + "\n"
    if corpo:
        # Il gateway fa l'MD5 del corpo come arriva: deve già avere le chiavi in ordine.
        assert corpo == json.dumps(json.loads(corpo), sort_keys=True, separators=(",", ":"))
        testo += base64.b64encode(hashlib.md5(corpo.encode()).digest()).decode() + "\n"
    testo += f"{r.method}\n{r.path}"
    return base64.b64encode(hmac.new(PROD_SECRET.encode(), testo.encode(), "sha256").digest()).decode()


def _firma_cu_attesa(r: web.Request, corpo: str) -> tuple[str, str]:
    coppie = sorted((p.split("=", 1) for p in r.query_string.split("&") if p), key=lambda c: c[0].lower())
    query = "&".join(f"{k}={v}" for k, v in coppie)
    testo = f"{r.method}\n{r.path}\n{query}\n{ACCESS_KEY}\n{r.headers['X-DATE']}\n"
    firma = base64.b64encode(hmac.new(SECRET_KEY.encode(), testo.encode(), "sha256").digest()).decode()
    impronta = base64.b64encode(hmac.new(SECRET_KEY.encode(), corpo.encode(), "sha256").digest()).decode()
    return firma, impronta


def _ok(dati: Any = None) -> web.Response:
    return web.json_response({"code": "000000", "msg": "success", "success": True, "data": dati})


class FintoZeekr:
    def __init__(self) -> None:
        self.login = 0
        self.bearer_dati = 0
        self.scaduto = False
        self.altrove = False
        self.giu = False
        self.comandi: list[tuple[str, dict[str, Any]]] = []
        self.rifiuta_comandi = False
        self.stato: dict[str, Any] = STATO

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_route("*", "/overseas-app/{resto:.*}", self._app_globale)
        app.router.add_route("*", "/cuc/{resto:.*}", self._centro_utenti)
        app.router.add_route("*", "/tsp/{resto:.*}", self._tsp)
        return app

    async def _app_globale(self, r: web.Request) -> web.Response:
        assert r.match_info["resto"] == "region/url"
        return _ok([{"countryCode": "AU", "regionCode": "SEA", "url": {"userCenterUrl": f"{self.url}/cuc"}}])

    async def _centro_utenti(self, r: web.Request) -> web.Response:
        corpo = await r.text()
        firma, impronta = _firma_cu_attesa(r, corpo)
        if r.headers.get("X-HMAC-SIGNATURE") != firma or r.headers.get("X-HMAC-DIGEST") != impronta:
            return web.json_response({"code": "401", "msg": "sign error", "success": False}, status=401)
        assert r.headers["X-HMAC-ACCESS-KEY"] == ACCESS_KEY
        dove = r.match_info["resto"]
        if self.giu:
            return web.Response(status=502, text="bad gateway")
        if dove == "auth/checkUserV2":
            if json.loads(corpo)["email"] != EMAIL:
                return web.json_response({"code": "9007", "msg": None, "success": False})
            return _ok(True)
        if dove == "auth/loginByEmailEncrypt":
            password = _PRIVATA.decrypt(base64.b64decode(json.loads(corpo)["password"]), rsa_padding.PKCS1v15())
            if password.decode() != PASSWORD:
                return web.json_response({"code": "9300", "msg": "Incorrect password", "success": False})
            self.login += 1
            return _ok({"tokenName": "Authorization", "tokenValue": f"UC{self.login}"})
        assert r.headers.get("authorization") == f"UC{self.login}"
        if dove == "user/info":
            return _ok({"uuid": "u-1", "email": EMAIL})
        if dove == "user/tspCode":
            assert r.query["tspClientId"] == modulo_cliente.CLIENT_ID
            return _ok({"code": f"TSP{self.login}", "loginId": "L1"})
        return web.json_response({"code": "404", "success": False}, status=404)

    async def _tsp(self, r: web.Request) -> web.Response:
        corpo = await r.text()
        if r.headers.get("X-SIGNATURE") != _firma_tsp_attesa(r, corpo):
            return web.json_response({"code": "079024", "msg": "signature error", "success": False}, status=401)
        dove = r.match_info["resto"]
        if dove == "ms-user-auth/v1.0/auth/login":
            assert "authorization" not in r.headers
            assert json.loads(corpo)["identifier"] == f"TSP{self.login}"
            self.bearer_dati += 1
            return _ok({"accessToken": f"Bearer B{self.bearer_dati}"})
        if r.headers.get("authorization") != f"Bearer B{self.bearer_dati}" or self.scaduto:
            self.scaduto = False
            return web.json_response({"code": "079012", "msg": "Token expired", "success": False}, status=401)
        if self.altrove:
            return web.json_response({"code": "079021", "msg": "logged in elsewhere", "success": False}, status=401)
        if dove == "ms-app-bff/api/v4.0/veh/vehicle-list":
            assert r.query["needSharedCar"] == "true"
            return _ok([AUTO])
        assert _decifra_vin(r.headers["X-VIN"]) == VIN
        if dove == "ms-vehicle-status/api/v1.0/vehicle/status/latest":
            assert r.query_string == "latest=false&target=new"
            return _ok(self.stato)
        risposte = {
            "ms-charge-manage/api/v1.0/charge/getLatestSoc": LIMITE,
            "ms-app-bff/api/v1.0/remoteControl/getVehicleState": STATO_COMANDI,
            "ms-vehicle-status/api/v1.0/vehicle/status/qrvs": QRVS,
            "ms-charge-manage/api/v1.0/charge/getChargingPlan": PIANO,
        }
        if dove in risposte:
            return _ok(risposte[dove])
        if dove == "ms-vehicle-trail/v1.0/journalLog/trip/listForPage":
            inizio = json.loads(corpo)["endTime"] - 86_400_000
            return _ok({"total": 1, "data": [viaggio(inizio, 42, 17.0)]})
        if dove in (
            "ms-remote-control/v1.0/remoteControl/control",
            "ms-charge-manage/api/v1.0/charge/control",
            "ms-lbs-service/api/v2.0/sendToCar",
        ):
            self.comandi.append((dove, json.loads(corpo)))
            if self.rifiuta_comandi:
                return web.json_response({"code": "037005", "msg": "execution failed", "success": False})
            return _ok({"operationId": "op1"})
        return web.json_response({"code": "404", "msg": "sconosciuto", "success": False}, status=404)


@pytest.fixture
async def cloud(socket_enabled: None) -> AsyncGenerator[tuple[FintoZeekr, str]]:
    finto = FintoZeekr()
    runner = web.AppRunner(finto.app())
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    url = f"http://127.0.0.1:{runner.addresses[0][1]}"
    finto.url = url
    yield finto, url
    await runner.cleanup()


def _regioni(url: str) -> dict[str, dict[str, str]]:
    return {
        "EU": {"centro_utenti": f"{url}/cuc/", "app": f"{url}/overseas-app/", "tsp": f"{url}/tsp/"},
        "SEA": {"tsp": f"{url}/tsp/"},
    }


def _cliente(s: aiohttp.ClientSession, url: str, **altro: Any) -> ClienteZeekr:
    parametri: dict[str, Any] = {
        "email": EMAIL,
        "password": PASSWORD,
        "paese": "IT",
        "regione": "EU",
        "chiavi": CHIAVI,
        "regioni": _regioni(url),
        "app_globale": f"{url}/overseas-app/",
        **altro,
    }
    return ClienteZeekr(s, **parametri)


VETTURA = Vettura(vin=VIN, marca="zeekr", modello="7X", nome="Freccia")


async def test_dal_login_allo_stato(cloud) -> None:
    finto, url = cloud
    salvati: list[dict[str, Any]] = []
    finto.stato = _stato_in_carica()
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, salva=salvati.append)
        await c.accedi()
        assert c.token["bearer"] == "Bearer B1"
        assert salvati[-1]["centro_utenti"] == "UC1"
        auto = await c.vetture()
        assert [(v.vin, v.modello, v.nome, v.targa, v.condivisa) for v in auto] == [
            (VIN, "7X", "Freccia", "GD123AA", True)
        ]
        st = await c.stato(VETTURA)
    assert st.batteria == 76
    assert st.limite_ricarica == 80
    assert st.sentinella is True
    assert st.stato_ricarica == StatoRicarica.IN_CARICA
    assert st.potenza_ricarica_diretta_kw == 2.1  # quella del qrvs
    assert st.ricarica_programmata is True
    assert (st.ricarica_ora_inizio, st.ricarica_ora_fine) == ("01:15", "06:45")
    assert st.km_7_giorni == 42.0
    assert st.energia_7_giorni_kwh == 7.14
    assert finto.login == 1


async def test_un_token_salvato_si_riusa_e_scaduto_si_rifa(cloud) -> None:
    finto, url = cloud
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url)
        await c.accedi()
        token = dict(c.token)
        # Un cliente nuovo con il token salvato non rientra.
        c2 = _cliente(s, url, token=token, salva=salvati.append)
        await c2.vetture()
        assert finto.login == 1 and not salvati
        assert c2.token["dispositivo"] == token["dispositivo"]
        finto.scaduto = True
        await c2.vetture()
    assert finto.login == 2
    assert salvati[-1]["bearer"] == "Bearer B2"


async def test_l_account_entrato_altrove_non_litiga_col_telefono(cloud) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url)
        await c.accedi()
        finto.altrove = True
        with pytest.raises(ErroreApi):
            await c.vetture()  # rientra una volta, ma l'altro dispositivo c'è ancora
        assert finto.login == 2
        with pytest.raises(ErroreApi, match="dedicato"):
            await c.vetture()  # e per un po' non rientra più
    assert finto.login == 2


async def test_credenziali_chiavi_e_guasti_non_si_confondono(cloud) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        with pytest.raises(ErroreCredenziali):
            await _cliente(s, url, password="sbagliata").accedi()
        with pytest.raises(ErroreCredenziali, match="regione"):
            await _cliente(s, url, email="altro@example.com").accedi()
        sbagliate = chiavi_da(json.dumps({**json.loads(SEGRETI), "hmac_secret_key": "altra"}))
        with pytest.raises(ErroreCertificato):
            await _cliente(s, url, chiavi=sbagliate).accedi()
        prod_sbagliato = chiavi_da(json.dumps({**json.loads(SEGRETI), "prod_secret": "altro"}))
        with pytest.raises(ErroreCertificato):
            await _cliente(s, url, chiavi=prod_sbagliato).accedi()
        finto.giu = True
        with pytest.raises(ErroreRete):
            await _cliente(s, url).accedi()
        with pytest.raises(ErroreRete):
            await _cliente(s, "http://127.0.0.1:1").accedi()


async def test_un_paese_fuori_dall_europa_chiede_gli_host(cloud) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        regioni = {"EU": {"tsp": "http://127.0.0.1:1/"}, "SEA": {"tsp": f"{url}/tsp/"}}
        c = _cliente(s, url, paese="AU", regione="SEA", regioni=regioni)
        await c.accedi()
        assert c.token["tsp"] == f"{url}/tsp/"
        assert c.regione == "SEA"
        with pytest.raises(ErroreApi, match="paese"):
            await _cliente(s, url, paese="BR", regione="LA", regioni=regioni).accedi()
    assert finto.login == 1


async def test_i_comandi(cloud) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url)
        await c.chiudi(VETTURA, None)
        await c.apri(VETTURA, None)
        await c.trova(VETTURA, None)
        await c.clima(VETTURA, True, None, 31)
        await c.clima(VETTURA, False, None)
        await c.ricarica(VETTURA, True, None)
        await c.ricarica(VETTURA, False, None)
        await c.limite_ricarica(VETTURA, 82, None)
        await c.destinazione(VETTURA, "Duomo", 45.4642, 9.19, "Piazza del Duomo")
        with pytest.raises(ErroreComando):
            await c.limite_ricarica(VETTURA, 30, None)
        finto.rifiuta_comandi = True
        with pytest.raises(ErroreComando, match="execution failed"):
            await c.chiudi(VETTURA, None)

    def parametri(corpo: dict[str, Any]) -> list[tuple[str, str]]:
        return [(p["key"], p["value"]) for p in corpo["setting"]["serviceParameters"]]

    controllo = "ms-remote-control/v1.0/remoteControl/control"
    ricarica = "ms-charge-manage/api/v1.0/charge/control"
    fatti = [(dove, c["serviceId"], c["command"], parametri(c)) for dove, c in finto.comandi[:8]]
    assert fatti == [
        (controllo, "RDL", "start", [("door", "all")]),
        (controllo, "RDU", "stop", [("door", "all")]),
        (controllo, "RHL", "start", [("rhl", "light-flash")]),
        (controllo, "ZAF", "start", [("AC", "true"), ("AC.temp", "28.0"), ("AC.duration", "15")]),
        (controllo, "ZAF", "start", [("AC", "false")]),
        (ricarica, "RCS", "start", [("rcs.restart", "1")]),
        (ricarica, "RCS", "stop", [("rcs.terminate", "1")]),
        (ricarica, "RCS", "start", [("soc", "800"), ("rcs.setting", "1"), ("altCurrent", "1")]),
    ]
    dove, nav = finto.comandi[8]
    assert dove == "ms-lbs-service/api/v2.0/sendToCar"
    assert nav["csys"] == "WGS-84" and nav["name"] == "Duomo" and nav["latitude"] == 45.4642
    assert zeekr.comandi_di(VETTURA) >= {"chiudi", "apri", "clima", "ricarica", "limite_ricarica", "destinazione"}


async def test_le_letture_facoltative_non_fermano_lo_stato(cloud) -> None:
    finto, _url = cloud
    originale = finto._tsp

    async def con_guasti(r: web.Request) -> web.Response:
        if r.match_info["resto"].startswith(("ms-charge-manage", "ms-vehicle-trail", "ms-app-bff/api/v1.0")):
            return web.Response(status=500, text="giù")
        return await originale(r)

    app = web.Application()
    app.router.add_route("*", "/cuc/{resto:.*}", finto._centro_utenti)
    app.router.add_route("*", "/tsp/{resto:.*}", con_guasti)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    altro = f"http://127.0.0.1:{runner.addresses[0][1]}"
    try:
        async with aiohttp.ClientSession() as s:
            st = await _cliente(s, altro).stato(VETTURA)
    finally:
        await runner.cleanup()
    assert st.batteria == 76
    assert st.limite_ricarica is None
    assert st.sentinella is None
    assert st.km_7_giorni is None


# --- dentro Home Assistant -------------------------------------------------------------


MARCHIO = Marchio("zeekr", "Zeekr", "zeekr", {}, "Zeekr EV (HACS)", "https://github.com/Fryyyyy/zeekr_homeassistant")


async def test_configurare_zeekr_dal_catalogo(hass: HomeAssistant, cloud) -> None:
    finto, url = cloud
    with patch.dict(PER_ID, {"zeekr": MARCHIO}), patch.dict(modulo_cliente.REGIONI, _regioni(url)):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "zeekr"})
        assert r["step_id"] == "credenziali"
        assert set(r["data_schema"].schema) >= {"email", "password", "paese", "api_key"}
        modulo = {"email": EMAIL, "password": "sbagliata", "paese": "IT", "api_key": SEGRETI}
        r = await hass.config_entries.flow.async_configure(r["flow_id"], modulo)
        assert r["errors"] == {"base": "credenziali"}
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {**modulo, "api_key": "{}"})
        assert r["errors"] == {"base": "certificato"}
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {**modulo, "password": PASSWORD})
        await hass.async_block_till_done()
        assert r["type"] is FlowResultType.CREATE_ENTRY
        assert r["title"] == f"Zeekr · {EMAIL}"
        assert r["result"].unique_id == "zeekr_auto@example.com"
        assert r["data"][CONF_MARCA] == "zeekr"
        assert r["data"]["token"]["bearer"] == "Bearer B1"
        assert hass.states.get("sensor.freccia_batteria").state == "76"
        assert hass.states.get("lock.freccia_serratura").state == "locked"
        assert hass.states.get("device_tracker.freccia_posizione") is not None
    assert zeekr.comandi_di(r["result"].runtime_data.auto[0].vettura) >= {"chiudi", "clima"}
    assert finto.login == 1  # il setup riusa il token della configurazione


def test_la_data_della_firma_e_in_inglese() -> None:
    # La data va in inglese qualunque sia la lingua del sistema: formatdate non usa il locale.
    assert formatdate(0, usegmt=True) == "Thu, 01 Jan 1970 00:00:00 GMT"
