"""Volkswagen: lo stato dei payload, il login col PKCE e il cliente contro un cloud finto, e il flusso in HA."""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator, Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
import hashlib
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
from custom_components.gdaauto.marche.errori import ErroreComando, ErroreCredenziali, ErrorePin, ErroreRete
from custom_components.gdaauto.marche.volkswagen import (
    CAMPI,
    VUOLE_PIN,
    ClienteVolkswagen,
    accesso,
    avvia_browser,
    completa_browser,
)
from custom_components.gdaauto.marche.volkswagen.accesso import (
    CLIENT_ID,
    CONDIZIONI,
    TokenVolkswagen,
    codice_da,
    coppia_pkce,
    dal_jwt,
    indirizzo_login,
    sfida_di,
)
from custom_components.gdaauto.marche.volkswagen.cliente import comandi_di, lavori_di, vettura_da_json
from custom_components.gdaauto.marche.volkswagen.dati import consumo_medio, interpreta, posizione
from custom_components.gdaauto.vettura import StatoRicarica, StatoVettura, Vettura

from .volkswagen_campioni import (
    ELENCO,
    EUP,
    GTE,
    ID3,
    PARCHEGGIO,
    VIAGGIO_LUNGO,
    VIN_EUP,
    VIN_GTE,
    VIN_ID3,
)

# --- il contratto e l'anagrafica -------------------------------------------------------


def test_il_contratto() -> None:
    modulo = MARCHE["volkswagen"]
    assert modulo.ACCESSO == ACCESSO_CODICE_BROWSER
    assert [c.nome for c in CAMPI] == ["pin"] and CAMPI[0].segreto and not CAMPI[0].obbligatorio
    assert VUOLE_PIN is True
    assert modulo.INTERVALLO_MINUTI == 10 and modulo.INTERVALLO_IN_MOTO_SECONDI == 180
    # Per ora il catalogo non punta alla marca: la strada di Home Assistant c'è già.
    assert PER_ID["volkswagen"].ha


def _vetture() -> list[Vettura]:
    return [v for voce in ELENCO["data"] if (v := vettura_da_json(voce)) is not None]


def test_l_anagrafica_e_i_comandi() -> None:
    id3, eup, gte = _vetture()  # la diesel non c'è
    assert (id3.vin, id3.nome, id3.modello, id3.marca, id3.condivisa) == (
        VIN_ID3,
        "Fulmine",
        "ID.3",
        "volkswagen",
        False,
    )
    assert (eup.nome, eup.condivisa) == ("e-up!", True)
    assert gte.vin == VIN_GTE
    assert comandi_di(id3) == {"chiudi", "trova", "clima", "ricarica", "limite_ricarica"}
    # La serratura dell'e-up! è spenta dalla licenza, e clacson e luci non ci sono.
    assert comandi_di(eup) == {"clima", "ricarica", "limite_ricarica"}
    assert comandi_di(gte) == {"chiudi", "clima", "ricarica", "limite_ricarica"}
    assert comandi_di(Vettura(vin="X", marca="volkswagen", modello="")) == {"clima", "ricarica", "limite_ricarica"}
    assert lavori_di(id3) == [
        "access",
        "charging",
        "batteryChargingCare",
        "climatisation",
        "fuelStatus",
        "readiness",
        "vehicleHealthInspection",
        "measurements",
    ]
    assert lavori_di(eup) == ["charging", "climatisation", "fuelStatus"]


# --- lo stato dei payload ---------------------------------------------------------------


def test_una_id3_in_carica_alla_colonnina() -> None:
    s = interpreta(ID3)
    assert (s.batteria, s.autonomia_km, s.odometro_km) == (63, 251, 23817)
    assert s.stato_ricarica == StatoRicarica.IN_CARICA and s.in_carica
    assert s.ricarica_rapida is True
    assert s.potenza_ricarica_kw == 87.5
    assert s.tempo_ricarica_min == 25
    assert s.limite_ricarica == 80
    assert s.cavo_collegato is True
    assert s.ricarica_programmata is False
    assert s.v2l_attivo is False
    assert (s.clima_acceso, s.temperatura_clima_c, s.sbrinamento) == (False, 21.5, False)
    assert (s.chiusa, s.portiere_aperte, s.bagagliaio_aperto, s.finestrini_aperti) == (True, False, False, False)
    assert s.temperatura_esterna_c == 14.0
    assert s.temperatura_batteria_c == 31.0
    assert (s.quadro_acceso, s.in_sonno) == (False, False)
    assert s.letto == datetime(2025, 4, 20, 18, 5, 42, tzinfo=UTC)
    # Un'elettrica non ha serbatoio.
    assert (s.carburante_percento, s.autonomia_carburante_km, s.autonomia_totale_km) == (None, None, None)
    assert s.pneumatici_kpa == {}


def test_una_e_up_aperta_col_clima_acceso() -> None:
    s = interpreta(EUP)
    # Senza «batteryStatus» la carica viene dal «fuelStatus».
    assert (s.batteria, s.autonomia_km) == (44, 96)
    assert s.stato_ricarica == StatoRicarica.NON_IN_CARICA
    # I minuti che restano valgono solo in carica.
    assert s.tempo_ricarica_min is None
    assert s.cavo_collegato is False
    assert s.ricarica_programmata is None
    assert (s.clima_acceso, s.temperatura_clima_c, s.sbrinamento) == (True, 22.0, True)
    assert (s.chiusa, s.portiere_aperte, s.bagagliaio_aperto) == (False, True, False)
    # I finestrini dell'e-up! non si sentono: non si sa.
    assert s.finestrini_aperti is None
    assert s.odometro_km is None and s.in_sonno is None


def test_una_golf_gte_ibrida_in_attesa() -> None:
    s = interpreta(GTE)
    assert (s.batteria, s.autonomia_km) == (35, 18)
    assert (s.carburante_percento, s.autonomia_carburante_km, s.autonomia_totale_km) == (58, 410, 428)
    assert s.stato_ricarica == StatoRicarica.PROGRAMMATA
    assert s.ricarica_programmata is True
    assert s.cavo_collegato is True
    assert s.potenza_ricarica_kw == 0.0
    assert s.limite_ricarica is None
    assert s.temperatura_esterna_c == -2.5
    assert s.in_sonno is True
    assert s.chiusa is None


def test_parcheggio_e_consumo() -> None:
    assert posizione(PARCHEGGIO) == (45.07049, 7.68201)
    assert posizione(None) is None and posizione({"data": {"lat": 0, "lon": 0}}) is None
    assert consumo_medio(VIAGGIO_LUNGO) == 16.4
    assert consumo_medio({"data": {"averageElectricConsumption": 0}}) is None


# --- il login nel browser ---------------------------------------------------------------


def test_il_link_ha_il_pkce() -> None:
    verifier, sfida = coppia_pkce()
    assert sfida == base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert 43 <= len(verifier) <= 128
    link = indirizzo_login(sfida, "STATO", "NONCE")
    assert link.startswith("https://identity.vwgroup.io/oidc/v1/authorize?")
    domanda = dict(parse_qsl(urlsplit(link).query))
    assert domanda["client_id"] == CLIENT_ID
    assert domanda["redirect_uri"] == "weconnect://authenticated"
    assert (domanda["code_challenge"], domanda["code_challenge_method"]) == (sfida, "S256")
    assert domanda["state"] == "STATO"
    assert verifier not in link


def test_l_indirizzo_incollato_in_tutte_le_forme() -> None:
    intero = "weconnect://authenticated#state=S1&code=C1&id_token=I&access_token=A&token_type=bearer"
    assert codice_da(intero) == {
        "state": "S1",
        "code": "C1",
        "id_token": "I",
        "access_token": "A",
        "token_type": "bearer",
    }
    assert codice_da("  weconnect://authenticated?code=C2&state=S2 ") == {"code": "C2", "state": "S2"}
    assert codice_da("#state=S3&code=C3")["code"] == "C3"
    assert codice_da("code=C4")["code"] == "C4"
    assert codice_da('"C5"') == {"code": "C5"}
    with pytest.raises(ErroreCredenziali, match="niente"):
        codice_da("   ")
    with pytest.raises(ErroreCredenziali, match="access_denied"):
        codice_da("weconnect://authenticated#error=access_denied&state=S")
    with pytest.raises(ErroreCredenziali, match="code="):
        codice_da("weconnect://authenticated#state=S")
    with pytest.raises(ErroreCredenziali, match="condizioni") as err:
        codice_da("https://identity.vwgroup.io/signin-service/v1/x/terms-and-conditions?relayState=1")
    assert str(err.value) == CONDIZIONI and "app" in CONDIZIONI


# --- il cloud finto -------------------------------------------------------------------------


def _jwt(contenuto: dict[str, Any]) -> str:
    def b64(d: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

    return f"{b64({'alg': 'RS256'})}.{b64(contenuto)}.firma"


@dataclass
class FintoCloud:
    url: str = ""
    utente: str = "utente-1"
    #: codice → code_challenge, come li ha visti il login
    codici: dict[str, str] = field(default_factory=dict)
    access_validi: set[str] = field(default_factory=set)
    refresh_validi: set[str] = field(default_factory=set)
    emessi: int = 0
    rinnovi: int = 0
    scambio_bff_rotto: bool = False
    scambio_identita_rotto: bool = False
    rinnovo_guasto: bool = False
    stato_guasto: int = 0
    pin: str = "1234"
    rifiuta_comando: bool = False
    chiamate: list[str] = field(default_factory=list)
    comandi: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    punti_token: list[str] = field(default_factory=list)

    def app(self) -> web.Application:
        app = web.Application()
        r = app.router
        r.add_get("/oidc/v1/authorize", self._autorizza)
        r.add_post("/login/v1/idk/token", self._token_bff)
        r.add_post("/oidc/v1/token", self._token_identita)
        r.add_route("*", "/vehicle/v1/{resto:.*}", self._veicoli)
        return app

    # --- il login (quello che farebbe il browser) e i token ----------------------------

    async def _autorizza(self, r: web.Request) -> web.Response:
        q = r.query
        assert q["client_id"] == CLIENT_ID and q["code_challenge_method"] == "S256"
        assert "code" in q["response_type"].split()
        self.emessi += 1
        codice = f"COD-{self.emessi}"
        self.codici[codice] = q["code_challenge"]
        frammento = f"state={q['state']}&code={codice}&id_token=IDT&access_token=AT-DEL-FRAMMENTO&token_type=bearer"
        return web.Response(status=302, headers={"Location": f"{q['redirect_uri']}#{frammento}"})

    def _rilascia(self) -> dict[str, Any]:
        self.emessi += 1
        n = self.emessi
        access = _jwt({"sub": self.utente, "exp": int(time.time()) + 3600, "n": n})
        self.access_validi.add(access)
        self.refresh_validi.add(f"RT-{n}")
        idt = _jwt({"sub": self.utente, "email": "Mario@Esempio.it"})
        return {"accessToken": access, "idToken": idt, "refreshToken": f"RT-{n}", "expiresIn": 3600}

    async def _token(self, r: web.Request, punto: str) -> web.Response:
        f = await r.post()
        self.punti_token.append(f"{punto} {f['grant_type']}")
        assert f["client_id"] == CLIENT_ID
        if f["grant_type"] == "authorization_code":
            if (punto == "bff" and self.scambio_bff_rotto) or (punto == "identita" and self.scambio_identita_rotto):
                return web.json_response({"error": "server_error"}, status=500)
            sfida = self.codici.pop(str(f["code"]), None)
            if sfida is None or sfida_di(str(f["code_verifier"])) != sfida:
                return web.json_response({"error": "invalid_grant"}, status=400)
            assert f["redirect_uri"] == "weconnect://authenticated"
            return web.json_response(self._rilascia())
        assert f["grant_type"] == "refresh_token"
        if self.rinnovo_guasto:
            return web.Response(status=503, text="giù")
        if f["refresh_token"] not in self.refresh_validi:
            return web.json_response({"error": "invalid_grant"}, status=400)
        self.refresh_validi.discard(str(f["refresh_token"]))
        self.rinnovi += 1
        return web.json_response(self._rilascia())

    async def _token_bff(self, r: web.Request) -> web.Response:
        return await self._token(r, "bff")

    async def _token_identita(self, r: web.Request) -> web.Response:
        return await self._token(r, "identita")

    # --- le auto ---------------------------------------------------------------------

    async def _veicoli(self, r: web.Request) -> web.Response:
        resto = r.match_info["resto"]
        self.chiamate.append(f"{r.method} {resto}")
        if r.headers.get("Authorization", "").removeprefix("Bearer ") not in self.access_validi:
            return web.json_response({"error": {"message": "Unauthorized", "code": 401}}, status=401)
        assert r.headers["x-android-package-name"] == "com.volkswagen.weconnect"
        if r.method == "GET":
            return self._leggi(r, resto)
        corpo = json.loads(await r.text() or "{}")
        if resto == "spin/verify":
            if corpo["spin"] != self.pin:
                return web.json_response({"error": {"message": "SPIN invalid", "code": 4003}}, status=400)
            return web.Response(status=204)
        if self.rifiuta_comando:
            return web.json_response(
                {"error": {"message": "Forbidden", "info": "the user is not the primary user"}}, status=403
            )
        if resto.endswith(("/access/lock", "/access/unlock")) and corpo.get("spin") != self.pin:
            return web.json_response({"error": {"message": "S-PIN wrong", "code": 4029}}, status=403)
        self.comandi.append((r.method, resto.split("/", 2)[-1], corpo))
        return web.json_response({"data": {"requestID": f"R-{len(self.comandi)}"}}, status=202)

    def _leggi(self, r: web.Request, resto: str) -> web.Response:
        if resto == "vehicles":
            return web.json_response(ELENCO)
        if resto.endswith("/selectivestatus"):
            if self.stato_guasto:
                return web.Response(status=self.stato_guasto, text="guasto")
            vin = resto.split("/")[1]
            lavori = r.query["jobs"].split(",")
            assert "charging" in lavori
            stati = {VIN_ID3: (ID3, 207), VIN_EUP: (EUP, 200), VIN_GTE: (GTE, 200)}
            stato, http = stati[vin]
            return web.json_response({k: v for k, v in stato.items() if k in lavori}, status=http)
        if resto.endswith("/parkingposition"):
            if VIN_ID3 in resto:
                return web.json_response(PARCHEGGIO)
            return web.Response(status=204)
        if resto.startswith("trips/") and resto.endswith("/longterm/last"):
            if VIN_ID3 in resto:
                return web.json_response(VIAGGIO_LUNGO)
            return web.json_response({"error": {"message": "not found"}}, status=404)
        return web.json_response({"error": {"message": f"nessuna strada per {resto}"}}, status=404)


@pytest.fixture
async def cloud(socket_enabled: None) -> AsyncGenerator[FintoCloud]:
    finto = FintoCloud()
    runner = web.AppRunner(finto.app())
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    finto.url = f"http://127.0.0.1:{runner.addresses[0][1]}"
    with patch.object(accesso, "BFF", finto.url), patch.object(accesso, "IDENTITA", finto.url):
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
    return await TokenVolkswagen(s).scambia(codice_da(finale), verifier)


async def test_dal_codice_allo_stato(cloud: FintoCloud) -> None:
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        token = await _token(s)
        assert token["refresh_token"] and token["punto"] == "bff"
        assert dal_jwt(token["access_token"])["sub"] == "utente-1"
        c = ClienteVolkswagen(s, token, salvati.append)
        vetture = await c.vetture()
        assert [v.vin for v in vetture] == [VIN_ID3, VIN_EUP, VIN_GTE]
        id3 = await c.stato(vetture[0])
        assert (id3.batteria, id3.stato_ricarica, id3.limite_ricarica) == (63, StatoRicarica.IN_CARICA, 80)
        assert (id3.latitudine, id3.longitudine, id3.posizione_con_segno) == (45.07049, 7.68201, True)
        assert id3.consumo_medio_kwh_100km == 16.4
        await c.stato(vetture[0])
        # Il consumo si rilegge ogni mezz'ora, non a ogni giro.
        assert sum("longterm" in x for x in cloud.chiamate) == 1
        eup = await c.stato(vetture[1])
        # Niente posizione durante il viaggio (204), e niente viaggi: si legge lo stesso.
        assert (eup.batteria, eup.latitudine, eup.consumo_medio_kwh_100km) == (44, None, None)
        await c.stato(vetture[1])
        # I viaggi che l'auto non ha (404) non si chiedono più.
        assert sum("longterm" in x and VIN_EUP in x for x in cloud.chiamate) == 1
        assert not salvati  # nessun rinnovo servito


async def test_il_token_scaduto_si_rinnova(cloud: FintoCloud) -> None:
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        token = await _token(s)
        c = ClienteVolkswagen(s, {**token, "scade": time.time() - 10}, salvati.append)
        await c.vetture()
        assert cloud.rinnovi == 1
        assert salvati[-1]["refresh_token"] != token["refresh_token"]
        assert salvati[-1] == c.sessione
        await c.vetture()
        assert cloud.rinnovi == 1


async def test_il_token_rifiutato_si_rinnova_e_si_riprova(cloud: FintoCloud) -> None:
    async with aiohttp.ClientSession() as s:
        token = await _token(s)
        c = ClienteVolkswagen(s, token)
        cloud.access_validi.clear()  # il cloud lo ha dimenticato prima della scadenza
        assert len(await c.vetture()) == 3
        assert cloud.rinnovi == 1


async def test_il_rinnovo_rifiutato_chiede_il_login(cloud: FintoCloud) -> None:
    async with aiohttp.ClientSession() as s:
        token = await _token(s)
        cloud.refresh_validi.clear()
        c = ClienteVolkswagen(s, {**token, "scade": 0})
        with pytest.raises(ErroreCredenziali, match="browser"):
            await c.vetture()
        # Senza refresh token non c'è niente da rinnovare.
        c = ClienteVolkswagen(s, {"access_token": "x", "scade": 0})
        with pytest.raises(ErroreCredenziali):
            await c.vetture()


async def test_i_guasti_non_chiedono_di_rientrare(cloud: FintoCloud) -> None:
    async with aiohttp.ClientSession() as s:
        token = await _token(s)
        c = ClienteVolkswagen(s, token)
        vetture = await c.vetture()
        for http in (503, 429):
            cloud.stato_guasto = http
            with pytest.raises(ErroreRete):
                await c.stato(vetture[0])
        cloud.rinnovo_guasto = True
        c = ClienteVolkswagen(s, {**token, "scade": 0})
        with pytest.raises(ErroreRete):
            await c.vetture()
    async with aiohttp.ClientSession() as s:
        c = ClienteVolkswagen(s, token, bff="http://127.0.0.1:9")
        with pytest.raises(ErroreRete):
            await c.vetture()


async def test_lo_scambio_sul_sito_di_login(cloud: FintoCloud) -> None:
    cloud.scambio_bff_rotto = True
    async with aiohttp.ClientSession() as s:
        token = await _token(s)
        assert token["punto"] == "identita" and token["refresh_token"]
        c = ClienteVolkswagen(s, {**token, "scade": 0})
        await c.vetture()
        # Si rinnova dove i token sono nati.
        assert cloud.punti_token == [
            "bff authorization_code",
            "identita authorization_code",
            "identita refresh_token",
        ]


async def test_il_codice_sbagliato_o_rubato(cloud: FintoCloud) -> None:
    async with aiohttp.ClientSession() as s:
        verifier, sfida = coppia_pkce()
        finale = await _browser(s, indirizzo_login(sfida, "S", "N"))
        valori = codice_da(finale)
        # Con un verifier che non è quello del link il codice non vale.
        with pytest.raises(ErroreCredenziali, match="scaduto"):
            await TokenVolkswagen(s).scambia({"code": valori["code"]}, coppia_pkce()[0])
        with pytest.raises(ErroreCredenziali, match="scaduto"):
            await TokenVolkswagen(s).scambia({"code": "COD-INVENTATO"}, verifier)


async def test_senza_scambio_restano_i_token_dell_indirizzo(cloud: FintoCloud) -> None:
    cloud.scambio_bff_rotto = cloud.scambio_identita_rotto = True
    async with aiohttp.ClientSession() as s:
        verifier, sfida = coppia_pkce()
        valori = codice_da(await _browser(s, indirizzo_login(sfida, "S", "N")))
        token = await TokenVolkswagen(s).scambia(valori, verifier)
        assert token["access_token"] == "AT-DEL-FRAMMENTO" and token["refresh_token"] == ""
        with pytest.raises(ErroreRete):
            await TokenVolkswagen(s).scambia({"code": valori["code"]}, verifier)


# --- i comandi --------------------------------------------------------------------------------


async def test_i_comandi(cloud: FintoCloud) -> None:
    async with aiohttp.ClientSession() as s:
        c = ClienteVolkswagen(s, await _token(s))
        id3, _eup, gte = await c.vetture()
        id3.stato = await c.stato(id3)
        await c.stato(gte)
        await c.verifica_pin("1234")
        with pytest.raises(ErrorePin):
            await c.verifica_pin("0000")

        await c.chiudi(id3, "1234")
        await c.apri(id3, "1234")
        await c.clima(id3, True, None, 22)
        await c.clima(id3, False, None)
        await c.ricarica(id3, False, None)
        await c.ricarica(id3, True, None)
        await c.limite_ricarica(id3, 77, None)
        await c.limite_ricarica(gte, 30, None)
        await c.trova(id3, None)
        assert cloud.comandi == [
            ("POST", "access/lock", {"spin": "1234"}),
            ("POST", "access/unlock", {"spin": "1234"}),
            (
                "POST",
                "climatisation/start",
                {
                    "targetTemperature": 22.0,
                    "targetTemperatureUnit": "celsius",
                    "climatisationWithoutExternalPower": True,
                    "climatizationAtUnlock": False,
                    "windowHeatingEnabled": True,
                    "zoneFrontLeftEnabled": True,
                    "zoneFrontRightEnabled": False,
                },
            ),
            ("POST", "climatisation/stop", {}),
            ("POST", "charging/stop", {}),
            ("POST", "charging/start", {}),
            (
                "PUT",
                "charging/settings",
                {"maxChargeCurrentAC": "maximum", "autoUnlockPlugWhenChargedAC": "off", "targetSOC_pct": 80},
            ),
            (
                "PUT",
                "charging/settings",
                {"maxChargeCurrentAC_A": 10, "autoUnlockPlugWhenChargedAC": "on", "targetSOC_pct": 50},
            ),
            (
                "POST",
                "honkandflash",
                {
                    "mode": "honk-and-flash",
                    "duration_s": 10,
                    "userPosition": {"latitude": 45.07049, "longitude": 7.68201},
                },
            ),
        ]


async def test_i_comandi_che_non_vanno(cloud: FintoCloud) -> None:
    async with aiohttp.ClientSession() as s:
        c = ClienteVolkswagen(s, await _token(s))
        id3 = (await c.vetture())[0]
        with pytest.raises(ErrorePin, match="opzioni"):
            await c.chiudi(id3, None)
        with pytest.raises(ErrorePin, match="S-PIN"):
            await c.apri(id3, "9999")
        # Clacson e luci vogliono sapere dov'è l'auto.
        with pytest.raises(ErroreComando, match="dov'è"):
            await c.trova(replace(id3, stato=StatoVettura()), None)
        cloud.rifiuta_comando = True
        with pytest.raises(ErroreComando, match="primary user"):
            await c.ricarica(id3, True, None)
        assert cloud.comandi == []


# --- il flusso in Home Assistant ------------------------------------------------------------


@pytest.fixture
def catalogo() -> Iterator[None]:
    """Il marchio del catalogo che punta alla marca, come sarà quando il catalogo la conoscerà."""
    with patch.dict(PER_ID, {"volkswagen": replace(PER_ID["volkswagen"], nativo="volkswagen")}):
        yield


async def test_avvia_e_completa_dal_browser(hass: HomeAssistant, cloud: FintoCloud) -> None:
    dispositivo = await avvia_browser(hass, {})
    assert dispositivo.codice_utente == "" and set(dispositivo.stato) == {"verifier", "state", "nonce"}
    assert sfida_di(dispositivo.stato["verifier"]) in dispositivo.link
    async with aiohttp.ClientSession() as s:
        finale = await _browser(s, dispositivo.link)
    # Un indirizzo di un altro login non passa.
    altro = Dispositivo(link="", codice_utente="", stato={**dispositivo.stato, "state": "ALTRO"})
    with pytest.raises(ErroreCredenziali, match="altro login"):
        await completa_browser(hass, {}, altro, finale)
    with pytest.raises(ErrorePin):
        await completa_browser(hass, {"pin": "0000"}, dispositivo, finale)
    async with aiohttp.ClientSession() as s:
        finale = await _browser(s, dispositivo.link)
    # Anche solo il codice va bene.
    accesso_ = await completa_browser(hass, {"pin": "1234"}, dispositivo, codice_da(finale)["code"])
    assert accesso_.unique_id == "volkswagen_utente-1"
    assert accesso_.titolo == "Volkswagen · Mario@Esempio.it"
    assert accesso_.dati["pin"] == "1234" and accesso_.dati["token"]["refresh_token"]
    assert len(accesso_.vetture) == 3


async def test_configurare_una_volkswagen(hass: HomeAssistant, cloud: FintoCloud, catalogo: None) -> None:
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "volkswagen"})
    assert r["step_id"] == "credenziali"
    assert {str(k) for k in r["data_schema"].schema} == {"pin"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"pin": "1234"})
    assert r["step_id"] == "browser"
    link = r["description_placeholders"]["link"]
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"codice": "weconnect://authenticated#code=NO"})
    assert r["errors"] == {"base": "codice"}
    async with aiohttp.ClientSession() as s:
        finale = await _browser(s, link)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"codice": finale})
    await hass.async_block_till_done()
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["title"] == "Volkswagen · Mario@Esempio.it"
    voce = r["result"]
    assert voce.unique_id == "volkswagen_utente-1"
    assert r["data"][CONF_MARCA] == "volkswagen" and r["data"][CONF_MARCHIO] == "volkswagen"
    assert r["data"]["token"]["refresh_token"].startswith("RT-")
    assert "pin" not in r["data"] and voce.options["pin"] == "1234"
    assert hass.states.get("sensor.fulmine_batteria").state == "63"
    assert hass.states.get("lock.fulmine_serratura").state == "locked"
    assert hass.states.get("sensor.e_up_batteria").state == "44"
    assert hass.states.get("sensor.ibrida_batteria").state == "35"
    assert voce.runtime_data.auto[0]._normale.total_seconds() == 600
    await hass.config_entries.async_unload(voce.entry_id)
    await hass.async_block_till_done()


async def test_il_token_che_cambia_non_ricarica_l_integrazione(
    hass: HomeAssistant, cloud: FintoCloud, catalogo: None
) -> None:
    async with aiohttp.ClientSession() as s:
        token = await _token(s)
    voce = MockConfigEntry(
        domain=DOMAIN,
        title="Volkswagen · Mario@Esempio.it",
        unique_id="volkswagen_utente-1",
        data={
            CONF_FONTE: FONTE_CLOUD,
            CONF_MARCA: "volkswagen",
            CONF_MARCHIO: "volkswagen",
            "token": {**token, "scade": 0},
        },
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
    for segreto in ("45.07", "7.682", VIN_ID3, "RT-", token["access_token"], "1234"):
        assert segreto not in testo, segreto
    await hass.config_entries.async_unload(voce.entry_id)
    await hass.async_block_till_done()


async def test_il_rinnovo_rifiutato_chiede_il_reauth(hass: HomeAssistant, cloud: FintoCloud, catalogo: None) -> None:
    voce = MockConfigEntry(
        domain=DOMAIN,
        unique_id="volkswagen_utente-1",
        data={
            CONF_FONTE: FONTE_CLOUD,
            CONF_MARCA: "volkswagen",
            CONF_MARCHIO: "volkswagen",
            "token": {"access_token": "VECCHIO", "refresh_token": "RT-MORTO", "scade": 0, "punto": "bff"},
        },
    )
    voce.add_to_hass(hass)
    await hass.config_entries.async_setup(voce.entry_id)
    await hass.async_block_till_done()
    flussi = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == config_entries.SOURCE_REAUTH for f in flussi)
    reauth = next(f for f in flussi if f["context"]["source"] == config_entries.SOURCE_REAUTH)
    # Il reauth riapre il browser.
    assert reauth["step_id"] == "browser"
