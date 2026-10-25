"""Audi: l'interpretazione dello stato, il login dal browser, il cliente contro un cloud finto, e il flusso di HA."""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import hashlib
import json
from typing import Any
from urllib.parse import parse_qs, urlparse

import aiohttp
from aiohttp import web
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.gdaauto.const import DOMAIN
from custom_components.gdaauto.marche import MARCHE, audi
from custom_components.gdaauto.marche.audi import cliente as modulo_cliente, identita as modulo_identita
from custom_components.gdaauto.marche.audi.cliente import ClienteAudi, comandi_di
from custom_components.gdaauto.marche.audi.dati import interpreta, riassumi_viaggi, vettura_da
from custom_components.gdaauto.marche.audi.identita import (
    REGIONI,
    Identita,
    Regione,
    leggi_incollato,
    nuovo_pkce,
    x_qmauth,
)
from custom_components.gdaauto.marche.base import ACCESSO_CODICE_BROWSER, InAttesa
from custom_components.gdaauto.marche.catalogo import PER_ID
from custom_components.gdaauto.marche.errori import (
    CodiceSbagliato,
    ErroreComando,
    ErroreCredenziali,
    ErrorePin,
    ErroreRete,
)
from custom_components.gdaauto.vettura import StatoRicarica, StatoVettura, Vettura

from .audi_campioni import (
    A3_TFSI_E,
    POSIZIONE,
    Q4_IN_CARICA,
    Q8_ETRON,
    TUTTI_IN_ERRORE,
    VEICOLI,
    VIAGGI_LUNGO,
    VIN_Q4,
    VIN_Q8,
    viaggi_corti,
)

# --- l'interpretazione ----------------------------------------------------------------------


def test_una_q8_ferma_col_cavo() -> None:
    s = interpreta(Q8_ETRON)
    assert s.batteria == 69
    assert s.autonomia_km == 300
    assert s.odometro_km == 43740
    assert s.limite_ricarica == 70
    assert s.cavo_collegato is True
    assert s.stato_ricarica == StatoRicarica.NON_IN_CARICA  # pronta, ma in manuale e senza corrente
    assert s.potenza_ricarica_kw == 0.0
    assert s.ricarica_rapida is False
    assert s.chiusa is True
    assert s.portiere_aperte is False and s.bagagliaio_aperto is False and s.finestrini_aperti is False
    assert s.clima_acceso is False
    assert s.temperatura_clima_c == 15.5
    assert s.sbrinamento is None  # i lunotti dicono «invalid»
    assert s.ricarica_programmata is False  # la fascia del profilo «Home» è spenta
    assert s.ricarica_ora_inizio is None
    assert s.carburante_percento is None and s.autonomia_totale_km is None
    assert s.letto == datetime(2026, 8, 18, 21, 31, 52, tzinfo=UTC)
    assert s.grezzo["tagliando_km"] == 16898
    assert s.grezzo["tagliando_giorni"] == 381
    assert s.grezzo["limite_profilo"] == 60
    assert s.grezzo["luci_accese"] is False
    assert s.latitudine is None


def test_una_q4_in_carica_con_la_portiera_aperta() -> None:
    s = interpreta(Q4_IN_CARICA, POSIZIONE["data"])
    assert s.batteria == 54 and s.autonomia_km == 212
    assert s.stato_ricarica == StatoRicarica.IN_CARICA
    assert s.in_carica is True
    assert s.potenza_ricarica_kw == 10.8
    assert s.tempo_ricarica_min == 95
    assert s.ricarica_rapida is False
    assert s.limite_ricarica == 80
    assert s.chiusa is False
    assert s.portiere_aperte is True and s.bagagliaio_aperto is True and s.finestrini_aperti is True
    assert s.grezzo["cofano_aperto"] is False
    assert s.clima_acceso is True and s.temperatura_clima_c == 21.5
    assert s.sbrinamento is True
    assert s.temperatura_esterna_c == 8.0
    assert s.temperatura_batteria_c == 21.0  # la media fra 20 e 22 °C
    assert s.in_sonno is False and s.quadro_acceso is False
    assert s.ricarica_programmata is True
    assert (s.ricarica_ora_inizio, s.ricarica_ora_fine) == ("23:30", "06:00")
    assert (s.latitudine, s.longitudine, s.posizione_con_segno) == (45.464211, 9.191383, True)
    assert s.letto == datetime(2026, 10, 17, 18, 43, tzinfo=UTC)  # la posizione è la più fresca
    assert s.grezzo["velocita_ricarica_kmh"] == 52


def test_una_a3_tfsi_e_con_benzina_e_batteria() -> None:
    s = interpreta(A3_TFSI_E)
    assert s.batteria == 81 and s.autonomia_km == 48
    assert s.carburante_percento == 62
    assert s.autonomia_carburante_km == 418
    assert s.autonomia_totale_km == 466
    # Spina pronta in modo «timer»: aspetta l'orario.
    assert s.stato_ricarica == StatoRicarica.PROGRAMMATA
    assert s.ricarica_rapida is None  # «invalid»
    assert s.in_sonno is True
    assert s.grezzo["cambio_olio_km"] == 9100
    assert s.grezzo["tipo_auto"] == "hybrid"


def test_una_risposta_vuota_non_dice_niente() -> None:
    s = interpreta({})
    assert s == StatoVettura(grezzo=s.grezzo)
    assert s.letto is None


def test_le_ricariche_finite_e_in_errore() -> None:
    def con(stato: str, tipo: str = "dc") -> StatoVettura:
        dati = json.loads(json.dumps(Q4_IN_CARICA))
        dati["charging"]["chargingStatus"]["value"].update(chargingState=stato, chargeType=tipo)
        return interpreta(dati)

    assert con("charging").ricarica_rapida is True
    assert con("chargePurposeReachedAndConservation").stato_ricarica == StatoRicarica.COMPLETATA
    assert con("chargePurposeReachedAndConservation").potenza_ricarica_kw == 0.0
    assert con("error").stato_ricarica == StatoRicarica.ERRORE
    assert con("discharging").v2l_attivo is True
    assert con("qualcosa di nuovo").stato_ricarica is None


def test_le_auto_dell_account_e_i_comandi() -> None:
    q8, q4 = (vettura_da(v) for v in VEICOLI["data"])
    assert q8 is not None and q4 is not None
    assert (q8.vin, q8.modello, q8.nome, q8.titolo, q8.condivisa) == (
        VIN_Q8,
        "Q8 Sportback e-tron",
        "Anello",
        "Anello",
        False,
    )
    assert q4.titolo == "Audi Q4 Sportback e-tron" and q4.condivisa is True
    assert comandi_di(q8) == {"chiudi", "apri", "trova", "clima", "ricarica", "limite_ricarica"}
    # Le capacità sospese (con uno «status») non contano.
    assert comandi_di(q4) == {"ricarica", "limite_ricarica"}
    assert vettura_da({"model": "senza vin"}) is None


def test_la_settimana_e_il_consumo_dalle_statistiche_di_viaggio() -> None:
    ora = datetime(2026, 10, 18, 12, tzinfo=UTC)
    campi = riassumi_viaggi(VIAGGI_LUNGO, viaggi_corti("2026-10-17"), ora)
    # 40 km a 18 kWh/100 km (l'ultimo record del viaggio) e 60 km a 25: il vecchio viaggio di giugno no.
    assert campi == {"km_7_giorni": 100.0, "energia_7_giorni_kwh": 22.2, "consumo_medio_kwh_100km": 22.2}
    # Senza viaggi della settimana, il consumo dall'ultimo azzeramento.
    assert riassumi_viaggi(VIAGGI_LUNGO, {}, ora) == {"consumo_medio_kwh_100km": 22.1}
    assert riassumi_viaggi({}, {}, ora) == {}


def test_quello_che_si_incolla() -> None:
    assert leggi_incollato(" myaudi:///?code=ABC&state=S1 ") == ("ABC", "S1")
    assert leggi_incollato("myaudi:///#code=ABC") == ("ABC", None)
    assert leggi_incollato("ABC.123-xyz") == ("ABC.123-xyz", None)
    with pytest.raises(CodiceSbagliato):
        leggi_incollato("")
    with pytest.raises(CodiceSbagliato, match="access_denied"):
        leggi_incollato("myaudi:///?error=access_denied")
    with pytest.raises(CodiceSbagliato):
        leggi_incollato("https://www.audi.it/it/myaudi")


def test_x_qmauth_cambia_ogni_cento_secondi() -> None:
    assert x_qmauth(1_700_000_000).startswith("v1:01da27b0:")
    assert x_qmauth(1_700_000_000) == x_qmauth(1_700_000_099)
    assert x_qmauth(1_700_000_000) != x_qmauth(1_700_000_100)


def test_il_contratto() -> None:
    assert MARCHE["audi"] is audi
    assert audi.ACCESSO == ACCESSO_CODICE_BROWSER
    assert audi.VUOLE_PIN is True
    assert audi.INTERVALLO_MINUTI >= 15
    assert [c.nome for c in audi.CAMPI] == ["regione", "pin"]
    assert {v for v, _ in audi.CAMPI[0].scelte} == set(REGIONI)


# --- il cloud finto -------------------------------------------------------------------------------


def jwt(**corpo: Any) -> str:
    return "x." + base64.urlsafe_b64encode(json.dumps(corpo).encode()).decode().rstrip("=") + ".y"


ID_TOKEN = jwt(sub="b0f3c2d1-utente", email="Mario.Rossi@example.it")


class FintoAudi:
    """Il login IDK, il BFF delle auto e il vecchio cloud MBB delle statistiche, su 127.0.0.1."""

    def __init__(self) -> None:
        self.url = ""
        self.validi = {"A1"}
        self.refresh = {"R1": ("A2", "R2")}
        self.sfide: dict[str, str] = {}
        self.confermato = False
        self.guasto_token = False
        self.stati: dict[str, dict[str, Any]] = {VIN_Q8: Q8_ETRON, VIN_Q4: Q4_IN_CARICA}
        self.posizioni: dict[str, dict[str, Any]] = {VIN_Q8: POSIZIONE}
        self.esito = "successful"
        self.ricarica_vecchia = False
        self.chiamate: list[tuple[str, str, Any]] = []

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_route("*", "/{coda:.*}", self._gestisci)
        return app

    def _token(self, access: str, refresh: str) -> web.Response:
        return web.json_response(
            {"access_token": access, "refresh_token": refresh, "id_token": ID_TOKEN, "expires_in": 3600}
        )

    async def _gestisci(self, r: web.Request) -> web.StreamResponse:
        percorso = r.path
        corpo: Any = None
        if r.method in ("POST", "PUT") and r.can_read_body:
            testo = await r.text()
            corpo = dict(await r.post()) if "form" in r.content_type else (json.loads(testo) if testo else None)
        self.chiamate.append((r.method, percorso, corpo))

        if percorso == "/auth/v1/idk/oidc/openid-configuration":
            return web.json_response(
                {
                    "authorization_endpoint": f"{self.url}/oidc/v1/authorize",
                    "device_authorization_endpoint": f"{self.url}/oidc/v1/device_authorization",
                    "token_endpoint": f"{self.url}/auth/v1/idk/oidc/token",
                }
            )
        if percorso == "/oidc/v1/device_authorization":
            assert corpo["client_id"] == modulo_identita.CLIENT_ID
            return web.json_response(
                {
                    "device_code": "DC1",
                    "user_code": "AUDI-1234",
                    "verification_uri": f"{self.url}/device",
                    "verification_uri_complete": f"{self.url}/device?user_code=AUDI-1234",
                    "interval": 5,
                    "expires_in": 600,
                }
            )
        if percorso == "/auth/v1/idk/oidc/token":
            return self._scambio(r, corpo)
        if percorso.startswith("/vehicle/v1/"):
            if r.headers.get("Authorization", "").removeprefix("Bearer ") not in self.validi:
                return web.json_response({"error": {"message": "token expired"}}, status=401)
            return self._bff(r, percorso.removeprefix("/vehicle/v1/"), corpo)
        if percorso == "/mbb/mobile/register/v1":
            return web.json_response({"client_id": "XCLIENT"})
        if percorso == "/mbb/mobile/oauth2/v1/token":
            assert corpo["grant_type"] == "id_token" and corpo["token"] == ID_TOKEN
            return web.json_response({"access_token": "MBB1", "expires_in": 3600})
        if percorso.startswith("/mal/api/bs/tripstatistics/v1/vehicles/"):
            assert r.headers["Authorization"] == "Bearer MBB1" and r.headers["X-Client-ID"] == "XCLIENT"
            ieri = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
            return web.json_response(VIAGGI_LUNGO if percorso.endswith("longTerm") else viaggi_corti(ieri))
        return web.json_response({"error": "sconosciuto"}, status=404)

    def _scambio(self, r: web.Request, corpo: dict[str, str]) -> web.Response:
        if self.guasto_token:
            return web.Response(status=503, text="Service Unavailable")
        tipo = corpo["grant_type"]
        if tipo == "authorization_code":
            assert r.headers["X-QMAuth"].startswith("v1:01da27b0:")
            assert corpo["redirect_uri"] == "myaudi:///"
            if corpo["code"] == "ATTESTAZIONE":
                return web.json_response(
                    {"error": "invalid_request", "error_description": "invalid assertion headers"}, status=400
                )
            sfida = base64.urlsafe_b64encode(hashlib.sha256(corpo["code_verifier"].encode()).digest()).rstrip(b"=")
            if self.sfide.get(corpo["code"]) != sfida.decode():
                return web.json_response({"error": "invalid_grant", "error_description": "code invalid"}, status=400)
            self.validi.add("A1")
            return self._token("A1", "R1")
        if tipo == "refresh_token":
            if corpo["refresh_token"] not in self.refresh:
                return web.json_response({"error": "invalid_grant", "error_description": "expired"}, status=400)
            access, refresh = self.refresh.pop(corpo["refresh_token"])
            self.validi.add(access)
            self.refresh[refresh] = (access + "x", refresh + "x")
            return self._token(access, refresh)
        if tipo == "urn:ietf:params:oauth:grant-type:device_code":
            if not self.confermato:
                return web.json_response({"error": "authorization_pending"}, status=400)
            return self._token("A1", "R1")
        return web.json_response({"error": "unsupported_grant_type"}, status=400)

    def _bff(self, r: web.Request, resto: str, corpo: Any) -> web.Response:
        if resto == "vehicles":
            return web.json_response(VEICOLI)
        vin, _, azione = resto.removeprefix("vehicles/").partition("/")
        if azione == "selectivestatus":
            assert "charging" in r.query["jobs"].split(",")
            return web.json_response(self.stati[vin], headers={"Vcf-Remaining-Calls": "742"})
        if azione == "parkingposition":
            if vin not in self.posizioni:
                return web.Response(status=204)
            return web.json_response(self.posizioni[vin])
        if azione == "pendingrequests":
            return web.json_response({"data": [{"id": "RQ1", "status": self.esito}]})
        if azione in ("access/lock", "access/unlock"):
            if corpo.get("spin") != "1234":
                return web.json_response(
                    {"error": {"message": "S-PIN invalid", "errorCode": "spin.invalid", "group": 2}}, status=403
                )
            return web.json_response({"data": {"requestID": "RQ1"}})
        if azione in ("charging/start", "charging/stop") and self.ricarica_vecchia:
            return web.json_response({"error": {"message": "Not found"}}, status=404)
        if azione in (
            "climatisation/start",
            "climatisation/stop",
            "charging/start",
            "charging/stop",
            "charging/start-stop",
            "honkandflash",
        ):
            return web.json_response({"data": {"requestID": "RQ1"}})
        if azione == "charging/settings" and r.method == "PUT":
            return web.Response(status=204)
        return web.json_response({"error": {"message": f"sconosciuto: {azione}"}}, status=404)

    def comandi(self) -> list[tuple[str, str, Any]]:
        lettura = ("selectivestatus", "parkingposition", "pendingrequests", "/vehicles")
        return [
            (m, p.split("/", 5)[-1], c)
            for m, p, c in self.chiamate
            if p.startswith("/vehicle/v1/vehicles/") and not p.endswith(lettura)
        ]


@pytest.fixture
async def cloud(socket_enabled: None) -> AsyncGenerator[FintoAudi]:
    finto = FintoAudi()
    runner = web.AppRunner(finto.app())
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    finto.url = f"http://127.0.0.1:{runner.addresses[0][1]}"
    yield finto
    await runner.cleanup()


def _token(access: str = "A1", refresh: str = "R1", fra: timedelta = timedelta(hours=1)) -> dict[str, Any]:
    return {
        "access_token": access,
        "refresh_token": refresh,
        "id_token": ID_TOKEN,
        "scade": (datetime.now(UTC) + fra).isoformat(),
    }


def _cliente(s: aiohttp.ClientSession, finto: FintoAudi, token: dict[str, Any] | None = None, salvati=None):
    return ClienteAudi(
        s,
        regione="europa",
        token=token if token is not None else _token(),
        salva=(salvati.append if salvati is not None else None),
        bff=finto.url,
        mbb=f"{finto.url}/mbb",
        mal=f"{finto.url}/mal",
        attesa_s=0,
    )


Q8 = Vettura(vin=VIN_Q8, marca="audi", modello="Q8 Sportback e-tron", nome="Anello")


# --- il login --------------------------------------------------------------------------------------


async def test_il_login_dal_browser_e_lo_scambio_del_codice(cloud) -> None:
    async with aiohttp.ClientSession() as s:
        identita = Identita(s, cloud.url)
        verifier, sfida = nuovo_pkce()
        link = await identita.indirizzo_login(sfida, "S1", "N1")
        q = parse_qs(urlparse(link).query)
        assert link.startswith(f"{cloud.url}/oidc/v1/authorize?")
        assert q["client_id"] == [modulo_identita.CLIENT_ID]
        assert q["redirect_uri"] == ["myaudi:///"]
        assert q["code_challenge_method"] == ["S256"] and q["state"] == ["S1"]
        cloud.sfide["CODICE"] = q["code_challenge"][0]

        token = await identita.scambia_codice_login("CODICE", verifier)
        assert token["access_token"] == "A1" and token["refresh_token"] == "R1"
        assert token["id_token"] == ID_TOKEN

        with pytest.raises(CodiceSbagliato):
            await identita.scambia_codice_login("CODICE", "un altro verifier")
        with pytest.raises(ErroreCredenziali, match="verifica dell'app"):
            await identita.scambia_codice_login("ATTESTAZIONE", verifier)


async def test_il_codice_del_dispositivo(cloud) -> None:
    async with aiohttp.ClientSession() as s:
        identita = Identita(s, cloud.url)
        codice = await identita.chiedi_codice()
        assert codice["codice"] == "AUDI-1234" and codice["link"].endswith("user_code=AUDI-1234")
        with pytest.raises(InAttesa):
            await identita.scambia_codice_dispositivo(codice["device_code"])
        cloud.confermato = True
        assert (await identita.scambia_codice_dispositivo(codice["device_code"]))["refresh_token"] == "R1"


async def test_le_auto_e_lo_stato(cloud) -> None:
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, cloud)
        vetture = await c.vetture()
        assert [v.vin for v in vetture] == [VIN_Q8, VIN_Q4]
        assert c.account == "Mario.Rossi@example.it"
        stato = await c.stato(vetture[0])
        assert stato.batteria == 69 and stato.odometro_km == 43740
        assert (stato.latitudine, stato.longitudine) == (45.464211, 9.191383)
        assert stato.grezzo["chiamate_rimaste"] == 742
        assert stato.km_7_giorni == 100.0 and stato.consumo_medio_kwh_100km == 22.2
        # La Q4 non ha una posizione (in marcia, 204): lo stato arriva lo stesso.
        stato = await c.stato(vetture[1])
        assert stato.latitudine is None and stato.in_carica is True
        # Lo storico non si rilegge a ogni stato.
        prima = len([p for _, p, _ in cloud.chiamate if p.startswith("/mal/")])
        await c.stato(vetture[0])
        assert len([p for _, p, _ in cloud.chiamate if p.startswith("/mal/")]) == prima


async def test_il_token_scaduto_si_rinnova_e_si_salva(cloud) -> None:
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, cloud, _token("vecchio", "R1", -timedelta(minutes=1)), salvati)
        await c.vetture()
    assert c.token["access_token"] == "A2" and c.token["refresh_token"] == "R2"
    assert c.token["id_token"] == ID_TOKEN
    assert salvati == [c.token]


async def test_un_401_rinnova_e_riprova_una_volta(cloud) -> None:
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, cloud, _token("revocato", "R1"), salvati)
        assert len(await c.vetture()) == 2
    assert [t["access_token"] for t in salvati] == ["A2"]


async def test_il_refresh_rifiutato_chiede_di_rientrare(cloud) -> None:
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, cloud, _token("vecchio", "MORTO", -timedelta(minutes=1)))
        with pytest.raises(ErroreCredenziali):
            await c.vetture()


async def test_i_guasti_sono_rete_non_credenziali(cloud, socket_enabled: None) -> None:
    async with aiohttp.ClientSession() as s:
        cloud.guasto_token = True
        c = _cliente(s, cloud, _token("vecchio", "R1", -timedelta(minutes=1)))
        with pytest.raises(ErroreRete):
            await c.vetture()
        cloud.guasto_token = False
        cloud.stati[VIN_Q8] = TUTTI_IN_ERRORE
        with pytest.raises(ErroreRete, match="lavori in errore"):
            await _cliente(s, cloud).stato(Q8)
        spento = ClienteAudi(s, regione="europa", token=_token(), bff="http://127.0.0.1:9", attesa_s=0)
        with pytest.raises(ErroreRete):
            await spento.vetture()


# --- i comandi ---------------------------------------------------------------------------------------


async def test_chiudere_e_aprire_con_l_s_pin(cloud) -> None:
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, cloud)
        await c.chiudi(Q8, "1234")
        await c.apri(Q8, "1234")
        with pytest.raises(ErrorePin):
            await c.apri(Q8, None)
        with pytest.raises(ErrorePin):
            await c.apri(Q8, "0000")
    assert cloud.comandi() == [
        ("POST", "access/lock", {"spin": "1234"}),
        ("POST", "access/unlock", {"spin": "1234"}),
        ("POST", "access/unlock", {"spin": "0000"}),
    ]


async def test_un_comando_che_l_auto_non_esegue(cloud) -> None:
    cloud.esito = "failed"
    async with aiohttp.ClientSession() as s:
        with pytest.raises(ErroreComando, match="failed"):
            await _cliente(s, cloud).chiudi(Q8, "1234")


async def test_clima_ricarica_limite_e_lampeggio(cloud) -> None:
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, cloud)
        auto = replace(Q8, stato=await c.stato(Q8))
        await c.clima(auto, True, None, 15)  # la temperatura che l'auto ha già: nessun corpo
        await c.clima(auto, True, None, 22)
        await c.clima(auto, False, None)
        await c.ricarica(auto, True, None)
        cloud.ricarica_vecchia = True
        await c.ricarica(auto, False, None)
        await c.limite_ricarica(auto, 80, None)
        with pytest.raises(ErroreComando):
            await c.limite_ricarica(auto, 10, None)
        await c.trova(auto, None)
        with pytest.raises(ErroreComando):
            await c.destinazione(auto, "Casa", 45.0, 9.0)
    assert cloud.comandi() == [
        ("POST", "climatisation/start", None),
        (
            "POST",
            "climatisation/start",
            {
                "climatisationWithoutExternalPower": True,
                "climatizationAtUnlock": False,
                "windowHeatingEnabled": False,
                "zoneFrontLeftEnabled": False,
                "zoneFrontRightEnabled": False,
                "targetTemperature": 22,
                "targetTemperatureUnit": "celsius",
                "climatisationMode": "comfort",
            },
        ),
        ("POST", "climatisation/stop", None),
        ("POST", "charging/start", {}),
        ("POST", "charging/stop", {}),
        ("POST", "charging/start-stop", {"action": "stop"}),
        ("PUT", "charging/settings", {"targetSOC_pct": 80}),
        (
            "POST",
            "honkandflash",
            {"duration_s": 10, "mode": "flash", "userPosition": {"latitude": 45.464211, "longitude": 9.191383}},
        ),
    ]


def test_il_modulo_non_importa_home_assistant() -> None:
    import ast
    from pathlib import Path

    for nome in ("cliente.py", "dati.py", "identita.py"):
        albero = ast.parse((Path(modulo_cliente.__file__).parent / nome).read_text())
        for nodo in ast.walk(albero):
            if isinstance(nodo, ast.ImportFrom | ast.Import):
                moduli = [nodo.module or ""] if isinstance(nodo, ast.ImportFrom) else [a.name for a in nodo.names]
                assert not any(m.startswith("homeassistant") for m in moduli)


# --- il flusso di Home Assistant -----------------------------------------------------------------


@pytest.fixture
def verso_il_finto(cloud, monkeypatch):
    for regione in REGIONI:
        monkeypatch.setitem(REGIONI, regione, Regione(regione, cloud.url, "it-IT it"))
    monkeypatch.setattr(modulo_cliente, "MBB_OAUTH", f"{cloud.url}/mbb")
    monkeypatch.setattr(modulo_cliente, "MAL_EUROPA", f"{cloud.url}/mal")
    monkeypatch.setattr(modulo_cliente, "MAL_MONDO", f"{cloud.url}/mal")
    monkeypatch.setattr(modulo_identita, "IDENTITA", cloud.url)
    # Il catalogo punterà ad «audi»: la prova non aspetta che lo faccia.
    if PER_ID["audi"].nativo != "audi":
        monkeypatch.setitem(PER_ID, "audi", replace(PER_ID["audi"], nativo="audi"))
    return cloud


async def test_configurare_un_audi_dal_browser(hass: HomeAssistant, verso_il_finto) -> None:
    finto = verso_il_finto
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "audi"})
    assert r["step_id"] == "credenziali"
    assert {str(k) for k in r["data_schema"].schema} == {"regione", "pin"}
    # Negli Stati Uniti si entra dal browser, incollando l'indirizzo finale.
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"regione": "usa", "pin": "1234"})
    assert r["step_id"] == "browser"
    link = r["description_placeholders"]["link"]
    q = parse_qs(urlparse(link).query)
    assert link.startswith(f"{finto.url}/oidc/v1/authorize?")

    # Un codice che Audi non accetta: si resta sul passo, con l'errore.
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"codice": "myaudi:///?code=FALSO"})
    # Il perché del rifiuto si vede nel modulo, non solo «il codice non va».
    assert r["errors"] == {"base": "codice_motivo"}
    assert r["description_placeholders"]["motivo"]
    finto.sfide["BUONO"] = q["code_challenge"][0]
    incollato = f"myaudi:///?code=BUONO&state={q['state'][0]}"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"codice": incollato})
    await hass.async_block_till_done()
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["title"] == "Audi · Mario.Rossi@example.it"
    voce = r["result"]
    assert voce.unique_id == "audi_b0f3c2d1-utente"
    assert voce.data["regione"] == "usa"
    assert voce.data["token"]["refresh_token"] == "R1"
    assert "pin" not in voce.data and voce.options["pin"] == "1234"

    assert hass.states.get("sensor.anello_batteria").state == "69"
    assert hass.states.get("sensor.audi_q4_sportback_e_tron_batteria").state == "54"
    assert hass.states.get("lock.anello_serratura").state == "locked"

    # Il token che ruota si salva senza ricaricare.
    dati_prima = voce.runtime_data
    dati_prima.conto.cliente._salva({**voce.data["token"], "refresh_token": "R9"})
    await hass.async_block_till_done()
    assert voce.data["token"]["refresh_token"] == "R9"
    assert voce.runtime_data is dati_prima


async def test_in_europa_un_audi_si_configura_col_codice_del_dispositivo(hass: HomeAssistant, verso_il_finto) -> None:
    """In Europa Audi rifiuta lo scambio del codice del browser senza la verifica dell'app."""
    finto = verso_il_finto
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "audi"})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"regione": "europa"})
    assert r["step_id"] == "conferma_dispositivo"
    assert r["description_placeholders"]["codice"] == "AUDI-1234"
    # Prima che la persona confermi il codice: si aspetta.
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    assert r["errors"] == {"base": "in_attesa"}
    finto.confermato = True
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    await hass.async_block_till_done()
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["result"].data["regione"] == "europa"
    assert hass.states.get("sensor.anello_batteria").state == "69"
