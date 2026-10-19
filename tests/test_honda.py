"""Honda (My Honda+): il cruscotto, il cliente contro un cloud finto su 127.0.0.1
(login cifrato con la registrazione del dispositivo, rinnovo, stato, viaggi,
comandi, errori) e la configurazione in Home Assistant.

I payload sono quelli dei fixture di enricobattocchi/pymyhondaplus (``honda_campioni``).
"""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import time
from typing import Any
from unittest.mock import AsyncMock, patch
from urllib.parse import unquote

import aiohttp
from aiohttp import web
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.gdaauto.const import DOMAIN
from custom_components.gdaauto.marche import honda
from custom_components.gdaauto.marche.catalogo import PER_ID, Marchio
from custom_components.gdaauto.marche.errori import (
    CodiceInviato,
    CodiceSbagliato,
    ErroreApi,
    ErroreComando,
    ErroreCredenziali,
    ErroreRete,
)
from custom_components.gdaauto.marche.honda import cliente as modulo_cliente, cripto
from custom_components.gdaauto.marche.honda.cliente import ClienteHonda, comandi_di, entra, leggi_link
from custom_components.gdaauto.marche.honda.dati import gradi, interpreta, righe, storico
from custom_components.gdaauto.vettura import StatoRicarica, Vettura

from . import honda_campioni as c

# --- il cruscotto -----------------------------------------------------------------


def test_una_honda_e_ferma_alla_colonnina() -> None:
    s = interpreta(c.CRUSCOTTO)
    assert s.batteria == 82
    assert s.autonomia_km == 176
    assert s.autonomia_nominale_km == 181  # a clima spento: 176 + 5
    assert s.autonomia_totale_km == 176
    assert s.stato_ricarica == StatoRicarica.NON_IN_CARICA
    assert s.cavo_collegato is True
    assert s.tempo_ricarica_min is None
    assert s.limite_ricarica == 90  # l'auto è «AWAY»: vale il limite di fuori casa
    assert s.odometro_km == 43202
    assert s.velocita_kmh == 0
    assert s.quadro_acceso is False
    assert s.temperatura_interna_c == 24
    assert s.clima_acceso is False
    assert s.chiusa is True
    assert s.portiere_aperte is False
    assert s.bagagliaio_aperto is False
    assert s.finestrini_aperti is False
    assert s.pneumatici_allarme is False
    assert s.latitudine == pytest.approx(41.890251, abs=1e-6)
    assert s.longitudine == pytest.approx(12.492373, abs=1e-6)
    assert s.posizione_con_segno is True
    assert s.letto == datetime(2026, 3, 24, 22, 53, 1, tzinfo=UTC)
    assert s.carburante_percento is None
    # La posizione non finisce nel grezzo (né quindi nella diagnostica).
    assert "gpsData" not in s.grezzo


def test_in_carica_rapida_e_aperta() -> None:
    s = interpreta(c.in_carica())
    assert s.stato_ricarica == StatoRicarica.IN_CARICA
    assert s.in_carica is True
    assert s.tempo_ricarica_min == 35
    assert s.ricarica_rapida is True
    assert s.chiusa is False
    assert s.bagagliaio_aperto is True
    assert s.portiere_aperte is False  # il bagagliaio non è una portiera
    assert s.finestrini_aperti is True
    assert s.clima_acceso is True
    assert s.sbrinamento is True


def test_un_account_britannico_in_miglia() -> None:
    s = interpreta(c.britannica())
    assert s.autonomia_km == pytest.approx(283.2, abs=0.1)
    assert s.odometro_km == pytest.approx(1609.3, abs=0.1)
    assert s.velocita_kmh == pytest.approx(16.1, abs=0.1)
    assert s.temperatura_interna_c == 20.0
    assert s.longitudine == pytest.approx(-0.125, abs=1e-6)
    assert s.latitudine == pytest.approx(51.5)


def test_un_ibrida_col_serbatoio() -> None:
    s = interpreta(c.ibrida())
    assert s.carburante_percento == 100
    assert s.autonomia_carburante_km == 675
    assert s.autonomia_totale_km == 675


def test_gradi_e_link() -> None:
    assert gradi("43,33,11.902") == pytest.approx(43.553306, abs=1e-6)
    assert gradi("") is None
    assert gradi("12.5") == 12.5
    assert leggi_link("https://mobile-api.connected.honda-eu.com/auth/verify-link?type=mfa&key=AB%2BC%3D") == (
        "AB+C=",
        "mfa",
    )
    assert leggi_link("niente") == ("", "mfa")


def test_lo_storico_dei_viaggi() -> None:
    oggi = datetime.now(UTC).date()
    tabella = righe(c.viaggi(oggi))
    assert len(tabella) == 3 and tabella[0]["Mileage"] == "5"
    s = storico(tabella, oggi, elettrica=True, miglia=False)
    assert s == {"km_7_giorni": 11.0, "energia_7_giorni_kwh": 1.8, "consumo_medio_kwh_100km": 14.0}
    assert storico(tabella, oggi, elettrica=False, miglia=False) == {"km_7_giorni": 11.0}
    assert storico([], oggi, elettrica=True, miglia=False) == {}


def test_i_comandi_secondo_le_capacita() -> None:
    v = Vettura(vin=c.VIN, marca="honda", modello="Honda e", extra={"capacita": ["telematicsRemoteLockUnlock", "x"]})
    assert comandi_di(v) == {"chiudi", "apri"}
    assert comandi_di(Vettura(vin="X", marca="honda", modello="")) == set()


# --- il cloud finto ----------------------------------------------------------------


def _jwt(sub: str) -> str:
    corpo = base64.urlsafe_b64encode(json.dumps({"sub": sub}).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJSUzI1NiJ9.{corpo}.firma"


@dataclass
class FintaHonda:
    password: str = "giusta"
    chiave_link: str = "CHIAVE+/="
    registrati: set[str] = field(default_factory=set)
    in_attesa: str = ""
    sfide: dict[str, tuple[str, str]] = field(default_factory=dict)
    emessi: int = 0
    access_valido: str = ""
    refresh_valido: str = "R0"
    guasto: bool = False
    #: il comando risponde «in corso» queste volte prima dell'esito
    attese: int = 1
    esito: dict[str, Any] = field(default_factory=lambda: {"RequestStatus": "success"})
    comandi: list[tuple[str, str, Any]] = field(default_factory=list)
    chiamate: list[str] = field(default_factory=list)
    email_mandate: int = 0
    cruscotto: dict[str, Any] = field(default_factory=lambda: c.CRUSCOTTO)
    termini: bool = False

    def __post_init__(self) -> None:
        self.privata = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        der = self.privata.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        self.pubblica = base64.b64encode(der).decode()
        self._in_corso = 0

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_route("*", "/{resto:.*}", self._tutto)
        return app

    def _token(self) -> dict[str, Any]:
        self.emessi += 1
        self.access_valido = _jwt("utente-1") + str(self.emessi)
        self.refresh_valido = f"R{self.emessi}"
        return {"access_token": self.access_valido, "refresh_token": self.refresh_valido, "expires_in": 3599}

    async def _tutto(self, r: web.Request) -> web.Response:
        percorso = "/" + r.match_info["resto"]
        self.chiamate.append(f"{r.method} {percorso}")
        if self.guasto:
            return web.Response(status=503, text="<html>Service Unavailable</html>")
        if percorso.startswith("/auth/"):
            return await self._auth(r, percorso)
        assert r.headers["x-app-device-os"] == "android"
        if r.headers.get("authorization") != f"Bearer {self.access_valido}":
            return web.json_response({"error": "unauthorized"}, status=401)
        if percorso == "/user/get-login-info":
            assert r.query["userid"] == "utente-1"
            return web.json_response(c.ACCOUNT)
        assert r.headers["x-app-personal-id"] == "pid789"
        if percorso == "/tsp/dashboard-latest":
            if self.termini:
                return web.json_response({"errorCode": "10052"}, status=400)
            assert r.query["vin"] == c.VIN
            return web.json_response(self.cruscotto)
        if percorso == "/tsp/journey-history":
            mese = r.query["monthStart"]
            oggi = datetime.now(UTC).date()
            if mese.startswith(oggi.replace(day=1).isoformat()):
                return web.json_response(c.viaggi(oggi))
            return web.json_response({"maxPage": 1, "payload": {"def": c.COLONNE, "data": []}})
        if percorso == "/euw/tsp/async-command-status":
            assert r.query["id"] == "CMD-1"
            if self._in_corso > 0:
                self._in_corso -= 1
                return web.json_response({}, status=202)
            return web.json_response({"output": {**self.esito, "RequestId": "CMD-1"}})
        if percorso.startswith("/tsp/") and r.method in ("POST", "PUT"):
            self.comandi.append((r.method, percorso, await r.json()))
            self._in_corso = self.attese
            uri = "https://finto/euw/tsp/async-command-status?id=CMD-1"
            return web.json_response({"statusQueryGetUri": uri}, status=202)
        return web.json_response({"errorCode": "404"}, status=404)

    async def _auth(self, r: web.Request, percorso: str) -> web.Response:
        if percorso == "/auth/isv-prod/refresh":
            corpo = await r.json()
            if corpo["refreshToken"] != self.refresh_valido:
                return web.json_response({"error": "invalid_grant"}, status=400)
            return web.json_response(self._token())
        if percorso == "/auth/verify-link":
            assert r.query["dontRedirect"] == "true"
            # Come il server vero, il «+» della chiave resta un «+» (non uno spazio).
            grezza = dict(p.split("=", 1) for p in r.rel_url.raw_query_string.split("&"))
            if unquote(grezza["key"]) != self.chiave_link or not self.in_attesa:
                return web.Response(status=400, text="link-expired")
            self.registrati.add(self.in_attesa)
            return web.Response(text="ok")
        dati = cripto.apri_busta(await r.json(), self.privata)
        if percorso == "/auth/initiate-login":
            if dati["userPassword"] != self.password:
                return web.Response(status=401, text='{"errorCode":"invalid-credentials"}')
            if dati["devicePublicKey"] not in self.registrati:
                return web.Response(status=400, text='{"errorCode":"device-authenticator-not-registered"}')
            self.sfide["T1"] = (dati["keyIdentifier"], f"sfida-{time.time()}")
            return web.json_response({"transactionId": "T1", "signatureChallenge": self.sfide["T1"][1]})
        if percorso == "/auth/complete-login":
            chiave, sfida = self.sfide.pop(dati["transactionId"])
            assert dati["identityProvider"] == "isv-prod"
            if not cripto.verifica_firma(chiave, sfida, dati["signedChallengeResponse"]):
                return web.Response(status=401, text="bad-signature")
            return web.json_response(self._token())
        if percorso == "/auth/reset-device-authenticator":
            assert dati["resetType"] == "Add"
            self.in_attesa = dati["devicePublicKey"]
            self.email_mandate += 1
            return web.json_response({})
        return web.Response(status=404)


@pytest.fixture
async def cloud(socket_enabled: None) -> AsyncGenerator[tuple[FintaHonda, str]]:
    finta = FintaHonda()
    runner = web.AppRunner(finta.app())
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    modulo_cliente._IN_SOSPESO.clear()
    yield finta, f"http://127.0.0.1:{runner.addresses[0][1]}"
    await runner.cleanup()


async def _entra(s: aiohttp.ClientSession, finta: FintaHonda, base: str, **kw: Any) -> tuple[dict[str, Any], str]:
    return await entra(
        s, "Mario@Esempio.it", kw.pop("password", "giusta"), base=base, chiave_server=finta.pubblica, **kw
    )


async def _registrato(s: aiohttp.ClientSession, finta: FintaHonda, base: str) -> tuple[dict[str, Any], str]:
    with pytest.raises(CodiceInviato):
        await _entra(s, finta, base)
    link = f"{base}/auth/verify-link?type=mfa&key=CHIAVE%2B%2F%3D"
    return await _entra(s, finta, base, codice=link)


async def test_il_primo_login_registra_il_dispositivo(cloud) -> None:
    finta, base = cloud
    async with aiohttp.ClientSession() as s:
        with pytest.raises(CodiceInviato):
            await _entra(s, finta, base)
        assert finta.email_mandate == 1
        # Un link storto, o quello vecchio, non va; quello giusto sì.
        with pytest.raises(CodiceSbagliato):
            await _entra(s, finta, base, codice="ciao")
        with pytest.raises(CodiceSbagliato):
            await _entra(s, finta, base, codice=f"{base}/auth/verify-link?type=mfa&key=VECCHIA")
        token, pem = await _entra(s, finta, base, codice=f"{base}/auth/verify-link?type=mfa&key=CHIAVE%2B%2F%3D")
        assert token["access_token"] == finta.access_valido
        assert token["user_id"] == "utente-1"
        assert pem.startswith("-----BEGIN PRIVATE KEY-----")
        # Il dispositivo registrato rientra da solo, senza email nuove.
        di_nuovo, stesso = await _entra(s, finta, base, chiave=pem)
        assert stesso == pem and di_nuovo["access_token"] == finta.access_valido
        assert finta.email_mandate == 1
        assert not modulo_cliente._IN_SOSPESO


async def test_password_sbagliata_e_guasto(cloud) -> None:
    finta, base = cloud
    async with aiohttp.ClientSession() as s:
        with pytest.raises(ErroreCredenziali) as err:
            await _entra(s, finta, base, password="sbagliata")
        assert not isinstance(err.value, CodiceInviato)
        finta.guasto = True
        with pytest.raises(ErroreRete):
            await _entra(s, finta, base)


async def test_auto_stato_viaggi_e_rinnovo(cloud) -> None:
    finta, base = cloud
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        token, _ = await _registrato(s, finta, base)
        cliente = ClienteHonda(s, token, salvati.append, base=base)
        await cliente.accedi()
        (auto,) = await cliente.vetture()
        assert (auto.vin, auto.nome, auto.modello, auto.anno, auto.targa) == (
            c.VIN,
            "Honda e",
            "Honda e",
            2020,
            "GE395KM",
        )
        assert auto.condivisa is False
        assert comandi_di(auto) == {"chiudi", "apri", "trova", "clima", "ricarica", "limite_ricarica"}
        assert salvati[-1]["personal_id"] == "pid789"

        st = await cliente.stato(auto)
        assert st.batteria == 82 and st.odometro_km == 43202
        assert st.km_7_giorni == 11.0 and st.consumo_medio_kwh_100km == 14.0
        viaggi_letti = sum("journey-history" in x for x in finta.chiamate)
        await cliente.stato(auto)
        # I viaggi si rileggono ogni mezz'ora, non a ogni lettura.
        assert sum("journey-history" in x for x in finta.chiamate) == viaggi_letti

        # Il token scade: si rinnova col refresh token e si salva.
        cliente.token["scade"] = time.time() - 10
        await cliente.stato(auto)
        assert cliente.token["access_token"] == finta.access_valido
        assert salvati[-1]["refresh_token"] == finta.refresh_valido

        # Il cloud lo butta prima del previsto: un 401 rinnova e riprova.
        finta.access_valido = "altro"
        finta.refresh_valido = cliente.token["refresh_token"]
        await cliente.stato(auto)

        # Refresh token rifiutato: serve rientrare. Un guasto invece no.
        cliente.token["scade"] = 0
        finta.refresh_valido = "nessuno"
        with pytest.raises(ErroreCredenziali):
            await cliente.stato(auto)
        finta.guasto = True
        with pytest.raises(ErroreRete):
            await cliente.stato(auto)


async def test_i_comandi(cloud) -> None:
    finta, base = cloud
    async with aiohttp.ClientSession() as s:
        token, _ = await _registrato(s, finta, base)
        cliente = ClienteHonda(s, token, base=base, passo_comando=0.01, attesa_comando=2)
        (auto,) = await cliente.vetture()
        auto.stato = await cliente.stato(auto)
        await cliente.chiudi(auto, None)
        await cliente.apri(auto, None)
        await cliente.trova(auto, None)
        await cliente.clima(auto, True, None)
        await cliente.clima(auto, False, None)
        await cliente.ricarica(auto, True, None)
        await cliente.limite_ricarica(auto, 83, None)
        assert [(m, p, b.get("command")) for m, p, b in finta.comandi[:6]] == [
            ("POST", "/tsp/remote-lock", "allLock"),
            ("POST", "/tsp/remote-lock", "doorUnlock"),
            ("POST", "/tsp/remote-horn-light", "horn"),
            ("POST", "/tsp/remote-climate", "start"),
            ("POST", "/tsp/remote-climate", "stop"),
            ("POST", "/tsp/remote-charge", "start"),
        ]
        # L'auto è fuori casa: cambia il limite di fuori, quello di casa resta 80.
        assert finta.comandi[6] == (
            "PUT",
            "/tsp/maximum-charge-config",
            {"vin": c.VIN, "locationHome": {"maxCharge": 80}, "locationAway": {"maxCharge": 85}},
        )

        finta.esito = {"RequestStatus": "fail", "StatusReason": "door-open"}
        with pytest.raises(ErroreComando, match="door-open"):
            await cliente.chiudi(auto, None)
        finta.esito = {"RequestStatus": "fail", "functionTimedOut": True}
        with pytest.raises(ErroreComando, match="non ha risposto"):
            await cliente.chiudi(auto, None)
        finta.esito = {"RequestStatus": "success"}
        finta.attese = 10_000
        cliente._attesa_comando = 0.05
        with pytest.raises(ErroreComando, match="in tempo"):
            await cliente.chiudi(auto, None)


async def test_i_termini_da_accettare_non_chiedono_di_rientrare(cloud) -> None:
    finta, base = cloud
    async with aiohttp.ClientSession() as s:
        token, _ = await _registrato(s, finta, base)
        cliente = ClienteHonda(s, token, base=base)
        (auto,) = await cliente.vetture()
        finta.termini = True
        with pytest.raises(ErroreApi, match="termini") as err:
            await cliente.stato(auto)
        assert not isinstance(err.value, ErroreCredenziali)


# --- in Home Assistant --------------------------------------------------------------

HONDA_E = Vettura(
    vin=c.VIN,
    marca="honda",
    modello="Honda e",
    nome="Honda e",
    extra={"capacita": ["telematicsRemoteLockUnlock", "telematicsRemoteClimate"], "carburante": "E"},
)
#: Il catalogo punterà «honda» alla marca nativa: qui lo si fa per la prova.
MARCHIO = Marchio(
    "honda",
    "Honda",
    "honda",
    {},
    "My Honda+ (myhondaplus-homeassistant, HACS)",
    "https://github.com/enricobattocchi/myhondaplus-homeassistant",
)


async def test_configurare_una_honda(hass: HomeAssistant) -> None:
    token = {"access_token": "A", "refresh_token": "R", "scade": time.time() + 3600, "user_id": "u1"}
    entra_finto = AsyncMock(side_effect=[CodiceInviato("guarda l'email"), (token, "PEM")])
    with (
        patch.dict(PER_ID, {"honda": MARCHIO}),
        patch.object(honda, "entra", entra_finto),
        patch.object(honda.ClienteHonda, "vetture", AsyncMock(return_value=[HONDA_E])),
        patch.object(honda.ClienteHonda, "stato", AsyncMock(return_value=interpreta(c.CRUSCOTTO))),
    ):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "honda"})
        assert r["step_id"] == "credenziali"
        assert {str(k) for k in r["data_schema"].schema} == {"email", "password", "codice"}
        dati = {"email": "Mario@Esempio.it", "password": "segreta"}
        r = await hass.config_entries.flow.async_configure(r["flow_id"], dati)
        assert r["errors"] == {"base": "codice_inviato"}
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {**dati, "codice": "https://link?key=K"})
        await hass.async_block_till_done()
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["title"] == "Honda · Mario@Esempio.it"
    assert r["result"].unique_id == "honda_mario@esempio.it"
    assert r["data"]["marca"] == "honda"
    assert r["data"]["token"] == token
    assert r["data"]["chiave_dispositivo"] == "PEM"
    assert entra_finto.await_args.kwargs["codice"] == "https://link?key=K"
    # La password e il link non restano da nessuna parte.
    assert "segreta" not in json.dumps(r["data"]) and "codice" not in r["data"]
    assert hass.states.get("sensor.honda_e_batteria").state == "82"
    voce = r["result"]
    assert voce.runtime_data.auto[0].comandi_possibili == {"chiudi", "apri", "clima"}
    # Il token rinnovato dal cliente aggiorna la voce senza ricaricarla.
    prima = voce.runtime_data
    voce.runtime_data.conto.cliente._salva({**token, "access_token": "B"})
    await hass.async_block_till_done()
    assert voce.data["token"]["access_token"] == "B"
    assert voce.runtime_data is prima
