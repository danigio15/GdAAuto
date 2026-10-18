"""Renault, Dacia e Alpine: le letture di Kamereon, il cliente contro un cloud
finto (Gigya + Kamereon su 127.0.0.1) e la configurazione in Home Assistant.

I payload sono quelli dei fixture di hacf-fr/renault-api (``renault_campioni``).
"""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
import json
import time
from typing import Any
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.gdaauto.const import DOMAIN
from custom_components.gdaauto.marche import renault
from custom_components.gdaauto.marche.errori import ErroreApi, ErroreComando, ErroreCredenziali, ErroreRete
from custom_components.gdaauto.marche.renault import gigya as modulo_gigya
from custom_components.gdaauto.marche.renault.cliente import ClienteRenault, comandi_di
from custom_components.gdaauto.marche.renault.dati import interpreta
from custom_components.gdaauto.marche.renault.gigya import CodiceInviato, CodiceSbagliato, Gigya, entra, scadenza_jwt
from custom_components.gdaauto.marche.renault.locali import LOCALI, server_di
from custom_components.gdaauto.vettura import StatoRicarica, Vettura

from . import renault_campioni as c

# --- le letture --------------------------------------------------------------------


def test_una_zoe_in_carica() -> None:
    s = interpreta(
        {
            "battery-status": c.BATTERIA_IN_CARICA,
            "cockpit": c.COCKPIT,
            "hvac-status": c.CLIMA,
            "location": c.POSIZIONE,
            "lock-status": c.PORTE,
            "pressure": c.GOMME,
        }
    )
    assert s.batteria == 60
    assert s.autonomia_km == 141
    assert s.temperatura_batteria_c == 20
    assert s.energia_residua_kwh == 31
    assert s.stato_ricarica == StatoRicarica.IN_CARICA
    assert s.in_carica is True
    assert s.cavo_collegato is True
    assert s.tempo_ricarica_min == 145
    assert s.potenza_ricarica_kw == 27.0
    assert s.odometro_km == 49114.27
    assert s.temperatura_esterna_c == 8.0
    assert s.clima_acceso is False
    assert (s.latitudine, s.longitudine) == (48.1234567, 11.1234567)
    assert s.posizione_con_segno is True
    assert s.chiusa is True
    assert s.portiere_aperte is False
    assert s.bagagliaio_aperto is True
    assert s.pneumatici_kpa == {
        "anteriore_sinistro": 246.0,
        "anteriore_destro": 273.0,
        "posteriore_sinistro": 279.0,
        "posteriore_destro": 279.0,
    }
    assert s.letto is not None and s.letto.year == 2022  # il dato più fresco: le porte
    # La posizione non finisce nel grezzo (né quindi nella diagnostica).
    assert "location" not in s.grezzo


def test_una_zoe_40_staccata() -> None:
    s = interpreta({"battery-status": c.BATTERIA_STACCATA})
    # -1.0 col cavo staccato è «non in carica», non un errore.
    assert s.stato_ricarica == StatoRicarica.NON_IN_CARICA
    assert s.cavo_collegato is False
    assert s.potenza_ricarica_kw == 0.0
    assert s.tempo_ricarica_min is None
    # 0 kWh vuol dire «non lo so».
    assert s.energia_residua_kwh is None
    assert s.letto is not None and s.letto.utcoffset() is not None


def test_una_renault_5_col_limite() -> None:
    s = interpreta(
        {"battery-status": c.BATTERIA_R5, "hvac-status": c.CLIMA_R5, "cockpit": c.COCKPIT_R5, "soc-levels": c.LIMITI}
    )
    assert s.batteria == 49
    assert s.autonomia_km == 169
    assert s.stato_ricarica == StatoRicarica.NON_IN_CARICA
    assert s.temperatura_interna_c == 15.0
    assert s.odometro_km == 559
    assert s.limite_ricarica == 80
    assert s.chiusa is None  # la R5 non dice le porte


def test_la_zoe_di_prima_serie_parla_in_watt_e_le_spine_in_errore() -> None:
    batteria = {**c.BATTERIA_IN_CARICA, "chargingInstantaneousPower": 7400, "plugStatus": -1}
    s = interpreta({"battery-status": batteria}, potenza_in_watt=True)
    assert s.potenza_ricarica_kw == 7.4
    assert s.cavo_collegato is True  # la spina «in errore», ma sta caricando
    s = interpreta({"battery-status": {"chargingStatus": -1.0, "plugStatus": 1}})
    assert s.stato_ricarica == StatoRicarica.ERRORE
    s = interpreta({"battery-status": {"chargingStatus": 0.1, "plugStatus": 1}})
    assert s.stato_ricarica == StatoRicarica.PROGRAMMATA
    s = interpreta({"battery-status": {"chargingStatus": 0.2, "plugStatus": 1}})
    assert s.stato_ricarica == StatoRicarica.COMPLETATA


def _vettura(codice: str, vin: str = c.VIN_ZOE) -> Vettura:
    return Vettura(vin=vin, marca="renault", modello="x", extra={"account": "account-id-1", "modello": codice})


def test_i_comandi_secondo_il_modello() -> None:
    assert comandi_di(_vettura("XBG1VE")) == {"clima", "ricarica"}  # Spring
    assert comandi_di(_vettura("R5E1VE")) == {"clima", "ricarica", "limite_ricarica", "trova"}
    assert comandi_di(_vettura("A5E1AE")) == {"clima", "ricarica", "limite_ricarica", "trova"}  # Alpine A290
    assert comandi_di(_vettura("XJA1VP")) == set()  # Clio V ibrida: niente da remoto
    assert comandi_di(_vettura("SCONOSCIUTO")) == {"clima", "ricarica"}


def test_i_paesi_e_il_jwt() -> None:
    assert server_di("it_IT")["gigya_url"] == "https://accounts.eu1.gigya.com"
    assert server_di("es_MX")["kamereon_url"].endswith("usw2.wrd-aws.com")
    assert server_di("fr_LU")["gigya_chiave"] != server_di("fr_FR")["gigya_chiave"]
    assert "it_IT" in LOCALI
    assert renault.CAMPI[2].predefinito == "it_IT"
    assert ("it_IT", "Italia · it_IT") in renault.CAMPI[2].scelte
    assert scadenza_jwt(_jwt(1234.0)) == 1234.0
    assert scadenza_jwt("non-un-jwt", adesso=100.0) == 1000.0


# --- il cloud finto ----------------------------------------------------------------


def _jwt(exp: float) -> str:
    corpo = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJSUzI1NiJ9.{corpo}.firma"


@dataclass
class FintaRenault:
    password: str = "giusta"
    verifica: bool = False
    codice: str = "123456"
    login_token_valido: bool = True
    jwt_durata: float = 900
    gigya_rotto: bool = False
    jwt_emessi: list[str] = field(default_factory=list)
    jwt_valido: str = ""
    chiamate: list[str] = field(default_factory=list)
    comandi: list[tuple[str, Any]] = field(default_factory=list)
    #: risposte particolari: coda del percorso dopo /kamereon → (stato, corpo)
    speciali: dict[str, tuple[int, Any]] = field(default_factory=dict)
    verificato: bool = False

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_route("*", "/accounts.{metodo}", self._gigya)
        app.router.add_route("*", "/commerce/v1/{resto:.*}", self._kamereon)
        return app

    # Gigya
    async def _gigya(self, r: web.Request) -> web.Response:
        metodo = r.match_info["metodo"]
        self.chiamate.append(f"gigya:{metodo}")
        if self.gigya_rotto:
            return web.Response(status=503, text="Service Unavailable")
        modulo = dict(await r.post()) if r.method == "POST" else dict(r.query)
        chiave = modulo.get("ApiKey") or modulo.get("APIKey")
        assert chiave == "CHIAVE-GIGYA"
        if metodo == "login":
            if modulo["password"] != self.password:
                return web.json_response(c.GIGYA_LOGIN_SBAGLIATO)
            if self.verifica and not (self.verificato and r.cookies.get("gmid") == "GMID-1"):
                return web.json_response(c.GIGYA_VERIFICA)
            return web.json_response(c.GIGYA_LOGIN)
        if metodo == "getAccountInfo":
            assert modulo["login_token"] == "sample-cookie-value"
            return web.json_response(c.GIGYA_ACCOUNT)
        if metodo == "getJWT":
            if not self.login_token_valido:
                return web.json_response(c.GIGYA_JWT_SCADUTO)
            assert modulo["login_token"] == "sample-cookie-value"
            self.jwt_valido = _jwt(time.time() + self.jwt_durata) + str(len(self.jwt_emessi))
            self.jwt_emessi.append(self.jwt_valido)
            return web.json_response({"errorCode": 0, "id_token": self.jwt_valido})
        if metodo == "webSdkBootstrap":
            risposta = web.json_response({"errorCode": 0})
            risposta.set_cookie("gmid", "GMID-1")
            risposta.set_cookie("ucid", "UCID-1")
            return risposta
        assert r.cookies.get("gmid") == "GMID-1"
        if metodo == "tfa.initTFA":
            assert modulo["regToken"] == "sample-reg-token"
            return web.json_response({"errorCode": 0, "gigyaAssertion": "ASSERZIONE"})
        if metodo == "tfa.email.getEmails":
            return web.json_response({"errorCode": 0, "emails": [{"id": "EMAIL-1", "obfuscated": "e***@example.com"}]})
        if metodo == "tfa.email.sendVerificationCode":
            assert (modulo["emailID"], modulo["lang"]) == ("EMAIL-1", "it")
            return web.json_response({"errorCode": 0, "phvToken": "PHV"})
        if metodo == "tfa.email.completeVerification":
            if modulo["code"] != self.codice:
                return web.json_response(c.GIGYA_CODICE_SBAGLIATO)
            return web.json_response({"errorCode": 0, "providerAssertion": "PROVA"})
        if metodo == "tfa.finalizeTFA":
            assert (modulo["providerAssertion"], modulo["tempDevice"]) == ("PROVA", "false")
            self.verificato = True
            return web.json_response({"errorCode": 0})
        return web.json_response({"errorCode": 400000}, status=400)

    # Kamereon
    async def _kamereon(self, r: web.Request) -> web.Response:
        resto = r.match_info["resto"]
        self.chiamate.append(f"{r.method} {resto}")
        assert r.headers["apikey"] == "CHIAVE-KAMEREON"
        assert r.query["country"] == "IT"
        if r.headers.get("x-gigya-id_token") != self.jwt_valido:
            return web.json_response(c.NON_AUTORIZZATO, status=401)
        coda = resto.split("/kamereon", 1)[-1]
        if coda in self.speciali:
            stato, corpo = self.speciali[coda]
            if isinstance(corpo, str):
                return web.Response(status=stato, text=corpo, content_type="text/html")
            return web.json_response(corpo, status=stato)
        if r.method == "POST":
            self.comandi.append((coda, await r.json()))
            return web.json_response({"data": {"type": "Azione", "id": "guid", "attributes": {}}})
        if resto == "persons/person-id-1":
            return web.json_response(c.PERSONA)
        if resto == "accounts/account-id-1/vehicles":
            return web.json_response(c.AUTO)
        letture = {
            f"/kca/car-adapter/v2/cars/{c.VIN_ZOE}/battery-status": c.dati(c.BATTERIA_IN_CARICA),
            f"/kca/car-adapter/v1/cars/{c.VIN_ZOE}/cockpit": c.dati(c.COCKPIT),
            f"/kca/car-adapter/v1/cars/{c.VIN_ZOE}/hvac-status": c.dati(c.CLIMA),
            f"/kca/car-adapter/v1/cars/{c.VIN_ZOE}/location": c.dati(c.POSIZIONE),
            f"/kca/car-adapter/v1/cars/{c.VIN_ZOE}/pressure": c.dati(c.GOMME),
            f"/kca/car-adapter/v2/cars/{c.VIN_SPRING}/battery-status": c.dati(c.BATTERIA_STACCATA, c.VIN_SPRING),
            f"/kca/car-adapter/v1/cars/{c.VIN_SPRING}/cockpit": c.dati(c.COCKPIT, c.VIN_SPRING),
            f"/kca/car-adapter/v1/cars/{c.VIN_SPRING}/hvac-status": c.dati(c.CLIMA, c.VIN_SPRING),
            f"/kca/car-adapter/v1/cars/{c.VIN_SPRING}/location": c.dati(c.POSIZIONE, c.VIN_SPRING),
            f"/kca/car-adapter/v2/cars/{c.VIN_R5}/battery-status": c.dati(c.BATTERIA_R5, c.VIN_R5),
            f"/kca/car-adapter/v1/cars/{c.VIN_R5}/cockpit": c.dati(c.COCKPIT_R5, c.VIN_R5),
            f"/kca/car-adapter/v1/cars/{c.VIN_R5}/hvac-status": c.dati(c.CLIMA_R5, c.VIN_R5),
            f"/kca/car-adapter/v1/cars/{c.VIN_R5}/location": c.dati(c.POSIZIONE, c.VIN_R5),
            f"/kcm/v1/vehicles/{c.VIN_R5}/ev/soc-levels": c.LIMITI,
            f"/kcm/v1/vehicles/{c.VIN_R5}/ev/settings": c.IMPOSTAZIONI_RICARICA,
        }
        if coda in letture:
            return web.json_response(letture[coda])
        return web.json_response(c.NON_TROVATO, status=404)


@pytest.fixture
async def cloud(socket_enabled: None) -> AsyncGenerator[tuple[FintaRenault, dict[str, str]]]:
    finta = FintaRenault()
    runner = web.AppRunner(finta.app())
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    base = f"http://127.0.0.1:{runner.addresses[0][1]}"
    server = {
        "gigya_url": base,
        "gigya_chiave": "CHIAVE-GIGYA",
        "kamereon_url": base,
        "kamereon_chiave": "CHIAVE-KAMEREON",
    }
    modulo_gigya._IN_SOSPESO.clear()
    yield finta, server
    await runner.cleanup()


def _gigya(s: aiohttp.ClientSession, server: dict[str, str]) -> Gigya:
    return Gigya(s, server["gigya_url"], server["gigya_chiave"])


async def _cliente(s: aiohttp.ClientSession, server: dict[str, str], **kw: Any) -> ClienteRenault:
    token = await entra(_gigya(s, server), "email@email.com", "giusta")
    return ClienteRenault(s, "it_IT", token, server=server, **kw)  # type: ignore[arg-type]


async def test_login_auto_e_stato(cloud) -> None:
    finta, server = cloud
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        token = await entra(_gigya(s, server), "email@email.com", "giusta")
        assert token == {"login_token": "sample-cookie-value"}
        cliente = ClienteRenault(s, "it_IT", token, salvati.append, marchio="dacia", server=server)  # type: ignore[arg-type]
        await cliente.accedi()
        vetture = await cliente.vetture()
        spring, r5, zoe = vetture
        assert (spring.vin, spring.nome, spring.modello, spring.anno) == (c.VIN_SPRING, "Dacia Spring", "Spring", 2021)
        assert spring.extra == {"account": "account-id-1", "modello": "XBG1VE", "energia": "ELEC"}
        assert spring.targa == "REG-NUMBER"
        assert r5.nome == "Renault 5"  # niente «Renault Renault 5»
        assert zoe.titolo == "Renault Zoe"
        stato = await cliente.stato(zoe)
        assert stato.batteria == 60
        assert stato.odometro_km == 49114.27
        assert stato.pneumatici_kpa["anteriore_sinistro"] == 246.0
        stato_spring = await cliente.stato(spring)
    assert stato_spring.batteria == 50
    assert stato_spring.cavo_collegato is False
    # La persona si è chiesta a Gigya una volta e si è salvata col token.
    assert salvati == [{"login_token": "sample-cookie-value", "person_id": "person-id-1"}]
    # L'account SFDC non si interroga; la Spring non ha porte né gomme: non si chiedono.
    assert not any("account-id-2" in x for x in finta.chiamate)
    assert not any(c.VIN_SPRING in x and ("lock-status" in x or "pressure" in x) for x in finta.chiamate)
    assert finta.chiamate.count("gigya:login") == 1
    assert finta.chiamate.count("gigya:getJWT") == 1


async def test_le_letture_lente_e_quelle_che_mancano(cloud) -> None:
    finta, server = cloud
    async with aiohttp.ClientSession() as s:
        cliente = await _cliente(s, server)
        await cliente.accedi()
        zoe = (await cliente.vetture())[2]
        await cliente.stato(zoe)
        await cliente.stato(zoe)
        # Un modello che la tabella non conosce: le letture di base, e la serratura si prova.
        ignota = _vettura("SCONOSCIUTO", c.VIN_SPRING)
        await cliente.stato(ignota)
        await cliente.stato(ignota)
    # I chilometri e le gomme ogni mezz'ora; la batteria ogni volta.
    assert sum(x.endswith(f"{c.VIN_ZOE}/battery-status") for x in finta.chiamate) == 2
    assert sum(x.endswith(f"{c.VIN_ZOE}/cockpit") for x in finta.chiamate) == 1
    assert sum(x.endswith(f"{c.VIN_ZOE}/pressure") for x in finta.chiamate) == 1
    # La serratura non c'è (404): chiesta una volta, poi mai più.
    assert sum(x.endswith(f"{c.VIN_SPRING}/lock-status") for x in finta.chiamate) == 1


async def test_la_verifica_in_due_passaggi(cloud) -> None:
    finta, server = cloud
    finta.verifica = True
    async with aiohttp.ClientSession() as s:
        g = _gigya(s, server)
        with pytest.raises(CodiceInviato, match=r"e\*\*\*@example.com"):
            await entra(g, "email@email.com", "giusta")
        with pytest.raises(CodiceSbagliato):
            await entra(g, "email@email.com", "giusta", codice="000000")
        token = await entra(g, "Email@Email.com", "giusta", codice=" 123456 ")
        assert token == {"login_token": "sample-cookie-value", "gmid": "GMID-1", "ucid": "UCID-1"}
        # Col dispositivo ricordato il codice non serve più.
        assert await entra(g, "email@email.com", "giusta", dispositivo=token) == token
    assert finta.chiamate.count("gigya:tfa.email.sendVerificationCode") == 1


async def test_password_sbagliata_e_token_scaduto(cloud) -> None:
    finta, server = cloud
    async with aiohttp.ClientSession() as s:
        with pytest.raises(ErroreCredenziali, match="403042"):
            await entra(_gigya(s, server), "email@email.com", "sbagliata")
        cliente = await _cliente(s, server)
        finta.login_token_valido = False
        with pytest.raises(ErroreCredenziali, match="403005"):
            await cliente.accedi()


async def test_il_jwt_si_rinnova_dal_login_token(cloud) -> None:
    finta, server = cloud
    finta.jwt_durata = 30  # scade dentro il margine: va rinnovato a ogni richiesta
    async with aiohttp.ClientSession() as s:
        cliente = await _cliente(s, server)
        await cliente.accedi()
        assert len(finta.jwt_emessi) == 2
        finta.jwt_durata = 900
        await cliente.vetture()
        # Kamereon rifiuta il JWT prima del tempo: se ne chiede un altro e si riprova.
        finta.jwt_valido = "un-altro"
        zoe = (await cliente.vetture())[2]
        assert (await cliente.stato(zoe)).batteria == 60
    assert finta.chiamate.count("gigya:login") == 1
    assert len(finta.jwt_emessi) == 4


async def test_guasti_e_quota_non_chiedono_di_rientrare(cloud) -> None:
    finta, server = cloud
    async with aiohttp.ClientSession() as s:
        cliente = await _cliente(s, server)
        await cliente.accedi()
        zoe = (await cliente.vetture())[2]
        batteria = f"/kca/car-adapter/v2/cars/{c.VIN_ZOE}/battery-status"
        finta.speciali[batteria] = (502, c.CATTIVO_GATEWAY)
        with pytest.raises(ErroreRete):
            await cliente.stato(zoe)
        finta.speciali.pop(batteria)
        prima = await cliente.stato(zoe)
        finta.speciali[batteria] = (429, c.QUOTA)
        with pytest.raises(ErroreApi, match=r"quota|richieste"):
            await cliente.stato(zoe)
        letture = len(finta.chiamate)
        # Per un quarto d'ora si tiene l'ultima lettura, senza chiedere niente.
        assert await cliente.stato(zoe) is prima
        assert len(finta.chiamate) == letture
        finta.gigya_rotto = True
        with pytest.raises(ErroreRete):
            await entra(_gigya(s, server), "email@email.com", "giusta")
    async with aiohttp.ClientSession() as s:
        rotto = {**server, "gigya_url": "http://127.0.0.1:9"}
        with pytest.raises(ErroreRete):
            await entra(_gigya(s, rotto), "email@email.com", "giusta")


async def test_i_comandi(cloud) -> None:
    finta, server = cloud
    async with aiohttp.ClientSession() as s:
        cliente = await _cliente(s, server)
        await cliente.accedi()
        spring, r5, zoe = await cliente.vetture()
        await cliente.clima(spring, True, None, 21)
        await cliente.clima(spring, False, None)
        await cliente.clima(zoe, False, None)
        await cliente.ricarica(spring, False, None)
        await cliente.ricarica(zoe, True, None)
        await cliente.ricarica(r5, True, None)
        await cliente.limite_ricarica(r5, 92, None)
        await cliente.trova(r5, None)
        with pytest.raises(ErroreComando, match="non ferma la ricarica"):
            await cliente.ricarica(r5, False, None)
        finta.speciali[f"/kca/car-adapter/v1/cars/{c.VIN_ZOE}/actions/hvac-start"] = (403, c.VIETATO)
        with pytest.raises(ErroreComando, match="non lo permette"):
            await cliente.clima(zoe, True, None)
    base = "/kca/car-adapter/v1/cars"
    impostazioni = json.loads(json.dumps(c.IMPOSTAZIONI_RICARICA))
    impostazioni["programs"][0]["programActivationStatus"] = False
    assert finta.comandi == [
        (
            f"{base}/{c.VIN_SPRING}/actions/hvac-start",
            {"data": {"type": "HvacStart", "attributes": {"action": "start", "targetTemperature": 21.0}}},
        ),
        # La Spring spegne il clima con «stop», la ZOE con «cancel».
        (
            f"{base}/{c.VIN_SPRING}/actions/hvac-start",
            {"data": {"type": "HvacStart", "attributes": {"action": "stop"}}},
        ),
        (f"{base}/{c.VIN_ZOE}/actions/hvac-start", {"data": {"type": "HvacStart", "attributes": {"action": "cancel"}}}),
        # La Spring mette in pausa con KCM.
        (
            f"/kcm/v1/vehicles/{c.VIN_SPRING}/charge/pause-resume",
            {"data": {"type": "ChargePauseResume", "attributes": {"action": "pause"}}},
        ),
        (
            f"{base}/{c.VIN_ZOE}/actions/charging-start",
            {"data": {"type": "ChargingStart", "attributes": {"action": "start"}}},
        ),
        # La Renault 5 carica subito spegnendo i programmi delle impostazioni.
        (f"/kcm/v1/vehicles/{c.VIN_R5}/ev/settings", impostazioni),
        # 92 % diventa 90 (passi di 5); la soglia minima resta quella dell'auto.
        (f"/kcm/v1/vehicles/{c.VIN_R5}/ev/soc-levels", {"socMin": 20, "socTarget": 90}),
        (
            f"{base}/{c.VIN_R5}/actions/horn-lights",
            {"data": {"type": "HornLights", "attributes": {"action": "start", "target": "lights"}}},
        ),
        (
            f"{base}/{c.VIN_R5}/actions/horn-lights",
            {"data": {"type": "HornLights", "attributes": {"action": "start", "target": "horn"}}},
        ),
    ]


# --- dentro Home Assistant ------------------------------------------------------------

SPRING = Vettura(
    vin=c.VIN_SPRING,
    marca="renault",
    modello="Spring",
    nome="Dacia Spring",
    extra={"account": "account-id-1", "modello": "XBG1VE", "energia": "ELEC"},
)
STATO_SPRING = interpreta(
    {"battery-status": c.BATTERIA_IN_CARICA, "hvac-status": c.CLIMA, "location": c.POSIZIONE, "cockpit": c.COCKPIT}
)


async def test_configurare_una_dacia(hass: HomeAssistant) -> None:
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "dacia"})
    assert r["step_id"] == "credenziali"
    assert {str(k) for k in r["data_schema"].schema} == {"email", "password", "paese", "codice"}
    entra_finto = AsyncMock(side_effect=[ErroreCredenziali("no"), {"login_token": "LT", "gmid": "G", "ucid": "U"}])
    accedi = AsyncMock()
    with (
        patch.object(renault, "entra", entra_finto),
        patch.object(renault.ClienteRenault, "accedi", accedi),
        patch.object(renault.ClienteRenault, "vetture", AsyncMock(return_value=[SPRING])),
        patch.object(renault.ClienteRenault, "stato", AsyncMock(return_value=STATO_SPRING)),
    ):
        dati = {"email": "Mario@Esempio.it", "password": "segreta", "paese": "it_IT"}
        r = await hass.config_entries.flow.async_configure(r["flow_id"], dati)
        assert r["errors"] == {"base": "credenziali"}
        r = await hass.config_entries.flow.async_configure(r["flow_id"], dati)
        await hass.async_block_till_done()
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["title"] == "Dacia · Mario@Esempio.it"
    assert r["result"].unique_id == "renault_mario@esempio.it"
    assert r["data"]["marca"] == "renault"
    assert r["data"]["marchio"] == "dacia"
    assert r["data"]["marchio_catalogo"] == "dacia"
    assert r["data"]["token"] == {"login_token": "LT", "gmid": "G", "ucid": "U"}
    # La password non resta da nessuna parte.
    assert "password" not in r["data"]
    assert "segreta" not in json.dumps(r["data"])
    assert entra_finto.await_args.kwargs["lingua"] == "it"
    assert hass.states.get("sensor.dacia_spring_batteria").state == "60"
    voce = r["result"]
    assert voce.runtime_data.auto[0].comandi_possibili == {"clima", "ricarica"}
    # La persona salvata dal cliente aggiorna la voce senza ricaricarla.
    cliente = voce.runtime_data.conto.cliente
    prima = voce.runtime_data
    cliente._salva({"login_token": "LT", "person_id": "P1"})
    await hass.async_block_till_done()
    assert voce.data["token"]["person_id"] == "P1"
    assert voce.runtime_data is prima
