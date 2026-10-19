"""Nissan: le letture dei due cloud, i clienti contro un cloud finto (WSO2,
Kamereon e Carwings su 127.0.0.1) e la configurazione in Home Assistant.

I payload sono quelli dei test di dan-r/HomeAssistant-NissanConnect e di
filcole/pycarwings2 (``nissan_campioni``).
"""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
import hashlib
from typing import Any
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import aiohttp
from aiohttp import web
from cryptography.hazmat.decrepit.ciphers.algorithms import Blowfish
from cryptography.hazmat.primitives.ciphers import Cipher, modes
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.gdaauto import config_flow
from custom_components.gdaauto.const import DOMAIN
from custom_components.gdaauto.marche import nissan
from custom_components.gdaauto.marche.base import Dispositivo
from custom_components.gdaauto.marche.catalogo import Marchio
from custom_components.gdaauto.marche.errori import ErroreApi, ErroreComando, ErroreCredenziali, ErroreRete
from custom_components.gdaauto.marche.nissan.carwings import ClienteCarwings, cifra_password
from custom_components.gdaauto.marche.nissan.cliente import ClienteNissan, comandi_di
from custom_components.gdaauto.marche.nissan.dati import (
    clima_carwings,
    interpreta_carwings,
    interpreta_kamereon,
    storico_carwings,
    storico_kamereon,
)
from custom_components.gdaauto.marche.nissan.kamereon import EUROPA, ClienteKamereon, codice_da, link_login
from custom_components.gdaauto.vettura import StatoRicarica, Vettura

from . import nissan_campioni as c

# --- le letture --------------------------------------------------------------------


def test_la_micra_dalla_batteria_v3() -> None:
    s = interpreta_kamereon(
        {
            "battery-status": c.MICRA_BATTERIA,
            "hvac-status": c.MICRA_CLIMA,
            "location": c.MICRA_POSIZIONE,
            "cockpit": c.MICRA_COCKPIT_V2,
        },
        batteria_v3=True,
    )
    assert (s.batteria, s.autonomia_km, s.odometro_km) == (57, 244, 12345.0)
    assert s.stato_ricarica == StatoRicarica.NON_IN_CARICA
    assert s.cavo_collegato is False
    assert s.potenza_ricarica_diretta_kw == 0.0
    assert s.tempo_ricarica_min is None
    assert (s.temperatura_interna_c, s.temperatura_esterna_c, s.clima_acceso) == (21.0, None, False)
    assert (s.latitudine, s.longitudine, s.posizione_con_segno) == (12.34, 56.78, True)
    assert s.letto == datetime(2026, 1, 1, 10, tzinfo=UTC)
    # Un'elettrica non ha serbatoio, anche se il cockpit dice «fuelQuantity: 0».
    assert s.carburante_percento is None
    assert "location" not in s.grezzo


def test_la_leaf_in_carica_dalla_batteria_v1() -> None:
    s = interpreta_kamereon(
        {
            "battery-status": c.LEAF_BATTERIA,
            "hvac-status": c.LEAF_CLIMA,
            "lock-status": c.LEAF_PORTE,
            "cockpit": c.LEAF_COCKPIT,
        }
    )
    assert s.batteria == 64
    assert s.capacita_batteria_kwh == 42.0
    assert s.stato_ricarica == StatoRicarica.IN_CARICA and s.in_carica
    assert s.cavo_collegato is True
    # chargePower 2: la carica normale, coi suoi minuti.
    assert (s.tempo_ricarica_min, s.potenza_ricarica_kw, s.ricarica_rapida) == (240, 6.6, False)
    # Il clima è acceso: l'autonomia è quella col clima.
    assert s.clima_acceso is True and s.autonomia_km == 165
    assert (s.temperatura_esterna_c, s.temperatura_interna_c, s.temperatura_clima_c) == (12.5, 15.0, 21)
    assert (s.chiusa, s.portiere_aperte, s.bagagliaio_aperto) == (False, True, False)
    assert s.odometro_km == 31250.4
    assert s.temperatura_batteria_c is None  # nella v1 l'unità non è chiara: non si inventa
    assert s.letto == datetime(2026, 10, 18, 9, 35, tzinfo=UTC)


def test_la_leaf_staccata_senza_clima() -> None:
    staccata = {**c.LEAF_BATTERIA, "chargeStatus": 0, "plugStatus": 0}
    s = interpreta_kamereon({"battery-status": staccata, "hvac-status": {**c.LEAF_CLIMA, "hvacStatus": "off"}})
    assert s.stato_ricarica == StatoRicarica.NON_IN_CARICA
    assert (s.cavo_collegato, s.potenza_ricarica_diretta_kw, s.tempo_ricarica_min) == (False, 0.0, None)
    assert s.autonomia_km == 180
    s = interpreta_kamereon({"battery-status": {**c.LEAF_BATTERIA, "chargeStatus": -1, "plugStatus": -1}})
    assert (s.stato_ricarica, s.cavo_collegato) == (StatoRicarica.ERRORE, None)


def test_i_viaggi_della_settimana() -> None:
    campi = storico_kamereon(c.LEAF_VIAGGI["data"]["attributes"]["summaries"])
    assert campi == {"km_7_giorni": 100.0, "energia_7_giorni_kwh": 16.0, "consumo_medio_kwh_100km": 16.0}
    # In Wh si riporta a kWh; senza viaggi non si dice niente.
    assert storico_kamereon([{"distance": 100, "consumedElectricity": 15000}])["energia_7_giorni_kwh"] == 15.0
    assert storico_kamereon([]) == {}
    assert "consumo_medio_kwh_100km" not in storico_kamereon([{"distance": 5, "consumedElectricity": 1}])


def test_la_leaf_30_staccata_dal_carwings() -> None:
    s = interpreta_carwings(c.BATTERIA_30["BatteryStatusRecords"], c.CLIMA_SPENTO["RemoteACRecords"])
    assert (s.batteria, s.energia_residua_kwh) == (91, 24.48)
    assert s.capacita_kwh == 26.9
    assert s.stato_ricarica == StatoRicarica.NON_IN_CARICA
    assert (s.cavo_collegato, s.ricarica_rapida, s.potenza_ricarica_kw) == (False, False, 0.0)
    assert s.tempo_ricarica_min is None
    assert (s.clima_acceso, s.autonomia_km) == (False, 168.0)
    assert s.letto == datetime(2019, 1, 21, 13, 29, tzinfo=UTC)


def test_la_leaf_24_alla_colonnina_rapida_e_alla_wallbox() -> None:
    s = interpreta_carwings(c.BATTERIA_RAPIDA["BatteryStatusRecords"], c.CLIMA_ACCESO["RemoteACRecords"])
    assert s.batteria == 91.7  # 11 tacche su 12
    assert s.energia_residua_kwh is None
    assert s.stato_ricarica == StatoRicarica.IN_CARICA
    assert (s.cavo_collegato, s.ricarica_rapida, s.tempo_ricarica_min) == (True, True, None)
    assert (s.clima_acceso, s.autonomia_km) == (True, 107.1)
    s = interpreta_carwings(c.BATTERIA_IN_CARICA["BatteryStatusRecords"])
    assert (s.batteria, s.ricarica_rapida, s.tempo_ricarica_min) == (100, False, 40)
    assert s.clima_acceso is None and s.autonomia_km == 134.0


def test_il_clima_e_i_consumi_del_carwings() -> None:
    assert clima_carwings(c.CLIMA_ACCESO["RemoteACRecords"]) is True
    assert clima_carwings(c.CLIMA_SPENTO["RemoteACRecords"]) is False
    assert clima_carwings(c.CLIMA_NIENTE["RemoteACRecords"]) is False
    assert clima_carwings(c.CLIMA_SENZA_CAMPO["RemoteACRecords"]) is None
    s = interpreta_carwings(c.BATTERIA_30["BatteryStatusRecords"], c.CLIMA_SENZA_CAMPO["RemoteACRecords"])
    assert s.temperatura_clima_c == 22
    assert storico_carwings(c.CONSUMI_MESE) == {"consumo_medio_kwh_100km": 15.0}
    assert storico_carwings({"status": 200}) == {}


def test_il_link_e_il_codice() -> None:
    link, stato = link_login()
    url = urlparse(link)
    domanda = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://login.mynissan-account.com/oauth2/authorize"
    assert domanda["client_id"] == "ZM3WK7ax1OtQKYQ8Qqzcv5VgiA8a"
    assert domanda["redirect_uri"] == "com://wso2.service.nci"
    assert domanda["state"] == stato["state"]
    atteso = base64.urlsafe_b64encode(hashlib.sha256(stato["verifier"].encode()).digest()).rstrip(b"=").decode()
    assert (domanda["code_challenge"], domanda["code_challenge_method"]) == (atteso, "S256")
    # L'indirizzo intero o il codice da solo.
    assert codice_da(f"com://wso2.service.nci?code=ABC-123&state={stato['state']}", stato["state"]) == "ABC-123"
    assert codice_da("  ABC-123 ") == "ABC-123"
    with pytest.raises(ErroreCredenziali):
        codice_da("com://wso2.service.nci?code=ABC&state=altro", stato["state"])
    with pytest.raises(ErroreCredenziali, match="access_denied"):
        codice_da("com://wso2.service.nci?error=access_denied&state=x")
    with pytest.raises(ErroreCredenziali):
        codice_da("https://login.mynissan-account.com/authenticationendpoint/login.do?x=1")


def test_i_comandi_secondo_i_servizi() -> None:
    micra = Vettura(c.VIN_MICRA, "nissan", "Micra", extra={"via": "kamereon", "servizi": ["12", "97", "299", "366"]})
    assert comandi_di(micra) == {"clima", "ricarica", "trova"}
    assert comandi_di(Vettura("X", "nissan", "Juke", extra={"via": "kamereon", "servizi": ["12"]})) == set()
    assert comandi_di(Vettura(c.VIN_VECCHIA, "nissan", "Leaf", extra={"via": "carwings"})) == {"clima", "ricarica"}


def test_la_password_del_carwings() -> None:
    # Blowfish ECB con riempimento PKCS#5, come pycarwings2.
    cifrata = cifra_password("password", "88dSp7wWnV3bvv9Z")
    assert cifrata == "yF5/wEa1J2ukh/jxl5I9Ew=="


# --- il cloud finto --------------------------------------------------------------------


def _decifra(cifrata: str, chiave: str) -> str:
    d = Cipher(Blowfish(chiave.encode()), modes.ECB()).decryptor()
    chiaro = d.update(base64.b64decode(cifrata)) + d.finalize()
    return chiaro[: -chiaro[-1]].decode()


@dataclass
class FintaNissan:
    challenge: str = ""
    codice: str = "codice-buono"
    refresh_valido: bool = True
    login_refresh_valido: bool = True
    emessi: int = 0
    access: str = ""
    login_rotto: bool = False
    chiamate: list[str] = field(default_factory=list)
    comandi: list[tuple[str, Any]] = field(default_factory=list)
    speciali: dict[str, tuple[int, Any]] = field(default_factory=dict)
    # Carwings
    password: str = "giusta"
    sessione: str = "SESSIONE-1"
    login_carwings: int = 0
    attese: int = 1
    risultato_clima: dict[str, Any] = field(default_factory=lambda: dict(c.CLIMA_RISULTATO_ACCESO))
    carwings_rotto: bool = False

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/oauth2/token", self._wso2)
        app.router.add_post("/bff-web/v1/oauth2/{come}", self._token)
        app.router.add_route("*", "/{resto:(bff-web|car-adapter|user-adapter)/.*}", self._kamereon)
        app.router.add_post("/gdc/{pagina}", self._carwings)
        return app

    def _nuovo_access(self) -> dict[str, Any]:
        self.emessi += 1
        self.access = f"kamereon-access-{self.emessi}"
        return {"access_token": self.access, "token_type": "Bearer", "expires_in": 1800}

    async def _wso2(self, r: web.Request) -> web.Response:
        modulo = dict(await r.post())
        self.chiamate.append(f"wso2:{modulo['grant_type']}")
        if self.login_rotto:
            return web.Response(status=503, text="<html>giù</html>")
        assert modulo["client_id"] == "CLIENT"
        if modulo["grant_type"] == "authorization_code":
            verifier = modulo["code_verifier"]
            sfida = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            if modulo["code"] != self.codice or sfida != self.challenge:
                return web.json_response({"error": "invalid_grant", "error_description": "Invalid code"}, status=400)
            return web.json_response(c.TOKEN_LOGIN)
        if modulo["grant_type"] == "refresh_token":
            if not self.login_refresh_valido or modulo["refresh_token"] != "wso2-refresh-token":
                return web.json_response({"error": "invalid_grant"}, status=400)
            return web.json_response(c.TOKEN_LOGIN)
        return web.json_response({"error": "unsupported_grant_type"}, status=400)

    async def _token(self, r: web.Request) -> web.Response:
        come = r.match_info["come"]
        self.chiamate.append(f"token:{come}")
        assert r.query["platform"] == "Android"
        if come == "access_token":
            assert r.headers["Authorization"] == "wso2-id-token"
            return web.json_response({**self._nuovo_access(), "refresh_token": "kamereon-refresh"})
        assert await r.json() == {"scope": "openid profile vehicles"}
        if not self.refresh_valido or r.headers["Authorization"] != "kamereon-refresh":
            return web.json_response({"error": "invalid_grant"}, status=401)
        return web.json_response(self._nuovo_access())

    async def _kamereon(self, r: web.Request) -> web.Response:
        resto = r.match_info["resto"]
        self.chiamate.append(f"{r.method} {resto}")
        if r.headers.get("Authorization") != f"Bearer {self.access}":
            return web.json_response({"errors": [{"code": "401", "title": "Unauthorized"}]}, status=401)
        if resto in self.speciali:
            stato, corpo = self.speciali[resto]
            if isinstance(corpo, str):
                return web.Response(status=stato, text=corpo)
            return web.json_response(corpo, status=stato)
        if r.method == "POST":
            self.comandi.append((resto, await r.json()))
            return web.json_response({"data": {"type": "Azione", "id": "guid"}})
        m, f = c.VIN_MICRA, c.VIN_LEAF
        letture: dict[str, tuple[int, Any]] = {
            "user-adapter/v1/users/current": (200, c.UTENTE),
            "bff-web/v5/users/test-user/cars": (200, c.AUTO),
            f"bff-web/v3/cars/{m}/battery-status": (200, c.dati(c.MICRA_BATTERIA)),
            f"car-adapter/v1/cars/{m}/location": (200, c.dati(c.MICRA_POSIZIONE)),
            f"car-adapter/v1/cars/{m}/hvac-status": (200, c.dati(c.MICRA_CLIMA)),
            f"car-adapter/v1/cars/{m}/cockpit": (501, c.NON_IMPLEMENTATO),
            f"car-adapter/v2/cars/{m}/cockpit": (200, c.dati(c.MICRA_COCKPIT_V2)),
            f"car-adapter/v1/cars/{f}/battery-status": (200, c.dati(c.LEAF_BATTERIA, f)),
            f"car-adapter/v1/cars/{f}/location": (200, c.dati(c.LEAF_POSIZIONE, f)),
            f"car-adapter/v1/cars/{f}/hvac-status": (200, c.dati(c.LEAF_CLIMA, f)),
            f"car-adapter/v1/cars/{f}/lock-status": (200, c.dati(c.LEAF_PORTE, f)),
            f"car-adapter/v1/cars/{f}/cockpit": (200, c.dati(c.LEAF_COCKPIT, f)),
            f"car-adapter/v1/cars/{f}/trip-history": (200, c.LEAF_VIAGGI),
        }
        if resto == f"bff-web/v3/cars/{m}/battery-status":
            assert r.query["canGen"] == "C1A_HS EVO"
        if resto.endswith("trip-history"):
            assert r.query["type"] == "0" and r.query["start"] < r.query["end"]
        stato, corpo = letture.get(resto, (404, c.NON_TROVATO))
        return web.json_response(corpo, status=stato)

    async def _carwings(self, r: web.Request) -> web.Response:
        pagina = r.match_info["pagina"]
        modulo = dict(await r.post())
        self.chiamate.append(f"carwings:{pagina}")
        if self.carwings_rotto:
            return web.Response(status=200, text="<html><title>503 Service Temporarily Unavailable</title></html>")
        assert modulo["initial_app_str"] == "9s5rfKVuMrT03RtzajWNcA"
        if pagina == "InitialApp_v2.php":
            assert modulo["RegionCode"] == "NE"
            return web.json_response(c.AVVIO)
        if pagina == "UserLoginRequest.php":
            if _decifra(str(modulo["Password"]), c.AVVIO["baseprm"]) != self.password:
                return web.json_response(c.LOGIN_SBAGLIATO)
            self.login_carwings += 1
            corpo = dict(c.LOGIN_CARWINGS)
            self.sessione = f"SESSIONE-{self.login_carwings}"
            info = dict(corpo["VehicleInfoList"]["vehicleInfo"][0], custom_sessionid=self.sessione)
            corpo["VehicleInfoList"] = {**corpo["VehicleInfoList"], "vehicleInfo": [info]}
            return web.json_response(corpo)
        if modulo["custom_sessionid"] != self.sessione:
            return web.json_response({"status": 401, "message": "INVALID PARAMS"})
        assert (modulo["VIN"], modulo["DCMID"], modulo["tz"]) == (c.VIN_VECCHIA, "123456789012", "Europe/Rome")
        if pagina == "BatteryStatusRecordsRequest.php":
            assert modulo["TimeFrom"] == "2015-08-17T14:16:32Z"
            return web.json_response(c.BATTERIA_30)
        if pagina == "RemoteACRecordsRequest.php":
            return web.json_response(c.CLIMA_ACCESO)
        if pagina == "PriceSimulatorDetailInfoRequest.php":
            return web.json_response(c.CONSUMI_MESE)
        if pagina in ("ACRemoteRequest.php", "ACRemoteOffRequest.php"):
            self.comandi.append((pagina, modulo))
            return web.json_response(c.CHIAVE_RISULTATO)
        if pagina in ("ACRemoteResult.php", "ACRemoteOffResult.php"):
            assert modulo["resultKey"] == c.CHIAVE_RISULTATO["resultKey"]
            assert modulo["UserId"] == "FG12345678"
            if self.attese:
                self.attese -= 1
                return web.json_response(c.IN_ATTESA)
            return web.json_response(self.risultato_clima)
        if pagina == "BatteryRemoteChargingRequest.php":
            self.comandi.append((pagina, modulo))
            return web.json_response({"status": 200})
        return web.json_response({"status": 404, "message": "INVALID PARAMS"})


@pytest.fixture
async def cloud(socket_enabled: None) -> AsyncGenerator[tuple[FintaNissan, Any, str]]:
    finta = FintaNissan()
    runner = web.AppRunner(finta.app())
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    base = f"http://127.0.0.1:{runner.addresses[0][1]}/"
    server = replace(
        EUROPA,
        login=base,
        client_id="CLIENT",
        car_adapter=base + "car-adapter/",
        user_adapter=base + "user-adapter/",
        bff=base + "bff-web/",
    )
    yield finta, server, base + "gdc/"
    await runner.cleanup()


def _sfida(finta: FintaNissan, server: Any) -> dict[str, str]:
    """Il link del login, come lo aprirebbe l'utente: il server finto ricorda la sfida PKCE."""
    link, stato = link_login(server)
    finta.challenge = parse_qs(urlparse(link).query)["code_challenge"][0]
    return stato


async def _kamereon(s: aiohttp.ClientSession, finta: FintaNissan, server: Any, salvati: list) -> ClienteKamereon:
    stato = _sfida(finta, server)
    k = ClienteKamereon(s, None, salvati.append, server=server)
    await k.scambia_codice(
        codice_da(f"com://wso2.service.nci?code=codice-buono&state={stato['state']}"), stato["verifier"]
    )
    return k


async def test_codice_auto_e_stato(cloud) -> None:
    finta, server, _ = cloud
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        k = await _kamereon(s, finta, server, salvati)
        assert salvati[-1]["access_token"] == "kamereon-access-1"
        assert (salvati[-1]["refresh_token"], salvati[-1]["login_refresh"]) == (
            "kamereon-refresh",
            "wso2-refresh-token",
        )
        await k.accedi()
        assert salvati[-1]["user_id"] == "test-user"
        micra, leaf = await k.vetture()
        assert (micra.vin, micra.modello, micra.nome, micra.anno, micra.targa) == (
            c.VIN_MICRA,
            "Micra",
            "Nissan Micra",
            2025,
            "GA123BC",
        )
        assert micra.extra == {
            "via": "kamereon",
            "modello": "MICRA",
            "servizi": sorted(str(x) for x in c.MICRA_SERVIZI),
            "can": "C1A_HS EVO",
        }
        assert (leaf.nome, leaf.colore) == ("La Leaf", "QAB")
        assert "96" not in leaf.extra["servizi"]  # un servizio spento non conta

        s1 = await k.stato(micra)
        assert (s1.batteria, s1.autonomia_km, s1.odometro_km, s1.latitudine) == (57, 244, 12345.0, 12.34)
        assert not [x for x in finta.chiamate if "lock-status" in x]  # la Micra non ha il servizio 2021
        finta.chiamate.clear()
        await k.stato(micra)
        # La versione del cockpit che risponde si ricorda: niente v1 la seconda volta.
        assert [x for x in finta.chiamate if "cockpit" in x] == [f"GET car-adapter/v2/cars/{c.VIN_MICRA}/cockpit"]

        s2 = await k.stato(leaf)
        assert (s2.batteria, s2.chiusa, s2.portiere_aperte, s2.odometro_km) == (64, False, True, 31250.4)
        assert (s2.km_7_giorni, s2.energia_7_giorni_kwh, s2.consumo_medio_kwh_100km) == (100.0, 16.0, 16.0)
        finta.chiamate.clear()
        await k.stato(leaf)
        assert not [x for x in finta.chiamate if "trip-history" in x]  # lo storico si rilegge ogni mezz'ora


async def test_letture_che_mancano_e_guasti(cloud) -> None:
    finta, server, _ = cloud
    async with aiohttp.ClientSession() as s:
        k = await _kamereon(s, finta, server, [])
        micra, leaf = await k.vetture()
        # Con la modalità privacy la posizione dice 403; un 502 sul clima: il resto arriva lo stesso.
        finta.speciali[f"car-adapter/v1/cars/{c.VIN_LEAF}/location"] = (403, c.PRIVACY)
        finta.speciali[f"car-adapter/v1/cars/{c.VIN_LEAF}/hvac-status"] = (502, {"errors": [{"code": "502"}]})
        st = await k.stato(leaf)
        assert st.batteria == 64 and st.latitudine is None and st.clima_acceso is None
        # Un guasto della batteria è un guasto, non le credenziali.
        finta.speciali[f"car-adapter/v1/cars/{c.VIN_LEAF}/battery-status"] = (503, "<html>giù</html>")
        with pytest.raises(ErroreRete):
            await k.stato(leaf)
        # Una batteria che la v3 non ha (404) si prova in v1, e si ricorda.
        finta.speciali[f"bff-web/v3/cars/{c.VIN_MICRA}/battery-status"] = (404, c.NON_TROVATO)
        finta.speciali[f"car-adapter/v1/cars/{c.VIN_MICRA}/battery-status"] = (200, c.dati(c.LEAF_BATTERIA))
        k._versioni.clear()
        assert (await k.stato(micra)).batteria == 64


async def test_codice_sbagliato_e_login_rotto(cloud) -> None:
    finta, server, _ = cloud
    async with aiohttp.ClientSession() as s:
        stato = _sfida(finta, server)
        k = ClienteKamereon(s, None, server=server)
        with pytest.raises(ErroreCredenziali, match="Invalid code"):
            await k.scambia_codice("codice-vecchio", stato["verifier"])
        # Un verifier che non è quello del link è rifiutato come un codice sbagliato.
        with pytest.raises(ErroreCredenziali):
            await k.scambia_codice("codice-buono", "altro-verifier")
        finta.login_rotto = True
        with pytest.raises(ErroreRete):
            await k.scambia_codice("codice-buono", stato["verifier"])
        # Senza token non si entra da soli: serve il browser.
        with pytest.raises(ErroreCredenziali, match="browser"):
            await ClienteKamereon(s, {}, server=server).accedi()


async def test_il_rinnovo_dei_token(cloud) -> None:
    finta, server, _ = cloud
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        k = await _kamereon(s, finta, server, salvati)
        await k.accedi()
        micra, _ = await k.vetture()
        # Scaduto: si rinnova col refresh di Kamereon, e si salva.
        k.token["scade"] = 0
        await k.stato(micra)
        assert "token:refresh-token" in finta.chiamate
        assert salvati[-1]["access_token"] == "kamereon-access-2"
        assert salvati[-1]["refresh_token"] == "kamereon-refresh"  # il refresh senza uno nuovo resta
        # Un 401 a metà: si rinnova e si riprova una volta.
        finta.access = "revocato"
        await k.stato(micra)
        assert k.token["access_token"] == "kamereon-access-3"
        # Il refresh di Kamereon non va più: si passa da quello del login.
        finta.refresh_valido = False
        finta.chiamate.clear()
        k.token["scade"] = 0
        await k.stato(micra)
        assert finta.chiamate[:3] == ["token:refresh-token", "wso2:refresh_token", "token:access_token"]
        # Nemmeno quello: bisogna rientrare dal browser.
        finta.login_refresh_valido = False
        k.token["scade"] = 0
        with pytest.raises(ErroreCredenziali, match="browser"):
            await k.stato(micra)


async def test_i_comandi_di_nissanconnect_services(cloud) -> None:
    finta, server, _ = cloud
    async with aiohttp.ClientSession() as s:
        k = await _kamereon(s, finta, server, [])
        micra, leaf = await k.vetture()
        await k.clima(leaf, True, 30)
        await k.clima(leaf, False)
        await k.ricarica(leaf, True)
        await k.ricarica(leaf, False)
        await k.trova(micra)
        base = f"car-adapter/v1/cars/{c.VIN_LEAF}/actions/"
        assert finta.comandi == [
            (
                base + "hvac-start",
                {"data": {"type": "HvacStart", "attributes": {"action": "start", "targetTemperature": 26}}},
            ),
            (base + "hvac-start", {"data": {"type": "HvacStart", "attributes": {"action": "stop"}}}),
            (base + "charging-start", {"data": {"type": "ChargingStart", "attributes": {"action": "start"}}}),
            (base + "charging-start", {"data": {"type": "ChargingStart", "attributes": {"action": "stop"}}}),
            (
                f"car-adapter/v1/cars/{c.VIN_MICRA}/actions/horn-lights",
                {
                    "data": {
                        "type": "HornLights",
                        "attributes": {"action": "start", "duration": 5, "target": "horn_lights"},
                    }
                },
            ),
        ]
        # La Micra non ha il servizio 303: non ferma la ricarica.
        with pytest.raises(ErroreComando, match="non ferma la ricarica"):
            await k.ricarica(micra, False)
        finta.speciali[f"car-adapter/v1/cars/{c.VIN_MICRA}/actions/hvac-start"] = (403, c.PRIVACY)
        with pytest.raises(ErroreComando, match="modalità privacy"):
            await k.clima(micra, True)


async def test_il_carwings(cloud) -> None:
    finta, _, url = cloud
    async with aiohttp.ClientSession() as s:
        cw = ClienteCarwings(s, "mario@esempio.it", "giusta", "NE", url=url, attesa=0)
        await cw.accedi()
        (leaf,) = await cw.vetture()
        assert (leaf.vin, leaf.modello, leaf.nome) == (c.VIN_VECCHIA, "Leaf", "Nissan Leaf")
        assert leaf.extra == {"via": "carwings", "dcm": "123456789012", "legato": "2015-08-17T14:16:32Z"}
        st = await cw.stato(leaf)
        assert (st.batteria, st.energia_residua_kwh, st.clima_acceso, st.autonomia_km) == (91, 24.48, True, 146.0)
        assert st.consumo_medio_kwh_100km == 15.0
        # La sessione scade: si rientra da soli, una volta.
        finta.sessione = "SCADUTA"
        assert (await cw.stato(leaf)).batteria == 91
        assert finta.login_carwings == 2
        # Non sveglia mai l'auto per leggerla.
        assert not [x for x in finta.chiamate if "BatteryStatusCheckRequest" in x]

        await cw.clima(leaf, True)
        assert finta.comandi[-1][0] == "ACRemoteRequest.php"
        finta.attese, finta.risultato_clima = 0, dict(c.CLIMA_RISULTATO_SPENTO)
        await cw.clima(leaf, False)
        finta.risultato_clima = {**c.CLIMA_RISULTATO_ACCESO, "operationResult": "ELECTRIC_WAVE_ABNORMAL"}
        with pytest.raises(ErroreComando, match="campo"):
            await cw.clima(leaf, True)
        await cw.ricarica(leaf, True)
        assert finta.comandi[-1][0] == "BatteryRemoteChargingRequest.php"
        with pytest.raises(ErroreComando):
            await cw.ricarica(leaf, False)


async def test_il_carwings_rifiuta_o_si_guasta(cloud) -> None:
    finta, _, url = cloud
    async with aiohttp.ClientSession() as s:
        with pytest.raises(ErroreCredenziali):
            await ClienteCarwings(s, "mario@esempio.it", "sbagliata", url=url).accedi()
        finta.carwings_rotto = True
        with pytest.raises(ErroreRete):
            await ClienteCarwings(s, "mario@esempio.it", "giusta", url=url).accedi()


async def test_abbonamento_scaduto(cloud) -> None:
    _, _, url = cloud
    async with aiohttp.ClientSession() as s:
        cw = ClienteCarwings(s, "mario@esempio.it", "giusta", url=url)
        with (
            patch.object(cw, "_posta", AsyncMock(side_effect=[c.AVVIO, c.ABBONAMENTO_SCADUTO])),
            pytest.raises(ErroreCredenziali, match="abbonamento"),
        ):
            await cw.accedi()
        with (
            patch.object(cw, "_posta", AsyncMock(side_effect=[c.AVVIO, c.LOGIN_CARWINGS, {"status": 200}])),
            pytest.raises(ErroreApi),
        ):
            await cw.stato(Vettura(c.VIN_VECCHIA, "nissan", "Leaf", extra={"via": "carwings", "dcm": "1"}))


async def test_europa_riconosce_da_sola(cloud) -> None:
    finta, server, url = cloud
    async with aiohttp.ClientSession() as s:

        def cliente(password: str = "giusta", regione: str = "auto_eu") -> ClienteNissan:
            return ClienteNissan(
                s, "mario@esempio.it", password, regione, server_kamereon=server, url_carwings=url, attesa_carwings=0
            )

        # Tutte e due: le auto nuove dal codice, la Leaf vecchia dalla password.
        n = cliente()
        stato = _sfida(finta, server)
        vetture = await n.prova("codice-buono", stato["verifier"])
        assert [v.vin for v in vetture] == [c.VIN_MICRA, c.VIN_LEAF, c.VIN_VECCHIA]
        assert n.vie == ["kamereon", "carwings"]
        assert (await n.stato(vetture[2])).batteria == 91
        await n.ricarica(vetture[1], True, None)
        assert finta.comandi[-1][0].endswith("charging-start")
        with pytest.raises(ErroreComando):
            await n.trova(vetture[2], None)

        # Un codice sbagliato, ma la password va: si tiene il vecchio cloud.
        n = cliente()
        assert [v.vin for v in await n.prova("codice-vecchio", "x")] == [c.VIN_VECCHIA]
        assert n.vie == ["carwings"]
        # Solo le auto nuove, senza password.
        n = cliente("")
        assert len(await n.prova("codice-buono", _sfida(finta, server)["verifier"])) == 2
        assert n.vie == ["kamereon"]
        # Tutti e due rifiutano: credenziali.
        with pytest.raises(ErroreCredenziali):
            await cliente("sbagliata").prova("codice-vecchio", "x")
        # Uno rifiuta e l'altro è guasto: è un guasto, non si chiede di rientrare.
        finta.carwings_rotto = True
        with pytest.raises(ErroreRete):
            await cliente().prova("codice-vecchio", "x")
        # Solo il vecchio cloud (Stati Uniti...): senza password non si entra.
        with pytest.raises(ErroreCredenziali):
            await cliente("", "NNA").prova()


# --- Home Assistant ----------------------------------------------------------------------------

MICRA = Vettura(
    vin=c.VIN_MICRA,
    marca="nissan",
    modello="Micra",
    nome="Nissan Micra",
    extra={"via": "kamereon", "modello": "MICRA", "servizi": ["12", "299", "366", "97"], "can": "C1A_HS EVO"},
)
STATO_MICRA = interpreta_kamereon(
    {"battery-status": c.MICRA_BATTERIA, "hvac-status": c.MICRA_CLIMA, "location": c.MICRA_POSIZIONE},
    batteria_v3=True,
)
NISSAN = Marchio(
    "nissan", "Nissan", "nissan", {}, "Nissan Connect (HACS)", "https://www.home-assistant.io/integrations/nissan_leaf"
)


async def test_configurare_una_nissan(hass: HomeAssistant) -> None:
    async def prova(self: ClienteNissan, codice: str = "", verifier: str = "") -> list[Vettura]:
        if codice != "codice-buono":
            raise ErroreCredenziali("Invalid code")
        assert verifier
        self.vie = ["kamereon"]
        self.kamereon.token.update({"access_token": "A", "refresh_token": "R", "user_id": "U"})
        return [MICRA]

    with (
        patch.dict(config_flow.PER_ID, {"nissan": NISSAN}),
        patch.object(ClienteNissan, "prova", prova),
        patch.object(ClienteNissan, "accedi", AsyncMock()),
        patch.object(ClienteNissan, "vetture", AsyncMock(return_value=[MICRA])),
        patch.object(ClienteNissan, "stato", AsyncMock(return_value=STATO_MICRA)),
    ):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "nissan"})
        assert r["step_id"] == "credenziali"
        assert {str(k) for k in r["data_schema"].schema} == {"email", "password", "regione"}
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"email": "Mario@Esempio.it", "regione": "eu"})
        assert r["step_id"] == "browser"
        link = r["description_placeholders"]["link"]
        assert link.startswith("https://login.mynissan-account.com/oauth2/authorize?")
        stato = parse_qs(urlparse(link).query)["state"][0]
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"codice": "com://wso2.service.nci?code=no"})
        assert r["errors"] == {"base": "codice"}
        r = await hass.config_entries.flow.async_configure(
            r["flow_id"], {"codice": f"com://wso2.service.nci?code=codice-buono&state={stato}"}
        )
        await hass.async_block_till_done()
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["title"] == "Nissan · Mario@Esempio.it"
    assert r["result"].unique_id == "nissan_mario@esempio.it"
    assert r["data"]["marca"] == "nissan" and r["data"]["vie"] == ["kamereon"]
    assert r["data"]["token"] == {"access_token": "A", "refresh_token": "R", "user_id": "U"}
    # Senza Leaf vecchie la password non serve, e non si salva.
    assert "password" not in r["data"]
    assert hass.states.get("sensor.nissan_micra_batteria").state == "57"
    voce = r["result"]
    assert voce.runtime_data.auto[0].comandi_possibili == {"clima", "ricarica", "trova"}
    # I token rinnovati aggiornano la voce senza ricaricarla.
    prima = voce.runtime_data
    voce.runtime_data.conto.cliente.kamereon._salva({"access_token": "B", "refresh_token": "R2"})
    await hass.async_block_till_done()
    assert voce.data["token"]["access_token"] == "B"
    assert voce.runtime_data is prima


async def test_le_leaf_vecchie_senza_browser(hass: HomeAssistant) -> None:
    dati = {"email": "mario@esempio.it", "password": "giusta", "regione": "NE"}
    assert not nissan.serve_browser(dati) and nissan.serve_browser({**dati, "regione": "auto_eu"})
    vecchia = Vettura(c.VIN_VECCHIA, "nissan", "Leaf", nome="Nissan Leaf", extra={"via": "carwings"})

    async def prova(self: ClienteNissan, codice: str = "", verifier: str = "") -> list[Vettura]:
        assert (codice, self.regione, self.password) == ("", "NE", "giusta")
        self.vie = ["carwings"]
        return [vecchia]

    d = await nissan.avvia_browser(hass, dati)
    assert d == Dispositivo(link="", codice_utente="", stato={"senza_browser": True})
    with patch.object(ClienteNissan, "prova", prova):
        accesso = await nissan.completa_browser(hass, dati, d, "qualunque cosa")
        assert accesso.dati == {
            "email": "mario@esempio.it",
            "regione": "NE",
            "vie": ["carwings"],
            "token": {},
            "password": "giusta",
        }
        assert (await nissan.prova_accesso(hass, dati)).vetture == [vecchia]
    with pytest.raises(ErroreCredenziali):
        await nissan.prova_accesso(hass, {**dati, "regione": "eu"})


async def test_una_leaf_vecchia_si_configura_senza_browser(hass: HomeAssistant) -> None:
    """Con la sola regione del vecchio cloud bastano email e password: niente passo «browser»."""
    vecchia = Vettura(c.VIN_VECCHIA, "nissan", "Leaf", nome="Nissan Leaf", extra={"via": "carwings"})

    async def prova(self: ClienteNissan, codice: str = "", verifier: str = "") -> list[Vettura]:
        self.vie = ["carwings"]
        return [vecchia]

    with (
        patch.dict(config_flow.PER_ID, {"nissan": NISSAN}),
        patch.object(ClienteNissan, "prova", prova),
        patch.object(ClienteNissan, "accedi", AsyncMock()),
        patch.object(ClienteNissan, "vetture", AsyncMock(return_value=[vecchia])),
        patch.object(ClienteNissan, "stato", AsyncMock(return_value=STATO_MICRA)),
    ):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "nissan"})
        r = await hass.config_entries.flow.async_configure(
            r["flow_id"], {"email": "mario@esempio.it", "password": "giusta", "regione": "NE"}
        )
        await hass.async_block_till_done()
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["data"]["password"] == "giusta" and r["data"]["vie"] == ["carwings"]
