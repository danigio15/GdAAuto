"""Škoda: lo stato dei payload, il login col PKCE e il cliente contro un cloud finto, e il flusso in HA."""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator, Iterator
import copy
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
import hashlib
import json
import time
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qsl, urlencode, urlsplit

import aiohttp
from aiohttp import web
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gdaauto.const import CONF_FONTE, CONF_MARCA, CONF_MARCHIO, DOMAIN, FONTE_CLOUD
from custom_components.gdaauto.marche import MARCHE
from custom_components.gdaauto.marche.base import ACCESSO_CODICE_BROWSER, Dispositivo
from custom_components.gdaauto.marche.catalogo import PER_ID
from custom_components.gdaauto.marche.errori import ErroreComando, ErroreCredenziali, ErrorePin, ErroreRete
from custom_components.gdaauto.marche.skoda import (
    CAMPI,
    VUOLE_PIN,
    ClienteSkoda,
    accesso,
    avvia_browser,
    cliente as modulo_cliente,
    completa_browser,
)
from custom_components.gdaauto.marche.skoda.accesso import (
    CLIENT_ID,
    CONDIZIONI,
    MARKETING,
    TokenSkoda,
    codice_da,
    coppia_pkce,
    dal_jwt,
    indirizzo_login,
    sfida_di,
)
from custom_components.gdaauto.marche.skoda.cliente import comandi_di, pezzi_di, vettura_da_json
from custom_components.gdaauto.marche.skoda.dati import (
    consumo_medio,
    data_cloud,
    interpreta,
    posizione,
    ultima_ricarica,
    ultima_ricarica_storica,
    ultimi_viaggi,
    verifica_spin,
)
from custom_components.gdaauto.vettura import StatoRicarica, StatoVettura, Vettura

from .skoda_campioni import (
    ENYAQ,
    ENYAQ_APERTA,
    GARAGE,
    INFO,
    SALUTE_ENYAQ,
    SPIN_GIUSTO,
    SPIN_SBAGLIATO,
    STATISTICHE_RICARICA,
    STORICO_RICARICA,
    SUPERB,
    UTENTE,
    VIAGGI_SETTIMANA,
    VIAGGI_SINGOLI,
    VIN_ENYAQ,
    VIN_OCTAVIA,
    VIN_SUPERB,
)

# --- il contratto e l'anagrafica -------------------------------------------------------


def test_il_contratto() -> None:
    modulo = MARCHE["skoda"]
    assert modulo.ACCESSO == ACCESSO_CODICE_BROWSER
    assert [c.nome for c in CAMPI] == ["pin"] and CAMPI[0].segreto and not CAMPI[0].obbligatorio
    assert VUOLE_PIN is True
    assert modulo.INTERVALLO_MINUTI == 15 and modulo.INTERVALLO_IN_MOTO_SECONDI == 300
    assert set(modulo.NOTA) == {"it", "en"} and "myskoda://" in modulo.NOTA["it"]
    assert PER_ID["skoda"].nome == "Škoda"


def _vetture() -> list[Vettura]:
    trovate = [vettura_da_json(voce, INFO[voce["vin"]]) for voce in GARAGE["vehicles"]]
    return [v for v in trovate if v is not None]


def test_l_anagrafica_e_i_comandi() -> None:
    enyaq, superb = _vetture()  # l'Octavia a benzina non c'è
    assert (enyaq.vin, enyaq.nome, enyaq.modello, enyaq.marca, enyaq.anno, enyaq.targa, enyaq.condivisa) == (
        VIN_ENYAQ,
        "Fulmine",
        "Enyaq",
        "skoda",
        2026,
        "AB 123 CD",
        False,
    )
    assert enyaq.extra["capacita_kwh"] == 77
    # Il servizio spento («INSUFFICIENT_RIGHTS») non conta.
    assert "PLUG_AND_CHARGE" not in enyaq.extra["servizi"]
    assert (superb.nome, superb.modello, superb.condivisa) == ("Superb", "Superb", True)
    assert "capacita_kwh" not in superb.extra
    assert comandi_di(enyaq) == {"chiudi", "trova", "clima", "ricarica", "limite_ricarica"}
    # Una plug-in MQB senza impostazioni estese non cambia il limite.
    assert comandi_di(superb) == {"chiudi", "trova", "clima", "ricarica"}
    assert comandi_di(Vettura(vin="X", marca="skoda", modello="")) == {"clima", "ricarica", "limite_ricarica"}
    assert [n for n, _ in pezzi_di(enyaq)] == [
        "ricarica",
        "stato",
        "autonomia",
        "clima",
        "connessione",
        "posizione",
        "manutenzione",
    ]
    assert [n for n, _ in pezzi_di(superb)] == ["ricarica", "stato", "autonomia", "clima", "posizione", "manutenzione"]
    assert dict(pezzi_di(enyaq))["posizione"] == f"/v1/maps/positions?vin={VIN_ENYAQ}"
    # Senza i servizi (la risposta col 429 dentro) l'auto entra lo stesso, coi pezzi di base.
    con_errore = {**INFO[VIN_OCTAVIA], "capabilities": {"capabilities": [{"id": "ACCESS"}], "errors": [{"x": 1}]}}
    ignota = vettura_da_json(GARAGE["vehicles"][2], con_errore)
    assert ignota is not None and "servizi" not in ignota.extra
    assert "connessione" not in dict(pezzi_di(ignota))
    assert vettura_da_json({"vin": ""}) is None
    # Solo la voce del garage: il titolo diventa il modello.
    sola = vettura_da_json({"vin": "TMBX", "title": "Škoda Elroq"})
    assert sola is not None and (sola.modello, sola.nome) == ("Elroq", "Elroq")


# --- lo stato dei payload ---------------------------------------------------------------


def test_una_enyaq_in_carica_alla_colonnina() -> None:
    s = interpreta({**ENYAQ, "salute": SALUTE_ENYAQ}, 77)
    assert (s.batteria, s.autonomia_km, s.odometro_km) == (63, 252, 5453)
    assert s.capacita_kwh == 77
    assert s.stato_ricarica == StatoRicarica.IN_CARICA and s.in_carica
    assert s.ricarica_rapida is True
    assert s.potenza_ricarica_kw == 87.0
    assert s.tempo_ricarica_min == 25
    assert s.limite_ricarica == 80
    assert s.cavo_collegato is True
    assert s.ricarica_programmata is False
    assert s.v2l_attivo is None
    assert (s.clima_acceso, s.temperatura_clima_c, s.sbrinamento) == (False, 21.5, False)
    assert (s.chiusa, s.portiere_aperte, s.bagagliaio_aperto, s.finestrini_aperti) == (True, False, False, False)
    assert s.temperatura_esterna_c == 10.5
    assert (s.quadro_acceso, s.in_sonno) == (False, False)
    assert s.pneumatici_allarme is True and s.pneumatici_kpa == {}
    assert s.letto == datetime(2025, 4, 25, 8, 46, 14, 625000, tzinfo=UTC)
    # Un'elettrica non ha serbatoio.
    assert (s.carburante_percento, s.autonomia_carburante_km, s.autonomia_totale_km) == (None, None, None)
    # Nel grezzo il tagliando, ma non i contatti né il disegno né la posizione.
    assert s.grezzo["tagliando"]["inspectionDueInDays"] == 722
    assert "posizione" not in s.grezzo and "renders" not in s.grezzo["stato"]
    assert "user@example.com" not in json.dumps(s.grezzo)


def test_una_superb_ibrida_col_cavo_staccato() -> None:
    s = interpreta(SUPERB)
    # La carica viene dalla ricarica; l'autonomia elettrica dal motore elettrico (il secondo).
    assert (s.batteria, s.autonomia_km) == (92, 118)
    assert (s.carburante_percento, s.autonomia_carburante_km, s.autonomia_totale_km) == (64, 460, 578)
    assert s.stato_ricarica == StatoRicarica.NON_IN_CARICA
    assert s.cavo_collegato is False
    assert s.potenza_ricarica_kw == 0.0 and s.tempo_ricarica_min is None
    assert s.limite_ricarica == 80
    assert s.sbrinamento is False
    assert s.odometro_km == 2105
    assert s.temperatura_esterna_c == 19.0
    # Le gomme e la connessione non sono state lette: non si sa.
    assert (s.pneumatici_allarme, s.quadro_acceso, s.in_sonno) == (None, None, None)
    assert posizione(SUPERB["posizione"]) is None
    assert s.letto == datetime(2025, 8, 27, 15, 12, 41, tzinfo=UTC)


def test_una_enyaq_aperta_a_pieno_e_addormentata() -> None:
    s = interpreta(ENYAQ_APERTA)
    # Collegata, ferma, e al limite: ha finito (anche col modo a orario).
    assert s.stato_ricarica == StatoRicarica.COMPLETATA
    assert s.ricarica_programmata is True
    assert s.cavo_collegato is True
    # L'autonomia dai metri, senza la sezione dell'autonomia.
    assert (s.batteria, s.autonomia_km) == (70, 261)
    assert (s.chiusa, s.portiere_aperte, s.bagagliaio_aperto, s.finestrini_aperti) == (False, True, True, False)
    # Le temperature in Fahrenheit diventano Celsius.
    assert (s.clima_acceso, s.temperatura_clima_c, s.temperatura_esterna_c) == (True, 22.2, 2.0)
    assert s.sbrinamento is True
    assert s.in_sonno is True and s.quadro_acceso is None
    # Programmata e non ancora al limite: aspetta l'orario.
    sotto = copy.deepcopy(ENYAQ_APERTA)
    sotto["ricarica"]["status"]["battery"]["stateOfChargeInPercent"] = 50
    assert interpreta(sotto).stato_ricarica == StatoRicarica.PROGRAMMATA
    sotto["ricarica"]["settings"]["preferredChargeMode"] = "MANUAL"
    assert interpreta(sotto).stato_ricarica == StatoRicarica.NON_IN_CARICA


def test_il_bagagliaio_da_solo_non_e_una_portiera() -> None:
    d = copy.deepcopy(ENYAQ)
    d["stato"]["overall"]["doors"] = "OPEN"
    d["stato"]["detail"]["trunk"] = "OPEN"
    s = interpreta(d)
    assert (s.portiere_aperte, s.bagagliaio_aperto) == (False, True)
    # Senza il disegno resta il riassunto.
    del d["stato"]["renders"]
    assert interpreta(d).portiere_aperte is True


def test_i_valori_che_mancano_o_sono_null() -> None:
    s = interpreta({})
    assert s == StatoVettura(grezzo={})
    vuoti = {
        "ricarica": {"status": None, "settings": None},
        "stato": {"overall": None, "detail": None, "renders": None},
        "autonomia": {"primaryEngineRange": None, "secondaryEngineRange": None},
        "clima": {"state": "INVALID", "targetTemperature": None, "windowHeatingState": None},
        "connessione": {"unreachable": None},
        "manutenzione": {"maintenanceReport": None},
        "salute": {"warningLights": None, "mileageInKm": 429496729},
        "posizione": None,
    }
    s = interpreta(vuoti)
    assert (s.batteria, s.stato_ricarica, s.chiusa, s.clima_acceso, s.odometro_km) == (None, None, None, None, None)
    # Lo stato «sconosciuto» dell'app non diventa «chiuso» né «aperto».
    sconosciuto = {
        "stato": {
            "overall": {"doorsLocked": "UNKNOWN", "doors": "UNKNOWN", "windows": "UNKNOWN"},
            "detail": {"trunk": "UNKNOWN", "sunroof": "UNSUPPORTED"},
            "renders": {"lightMode": {"oneX": "https://x/render?vehicleState=9-9-9-9-0-0-3-3-0"}},
        }
    }
    s = interpreta(sconosciuto)
    assert (s.chiusa, s.portiere_aperte, s.bagagliaio_aperto, s.finestrini_aperti) == (None, None, None, None)


def test_le_letture_lente() -> None:
    assert consumo_medio(VIAGGI_SETTIMANA) == 20.1
    assert consumo_medio({"overallAverageElectricConsumption": 0}) is None and consumo_medio(None) is None
    assert ultimi_viaggi(VIAGGI_SINGOLI) == (97.0, 16.17)
    assert ultimi_viaggi({"dailyTrips": []}) == (0.0, 0.0)
    assert ultimi_viaggi(None) == (None, None)
    # L'ultima finita (quella in corso no), con gli orari del CSV.
    assert ultima_ricarica(STATISTICHE_RICARICA) == {
        "ultima_ricarica_kwh": 17.0,
        "ultima_ricarica_tipo": "ac",
        "ultima_ricarica_inizio": datetime(2026, 6, 10, 5, 14, 55, tzinfo=UTC),
        "ultima_ricarica_fine": datetime(2026, 6, 10, 13, 31, 51, tzinfo=UTC),
    }
    senza_csv = {**STATISTICHE_RICARICA, "csvFile": None}
    assert ultima_ricarica(senza_csv) == {"ultima_ricarica_kwh": 17.0, "ultima_ricarica_tipo": "ac"}
    assert ultima_ricarica({"monthSections": []}) == {} and ultima_ricarica(None) == {}
    assert ultima_ricarica_storica(STORICO_RICARICA) == {
        "ultima_ricarica_inizio": datetime(2025, 9, 9, 16, 49, 38, tzinfo=UTC),
        "ultima_ricarica_kwh": 27.0,
        "ultima_ricarica_tipo": "dc",
        "ultima_ricarica_fine": datetime(2025, 9, 9, 17, 11, 38, tzinfo=UTC),
    }
    assert verifica_spin(SPIN_GIUSTO) == (True, None)
    assert verifica_spin(SPIN_SBAGLIATO) == (False, 2)
    assert data_cloud("2025-01-04T11:24:40.843030158Z") == datetime(2025, 1, 4, 11, 24, 40, 843030, tzinfo=UTC)
    assert data_cloud("non è una data") is None


# --- il login nel browser ---------------------------------------------------------------


def test_il_link_ha_il_pkce() -> None:
    verifier, sfida = coppia_pkce()
    assert sfida == base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert 43 <= len(verifier) <= 128
    link = indirizzo_login(sfida, "STATO", "NONCE")
    assert link.startswith("https://identity.vwgroup.io/oidc/v1/authorize?")
    domanda = dict(parse_qsl(urlsplit(link).query))
    assert domanda["client_id"] == CLIENT_ID
    assert domanda["redirect_uri"] == "myskoda://redirect/login/"
    assert domanda["response_type"] == "code"
    assert (domanda["code_challenge"], domanda["code_challenge_method"]) == (sfida, "s256")
    assert domanda["state"] == "STATO" and "openid" in domanda["scope"].split()
    assert verifier not in link


def test_l_indirizzo_incollato_in_tutte_le_forme() -> None:
    assert codice_da("myskoda://redirect/login/?code=C1&state=S1") == {"code": "C1", "state": "S1"}
    assert codice_da("  myskoda://redirect/login/#code=C2 ") == {"code": "C2"}
    assert codice_da("code=C3&state=S3")["code"] == "C3"
    assert codice_da("'eyJhbGciOi.C4'") == {"code": "eyJhbGciOi.C4"}
    with pytest.raises(ErroreCredenziali, match="niente"):
        codice_da("   ")
    with pytest.raises(ErroreCredenziali, match="access_denied"):
        codice_da("myskoda://redirect/login/?error=access_denied&state=S")
    with pytest.raises(ErroreCredenziali, match="code="):
        codice_da("myskoda://redirect/login/?state=S")
    with pytest.raises(ErroreCredenziali) as err:
        codice_da("https://identity.vwgroup.io/signin-service/v1/x/terms-and-conditions?relayState=1")
    assert str(err.value) == CONDIZIONI
    with pytest.raises(ErroreCredenziali) as err:
        codice_da("https://identity.vwgroup.io/consent/marketing/x?relayState=1")
    assert str(err.value) == MARKETING


# --- il cloud finto -------------------------------------------------------------------------


def _jwt(contenuto: dict[str, Any]) -> str:
    def b64(d: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

    return f"{b64({'alg': 'RS256'})}.{b64(contenuto)}.firma"


SEZIONI = {VIN_ENYAQ: ENYAQ, VIN_SUPERB: SUPERB}
_PERCORSI = {
    "ricarica": "/api/v1/charging/{vin}",
    "stato": "/api/v2/vehicle-status/{vin}",
    "autonomia": "/api/v2/vehicle-status/{vin}/driving-range",
    "clima": "/api/v2/air-conditioning/{vin}",
    "connessione": "/api/v2/connection-status/{vin}/readiness",
    "manutenzione": "/api/v3/vehicle-maintenance/vehicles/{vin}",
}


@dataclass
class FintoCloud:
    url: str = ""
    #: codice → code_challenge, come li ha visti il login
    codici: dict[str, str] = field(default_factory=dict)
    access_validi: set[str] = field(default_factory=set)
    refresh_validi: set[str] = field(default_factory=set)
    emessi: int = 0
    rinnovi: int = 0
    rinnovo_guasto: bool = False
    #: i pezzi dello stato che rispondono con questo HTTP
    guasti: dict[str, int] = field(default_factory=dict)
    pin: str = "1234"
    rifiuta_comando: bool = False
    chiamate: list[str] = field(default_factory=list)
    comandi: list[tuple[str, str, Any]] = field(default_factory=list)
    statistiche: list[dict[str, Any]] = field(default_factory=list)

    def app(self) -> web.Application:
        app = web.Application()
        r = app.router
        r.add_get("/oidc/v1/authorize", self._autorizza)
        r.add_post("/api/v1/authentication/exchange-authorization-code", self._scambio)
        r.add_post("/api/v1/authentication/refresh-token", self._rinnovo)
        r.add_post("/charging_statistics", self._statistiche)
        r.add_route("*", "/api/{resto:.*}", self._api)
        return app

    # --- il login (quello che farebbe il browser) e i token ----------------------------

    async def _autorizza(self, r: web.Request) -> web.Response:
        q = r.query
        assert q["client_id"] == CLIENT_ID and q["code_challenge_method"] == "s256"
        assert q["response_type"] == "code"
        self.emessi += 1
        codice = f"COD-{self.emessi}"
        self.codici[codice] = q["code_challenge"]
        domanda = urlencode({"code": codice, "state": q["state"]})
        return web.Response(status=302, headers={"Location": f"{q['redirect_uri']}?{domanda}"})

    def _rilascia(self) -> dict[str, Any]:
        self.emessi += 1
        n = self.emessi
        access = _jwt({"sub": UTENTE["id"], "exp": int(time.time()) + 3600, "n": n})
        self.access_validi.add(access)
        self.refresh_validi.add(f"RT-{n}")
        idt = _jwt({"sub": UTENTE["id"], "email": "mario@esempio.it"})
        return {"accessToken": access, "refreshToken": f"RT-{n}", "idToken": idt}

    async def _scambio(self, r: web.Request) -> web.Response:
        assert r.query["tokenType"] == "CONNECT"
        corpo = await r.json()
        assert corpo["redirectUri"] == "myskoda://redirect/login/"
        sfida = self.codici.pop(str(corpo["code"]), None)
        if sfida is None or sfida_di(str(corpo["verifier"])) != sfida:
            return web.json_response({"status": 400, "title": "Bad Request"}, status=400)
        return web.json_response(self._rilascia())

    async def _rinnovo(self, r: web.Request) -> web.Response:
        assert r.query["tokenType"] == "CONNECT"
        corpo = await r.json()
        if self.rinnovo_guasto:
            return web.Response(status=503, text="giù")
        if corpo["token"] not in self.refresh_validi:
            return web.json_response({"status": 401, "title": "Unauthorized"}, status=401)
        self.refresh_validi.discard(str(corpo["token"]))
        self.rinnovi += 1
        return web.json_response(self._rilascia())

    def _autorizzato(self, r: web.Request) -> bool:
        return r.headers.get("Authorization", "").removeprefix("Bearer ") in self.access_validi

    async def _statistiche(self, r: web.Request) -> web.Response:
        if not self._autorizzato(r):
            return web.json_response({"title": "Unauthorized"}, status=401)
        assert r.headers["X-Brand"] == "skoda"
        corpo = await r.json()
        self.statistiche.append(corpo)
        self.chiamate.append("POST charging_statistics")
        if corpo["selectedFilterOptions"][0]["vin"] == VIN_ENYAQ:
            return web.json_response(STATISTICHE_RICARICA)
        return web.json_response({"monthSections": []})

    # --- le auto ---------------------------------------------------------------------

    async def _api(self, r: web.Request) -> web.Response:
        resto = r.match_info["resto"]
        self.chiamate.append(f"{r.method} {resto}")
        if not self._autorizzato(r):
            return web.json_response({"status": 401, "title": "Unauthorized"}, status=401)
        if r.method == "GET":
            return self._leggi(r, "/api/" + resto)
        corpo = json.loads(await r.text() or "null")
        if resto == "v1/spin/verify":
            return web.json_response(SPIN_GIUSTO if corpo["currentSpin"] == self.pin else SPIN_SBAGLIATO)
        if self.rifiuta_comando:
            return web.json_response(
                {"status": 403, "title": "Forbidden", "detail": "the user is not the primary user"}, status=403
            )
        if resto.endswith(("/lock", "/unlock")) and corpo.get("currentSpin") != self.pin:
            return web.json_response({"status": 400, "title": "Bad Request", "detail": "Invalid S-PIN"}, status=400)
        self.comandi.append((r.method, resto.split("/", 1)[-1].replace(VIN_ENYAQ, "VIN"), corpo))
        return web.Response(status=202)

    def _leggi(self, r: web.Request, percorso: str) -> web.Response:
        if percorso == "/api/v1/users":
            return web.json_response(UTENTE)
        if percorso == "/api/v2/garage":
            assert r.query.getall("connectivityGenerations") == ["MOD1", "MOD2", "MOD3", "MOD4"]
            return web.json_response(GARAGE)
        if percorso.startswith("/api/v2/garage/vehicles/"):
            return web.json_response(INFO[percorso.rsplit("/", 1)[1]])
        if percorso == "/api/v1/maps/positions":
            sezioni = SEZIONI[r.query["vin"]]
            return web.json_response(sezioni["posizione"])
        if percorso.startswith("/api/v1/vehicle-health-report/warning-lights/"):
            return web.json_response(SALUTE_ENYAQ)
        if percorso.startswith("/api/v1/trip-statistics/"):
            if VIN_SUPERB in percorso:
                return web.json_response({"status": 403, "title": "Forbidden"}, status=403)
            if percorso.endswith("/single-trips"):
                assert r.query["timezone"] == "Europe/Rome" and r.query["from"] < r.query["to"]
                return web.json_response(VIAGGI_SINGOLI)
            assert r.query["offsetType"] == "week"
            return web.json_response(VIAGGI_SETTIMANA)
        if percorso.endswith("/history"):
            return web.json_response(STORICO_RICARICA)
        for vin, sezioni in SEZIONI.items():
            for nome, modello in _PERCORSI.items():
                if percorso == modello.format(vin=vin) and nome in sezioni:
                    if http := self.guasti.get(nome):
                        return web.Response(status=http, text="guasto")
                    return web.json_response(sezioni[nome])
        return web.json_response({"status": 404, "title": f"nessuna strada per {percorso}"}, status=404)


@pytest.fixture
async def cloud(socket_enabled: None) -> AsyncGenerator[FintoCloud]:
    finto = FintoCloud()
    runner = web.AppRunner(finto.app())
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    finto.url = f"http://127.0.0.1:{runner.addresses[0][1]}"
    with (
        patch.object(accesso, "BASE", finto.url),
        patch.object(accesso, "IDENTITA", finto.url),
        patch.object(modulo_cliente, "RICARICHE", finto.url),
    ):
        yield finto
    await runner.cleanup()


async def _browser(s: aiohttp.ClientSession, link: str) -> str:
    """Quello che fa chi configura: apre il link, entra, e copia l'indirizzo dove il login si ferma."""
    async with s.get(link, allow_redirects=False) as r:
        assert r.status == 302
        return r.headers["Location"]


async def _token(s: aiohttp.ClientSession) -> dict[str, Any]:
    verifier, sfida = coppia_pkce()
    finale = await _browser(s, indirizzo_login(sfida, "S", "N"))
    return await TokenSkoda(s).scambia(codice_da(finale), verifier)


def _cliente(s: aiohttp.ClientSession, token: dict[str, Any], salva: Any = None) -> ClienteSkoda:
    return ClienteSkoda(s, token, salva, fuso="Europe/Rome")


async def test_dal_codice_allo_stato(cloud: FintoCloud) -> None:
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        token = await _token(s)
        assert token["refresh_token"] == "RT-2" and dal_jwt(token["access_token"])["sub"] == UTENTE["id"]
        assert token["scade"] > time.time() + 3000
        c = _cliente(s, token, salvati.append)
        vetture = await c.vetture()
        assert [v.vin for v in vetture] == [VIN_ENYAQ, VIN_SUPERB]
        enyaq = await c.stato(vetture[0])
        assert (enyaq.batteria, enyaq.stato_ricarica, enyaq.limite_ricarica) == (63, StatoRicarica.IN_CARICA, 80)
        assert (enyaq.latitudine, enyaq.longitudine, enyaq.posizione_con_segno) == (45.07049, 7.68201, True)
        assert enyaq.capacita_kwh == 77 and enyaq.odometro_km == 5453
        assert enyaq.pneumatici_allarme is True
        assert enyaq.consumo_medio_kwh_100km == 20.1
        assert (enyaq.km_7_giorni, enyaq.energia_7_giorni_kwh) == (97.0, 16.17)
        assert enyaq.ultima_ricarica_kwh == 17.0 and enyaq.ultima_ricarica_tipo == "ac"
        assert cloud.statistiche[0]["selectedFilterOptions"] == [{"filterType": "VEHICLE", "vin": VIN_ENYAQ}]
        await c.stato(vetture[0])
        # Lo storico si rilegge ogni mezz'ora e le spie ogni sei ore, non a ogni giro.
        assert sum("single-trips" in x for x in cloud.chiamate) == 1
        assert sum("warning-lights" in x for x in cloud.chiamate) == 1
        assert sum("charging_statistics" in x for x in cloud.chiamate) == 1
        assert sum(x.endswith(f"v1/charging/{VIN_ENYAQ}") for x in cloud.chiamate) == 2
        superb = await c.stato(vetture[1])
        # Mentre va non c'è posizione; i viaggi vietati (403) e le statistiche vuote non fermano niente.
        assert (superb.batteria, superb.latitudine, superb.consumo_medio_kwh_100km) == (92, None, None)
        assert superb.carburante_percento == 64
        # Senza statistiche nuove l'ultima ricarica viene dal vecchio storico.
        assert superb.ultima_ricarica_kwh == 27.0
        # La Superb non ha «READINESS»: la connessione non si chiede.
        assert not any("connection-status" in x and VIN_SUPERB in x for x in cloud.chiamate)
        c._storici.clear()
        await c.stato(vetture[1])
        # I viaggi che l'auto non può leggere (403) non si chiedono più.
        assert sum("trip-statistics" in x and VIN_SUPERB in x for x in cloud.chiamate) == 2
        assert not salvati  # nessun rinnovo servito


async def test_un_pezzo_guasto_non_ferma_lo_stato(cloud: FintoCloud) -> None:
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, await _token(s))
        enyaq = (await c.vetture())[0]
        # Il cloud risponde spesso 500 a un pezzo solo: gli altri bastano.
        cloud.guasti = {"ricarica": 500}
        s1 = await c.stato(enyaq)
        assert (s1.batteria, s1.autonomia_km, s1.stato_ricarica, s1.chiusa) == (63, 252, None, True)
        # Se non va niente, è un guasto della rete: niente reauth.
        cloud.guasti = dict.fromkeys(_PERCORSI, 503)
        with pytest.raises(ErroreRete):
            await c.stato(enyaq)
        cloud.guasti = dict.fromkeys(_PERCORSI, 429)
        with pytest.raises(ErroreRete):
            await c.stato(enyaq)


async def test_il_token_scaduto_si_rinnova(cloud: FintoCloud) -> None:
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        token = await _token(s)
        c = _cliente(s, {**token, "scade": time.time() - 10}, salvati.append)
        await c.vetture()
        assert cloud.rinnovi == 1
        assert salvati[-1]["refresh_token"] != token["refresh_token"]
        assert salvati[-1] == c.sessione
        await c.vetture()
        assert cloud.rinnovi == 1


async def test_il_token_rifiutato_si_rinnova_e_si_riprova(cloud: FintoCloud) -> None:
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, await _token(s))
        cloud.access_validi.clear()  # il cloud lo ha dimenticato prima della scadenza
        assert len(await c.vetture()) == 2
        assert cloud.rinnovi == 1


async def test_il_rinnovo_rifiutato_chiede_il_login(cloud: FintoCloud) -> None:
    async with aiohttp.ClientSession() as s:
        token = await _token(s)
        cloud.refresh_validi.clear()
        c = _cliente(s, {**token, "scade": 0})
        with pytest.raises(ErroreCredenziali, match="browser"):
            await c.vetture()
        # Senza refresh token non c'è niente da rinnovare.
        with pytest.raises(ErroreCredenziali):
            await _cliente(s, {"access_token": "x", "scade": 0}).vetture()
        # Un refresh token scaduto non si prova nemmeno.
        prima = cloud.rinnovi
        vecchio = _jwt({"exp": int(time.time()) - 10})
        with pytest.raises(ErroreCredenziali, match="scaduto"):
            await _cliente(s, {"access_token": "x", "refresh_token": vecchio, "scade": 0}).vetture()
        assert cloud.rinnovi == prima


async def test_i_guasti_non_chiedono_di_rientrare(cloud: FintoCloud) -> None:
    async with aiohttp.ClientSession() as s:
        token = await _token(s)
        cloud.rinnovo_guasto = True
        with pytest.raises(ErroreRete):
            await _cliente(s, {**token, "scade": 0}).vetture()
    async with aiohttp.ClientSession() as s:
        c = ClienteSkoda(s, token, base="http://127.0.0.1:9")
        with pytest.raises(ErroreRete):
            await c.vetture()
        with pytest.raises(ErroreRete):
            await TokenSkoda(s, base="http://127.0.0.1:9").rinnova("RT-1")


async def test_il_codice_sbagliato_o_rubato(cloud: FintoCloud) -> None:
    async with aiohttp.ClientSession() as s:
        verifier, sfida = coppia_pkce()
        valori = codice_da(await _browser(s, indirizzo_login(sfida, "S", "N")))
        # Con un verifier che non è quello del link il codice non vale.
        with pytest.raises(ErroreCredenziali, match="scaduto"):
            await TokenSkoda(s).scambia({"code": valori["code"]}, coppia_pkce()[0])
        with pytest.raises(ErroreCredenziali, match="scaduto"):
            await TokenSkoda(s).scambia({"code": "COD-INVENTATO"}, verifier)


# --- i comandi --------------------------------------------------------------------------------


async def test_i_comandi(cloud: FintoCloud) -> None:
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, await _token(s))
        enyaq, _superb = await c.vetture()
        enyaq.stato = await c.stato(enyaq)
        await c.verifica_pin("1234")
        with pytest.raises(ErrorePin, match="restano 2"):
            await c.verifica_pin("0000")

        await c.chiudi(enyaq, "1234")
        await c.apri(enyaq, " 1234 ")
        await c.clima(enyaq, True, None, 22)
        await c.clima(enyaq, True, None, 40)
        await c.clima(enyaq, False, None)
        await c.ricarica(enyaq, False, None)
        await c.ricarica(enyaq, True, None)
        await c.limite_ricarica(enyaq, 77, None)
        await c.limite_ricarica(enyaq, 30, None)
        await c.trova(enyaq, None)
        clima = {"heaterSource": "ELECTRIC", "targetTemperature": {"temperatureValue": 22.0, "unitInCar": "CELSIUS"}}
        assert cloud.comandi == [
            ("POST", "vehicle-access/VIN/lock", {"currentSpin": "1234"}),
            ("POST", "vehicle-access/VIN/unlock", {"currentSpin": "1234"}),
            ("POST", "air-conditioning/VIN/start", clima),
            (
                "POST",
                "air-conditioning/VIN/start",
                {**clima, "targetTemperature": {"temperatureValue": 30.0, "unitInCar": "CELSIUS"}},
            ),
            ("POST", "air-conditioning/VIN/stop", None),
            ("POST", "charging/VIN/stop", None),
            ("POST", "charging/VIN/start", None),
            ("PUT", "charging/VIN/set-charge-limit", {"targetSOCInPercent": 80}),
            ("PUT", "charging/VIN/set-charge-limit", {"targetSOCInPercent": 50}),
            (
                "POST",
                "vehicle-access/VIN/honk-and-flash",
                {"mode": "HONK_AND_FLASH", "vehiclePosition": {"latitude": 45.07049, "longitude": 7.68201}},
            ),
        ]


async def test_i_comandi_che_non_vanno(cloud: FintoCloud) -> None:
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, await _token(s))
        enyaq = (await c.vetture())[0]
        with pytest.raises(ErrorePin, match="opzioni"):
            await c.chiudi(enyaq, None)
        with pytest.raises(ErrorePin, match="S-PIN"):
            await c.apri(enyaq, "9999")
        # Clacson e luci vogliono sapere dov'è l'auto.
        with pytest.raises(ErroreComando, match="dov'è"):
            await c.trova(replace(enyaq, stato=StatoVettura()), None)
        cloud.rifiuta_comando = True
        with pytest.raises(ErroreComando, match="primary user"):
            await c.ricarica(enyaq, True, None)
        assert cloud.comandi == []


# --- il flusso in Home Assistant ------------------------------------------------------------


@pytest.fixture
def catalogo() -> Iterator[None]:
    """Il marchio del catalogo che punta alla marca, come sarà quando il catalogo la conoscerà."""
    with patch.dict(PER_ID, {"skoda": replace(PER_ID["skoda"], nativo="skoda")}):
        yield


async def test_avvia_e_completa_dal_browser(hass: HomeAssistant, cloud: FintoCloud) -> None:
    dispositivo = await avvia_browser(hass, {})
    assert dispositivo.codice_utente == "" and set(dispositivo.stato) == {"verifier", "state", "nonce"}
    assert sfida_di(dispositivo.stato["verifier"]) in dispositivo.link
    async with aiohttp.ClientSession() as s:
        finale = await _browser(s, dispositivo.link)
    assert finale.startswith("myskoda://redirect/login/?code=")
    # Un indirizzo di un altro login non passa.
    altro = Dispositivo(link="", codice_utente="", stato={**dispositivo.stato, "state": "ALTRO"})
    with pytest.raises(ErroreCredenziali, match="altro login"):
        await completa_browser(hass, {}, altro, finale)
    with pytest.raises(ErrorePin, match="sbagliato"):
        await completa_browser(hass, {"pin": "0000"}, dispositivo, finale)
    async with aiohttp.ClientSession() as s:
        finale = await _browser(s, dispositivo.link)
    # Anche solo il codice va bene.
    accesso_ = await completa_browser(hass, {"pin": "1234"}, dispositivo, codice_da(finale)["code"])
    assert accesso_.unique_id == f"skoda_{UTENTE['id']}"
    assert accesso_.titolo == "Škoda · Mario@Esempio.it"
    assert accesso_.dati["pin"] == "1234" and accesso_.dati["token"]["refresh_token"]
    assert len(accesso_.vetture) == 2


async def test_configurare_una_skoda(hass: HomeAssistant, cloud: FintoCloud, catalogo: None) -> None:
    await hass.config.async_update(time_zone="Europe/Rome")
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "skoda"})
    assert r["step_id"] == "credenziali"
    assert {str(k) for k in r["data_schema"].schema} == {"pin"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"pin": "1234"})
    assert r["step_id"] == "browser"
    link = r["description_placeholders"]["link"]
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"codice": "myskoda://redirect/login/?code=NO"})
    assert r["errors"] == {"base": "codice"}
    async with aiohttp.ClientSession() as s:
        finale = await _browser(s, link)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"codice": finale})
    await hass.async_block_till_done()
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["title"] == "Škoda · Mario@Esempio.it"
    voce = r["result"]
    assert voce.unique_id == f"skoda_{UTENTE['id']}"
    assert r["data"][CONF_MARCA] == "skoda" and r["data"][CONF_MARCHIO] == "skoda"
    assert r["data"]["token"]["refresh_token"].startswith("RT-")
    assert "pin" not in r["data"] and voce.options["pin"] == "1234"
    assert hass.states.get("sensor.fulmine_batteria").state == "63"
    assert hass.states.get("lock.fulmine_serratura").state == "locked"
    assert hass.states.get("sensor.superb_batteria").state == "92"
    assert voce.runtime_data.auto[0]._normale.total_seconds() == 900
    await hass.config_entries.async_unload(voce.entry_id)
    await hass.async_block_till_done()


async def test_il_token_che_cambia_non_ricarica_l_integrazione(
    hass: HomeAssistant, cloud: FintoCloud, catalogo: None
) -> None:
    async with aiohttp.ClientSession() as s:
        token = await _token(s)
    voce = MockConfigEntry(
        domain=DOMAIN,
        title="Škoda · Mario@Esempio.it",
        unique_id=f"skoda_{UTENTE['id']}",
        data={CONF_FONTE: FONTE_CLOUD, CONF_MARCA: "skoda", CONF_MARCHIO: "skoda", "token": {**token, "scade": 0}},
        options={"pin": "1234"},
    )
    voce.add_to_hass(hass)
    assert await hass.config_entries.async_setup(voce.entry_id)
    await hass.async_block_till_done()
    dati_prima = voce.runtime_data
    assert voce.data["token"]["refresh_token"] != token["refresh_token"]
    assert voce.data["token"]["scade"] > time.time()
    assert hass.states.get("sensor.fulmine_batteria").state == "63"
    assert voce.runtime_data is dati_prima
    from custom_components.gdaauto.diagnostics import async_get_config_entry_diagnostics

    testo = str(await async_get_config_entry_diagnostics(hass, voce))
    for segreto in ("45.07", "7.682", VIN_ENYAQ, "RT-", token["access_token"], "1234", "user@example.com", "Torino"):
        assert segreto not in testo, segreto
    await hass.config_entries.async_unload(voce.entry_id)
    await hass.async_block_till_done()


async def test_il_rinnovo_rifiutato_chiede_il_reauth(hass: HomeAssistant, cloud: FintoCloud, catalogo: None) -> None:
    voce = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"skoda_{UTENTE['id']}",
        data={
            CONF_FONTE: FONTE_CLOUD,
            CONF_MARCA: "skoda",
            CONF_MARCHIO: "skoda",
            "token": {"access_token": "VECCHIO", "refresh_token": "RT-MORTO", "scade": 0},
        },
    )
    voce.add_to_hass(hass)
    await hass.config_entries.async_setup(voce.entry_id)
    await hass.async_block_till_done()
    flussi = hass.config_entries.flow.async_progress()
    reauth = next(f for f in flussi if f["context"]["source"] == config_entries.SOURCE_REAUTH)
    # Il reauth riapre il browser.
    assert reauth["step_id"] == "browser"
