"""Jaguar e Land Rover: le letture di InControl, il cliente contro un cloud finto
(ifas, ifop e if9 su 127.0.0.1) e la configurazione in Home Assistant.

I payload sono in ``jlr_campioni``: chiavi e valori di jlrpy e di
homeassistant-jlrincontrol.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime
import time
from typing import Any
from unittest.mock import patch

import aiohttp
from aiohttp import web
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.gdaauto import config_flow
from custom_components.gdaauto.const import DOMAIN
from custom_components.gdaauto.marche import jlr
from custom_components.gdaauto.marche.catalogo import PER_ID, Marchio
from custom_components.gdaauto.marche.errori import ErroreApi, ErroreComando, ErroreCredenziali, ErrorePin, ErroreRete
from custom_components.gdaauto.marche.jlr import cliente as modulo_cliente
from custom_components.gdaauto.marche.jlr.cliente import ClienteJLR, Indirizzi, comandi_di
from custom_components.gdaauto.marche.jlr.dati import interpreta, km_dei_viaggi, quando
from custom_components.gdaauto.vettura import StatoRicarica, Vettura

from . import jlr_campioni as c

EMAIL = "Mario@Esempio.it"
PASSWORD = "giusta"
PIN = "1234"

# --- le letture --------------------------------------------------------------------------


def test_una_i_pace_in_carica() -> None:
    s = interpreta(c.STATO_IPACE, c.POSIZIONE, elettrica=True)
    assert s.batteria == 64
    assert s.autonomia_km == 251
    assert s.odometro_km == 48123.5
    assert s.stato_ricarica == StatoRicarica.IN_CARICA
    assert s.in_carica is True
    assert s.cavo_collegato is True
    assert s.tempo_ricarica_min == 185
    assert s.limite_ricarica == 80  # quello di questa volta è «CLEAR»: vale quello fisso
    assert s.ultima_ricarica_kwh == 41.2  # decimi di kWh
    assert s.clima_acceso is False
    assert s.quadro_acceso is False
    assert s.chiusa is True
    assert s.portiere_aperte is False
    assert s.bagagliaio_aperto is True
    assert s.finestrini_aperti is False
    # Decimi di kPa sopra 1000.
    assert s.pneumatici_kpa == {
        "anteriore_sinistro": 248.0,
        "anteriore_destro": 247.0,
        "posteriore_sinistro": 251.0,
        "posteriore_destro": 199.0,
    }
    assert s.pneumatici_allarme is True
    assert s.antifurto is True
    assert (s.latitudine, s.longitudine) == (45.46427, 9.18951)
    assert s.posizione_con_segno is True
    assert s.letto == datetime.fromisoformat("2024-03-01T18:12:43+00:00")
    # Un'elettrica non ha serbatoio, anche se il cloud manda «0».
    assert s.carburante_percento is None
    assert s.autonomia_carburante_km is None
    assert s.grezzo["elettrica"]["EV_CHARGING_RATE_SOC_PER_HOUR"] == "12"
    assert "position" not in str(s.grezzo)


def test_una_range_rover_ibrida() -> None:
    s = interpreta(c.STATO_RR, None, elettrica=False)
    assert s.batteria == 38
    assert s.autonomia_km == 40.2  # 25 miglia: i km mancano
    assert s.autonomia_totale_km == 520
    assert s.autonomia_carburante_km == 480
    assert s.carburante_percento == 55
    assert s.odometro_km == 16093.4  # in miglia, perché i metri sono vuoti
    assert s.stato_ricarica == StatoRicarica.NON_IN_CARICA
    assert s.cavo_collegato is False
    assert s.tempo_ricarica_min is None
    assert s.limite_ricarica == 90  # quello di questa volta
    assert s.clima_acceso is True
    assert s.chiusa is False
    assert s.portiere_aperte is True
    assert s.bagagliaio_aperto is False
    assert s.finestrini_aperti is True  # «PARTIAL_OPEN»
    assert s.pneumatici_kpa == {"anteriore_sinistro": 250.0, "anteriore_destro": 251.0}
    assert s.pneumatici_allarme is False
    assert s.antifurto is False
    assert s.latitudine is None
    assert s.letto is not None and s.letto.year == 2024 and s.letto.utcoffset() is not None


def test_il_formato_vecchio_i_viaggi_e_le_date() -> None:
    s = interpreta(c.STATO_V3)
    assert (s.batteria, s.odometro_km, s.chiusa) == (90, 1.0, True)
    assert interpreta({}).batteria is None
    assert interpreta(None, {"position": {"latitude": 0, "longitude": 0}}).latitudine is None
    assert km_dei_viaggi(c.VIAGGI, datetime.fromisoformat(c.VIAGGI_DAL)) == 44.5
    assert km_dei_viaggi({}, datetime.fromisoformat(c.VIAGGI_DAL)) is None
    assert quando("2024-03-02T07:00:01.000Z") == datetime.fromisoformat("2024-03-02T07:00:01+00:00")
    assert quando("2024-03-02T07:00:01") is not None
    assert quando("ieri") is None
    # Valori fuori misura: «non lo so», non numeri inventati.
    strano = {"vehicleStatus": {"evStatus": [{"key": "EV_STATE_OF_CHARGE", "value": "255"}], "coreStatus": []}}
    assert interpreta(strano).batteria is None


def _vettura(vin: str = c.VIN_IPACE, **extra: Any) -> Vettura:
    return Vettura(vin=vin, marca="jlr", modello="I-PACE", nome="Jaguar I-PACE", extra=extra)


def test_i_comandi_secondo_i_servizi() -> None:
    tutti = {"chiudi", "apri", "trova", "clima", "ricarica", "limite_ricarica"}
    assert comandi_di(_vettura(servizi=["RDL", "RDU", "HBLF", "ECC", "CP", "VHS"], carburante="Electric")) == tutti
    assert comandi_di(_vettura(servizi=["RDL", "CP"], carburante="Petrol", trazione="PHEV")) == {
        "chiudi",
        "ricarica",
        "limite_ricarica",
    }
    # Una benzina senza batteria non carica.
    assert comandi_di(_vettura(servizi=["RDL", "CP"], carburante="Diesel", trazione="ICE")) == {"chiudi"}
    assert comandi_di(_vettura()) == set()


# --- il cloud finto ----------------------------------------------------------------------


@dataclass
class FintoJLR:
    password: str = "giusta"
    pin: str = PIN
    durata: int = 86400
    emessi: int = 0
    valido: str = ""
    registrati: set[str] = field(default_factory=set)
    chiamate: list[str] = field(default_factory=list)
    concessi: list[dict[str, Any]] = field(default_factory=list)
    comandi: list[tuple[str, dict[str, Any], str]] = field(default_factory=list)
    #: gli esiti che il cloud dà, uno per lettura della pratica
    esiti: list[str] = field(default_factory=lambda: ["Running", "Successful"])
    rinnovo_valido: bool = True
    #: risposte particolari: percorso → (stato, corpo)
    speciali: dict[str, tuple[int, Any]] = field(default_factory=dict)
    solo_secondario: bool = False

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/ifas/jlr/tokens/tokensSSO", self._token)
        app.router.add_post("/ifop/jlr/users/{email}/clients", self._dispositivo)
        app.router.add_route("*", "/if9/jlr/{resto:.*}", self._if9)
        return app

    def _speciale(self, percorso: str) -> web.Response | None:
        if percorso not in self.speciali:
            return None
        stato, corpo = self.speciali[percorso]
        if isinstance(corpo, str):
            return web.Response(status=stato, text=corpo, content_type="text/html")
        return web.json_response(corpo, status=stato)

    async def _token(self, r: web.Request) -> web.Response:
        self.chiamate.append("token")
        if (speciale := self._speciale("token")) is not None:
            return speciale
        assert r.headers["Authorization"] == "Basic YXM6YXNwYXNz"
        corpo = await r.json()
        self.concessi.append(corpo)
        if corpo["grant_type"] == "password":
            if (corpo["username"], corpo["password"]) != (EMAIL, self.password):
                return web.json_response(c.TOKEN_SBAGLIATO, status=403)
        elif corpo["grant_type"] == "refresh_token":
            if not self.rinnovo_valido or not corpo["refresh_token"].startswith("RINNOVO-"):
                return web.json_response({"error": "invalid_grant", "error_description": "expired"}, status=400)
        else:
            return web.json_response({"error": "unsupported_grant_type"}, status=400)
        self.emessi += 1
        self.valido = f"ACCESSO-{self.emessi}"
        return web.json_response(
            {
                **c.TOKEN,
                "access_token": self.valido,
                "refresh_token": f"RINNOVO-{self.emessi}",
                "expires_in": str(self.durata),
            }
        )

    async def _dispositivo(self, r: web.Request) -> web.Response:
        self.chiamate.append("dispositivo")
        assert r.match_info["email"] == EMAIL
        corpo = await r.json()
        assert corpo["access_token"] == self.valido
        assert corpo["authorization_token"] == "AUTORIZZAZIONE-1"
        assert corpo["deviceID"] == r.headers["X-Device-Id"]
        self.registrati.add(corpo["access_token"])
        return web.Response(status=204)

    async def _if9(self, r: web.Request) -> web.Response:
        resto = r.match_info["resto"]
        self.chiamate.append(f"{r.method} {resto}")
        assert r.headers["x-App-Id"] == "ICR_JAGUAR_ANDROID"
        assert r.headers["X-Device-Id"]
        token = r.headers.get("Authorization", "").removeprefix("Bearer ")
        if token != self.valido or token not in self.registrati:
            return web.json_response({"errorLabel": "Unauthorized"}, status=401)
        if (speciale := self._speciale(resto)) is not None:
            return speciale
        if resto == "users":
            assert r.query["loginName"] == EMAIL
            assert r.headers["Accept"].endswith("User-v3+json")
            return web.json_response(c.UTENTE)
        if resto == f"users/{c.ID_UTENTE}/vehicles":
            if self.solo_secondario and r.query["primaryOnly"] == "true":
                return web.json_response({"vehicles": []})
            return web.json_response(c.AUTO)
        vin, _, coda = resto.removeprefix("vehicles/").partition("/")
        letture = {
            (c.VIN_IPACE, "attributes"): c.ATTRIBUTI_IPACE,
            (c.VIN_RR, "attributes"): c.ATTRIBUTI_RR,
            (c.VIN_IPACE, "status"): c.STATO_IPACE,
            (c.VIN_RR, "status"): c.STATO_RR,
            (c.VIN_IPACE, "position"): c.POSIZIONE,
            (c.VIN_IPACE, "trips"): c.VIAGGI,
        }
        if r.method == "GET" and (vin, coda) in letture:
            if coda == "status":
                assert r.headers["Accept"].endswith("healthstatus-v4+json")
                assert r.query["includeInactive"] == "true"
            return web.json_response(letture[(vin, coda)])
        if r.method == "POST" and coda == f"users/{c.ID_UTENTE}/authenticate":
            corpo = await r.json()
            assert r.headers["Content-Type"].startswith("application/vnd.wirelesscar.ngtp.if9.AuthenticateRequest-v2")
            atteso = self.pin if corpo["serviceName"] in ("RDL", "RDU") else vin[-4:]
            if corpo["pin"] != atteso:
                return web.json_response({"errorLabel": "InvalidPin", "errorDescription": "PIN errato"}, status=401)
            return web.json_response({"token": f"SERVIZIO-{corpo['serviceName']}"})
        if r.method == "POST":
            corpo = await r.json()
            self.comandi.append((coda, corpo, r.headers["Content-Type"]))
            return web.json_response(c.esito("Started", coda))
        if r.method == "GET" and coda.startswith("services/"):
            return web.json_response(c.esito(self.esiti.pop(0) if self.esiti else "Successful", "X"))
        return web.json_response({"errorLabel": "NotFound"}, status=404)


@pytest.fixture
async def cloud(socket_enabled: None) -> AsyncGenerator[tuple[FintoJLR, Indirizzi]]:
    finto = FintoJLR()
    runner = web.AppRunner(finto.app())
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    base = f"http://127.0.0.1:{runner.addresses[0][1]}"
    yield finto, Indirizzi(ifas=f"{base}/ifas/jlr", ifop=f"{base}/ifop/jlr", if9=f"{base}/if9/jlr")
    await runner.cleanup()


def _cliente(s: aiohttp.ClientSession, indirizzi: Indirizzi, **kw: Any) -> ClienteJLR:
    kw.setdefault("passo_comando", 0.0)
    return ClienteJLR(s, EMAIL, kw.pop("password", PASSWORD), indirizzi=indirizzi, **kw)


async def test_login_auto_e_stato(cloud) -> None:
    finto, indirizzi = cloud
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        cliente = _cliente(s, indirizzi, dispositivo="TELEFONO-1", salva=salvati.append)
        await cliente.accedi()
        ipace, rr = await cliente.vetture()
        assert (ipace.vin, ipace.nome, ipace.modello, ipace.anno, ipace.targa) == (
            c.VIN_IPACE,
            "Jaguar I-PACE",
            "I-PACE",
            2019,
            "GA123BC",
        )
        assert ipace.marca == "jlr"
        assert ipace.colore == "Firenze Red"
        assert ipace.condivisa is False
        assert ipace.extra["servizi"] == ["RDL", "RDU", "HBLF", "ECC", "CP", "VHS", "ALOFF"]  # GMCC è spento
        assert comandi_di(ipace) == {"chiudi", "apri", "trova", "clima", "ricarica", "limite_ricarica"}
        assert rr.nome == "La Range di Mario"
        assert rr.extra["trazione"] == "PHEV"
        stato = await cliente.stato(ipace)
        assert stato.batteria == 64
        assert stato.latitudine == 45.46427
        assert stato.km_7_giorni is not None
        stato_rr = await cliente.stato(rr)  # niente posizione né viaggi: lo stato arriva lo stesso
        assert stato_rr.autonomia_totale_km == 520
        assert stato_rr.latitudine is None
        await cliente.stato(ipace)
    # Un login, un dispositivo registrato, l'utente chiesto una volta sola.
    assert finto.chiamate.count("token") == 1
    assert finto.chiamate.count("dispositivo") == 1
    assert finto.chiamate.count("GET users") == 1
    assert cliente.token["id_utente"] == c.ID_UTENTE
    assert salvati[-1]["access_token"] == "ACCESSO-1"
    assert salvati[-1]["refresh_token"] == "RINNOVO-1"
    assert salvati[-1]["scade"] > time.time() + 80000
    # I viaggi si rileggono ogni mezz'ora, non a ogni stato.
    assert sum(x.endswith(f"{c.VIN_IPACE}/trips") for x in finto.chiamate) == 1
    # Mai la richiesta che sveglia l'auto.
    assert not any("healthstatus" in x for x in finto.chiamate)


async def test_un_utente_secondario_vede_le_auto_condivise(cloud) -> None:
    finto, indirizzi = cloud
    finto.solo_secondario = True
    async with aiohttp.ClientSession() as s:
        vetture = await _cliente(s, indirizzi).vetture()
    assert [v.vin for v in vetture] == [c.VIN_IPACE, c.VIN_RR]


async def test_il_token_si_rinnova(cloud) -> None:
    finto, indirizzi = cloud
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        cliente = _cliente(s, indirizzi, salva=salvati.append)
        await cliente.accedi()
        # Scaduto: si rinnova col refresh token, e il token nuovo si registra di nuovo.
        cliente.token["scade"] = time.time() - 1
        await cliente.vetture()
        assert finto.concessi[-1] == {"grant_type": "refresh_token", "refresh_token": "RINNOVO-1"}
        assert salvati[-1]["access_token"] == "ACCESSO-2"
        assert salvati[-1]["id_utente"] == c.ID_UTENTE
        # Il cloud lo rifiuta prima del tempo: se ne chiede un altro e si riprova.
        finto.valido = "UN-ALTRO"
        assert (await cliente.stato((await cliente.vetture())[0])).batteria == 64
        assert salvati[-1]["access_token"] == "ACCESSO-3"
        # Il refresh token non va più: si rientra con la password.
        finto.rinnovo_valido = False
        cliente.token["scade"] = 0
        await cliente.accedi()
        assert finto.concessi[-1]["grant_type"] == "password"
        assert salvati[-1]["access_token"] == "ACCESSO-4"
    assert finto.chiamate.count("dispositivo") == 4
    assert finto.chiamate.count("GET users") == 1


async def test_da_un_token_salvato_senza_password(cloud) -> None:
    finto, indirizzi = cloud
    async with aiohttp.ClientSession() as s:
        primo = _cliente(s, indirizzi, dispositivo="TELEFONO-1")
        await primo.accedi()
        # Riparte dai token salvati: nessun login.
        secondo = _cliente(s, indirizzi, dispositivo="TELEFONO-1", token=primo.token, password="")
        await secondo.vetture()
        assert finto.chiamate.count("token") == 1
        # Rinnovo rifiutato e niente password: va rifatto l'accesso.
        finto.rinnovo_valido = False
        secondo.token["scade"] = 0
        with pytest.raises(ErroreCredenziali, match="password"):
            await secondo.accedi()


async def test_credenziali_e_guasti_non_si_confondono(cloud) -> None:
    finto, indirizzi = cloud
    async with aiohttp.ClientSession() as s:
        with pytest.raises(ErroreCredenziali, match="Bad credentials"):
            await _cliente(s, indirizzi, password="sbagliata").accedi()
        finto.speciali["token"] = (503, {"error": "unavailable"})
        with pytest.raises(ErroreRete):
            await _cliente(s, indirizzi).accedi()
        # Una pagina HTML del muro davanti al cloud non è una password sbagliata.
        finto.speciali["token"] = (403, "<html><body>Access Denied</body></html>")
        with pytest.raises(ErroreApi, match="blocca") as preso:
            await _cliente(s, indirizzi).accedi()
        assert not isinstance(preso.value, ErroreCredenziali)
        finto.speciali["token"] = (429, {"error": "too_many_requests"})
        with pytest.raises(ErroreApi, match="troppe"):
            await _cliente(s, indirizzi).accedi()
        finto.speciali.pop("token")
        cliente = _cliente(s, indirizzi)
        ipace = (await cliente.vetture())[0]
        finto.speciali[f"vehicles/{c.VIN_IPACE}/status"] = (502, "<html>Bad Gateway</html>")
        with pytest.raises(ErroreRete):
            await cliente.stato(ipace)
        finto.speciali[f"vehicles/{c.VIN_IPACE}/status"] = (200, "<html>non è json</html>")
        with pytest.raises(ErroreRete):
            await cliente.stato(ipace)
    rotto = Indirizzi(ifas="http://127.0.0.1:9/ifas", ifop="http://127.0.0.1:9/ifop", if9="http://127.0.0.1:9/if9")
    async with aiohttp.ClientSession() as s:
        with pytest.raises(ErroreRete):
            await _cliente(s, rotto).accedi()


async def test_i_comandi(cloud) -> None:
    finto, indirizzi = cloud
    async with aiohttp.ClientSession() as s:
        cliente = _cliente(s, indirizzi)
        ipace, _rr = await cliente.vetture()
        with pytest.raises(ErrorePin, match="PIN"):
            await cliente.chiudi(ipace, None)
        with pytest.raises(ErrorePin):
            await cliente.apri(ipace, "0000")
        await cliente.chiudi(ipace, PIN)
        assert finto.chiamate[-3:] == [
            f"POST vehicles/{c.VIN_IPACE}/lock",
            f"GET vehicles/{c.VIN_IPACE}/services/PRATICA-1",
            f"GET vehicles/{c.VIN_IPACE}/services/PRATICA-1",
        ]
        await cliente.apri(ipace, PIN)
        await cliente.trova(ipace, None)
        await cliente.clima(ipace, True, None, 21)
        await cliente.clima(ipace, False, None)
        await cliente.ricarica(ipace, True, None)
        await cliente.ricarica(ipace, False, None)
        await cliente.limite_ricarica(ipace, 85, None)
        finto.esiti = ["Failed"]
        with pytest.raises(ErroreComando, match="non ha eseguito"):
            await cliente.trova(ipace, None)
        with pytest.raises(ErroreComando, match="destinazioni"):
            await cliente.destinazione(ipace, "Casa", 45.0, 9.0)
        # Un servizio che l'auto non ha non si chiede nemmeno.
        senza = _vettura(servizi=["RDL"])
        with pytest.raises(ErroreComando, match="ECC"):
            await cliente.clima(senza, True, None)
    phev = "application/vnd.wirelesscar.ngtp.if9.PhevService-v1+json; charset=utf-8"
    avvio = "application/vnd.wirelesscar.ngtp.if9.StartServiceConfiguration-v3+json"
    assert finto.comandi[:8] == [
        ("lock", {"token": "SERVIZIO-RDL"}, avvio),
        ("unlock", {"token": "SERVIZIO-RDU"}, avvio),
        ("honkBlink", {"token": "SERVIZIO-HBLF"}, avvio + "; charset=utf-8"),
        (
            "preconditioning",
            {
                "token": "SERVIZIO-ECC",
                "serviceParameters": [
                    {"key": "PRECONDITIONING", "value": "START"},
                    {"key": "TARGET_TEMPERATURE_CELSIUS", "value": "210"},
                ],
            },
            phev,
        ),
        (
            "preconditioning",
            {"token": "SERVIZIO-ECC", "serviceParameters": [{"key": "PRECONDITIONING", "value": "STOP"}]},
            phev,
        ),
        (
            "chargeProfile",
            {"token": "SERVIZIO-CP", "serviceParameters": [{"key": "CHARGE_NOW_SETTING", "value": "FORCE_ON"}]},
            phev,
        ),
        (
            "chargeProfile",
            {"token": "SERVIZIO-CP", "serviceParameters": [{"key": "CHARGE_NOW_SETTING", "value": "FORCE_OFF"}]},
            phev,
        ),
        (
            "chargeProfile",
            {"token": "SERVIZIO-CP", "serviceParameters": [{"key": "SET_PERMANENT_MAX_SOC", "value": 85}]},
            phev,
        ),
    ]


async def test_un_comando_lungo_non_blocca_per_sempre(cloud) -> None:
    finto, indirizzi = cloud
    finto.esiti = ["Running"] * 100
    async with aiohttp.ClientSession() as s:
        cliente = _cliente(s, indirizzi, attesa_comando=0.05, passo_comando=0.01)
        ipace = (await cliente.vetture())[0]
        await cliente.ricarica(ipace, True, None)  # ancora in corso: si smette di aspettare, senza errore
    assert len(finto.comandi) == 1


# --- dentro Home Assistant -------------------------------------------------------------------


def _catalogo_con_jlr() -> dict[str, Marchio]:
    """I marchi del catalogo puntati a questa marca, finché ``catalogo.py`` non lo fa da sé."""
    per_id = dict(PER_ID)
    for marchio, nome in (("jaguar", "Jaguar"), ("land_rover", "Land Rover")):
        vecchio = per_id.get(marchio)
        per_id[marchio] = Marchio(
            marchio,
            nome,
            "jlr",
            {"marchio": marchio},
            vecchio.ha if vecchio else "Jaguar Land Rover InControl (HACS)",
            vecchio.link if vecchio else "https://github.com/msp1974/homeassistant-jlrincontrol",
        )
    return per_id


@pytest.mark.parametrize(
    ("marchio", "titolo", "entita"),
    [
        ("jaguar", f"Jaguar · {EMAIL}", "sensor.jaguar_i_pace_batteria"),
        ("land_rover", f"Land Rover · {EMAIL}", "sensor.jaguar_i_pace_batteria"),
    ],
)
async def test_configurare_una_jlr(hass: HomeAssistant, cloud, marchio: str, titolo: str, entita: str) -> None:
    finto, indirizzi = cloud
    with (
        patch.object(config_flow, "PER_ID", _catalogo_con_jlr()),
        patch.dict(modulo_cliente.REGIONI, {"mondo": indirizzi}),
    ):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": marchio})
        assert r["step_id"] == "credenziali"
        assert {str(k) for k in r["data_schema"].schema} == {"email", "password", "regione", "pin"}
        dati = {"email": f" {EMAIL} ", "password": "sbagliata", "regione": "mondo"}
        r = await hass.config_entries.flow.async_configure(r["flow_id"], dati)
        assert r["errors"] == {"base": "credenziali"}
        finto.speciali["token"] = (503, {"error": "unavailable"})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {**dati, "password": PASSWORD})
        assert r["errors"] == {"base": "rete"}
        finto.speciali.pop("token")
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {**dati, "password": PASSWORD, "pin": PIN})
        await hass.async_block_till_done()
        assert r["type"] is FlowResultType.CREATE_ENTRY
        assert r["title"] == titolo
        voce = r["result"]
        assert voce.unique_id == f"jlr_{EMAIL.lower()}"
        assert r["data"]["marca"] == "jlr"
        assert r["data"]["marchio"] == marchio
        assert r["data"]["marchio_catalogo"] == marchio
        assert r["data"]["email"] == EMAIL
        assert r["data"]["id_dispositivo"]
        assert r["data"]["token"]["id_utente"] == c.ID_UTENTE
        assert "pin" not in r["data"]
        assert voce.options["pin"] == PIN
        assert hass.states.get(entita).state == "64"
        assert hass.states.get("lock.jaguar_i_pace_serratura") is not None
        assert hass.states.get("sensor.la_range_di_mario_batteria").state == "38"
        auto = voce.runtime_data.auto[0]
        assert auto.comandi_possibili == {"chiudi", "apri", "trova", "clima", "ricarica", "limite_ricarica"}
        # Il token che ruota si salva nella voce senza ricaricarla.
        prima = voce.runtime_data
        cliente = prima.conto.cliente
        assert cliente.dispositivo == r["data"]["id_dispositivo"]
        cliente.token["scade"] = 0
        await cliente.accedi()
        await hass.async_block_till_done()
        assert voce.data["token"]["access_token"] == cliente.token["access_token"]
        assert voce.data["token"]["refresh_token"].startswith("RINNOVO-")
        assert voce.runtime_data is prima
        # Il comando chiudi passa dal coordinatore col PIN delle opzioni.
        await auto.async_comanda("chiudi")
        assert finto.comandi[-1][0] == "lock"
        await hass.config_entries.async_unload(voce.entry_id)


def test_il_contratto() -> None:
    assert jlr.ID == "jlr"
    assert jlr.VUOLE_PIN is True
    assert jlr.titolo({"marchio": "land_rover"}, "a@b.it") == "Land Rover · a@b.it"
    assert jlr.titolo({}, "a@b.it") == "Jaguar Land Rover · a@b.it"
