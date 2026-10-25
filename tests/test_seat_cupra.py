"""SEAT e CUPRA: i payload, il login dal browser, il cliente contro un cloud OLA finto, e il flusso in HA."""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator, Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
import json
import time
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qsl, urlsplit

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
from custom_components.gdaauto.marche.errori import (
    CodiceSbagliato,
    ErroreApi,
    ErroreComando,
    ErroreCredenziali,
    ErrorePin,
    ErroreRete,
)
from custom_components.gdaauto.marche.seat_cupra import (
    CAMPI,
    VUOLE_PIN,
    ClienteSeatCupra,
    avvia_browser,
    cliente as modulo_cliente,
    comandi_di,
    completa_browser,
    login,
)
from custom_components.gdaauto.marche.seat_cupra.app import APP, lingua_cloud
from custom_components.gdaauto.marche.seat_cupra.cliente import vettura_da_json
from custom_components.gdaauto.marche.seat_cupra.dati import interpreta, storico_viaggi
from custom_components.gdaauto.marche.seat_cupra.login import indirizzo_login, leggi_incollato, pkce, sfida
from custom_components.gdaauto.vettura import StatoRicarica, StatoVettura, Vettura

from . import seat_cupra_campioni as C

CUPRA = APP["cupra"]
SEAT = APP["seat"]

# --- il contratto e l'anagrafica -------------------------------------------------------


def test_il_contratto() -> None:
    modulo = MARCHE["seat_cupra"]
    assert modulo.ACCESSO == ACCESSO_CODICE_BROWSER
    assert [c.nome for c in CAMPI] == ["pin"] and CAMPI[0].segreto and not CAMPI[0].obbligatorio
    assert VUOLE_PIN is True
    assert modulo.INTERVALLO_MINUTI == 10 and modulo.INTERVALLO_IN_MOTO_SECONDI == 300
    assert set(modulo.NOTA) == {"it", "en"} and "App Check" in modulo.NOTA["it"]
    assert lingua_cloud("it") == "it_IT" and lingua_cloud(None) == "en_GB" and lingua_cloud("ja") == "en_GB"


def _vetture(app: Any = CUPRA) -> list[Vettura]:
    trovate = []
    for voce in C.GARAGE["vehicles"]:
        if (v := vettura_da_json(voce, app, C.CAPACITA[voce["vin"]])) is not None:
            trovate.append(v)
    return trovate


def test_l_anagrafica_e_i_comandi() -> None:
    born, formentor = _vetture()  # la Leon a benzina non si ricarica: resta fuori
    assert (born.vin, born.marca, born.modello, born.nome, born.anno) == (
        C.VIN_BORN,
        "seat_cupra",
        "Born",
        "Saetta",
        2023,
    )
    assert not born.condivisa and born.extra["piattaforma"] == "MOD4" and born.extra["marchio"] == "cupra"
    assert (formentor.nome, formentor.anno, formentor.condivisa) == ("CUPRA Formentor", 2024, True)
    assert comandi_di(born) == {"chiudi", "apri", "trova", "clima", "ricarica", "limite_ricarica", "destinazione"}
    # Senza serratura a distanza, col clacson scaduto: niente chiudi, apri e trova.
    assert comandi_di(formentor) == {"clima", "ricarica", "limite_ricarica", "destinazione"}
    # Senza le capacità (non lette) si tengono tutti.
    ignota = Vettura(vin="X", marca="seat_cupra", modello="")
    assert "chiudi" in comandi_di(ignota) and "trova" in comandi_di(ignota)
    # L'auto che non sa cambiare il limite non lo offre.
    senza_limite = vettura_da_json(
        C.GARAGE["vehicles"][0],
        CUPRA,
        {"capabilities": [{"id": "charging", "parameters": {"supportsTargetStateOfCharge": "false"}}]},
    )
    assert senza_limite is not None and comandi_di(senza_limite) == {"ricarica", "destinazione"}
    assert vettura_da_json({"vin": ""}, CUPRA) is None


# --- lo stato dai payload ----------------------------------------------------------------


def _born(**altro: Any) -> StatoVettura:
    pezzi: dict[str, Any] = {
        "stato": C.STATO_BORN,
        "mycar": C.MYCAR_BORN,
        "carica": C.CARICA_BORN,
        "impostazioni_carica": C.INFO_CARICA_BORN,
        "clima": C.CLIMA_BORN,
        "impostazioni_clima": C.IMPOSTAZIONI_CLIMA_BORN,
        "parcheggio": C.PARCHEGGIO_BORN,
        "chilometri": C.CHILOMETRI_BORN,
        "autonomie": C.AUTONOMIE_BORN,
        "disponibilita": C.DISPONIBILITA_BORN,
    }
    return interpreta(**{**pezzi, **altro})


def test_una_born_in_carica_alla_colonnina() -> None:
    s = _born()
    assert (s.batteria, s.autonomia_km, s.odometro_km) == (63, 251, 18342)
    assert s.stato_ricarica == StatoRicarica.IN_CARICA and s.in_carica
    assert (s.potenza_ricarica_kw, s.tempo_ricarica_min, s.ricarica_rapida) == (10.8, 95, False)
    assert (s.cavo_collegato, s.limite_ricarica, s.ricarica_programmata, s.v2l_attivo) == (True, 80, False, False)
    assert (s.clima_acceso, s.temperatura_clima_c, s.sbrinamento) == (False, 21.5, False)
    assert (s.chiusa, s.portiere_aperte, s.bagagliaio_aperto, s.finestrini_aperti) == (True, False, False, False)
    assert s.quadro_acceso is False and s.in_sonno is False
    assert (s.latitudine, s.longitudine, s.posizione_con_segno) == (41.50283, 1.81219, True)
    assert s.letto == datetime(2026, 10, 19, 17, 24, 2, tzinfo=UTC)
    # Le ibride hanno anche il serbatoio; un'elettrica no.
    assert s.carburante_percento is None and s.autonomia_totale_km is None
    assert set(s.grezzo) == {"stato", "mycar", "carica", "clima"}


def test_una_formentor_ibrida_aperta_in_attesa_del_timer() -> None:
    s = interpreta(
        stato=C.STATO_FORMENTOR,
        mycar=C.MYCAR_FORMENTOR,
        carica=C.CARICA_FORMENTOR,
        impostazioni_carica=C.INFO_CARICA_FORMENTOR,
        clima=C.CLIMA_FORMENTOR,
        chilometri=C.CHILOMETRI_FORMENTOR,
        autonomie=C.AUTONOMIE_FORMENTOR,
        disponibilita=C.DISPONIBILITA_FORMENTOR,
    )
    assert (s.batteria, s.autonomia_km) == (90, 45)
    assert (s.carburante_percento, s.autonomia_carburante_km, s.autonomia_totale_km) == (55, 480, 525)
    assert s.stato_ricarica == StatoRicarica.PROGRAMMATA and s.ricarica_programmata is True
    assert (s.potenza_ricarica_kw, s.cavo_collegato, s.limite_ricarica) == (0.0, True, 100)
    # Il clima è acceso; la temperatura viene dal riassunto di «mycar».
    assert (s.clima_acceso, s.temperatura_clima_c, s.sbrinamento) == (True, 22.0, True)
    assert (s.chiusa, s.portiere_aperte, s.bagagliaio_aperto, s.finestrini_aperti) == (False, True, True, True)
    assert s.in_sonno is True and s.odometro_km == 7021 and s.latitudine is None
    assert s.letto == datetime(2026, 10, 19, 7, 10, 0, 123000, tzinfo=UTC)


def test_i_pezzi_che_mancano_o_sono_vuoti() -> None:
    # Solo «mycar»: batteria, autonomia, stato e limite dal riassunto.
    s = interpreta(mycar=C.MYCAR_BORN)
    assert (s.batteria, s.autonomia_km, s.limite_ricarica, s.tempo_ricarica_min) == (63, 251, 80, 95)
    assert s.stato_ricarica == StatoRicarica.IN_CARICA and s.potenza_ricarica_kw is None
    # Campi a null e pezzi strani non rompono niente.
    vuoto = interpreta(
        stato={"doors": None, "windows": None, "trunk": None, "locked": None},
        mycar={"engines": None, "services": {"charging": None}},
        carica={"battery": None, "charging": None, "plug": None},
        clima={"climatisationStatus": None, "windowHeatingStatus": {"windowHeatingStatus": None}},
        impostazioni_carica={"settings": None},
        parcheggio={"lat": None, "lon": None},
        chilometri={"mileageKm": None},
        autonomie={"ranges": None},
        disponibilita={"remote-availability": "unknown"},
    )
    assert vuoto.batteria is None and vuoto.chiusa is None and vuoto.in_sonno is None and vuoto.latitudine is None
    assert interpreta() == StatoVettura()
    # La serratura di ogni portiera, quando manca quella generale; il quadro acceso.
    s = interpreta(
        stato={"doors": {"frontLeft": {"locked": "true"}, "frontRight": {"locked": "false"}}, "engine": "on"}
    )
    assert s.chiusa is False and s.quadro_acceso is True
    # Una ricarica in corrente continua; un'auto che dà corrente fuori.
    dc = interpreta(carica={"charging": {"state": "charging", "chargeType": "dc", "chargedPowerInKw": 95.5}})
    assert (dc.ricarica_rapida, dc.potenza_ricarica_kw) == (True, 95.5)
    v2l = interpreta(carica={"charging": {"state": "discharging"}})
    assert v2l.v2l_attivo is True and v2l.stato_ricarica == StatoRicarica.NON_IN_CARICA


def test_lo_storico_dei_viaggi() -> None:
    oggi = date(2026, 10, 20)
    campi = storico_viaggi(C.viaggi(oggi), oggi)
    # Negli ultimi sette giorni tre giorni da 40 km a 16 kWh/100 km.
    assert campi == {"km_7_giorni": 120.0, "energia_7_giorni_kwh": 19.2, "consumo_medio_kwh_100km": 16.0}
    assert storico_viaggi({"data": {"dailySummary": []}}, oggi) == {}
    assert storico_viaggi(None, oggi) == {}
    # Pochi km: il consumo medio non si dice.
    poco = {"dailySummary": [{"day": "2026-10-20", "values": [{"id": "distanceDriven", "value": 5}]}]}
    assert storico_viaggi(poco, oggi)["consumo_medio_kwh_100km"] is None


# --- il login nel browser -----------------------------------------------------------------


def test_il_link_ha_il_pkce_e_l_app_giusta() -> None:
    verifier, sfida_ = pkce()
    assert sfida(verifier) == sfida_ and "=" not in sfida_
    for app in (CUPRA, SEAT):
        link = indirizzo_login(app, sfida_, "S", "N")
        parti = urlsplit(link)
        q = dict(parse_qsl(parti.query))
        assert f"{parti.scheme}://{parti.netloc}{parti.path}" == "https://identity.vwgroup.io/oidc/v1/authorize"
        assert q["client_id"] == app.client_id and q["redirect_uri"] == f"{app.marchio}://oauth-callback"
        assert (q["response_type"], q["code_challenge"], q["code_challenge_method"]) == ("code", sfida_, "S256")
        assert (q["state"], q["nonce"]) == ("S", "N") and verifier not in link


def test_l_indirizzo_incollato_in_tutte_le_forme() -> None:
    assert leggi_incollato("cupra://oauth-callback?code=C1&state=S") == ("C1", "S")
    assert leggi_incollato("  seat://oauth-callback#state=S&code=C2  ") == ("C2", "S")
    assert leggi_incollato("code=C3&state=S") == ("C3", "S")
    assert leggi_incollato("<C4>") == ("C4", None)
    with pytest.raises(CodiceSbagliato, match="niente"):
        leggi_incollato("  ")
    with pytest.raises(CodiceSbagliato, match="code="):
        leggi_incollato("cupra://oauth-callback?state=S")
    with pytest.raises(CodiceSbagliato, match="cupra://"):
        leggi_incollato("non so cosa incollare")
    with pytest.raises(ErroreCredenziali, match="access_denied"):
        leggi_incollato("cupra://oauth-callback?error=access_denied&state=S")


def test_nessun_modulo_html_con_la_password() -> None:
    """La regola ferma: GdAAuto non compila le pagine di login, la password non passa di qui."""
    import pathlib

    cartella = pathlib.Path(modulo_cliente.__file__).parent
    for file in cartella.glob("*.py"):
        testo = file.read_text(encoding="utf-8").lower()
        for vietato in ("_csrf", "hmac", "<form", "htmlparser", '"password"', "'password'"):
            assert vietato not in testo, f"{file.name}: {vietato}"


# --- il cloud finto -------------------------------------------------------------------------


def _jwt(contenuto: dict[str, Any]) -> str:
    def b64(d: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

    return f"{b64({'alg': 'RS256'})}.{b64(contenuto)}.firma"


_LETTURE: dict[tuple[str, str], dict[str, Any]] = {
    (C.VIN_BORN, "v2/vehicles/{vin}/status"): C.STATO_BORN,
    (C.VIN_BORN, "v5/users/{u}/vehicles/{vin}/mycar"): C.MYCAR_BORN,
    (C.VIN_BORN, "v1/vehicles/{vin}/mileage"): C.CHILOMETRI_BORN,
    (C.VIN_BORN, "v1/vehicles/{vin}/charging/status"): C.CARICA_BORN,
    (C.VIN_BORN, "v1/vehicles/{vin}/climatisation/status"): C.CLIMA_BORN,
    (C.VIN_BORN, "v1/vehicles/{vin}/parkingposition"): C.PARCHEGGIO_BORN,
    (C.VIN_BORN, "v1/vehicles/{vin}/ranges"): C.AUTONOMIE_BORN,
    (C.VIN_BORN, "v1/vehicles/{vin}/remote-availability"): C.DISPONIBILITA_BORN,
    (C.VIN_BORN, "v1/vehicles/{vin}/charging/info"): C.INFO_CARICA_BORN,
    (C.VIN_BORN, "v2/vehicles/{vin}/climatisation/settings"): C.IMPOSTAZIONI_CLIMA_BORN,
    (C.VIN_FORMENTOR, "v2/vehicles/{vin}/status"): C.STATO_FORMENTOR,
    (C.VIN_FORMENTOR, "v5/users/{u}/vehicles/{vin}/mycar"): C.MYCAR_FORMENTOR,
    (C.VIN_FORMENTOR, "v1/vehicles/{vin}/mileage"): C.CHILOMETRI_FORMENTOR,
    (C.VIN_FORMENTOR, "v1/vehicles/{vin}/charging/status"): C.CARICA_FORMENTOR,
    (C.VIN_FORMENTOR, "v1/vehicles/{vin}/climatisation/status"): C.CLIMA_FORMENTOR,
    (C.VIN_FORMENTOR, "v1/vehicles/{vin}/ranges"): C.AUTONOMIE_FORMENTOR,
    (C.VIN_FORMENTOR, "v1/vehicles/{vin}/remote-availability"): C.DISPONIBILITA_FORMENTOR,
    (C.VIN_FORMENTOR, "v1/vehicles/{vin}/charging/info"): C.INFO_CARICA_FORMENTOR,
}


@dataclass
class FintoCloud:
    url: str = ""
    utente: str = "B6F1C0DE-0000-4000-8000-000000000001"
    #: codice → (code_challenge, client_id), come li ha visti il login
    codici: dict[str, tuple[str, str]] = field(default_factory=dict)
    access_validi: set[str] = field(default_factory=set)
    #: refresh token → client id
    refresh_validi: dict[str, str] = field(default_factory=dict)
    emessi: int = 0
    rinnovi: int = 0
    rinnovo_guasto: bool = False
    stato_guasto: int = 0
    #: il blocco Firebase App Check: le letture e i token rispondono 403
    app_check: bool = False
    token_app_check: bool = False
    #: OLA rifiuta ogni codice (come capita ad alcuni account CUPRA): resta il login del gruppo
    ola_rifiuta_codice: bool = False
    #: le richieste ai token: (dove, grant_type)
    token_chiesti: list[tuple[str, str]] = field(default_factory=list)
    pin: str = "1234"
    sec_token: set[str] = field(default_factory=set)
    rifiuta_comando: bool = False
    chiamate: list[str] = field(default_factory=list)
    comandi: list[tuple[str, str, Any]] = field(default_factory=list)
    marchi: set[str] = field(default_factory=set)

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/oidc/v1/authorize", self._autorizza)
        app.router.add_post("/authorization/api/v1/token", self._token)
        app.router.add_post("/oidc/v1/token", self._token_identita)
        app.router.add_route("*", "/{resto:.*}", self._api)
        return app

    # --- il login (quello che farebbe il browser) e i token ----------------------------

    async def _autorizza(self, r: web.Request) -> web.Response:
        q = r.query
        app = next(a for a in APP.values() if a.client_id == q["client_id"])
        assert q["redirect_uri"] == app.redirect_uri and q["response_type"] == "code"
        assert q["code_challenge_method"] == "S256" and q["scope"].startswith("openid")
        self.emessi += 1
        codice = f"COD-{self.emessi}"
        self.codici[codice] = (q["code_challenge"], app.client_id)
        return web.Response(status=302, headers={"Location": f"{app.redirect_uri}?code={codice}&state={q['state']}"})

    def _rilascia(self, client_id: str, camel: bool) -> dict[str, Any]:
        self.emessi += 1
        n = self.emessi
        access = _jwt({"sub": self.utente, "exp": int(time.time()) + 3600, "n": n})
        self.access_validi.add(access)
        self.refresh_validi[f"RT-{n}"] = client_id
        idt = _jwt({"sub": self.utente, "email": "Mario@Esempio.it"})
        if camel:
            return {"accessToken": access, "idToken": idt, "refreshToken": f"RT-{n}", "expiresIn": 3600}
        return {"access_token": access, "id_token": idt, "refresh_token": f"RT-{n}", "expires_in": 3600}

    async def _token(self, r: web.Request) -> web.Response:
        if self.token_app_check:
            return web.json_response({"message": "Forbidden"}, status=403)
        f = await r.post()
        app = next(a for a in APP.values() if a.client_id == f["client_id"])
        assert r.headers["app-brand"] == app.marchio and r.headers["User-Agent"] == app.user_agent
        assert r.headers["x-requested-with"] == app.nome_app
        self.token_chiesti.append(("ola", str(f["grant_type"])))
        if f["grant_type"] == "authorization_code":
            if self.ola_rifiuta_codice:
                return web.json_response({"error": "invalid_request", "error_description": "bad client"}, status=400)
            sfida_, client = self.codici.pop(str(f["code"]), ("", ""))
            if client != app.client_id or sfida(str(f["code_verifier"])) != sfida_:
                return web.json_response({"error": "invalid_grant"}, status=400)
            assert f["redirect_uri"] == app.redirect_uri
            return web.json_response(self._rilascia(app.client_id, camel=False))
        assert f["grant_type"] == "refresh_token"
        # Solo l'app CUPRA manda il suo segreto nel rinnovo.
        assert f.get("client_secret", "") == app.client_secret
        if self.rinnovo_guasto:
            return web.Response(status=503, text="giù")
        if self.refresh_validi.pop(str(f["refresh_token"]), None) != app.client_id:
            return web.json_response({"error": "invalid_grant", "error_description": "Token expired"}, status=400)
        self.rinnovi += 1
        return web.json_response(self._rilascia(app.client_id, camel=True))

    async def _token_identita(self, r: web.Request) -> web.Response:
        """Il login del gruppo: lo stesso scambio, ma col segreto dell'app CUPRA."""
        f = await r.post()
        app = next(a for a in APP.values() if a.client_id == f["client_id"])
        self.token_chiesti.append(("identita", str(f["grant_type"])))
        if f.get("client_secret", "") != app.client_secret or not app.client_secret:
            return web.json_response({"error": "invalid_client"}, status=401)
        if f["grant_type"] == "authorization_code":
            sfida_, client = self.codici.pop(str(f["code"]), ("", ""))
            if client != app.client_id or sfida(str(f["code_verifier"])) != sfida_:
                return web.json_response({"error": "invalid_grant"}, status=400)
            return web.json_response(self._rilascia(app.client_id, camel=False))
        if self.refresh_validi.pop(str(f["refresh_token"]), None) != app.client_id:
            return web.json_response({"error": "invalid_grant"}, status=400)
        self.rinnovi += 1
        return web.json_response(self._rilascia(app.client_id, camel=False))

    # --- le auto ---------------------------------------------------------------------

    async def _api(self, r: web.Request) -> web.Response:
        resto = r.match_info["resto"]
        self.chiamate.append(f"{r.method} {resto}")
        if r.headers.get("Authorization", "").removeprefix("Bearer ") not in self.access_validi:
            return web.json_response({"message": "Unauthorized"}, status=401)
        assert r.headers["User-ID"] == self.utente and r.headers["origin"] == "app"
        self.marchi.add(r.headers["app-brand"])
        if r.method == "GET":
            return self._leggi(r, resto)
        corpo = json.loads(await r.text() or "null")
        if resto == f"v2/users/{self.utente}/spin/verify":
            if corpo != {"spin": self.pin}:
                return web.json_response({"errors": [{"code": "4003", "message": "S-PIN wrong"}]}, status=403)
            sec = f"SEC-{len(self.sec_token) + 1}"
            self.sec_token.add(sec)
            return web.json_response({"securityToken": sec}, status=201)
        if self.rifiuta_comando:
            return web.json_response({"errors": [{"message": "User is not the primary user"}]}, status=403)
        generico = resto.replace(C.VIN_BORN, "{vin}").replace(C.VIN_FORMENTOR, "{vin}")
        if generico.endswith(("/access/lock", "/access/unlock")):
            if r.headers.get("SecToken") not in self.sec_token:
                return web.json_response({"message": "SecToken missing"}, status=403)
            self.sec_token.discard(r.headers["SecToken"])
        self.comandi.append((r.method, generico, corpo))
        return web.json_response({"requestId": f"R-{len(self.comandi)}"}, status=202)

    def _leggi(self, r: web.Request, resto: str) -> web.Response:
        if self.app_check:
            return web.json_response({"message": "Forbidden"}, status=403)
        if resto == f"v2/users/{self.utente}/garage/vehicles":
            return web.json_response(C.GARAGE)
        pezzi = resto.split("/")
        vin = next((p for p in pezzi if p in (C.VIN_BORN, C.VIN_FORMENTOR, C.VIN_LEON)), "")
        generico = resto.replace(vin, "{vin}").replace(self.utente, "{u}")
        if generico == "v1/user/{u}/vehicle/{vin}/capabilities":
            return web.json_response(C.CAPACITA[vin])
        if generico == "v2/vehicles/{vin}/status" and self.stato_guasto:
            return web.Response(status=self.stato_guasto, text="guasto")
        if generico == "v2/vehicles/{vin}/driving-data/CUSTOM":
            assert r.query["distanceUnit"] == "km" and r.query["from"].endswith("T00:00:00Z")
            return web.json_response(C.viaggi(datetime.now(UTC).date()))
        if (vin, generico) in _LETTURE:
            return web.json_response(_LETTURE[(vin, generico)])
        return web.json_response({"message": f"nessuna strada per {generico}"}, status=404)


@pytest.fixture
async def cloud(socket_enabled: None) -> AsyncGenerator[FintoCloud]:
    finto = FintoCloud()
    runner = web.AppRunner(finto.app())
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    finto.url = f"http://127.0.0.1:{runner.addresses[0][1]}"
    with (
        patch.object(modulo_cliente, "OLA", finto.url),
        patch.object(login, "IDENTITA", finto.url),
        patch.object(modulo_cliente, "IDENTITA", finto.url),
    ):
        yield finto
    await runner.cleanup()


async def _browser(s: aiohttp.ClientSession, link: str) -> str:
    """Quello che fa chi configura: apre il link, entra, e copia l'indirizzo dove il login si ferma."""
    async with s.get(link, allow_redirects=False) as r:
        assert r.status == 302
        return r.headers["Location"]


async def _cliente(s: aiohttp.ClientSession, marchio: str = "cupra", salva: Any = None) -> ClienteSeatCupra:
    verifier, sfida_ = pkce()
    finale = await _browser(s, indirizzo_login(APP[marchio], sfida_, "S", "N"))
    codice, stato = leggi_incollato(finale)
    assert stato == "S"
    c = ClienteSeatCupra(s, marchio, salva=salva, lingua="it")
    await c.scambia_codice(codice, verifier)
    return c


async def test_dal_codice_allo_stato(cloud: FintoCloud) -> None:
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        c = await _cliente(s, salva=salvati.append)
        assert c.utente == cloud.utente and c.sessione["chi"] == "Mario@Esempio.it"
        assert salvati == [c.sessione] and c.sessione["refresh_token"] == "RT-2"
        vetture = await c.vetture()
        assert [v.vin for v in vetture] == [C.VIN_BORN, C.VIN_FORMENTOR]
        born, formentor = vetture
        s1 = await c.stato(born)
        assert (s1.batteria, s1.stato_ricarica, s1.limite_ricarica, s1.temperatura_clima_c) == (
            63,
            StatoRicarica.IN_CARICA,
            80,
            21.5,
        )
        assert (s1.latitudine, s1.odometro_km, s1.in_sonno) == (41.50283, 18342, False)
        assert (s1.km_7_giorni, s1.consumo_medio_kwh_100km) == (120.0, 16.0)
        await c.stato(born)
        # Impostazioni, autonomie e viaggi si rileggono ogni mezz'ora, non a ogni giro.
        assert sum("driving-data" in x for x in cloud.chiamate) == 1
        assert sum("charging/info" in x for x in cloud.chiamate) == 1
        assert sum(x.endswith(f"{C.VIN_BORN}/status") for x in cloud.chiamate) == 2
        s2 = await c.stato(formentor)
        assert (s2.batteria, s2.autonomia_totale_km, s2.limite_ricarica) == (90, 525, 100)
        # La Formentor non ha la posizione (capacità spenta) né i viaggi: non si chiedono.
        assert not any(C.VIN_FORMENTOR in x and ("parkingposition" in x or "driving" in x) for x in cloud.chiamate)
        # Nessuna lettura sveglia l'auto.
        assert not any("wakeup" in x for x in cloud.chiamate)
        assert cloud.marchi == {"cupra"} and len(salvati) == 1


async def test_il_token_scaduto_si_rinnova_e_ruota(cloud: FintoCloud) -> None:
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        c = await _cliente(s)
        vecchio = dict(c.sessione)
        c = ClienteSeatCupra(s, "cupra", {**vecchio, "scade": "2000-01-01T00:00:00+00:00"}, salvati.append)
        await c.vetture()
        assert cloud.rinnovi == 1
        assert salvati[-1]["refresh_token"] != vecchio["refresh_token"] and salvati[-1] == c.sessione
        assert salvati[-1]["user_id"] == cloud.utente and salvati[-1]["chi"] == "Mario@Esempio.it"
        await c.vetture()
        assert cloud.rinnovi == 1
        # Il refresh token vecchio non vale più.
        c = ClienteSeatCupra(s, "cupra", {**vecchio, "scade": "2000-01-01T00:00:00+00:00"})
        with pytest.raises(ErroreCredenziali, match="browser"):
            await c.vetture()


async def test_il_token_rifiutato_si_rinnova_e_si_riprova(cloud: FintoCloud) -> None:
    async with aiohttp.ClientSession() as s:
        c = await _cliente(s)
        cloud.access_validi.clear()  # il cloud lo ha dimenticato prima della scadenza
        assert len(await c.vetture()) == 2
        assert cloud.rinnovi == 1


async def test_seat_entra_con_la_sua_app(cloud: FintoCloud) -> None:
    async with aiohttp.ClientSession() as s:
        c = await _cliente(s, "seat")
        c = ClienteSeatCupra(s, "seat", {**c.sessione, "scade": "2000-01-01T00:00:00+00:00"})
        # Il rinnovo SEAT va senza segreto (il cloud finto lo controlla).
        assert len(await c.vetture()) == 2
        assert cloud.rinnovi == 1 and cloud.marchi == {"seat"}
    with pytest.raises(ValueError):
        ClienteSeatCupra(None, "skoda")  # type: ignore[arg-type]


async def test_senza_refresh_token_si_rientra(cloud: FintoCloud) -> None:
    async with aiohttp.ClientSession() as s:
        c = ClienteSeatCupra(s, "cupra", {"access_token": "x", "scade": "2000-01-01T00:00:00+00:00"})
        with pytest.raises(ErroreCredenziali):
            await c.vetture()
        with pytest.raises(ErroreCredenziali):
            await ClienteSeatCupra(s, "cupra").vetture()


async def test_il_codice_sbagliato_o_rubato(cloud: FintoCloud) -> None:
    async with aiohttp.ClientSession() as s:
        _verifier, sfida_ = pkce()
        codice, _ = leggi_incollato(await _browser(s, indirizzo_login(CUPRA, sfida_, "S", "N")))
        c = ClienteSeatCupra(s, "cupra")
        # Con un verifier che non è quello del link il codice non vale.
        with pytest.raises(CodiceSbagliato, match="rifai il login"):
            await c.scambia_codice(codice, pkce()[0])
        with pytest.raises(CodiceSbagliato):
            await c.scambia_codice("COD-INVENTATO", "x")


async def test_cupra_quando_ola_rifiuta_il_codice(cloud: FintoCloud) -> None:
    cloud.ola_rifiuta_codice = True
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        c = await _cliente(s, salva=salvati.append)
        # Il codice è passato dal login del gruppo, e da lì si rinnova.
        assert c.sessione["via"] == "identita" and salvati == [c.sessione]
        assert cloud.token_chiesti == [("ola", "authorization_code"), ("identita", "authorization_code")]
        c = ClienteSeatCupra(s, "cupra", {**c.sessione, "scade": "2000-01-01T00:00:00+00:00"}, salvati.append)
        assert len(await c.vetture()) == 2
        assert cloud.token_chiesti[-1] == ("identita", "refresh_token") and salvati[-1]["via"] == "identita"
        # SEAT non ha la seconda strada: il codice rifiutato resta rifiutato.
        with pytest.raises(CodiceSbagliato):
            await _cliente(s, "seat")
        # Un codice falso non passa da nessuna delle due, e il messaggio le dice tutte e due.
        with pytest.raises(CodiceSbagliato, match="login del gruppo"):
            await ClienteSeatCupra(s, "cupra").scambia_codice("COD-INVENTATO", "x")


async def test_cupra_quando_app_check_blocca_lo_scambio(cloud: FintoCloud) -> None:
    cloud.token_app_check = True
    async with aiohttp.ClientSession() as s:
        c = await _cliente(s)
        assert c.sessione["via"] == "identita"


async def test_i_guasti_non_chiedono_di_rientrare(cloud: FintoCloud) -> None:
    async with aiohttp.ClientSession() as s:
        c = await _cliente(s)
        born = (await c.vetture())[0]
        # Un pezzo guasto non ferma gli altri.
        cloud.stato_guasto = 503
        assert (await c.stato(born)).batteria == 63
        cloud.rinnovo_guasto = True
        c2 = ClienteSeatCupra(s, "cupra", {**c.sessione, "scade": "2000-01-01T00:00:00+00:00"})
        with pytest.raises(ErroreRete):
            await c2.vetture()
    async with aiohttp.ClientSession() as s:
        c3 = ClienteSeatCupra(s, "cupra", c.sessione, ola="http://127.0.0.1:9")
        with pytest.raises(ErroreRete):
            await c3.vetture()
        with pytest.raises(ErroreRete):
            await c3.stato(born)


async def test_il_blocco_app_check_non_chiede_di_rientrare(cloud: FintoCloud) -> None:
    async with aiohttp.ClientSession() as s:
        c = await _cliente(s)
        born = (await c.vetture())[0]
        cloud.app_check = True
        with pytest.raises(ErroreApi) as err:
            await c.stato(born)
        assert err.value.codice == "app_check" and "App Check" in err.value.messaggio
        assert not err.value.token_scaduto
        with pytest.raises(ErroreApi, match="App Check"):
            await c.vetture()
        cloud.token_app_check = True
        c2 = ClienteSeatCupra(s, "cupra", {**c.sessione, "scade": "2000-01-01T00:00:00+00:00"})
        with pytest.raises(ErroreApi, match="App Check"):
            await c2.vetture()


# --- i comandi --------------------------------------------------------------------------------


async def test_i_comandi(cloud: FintoCloud) -> None:
    async with aiohttp.ClientSession() as s:
        c = await _cliente(s)
        born, formentor = await c.vetture()
        born.stato = await c.stato(born)
        await c.stato(formentor)
        await c.verifica_pin("1234")
        with pytest.raises(ErrorePin):
            await c.verifica_pin("0000")

        await c.chiudi(born, "1234")
        await c.apri(born, "1234")
        await c.clima(born, True, None, 22)
        await c.clima(born, False, None)
        await c.ricarica(born, False, None)
        await c.ricarica(born, True, None)
        await c.limite_ricarica(born, 77, None)
        await c.limite_ricarica(formentor, 60, None)
        await c.trova(born, None)
        await c.destinazione(born, "Martorell", 41.4747, 1.9303)
        assert cloud.comandi == [
            ("POST", "v1/vehicles/{vin}/access/lock", {}),
            ("POST", "v1/vehicles/{vin}/access/unlock", {}),
            (
                "POST",
                "v2/vehicles/{vin}/climatisation/start",
                {"targetTemperature": 22.0, "targetTemperatureUnit": "celsius"},
            ),
            ("POST", "vehicles/{vin}/climatisation/requests/stop", None),
            ("POST", "vehicles/{vin}/charging/requests/stop", None),
            ("POST", "vehicles/{vin}/charging/requests/start", None),
            # La Born (MOD4) cambia il limite con l'azione sua; la Formentor rimanda le impostazioni intere.
            ("POST", "v1/vehicles/{vin}/charging/actions/update-settings", {"targetSocPercentage": 77}),
            (
                "POST",
                "v1/vehicles/{vin}/charging/settings",
                {
                    "maxChargeCurrentAC": "reduced",
                    "carCapturedTimestamp": "2025-03-18T16:50:33Z",
                    "autoUnlockPlugWhenCharged": None,
                    "targetSoc_pct": 100,
                    "batteryCareTargetSocPercentage": 80,
                    "targetSoc": 60,
                },
            ),
            (
                "POST",
                "v1/vehicles/{vin}/honk-and-flash",
                {"mode": "honkandflash", "userPosition": {"latitude": 41.5028, "longitude": 1.8121}},
            ),
            (
                "PUT",
                "v1/users/vehicles/{vin}/destination",
                [
                    {
                        "destinationName": "Martorell",
                        "poiProvider": "google",
                        "geoCoordinate": {"latitude": 41.4747, "longitude": 1.9303},
                    }
                ],
            ),
        ]


async def test_i_comandi_che_non_vanno(cloud: FintoCloud) -> None:
    async with aiohttp.ClientSession() as s:
        c = await _cliente(s)
        born, formentor = await c.vetture()
        with pytest.raises(ErrorePin, match="opzioni"):
            await c.chiudi(born, None)
        with pytest.raises(ErrorePin, match="S-PIN"):
            await c.apri(born, "9999")
        # Clacson e luci vogliono sapere dov'è l'auto.
        with pytest.raises(ErroreComando, match="posizione"):
            await c.trova(formentor, None)
        with pytest.raises(ErroreComando, match="da 1 a 100"):
            await c.limite_ricarica(born, 120, None)
        cloud.rifiuta_comando = True
        with pytest.raises(ErroreComando, match="primary user"):
            await c.ricarica(born, True, None)
        assert cloud.comandi == []


# --- il flusso in Home Assistant ------------------------------------------------------------


@pytest.fixture
def catalogo() -> Iterator[None]:
    """I marchi del catalogo che puntano alla marca, come saranno quando il catalogo la conoscerà."""
    nuovi = {m: replace(PER_ID[m], nativo="seat_cupra", preimpostati={"marchio": m}) for m in ("cupra", "seat")}
    with patch.dict(PER_ID, nuovi):
        yield


async def test_avvia_e_completa_dal_browser(hass: HomeAssistant, cloud: FintoCloud) -> None:
    dispositivo = await avvia_browser(hass, {"marchio": "cupra"})
    assert dispositivo.codice_utente == "" and set(dispositivo.stato) == {"verifier", "state", "marchio"}
    assert (
        sfida(dispositivo.stato["verifier"]) in dispositivo.link and CUPRA.client_id.split("@")[0] in dispositivo.link
    )
    async with aiohttp.ClientSession() as s:
        finale = await _browser(s, dispositivo.link)
    # Un indirizzo di un altro login non passa.
    altro = Dispositivo(link="", codice_utente="", stato={**dispositivo.stato, "state": "ALTRO"})
    with pytest.raises(ErroreCredenziali, match="altro login"):
        await completa_browser(hass, {"marchio": "cupra"}, altro, finale)
    with pytest.raises(ErrorePin):
        await completa_browser(hass, {"marchio": "cupra", "pin": "0000"}, dispositivo, finale)
    async with aiohttp.ClientSession() as s:
        finale = await _browser(s, dispositivo.link)
    # Anche solo il codice va bene.
    codice, _ = leggi_incollato(finale)
    accesso_ = await completa_browser(hass, {"marchio": "cupra", "pin": "1234"}, dispositivo, codice)
    assert accesso_.unique_id == f"seat_cupra_cupra_{cloud.utente.lower()}"
    assert accesso_.titolo == "CUPRA · Mario@Esempio.it"
    assert accesso_.dati["pin"] == "1234" and accesso_.dati["marchio"] == "cupra"
    assert accesso_.dati["token"]["refresh_token"].startswith("RT-")
    assert len(accesso_.vetture) == 2

    # SEAT: l'app sua, e una voce sua.
    dispositivo = await avvia_browser(hass, {"marchio": "seat"})
    assert "seat%3A%2F%2Foauth-callback" in dispositivo.link
    async with aiohttp.ClientSession() as s:
        finale = await _browser(s, dispositivo.link)
    accesso_ = await completa_browser(hass, {"marchio": "seat"}, dispositivo, finale)
    assert accesso_.unique_id == f"seat_cupra_seat_{cloud.utente.lower()}" and accesso_.titolo.startswith("SEAT · ")
    with pytest.raises(ErroreApi, match="sconosciuto"):
        await avvia_browser(hass, {"marchio": "skoda"})


async def test_configurare_una_cupra(hass: HomeAssistant, cloud: FintoCloud, catalogo: None) -> None:
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "cupra"})
    assert r["step_id"] == "credenziali"
    assert {str(k) for k in r["data_schema"].schema} == {"pin"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"pin": "1234"})
    assert r["step_id"] == "browser"
    link = r["description_placeholders"]["link"]
    assert CUPRA.client_id.split("@")[0] in link
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"codice": "cupra://oauth-callback?code=NO"})
    assert r["errors"] == {"base": "codice"}
    async with aiohttp.ClientSession() as s:
        finale = await _browser(s, link)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"codice": finale})
    await hass.async_block_till_done()
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["title"] == "CUPRA · Mario@Esempio.it"
    voce = r["result"]
    assert voce.unique_id == f"seat_cupra_cupra_{cloud.utente.lower()}"
    assert r["data"][CONF_MARCA] == "seat_cupra" and r["data"][CONF_MARCHIO] == "cupra"
    assert r["data"]["marchio"] == "cupra" and r["data"]["token"]["refresh_token"].startswith("RT-")
    assert "pin" not in r["data"] and voce.options["pin"] == "1234"
    assert hass.states.get("sensor.saetta_batteria").state == "63"
    assert hass.states.get("lock.saetta_serratura").state == "locked"
    assert hass.states.get("sensor.cupra_formentor_batteria").state == "90"
    assert voce.runtime_data.auto[0]._normale.total_seconds() == 600
    await hass.config_entries.async_unload(voce.entry_id)
    await hass.async_block_till_done()


async def test_il_token_che_cambia_non_ricarica_l_integrazione(
    hass: HomeAssistant, cloud: FintoCloud, catalogo: None
) -> None:
    async with aiohttp.ClientSession() as s:
        sessione = dict((await _cliente(s)).sessione)
    voce = MockConfigEntry(
        domain=DOMAIN,
        title="CUPRA · Mario@Esempio.it",
        unique_id=f"seat_cupra_cupra_{cloud.utente.lower()}",
        data={
            CONF_FONTE: FONTE_CLOUD,
            CONF_MARCA: "seat_cupra",
            CONF_MARCHIO: "cupra",
            "marchio": "cupra",
            "token": {**sessione, "scade": "2000-01-01T00:00:00+00:00"},
        },
        options={"pin": "1234"},
    )
    voce.add_to_hass(hass)
    assert await hass.config_entries.async_setup(voce.entry_id)
    await hass.async_block_till_done()
    dati_prima = voce.runtime_data
    assert voce.data["token"]["refresh_token"] != sessione["refresh_token"]
    assert datetime.fromisoformat(voce.data["token"]["scade"]) > datetime.now(UTC)
    assert hass.states.get("sensor.saetta_batteria").state == "63"
    assert voce.runtime_data is dati_prima
    from custom_components.gdaauto.diagnostics import async_get_config_entry_diagnostics

    testo = str(await async_get_config_entry_diagnostics(hass, voce))
    for segreto in ("41.50", "1.812", C.VIN_BORN, "RT-", voce.data["token"]["access_token"], "1234"):
        assert segreto not in testo, segreto
    await hass.config_entries.async_unload(voce.entry_id)
    await hass.async_block_till_done()


async def test_il_rinnovo_rifiutato_chiede_il_reauth(hass: HomeAssistant, cloud: FintoCloud, catalogo: None) -> None:
    voce = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"seat_cupra_seat_{cloud.utente.lower()}",
        data={
            CONF_FONTE: FONTE_CLOUD,
            CONF_MARCA: "seat_cupra",
            CONF_MARCHIO: "seat",
            "marchio": "seat",
            "token": {
                "access_token": "VECCHIO",
                "refresh_token": "RT-MORTO",
                "scade": "2000-01-01T00:00:00+00:00",
                "user_id": cloud.utente,
            },
        },
    )
    voce.add_to_hass(hass)
    await hass.config_entries.async_setup(voce.entry_id)
    await hass.async_block_till_done()
    flussi = hass.config_entries.flow.async_progress()
    reauth = next(f for f in flussi if f["context"]["source"] == config_entries.SOURCE_REAUTH)
    # Il reauth riapre il browser, con l'app SEAT.
    assert reauth["step_id"] == "browser"
    modulo = await hass.config_entries.flow.async_configure(reauth["flow_id"], None)
    assert SEAT.client_id.split("@")[0] in modulo["description_placeholders"]["link"]
