"""NissanConnect Services: il cloud delle Nissan dal 2019 (Ariya, Leaf ZE1, Micra…).

È Kamereon, la piattaforma dell'Alleanza che usa anche Renault, ma con un
login suo:

1. **Il login** è una pagina web (WSO2, ``login.mynissan-account.com``) con
   PKCE. GdAAuto non la compila al posto dell'utente: prepara il link
   ``oauth2/authorize``, l'utente entra nel suo browser, e alla fine il server
   rimanda all'indirizzo dell'app (``com://wso2.service.nci?code=…``), che il
   browser non sa aprire. Quell'indirizzo (o solo il ``code``) si incolla in
   GdAAuto.
2. Il ``code`` diventa un ``id_token`` (``oauth2/token``), e l'``id_token``
   diventa il token di Kamereon (``bff-web/v1/oauth2/access_token``), che dura
   circa mezz'ora e ha il suo ``refresh_token`` (``…/oauth2/refresh-token``).
   Se quel rinnovo non va più si prova col ``refresh_token`` del login
   (``offline_access``); se non va neanche quello, bisogna rientrare dal browser.
3. ``user-adapter/v1/users/current`` dice chi è l'utente,
   ``bff-web/v5/users/{utente}/cars`` le sue auto, con i servizi attivi
   (numeri: 12 la posizione, 366 il clima, 299 la ricarica, 97 luci e
   clacson, 2021 lo stato delle porte, 235 i viaggi…).
4. Le letture (``car-adapter/v1/cars/{vin}/battery-status``, ``hvac-status``,
   ``location``, ``lock-status``, ``cockpit``) danno l'ultimo dato che l'auto
   ha mandato: leggere non la sveglia. Ariya e Micra leggono la batteria da
   ``bff-web/v3/cars/{vin}/battery-status``; alcuni modelli il cockpit solo in v2.
5. I comandi sono ``car-adapter/v1/cars/{vin}/actions/…`` (``hvac-start``,
   ``charging-start``, ``horn-lights``). Chiudere e aprire le porte vuole una
   prova SRP che nessun progetto aperto sa ancora fare: qui non c'è.

Indirizzi, chiave dell'app (``client_id``), parametri del login e servizi
vengono da dan-r/HomeAssistant-NissanConnect (licenza MIT, Copyright (c) 2024
Daniel Raper; parti da Apache-2.0 di @mitchellrj e @Tobiaswk): vedi
RINGRAZIAMENTI.md.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
import logging
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import aiohttp

from ...vettura import StatoVettura, Vettura
from ..errori import ErroreApi, ErroreComando, ErroreCredenziali, ErroreRete
from .dati import interpreta_kamereon, storico_kamereon

_LOGGER = logging.getLogger(__name__)
_TIMEOUT = aiohttp.ClientTimeout(total=30)
_MARGINE = 60
#: ogni quanto si rilegge lo storico dei viaggi
OGNI_STORICO = 30 * 60

#: i servizi (``services[].id``) che contano qui
CERCA_AUTO = "12"
CLIMA = "366"
CLIMA_TEMPERATURA = ("307", "2042")
AVVIA_RICARICA = "299"
FERMA_RICARICA = "303"
LUCI_CLACSON = "97"
STATO_PORTE = "2021"
VIAGGI = "235"

#: i modelli che leggono la batteria dalla v3
_BATTERIA_V3 = {"ARIYA", "MICRA"}


@dataclass(frozen=True)
class ServerKamereon:
    login: str
    client_id: str
    scope: str
    scope_kamereon: str
    redirect: str
    marchio: str
    app: str
    piattaforma: str
    locale: str
    car_adapter: str
    user_adapter: str
    bff: str


#: l'Europa, l'unica regione che dan-r/HomeAssistant-NissanConnect conosce
EUROPA = ServerKamereon(
    login="https://login.mynissan-account.com/",
    client_id="ZM3WK7ax1OtQKYQ8Qqzcv5VgiA8a",
    scope="openid name profile email offline_access",
    scope_kamereon="openid profile vehicles",
    redirect="com://wso2.service.nci",
    marchio="Nissan",
    app="mynissanapp",
    piattaforma="Android",
    locale="en_GB",
    car_adapter="https://alliance-platform-caradapter-prod.apps.eu2.kamereon.io/car-adapter/",
    user_adapter="https://alliance-platform-usersadapter-prod.apps.eu2.kamereon.io/user-adapter/",
    bff="https://nci-bff-web-prod.apps.eu2.kamereon.io/bff-web/",
)

#: le risposte che vogliono dire «questa auto non ce l'ha»
_NON_C_E = {"404", "501"}
_MOTIVI = {
    "403009": "nell'auto è accesa la modalità privacy",
    "403": "il cloud non lo permette per questa auto",
    "404": "l'auto non lo sa fare",
    "501": "l'auto non lo sa fare",
    "409": "un altro comando è ancora in corso",
    "429": "troppe richieste al cloud: riprova fra un po'",
}


class ErroreKamereon(ErroreApi):
    """Kamereon ha risposto con un errore suo (``errors[0].code``)."""

    @property
    def non_c_e(self) -> bool:
        return str(self.codice) in _NON_C_E or self.http in (404, 501)


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()
    return verifier, challenge


def link_login(server: ServerKamereon = EUROPA) -> tuple[str, dict[str, str]]:
    """Il link da aprire nel browser, e quello che serve per lo scambio del codice."""
    verifier, challenge = _pkce()
    stato = secrets.token_urlsafe(32)
    parametri = {
        "response_type": "code",
        "redirect_uri": server.redirect,
        "client_id": server.client_id,
        "state": stato,
        "scope": server.scope,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "locale": server.locale,
        "brand": server.marchio,
        "client": server.app,
    }
    return urljoin(server.login, "oauth2/authorize") + "?" + urlencode(parametri), {
        "verifier": verifier,
        "state": stato,
    }


def codice_da(incollato: str, stato: str = "") -> str:
    """Il ``code`` da quello che l'utente incolla: l'indirizzo finale intero, o il codice da solo."""
    testo = incollato.strip().strip("\"'<>")
    if "code=" not in testo and "error=" not in testo:
        if not testo or any(c in testo for c in " /?&="):
            raise ErroreCredenziali("Non trovo il codice: incolla l'indirizzo che inizia con com://wso2.service.nci")
        return testo
    domanda = parse_qs(urlparse(testo).query or testo.split("?", 1)[-1])
    if domanda.get("error"):
        motivo = (domanda.get("error_description") or domanda["error"])[0]
        raise ErroreCredenziali(f"Nissan non ha dato il codice: {motivo}")
    if stato and (domanda.get("state") or [stato])[0] != stato:
        raise ErroreCredenziali("Questo indirizzo è di un altro login: riapri il link e riprova")
    codice = (domanda.get("code") or [""])[0]
    if not codice:
        raise ErroreCredenziali("Non trovo il codice nell'indirizzo incollato")
    return codice


def servizi_di(v: Vettura) -> set[str]:
    return {str(s) for s in v.extra.get("servizi") or []}


def comandi_kamereon(v: Vettura) -> set[str]:
    servizi = servizi_di(v)
    comandi: set[str] = set()
    if CLIMA in servizi:
        comandi.add("clima")
    if AVVIA_RICARICA in servizi:
        comandi.add("ricarica")
    if LUCI_CLACSON in servizi:
        comandi.add("trova")
    return comandi


class ClienteKamereon:
    """Un account NissanConnect Services."""

    def __init__(
        self,
        sessione_http: aiohttp.ClientSession,
        token: dict[str, Any] | None = None,
        salva: Callable[[dict[str, Any]], None] | None = None,
        *,
        server: ServerKamereon = EUROPA,
    ) -> None:
        self._http = sessione_http
        self.server = server
        self.token: dict[str, Any] = dict(token or {})
        self._salva = salva
        self._serratura_token = asyncio.Lock()
        self._serratura_comandi = asyncio.Lock()
        #: per auto: la versione della batteria e del cockpit che risponde, e le letture che mancano
        self._versioni: dict[tuple[str, str], str] = {}
        self._mancanti: dict[str, set[str]] = {}
        self._riuscite: dict[str, set[str]] = {}
        self._storico: dict[str, tuple[float, dict[str, Any]]] = {}

    # --- il login (nel browser) e i token ---------------------------------------

    async def _posta_token(self, cosa: str, url: str, **kw: Any) -> dict[str, Any]:
        """Una richiesta di token: ``ErroreCredenziali`` se il token è rifiutato."""
        try:
            async with self._http.post(url, allow_redirects=False, timeout=_TIMEOUT, **kw) as r:
                http = r.status
                testo = await r.text()
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise ErroreRete(f"{cosa}: Nissan non risponde: {err}") from err
        if http >= 500:
            raise ErroreRete(f"{cosa}: Nissan risponde HTTP {http}")
        try:
            corpo = json.loads(testo) if testo.strip() else {}
        except ValueError:
            corpo = None
        if not isinstance(corpo, dict):
            raise ErroreRete(f"{cosa}: Nissan risponde una cosa che non si capisce (HTTP {http})")
        if http >= 400 or corpo.get("error") or not corpo.get("access_token"):
            motivo = corpo.get("error_description") or corpo.get("error") or f"HTTP {http}"
            raise ErroreCredenziali(f"{cosa}: Nissan rifiuta il token ({motivo})")
        return corpo

    async def _id_token(self, **modulo: str) -> dict[str, Any]:
        s = self.server
        r = await self._posta_token(
            "token del login",
            urljoin(s.login, "oauth2/token"),
            data={"redirect_uri": s.redirect, "client_id": s.client_id, "scope": s.scope, **modulo},
        )
        if not r.get("id_token"):
            raise ErroreCredenziali("Il login Nissan non ha dato l'id_token")
        return r

    async def _da_id_token(self, risposta_login: dict[str, Any]) -> None:
        """L'``id_token`` del login diventa il token di Kamereon."""
        s = self.server
        r = await self._posta_token(
            "token di Kamereon",
            urljoin(s.bff, "v1/oauth2/access_token"),
            params={"platform": s.piattaforma},
            headers={"Authorization": str(risposta_login["id_token"]), "Content-Type": "application/vnd.api+json"},
        )
        self._metti(r)
        if risposta_login.get("refresh_token"):
            self.token["login_refresh"] = str(risposta_login["refresh_token"])

    def _metti(self, r: dict[str, Any]) -> None:
        durata = _durata(r.get("expires_in"))
        self.token["access_token"] = str(r["access_token"])
        if r.get("refresh_token"):
            self.token["refresh_token"] = str(r["refresh_token"])
        self.token["scade"] = time.time() + durata

    async def scambia_codice(self, codice: str, verifier: str) -> None:
        """Il ``code`` incollato dopo il login nel browser diventa il token di Kamereon."""
        login = await self._id_token(grant_type="authorization_code", code=codice, code_verifier=verifier)
        await self._da_id_token(login)
        if self._salva:
            self._salva(dict(self.token))

    async def _rinnova(self) -> None:
        """Il token è scaduto: prima il refresh di Kamereon, poi quello del login."""
        s = self.server
        if self.token.get("refresh_token"):
            try:
                r = await self._posta_token(
                    "rinnovo del token",
                    urljoin(s.bff, "v1/oauth2/refresh-token"),
                    params={"platform": s.piattaforma},
                    headers={
                        "Authorization": str(self.token["refresh_token"]),
                        "Content-Type": "application/vnd.api+json",
                    },
                    data=json.dumps({"scope": s.scope_kamereon}),
                )
            except ErroreCredenziali as err:
                _LOGGER.debug("Nissan: il refresh di Kamereon non va più (%s)", err)
            else:
                self._metti(r)
                return
        if self.token.get("login_refresh"):
            try:
                login = await self._id_token(grant_type="refresh_token", refresh_token=str(self.token["login_refresh"]))
                await self._da_id_token(login)
            except ErroreCredenziali as err:
                _LOGGER.debug("Nissan: il refresh del login non va più (%s)", err)
            else:
                return
        raise ErroreCredenziali("NissanConnect Services non rinnova più il token: serve rientrare dal browser")

    async def _valido(self, *, nuovo: bool = False) -> str:
        async with self._serratura_token:
            prima = dict(self.token)
            if not self.token.get("access_token"):
                raise ErroreCredenziali("NissanConnect Services: manca il token, serve entrare dal browser")
            if nuovo or time.time() > float(self.token.get("scade") or 0) - _MARGINE:
                await self._rinnova()
            if self.token != prima and self._salva:
                self._salva(dict(self.token))
            return str(self.token["access_token"])

    # --- la rete -----------------------------------------------------------------

    async def _chiedi(
        self,
        cosa: str,
        metodo: str,
        url: str,
        *,
        corpo: dict[str, Any] | None = None,
        parametri: dict[str, str] | None = None,
        di_nuovo: bool = True,
    ) -> dict[str, Any]:
        token = await self._valido()
        intestazioni = {"Authorization": f"Bearer {token}", "Content-Type": "application/vnd.api+json"}
        try:
            async with self._http.request(
                metodo,
                url,
                headers=intestazioni,
                params=parametri,
                data=json.dumps(corpo) if corpo is not None else None,
                timeout=_TIMEOUT,
            ) as r:
                http = r.status
                testo = await r.text()
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise ErroreRete(f"{cosa}: Kamereon non risponde: {err}") from err
        if http == 401:
            if di_nuovo:
                await self._valido(nuovo=True)
                return await self._chiedi(cosa, metodo, url, corpo=corpo, parametri=parametri, di_nuovo=False)
            raise ErroreKamereon(cosa, 401, "Kamereon rifiuta un token appena rinnovato", http)
        try:
            dati = json.loads(testo) if testo.strip() else {}
        except ValueError:
            dati = None
        errori = (dati.get("errors") if isinstance(dati, dict) else None) or []
        errore = errori[0] if isinstance(errori, list) and errori and isinstance(errori[0], dict) else {}
        codice = str(errore.get("code") or "")
        dettagli = " ".join(str(x) for x in (errore.get("title"), errore.get("detail")) if x)
        if http == 501 or codice in _NON_C_E:
            raise ErroreKamereon(cosa, codice or http, dettagli, http)
        if http >= 500 or dati is None:
            raise ErroreRete(f"{cosa}: Kamereon risponde HTTP {http}")
        if errore or http >= 400:
            raise ErroreKamereon(cosa, codice or http, dettagli or testo[:200], http)
        return dati if isinstance(dati, dict) else {"data": dati}

    # --- entrare e leggere ---------------------------------------------------------

    async def accedi(self) -> None:
        await self._valido()
        if not self.token.get("user_id"):
            r = await self._chiedi("utente", "GET", urljoin(self.server.user_adapter, "v1/users/current"))
            if not r.get("userId"):
                raise ErroreApi("utente", None, "Kamereon non dice chi è l'utente")
            self.token["user_id"] = str(r["userId"])
            if self._salva:
                self._salva(dict(self.token))

    async def vetture(self) -> list[Vettura]:
        if not self.token.get("user_id"):
            await self.accedi()
        r = await self._chiedi(
            "elenco delle auto", "GET", urljoin(self.server.bff, f"v5/users/{self.token['user_id']}/cars")
        )
        elenco: list[Vettura] = []
        for auto in r.get("data") or []:
            if not isinstance(auto, dict) or not auto.get("vin"):
                continue
            modello = str(auto.get("modelName") or "").strip()
            servizi = sorted(
                str(s.get("id"))
                for s in auto.get("services") or []
                if isinstance(s, dict) and str(s.get("activationState") or "").upper() == "ACTIVATED"
            )
            anno = auto.get("modelYear")
            elenco.append(
                Vettura(
                    vin=str(auto["vin"]).upper(),
                    marca="nissan",
                    modello=modello.title() or "Nissan",
                    nome=str(auto.get("nickname") or "").strip() or f"Nissan {modello.title()}".strip(),
                    anno=int(anno) if str(anno or "").isdigit() else None,
                    targa=str(auto.get("registrationNumber") or ""),
                    colore=str(auto.get("color") or ""),
                    extra={
                        "via": "kamereon",
                        "modello": modello.upper(),
                        "servizi": servizi,
                        "can": str(auto.get("canGeneration") or ""),
                    },
                )
            )
        return elenco

    def _url(self, v: Vettura, lettura: str, versione: str = "v1") -> str:
        if lettura == "battery-status" and versione == "v3":
            return urljoin(self.server.bff, f"v3/cars/{v.vin}/battery-status")
        return urljoin(self.server.car_adapter, f"{versione}/cars/{v.vin}/{lettura}")

    async def _con_versioni(self, v: Vettura, lettura: str, versioni: tuple[str, ...]) -> tuple[str, dict[str, Any]]:
        """Prova la versione che ha già risposto, poi le altre se questa «non c'è»."""
        nota = self._versioni.get((v.vin, lettura))
        ordine = [nota, *(x for x in versioni if x != nota)] if nota else list(versioni)
        errore: ErroreKamereon | None = None
        for versione in ordine:
            parametri = {"canGen": str(v.extra.get("can") or "")} if versione == "v3" else None
            try:
                r = await self._chiedi(
                    f"lettura {lettura}", "GET", self._url(v, lettura, versione), parametri=parametri
                )
            except ErroreKamereon as err:
                if not err.non_c_e:
                    raise
                errore = err
                continue
            self._versioni[(v.vin, lettura)] = versione
            return versione, _attributi(r)
        assert errore is not None
        raise errore

    async def stato(self, v: Vettura) -> StatoVettura:
        servizi = servizi_di(v)
        parti: dict[str, dict[str, Any]] = {}
        v3_prima = str(v.extra.get("modello") or "") in _BATTERIA_V3
        versione, parti["battery-status"] = await self._con_versioni(
            v, "battery-status", ("v3", "v1") if v3_prima else ("v1", "v3")
        )
        facoltative: list[str] = []
        if CLIMA in servizi or any(c in servizi for c in CLIMA_TEMPERATURA):
            facoltative.append("hvac-status")
        if CERCA_AUTO in servizi:
            facoltative.append("location")
        if STATO_PORTE in servizi:
            facoltative.append("lock-status")
        facoltative.append("cockpit")
        mancanti = self._mancanti.setdefault(v.vin, set())
        riuscite = self._riuscite.setdefault(v.vin, set())
        for lettura in facoltative:
            if lettura in mancanti:
                continue
            try:
                if lettura == "cockpit":
                    _, parti[lettura] = await self._con_versioni(v, lettura, ("v1", "v2"))
                else:
                    parti[lettura] = _attributi(await self._chiedi(f"lettura {lettura}", "GET", self._url(v, lettura)))
            except ErroreKamereon as err:
                if err.non_c_e and lettura not in riuscite:
                    # Mai riuscita: l'auto non ce l'ha, e non la si chiede più.
                    mancanti.add(lettura)
                _LOGGER.debug("Nissan: lettura %s di %s non riuscita: %s", lettura, v.titolo, err)
                continue
            except ErroreRete as err:
                # Alcuni modelli rispondono 502 su una lettura sola: le altre arrivano lo stesso.
                _LOGGER.debug("Nissan: lettura %s di %s non riuscita: %s", lettura, v.titolo, err)
                continue
            riuscite.add(lettura)
        s = interpreta_kamereon(parti, batteria_v3=versione == "v3")
        if VIAGGI in servizi:
            for campo, valore in (await self._viaggi(v)).items():
                setattr(s, campo, valore)
        return s

    async def _viaggi(self, v: Vettura) -> dict[str, Any]:
        """Gli ultimi sette giorni di viaggi, riletti ogni mezz'ora; facoltativi."""
        quando, campi = self._storico.get(v.vin, (0.0, {}))
        if quando and time.monotonic() - quando < OGNI_STORICO:
            return campi
        oggi = datetime.now(UTC).date()
        try:
            r = await self._chiedi(
                "storico dei viaggi",
                "GET",
                self._url(v, "trip-history"),
                parametri={"type": "0", "start": (oggi - timedelta(days=6)).isoformat(), "end": oggi.isoformat()},
            )
            riassunti = ((r.get("data") or {}).get("attributes") or {}).get("summaries") or []
            campi = storico_kamereon(riassunti if isinstance(riassunti, list) else [])
        except (ErroreApi, ErroreRete, AttributeError) as err:
            _LOGGER.debug("Nissan: storico dei viaggi di %s non riuscito: %s", v.titolo, err)
        self._storico[v.vin] = (time.monotonic(), campi)
        return campi

    # --- comandare ---------------------------------------------------------------

    async def _comanda(self, v: Vettura, cosa: str, azione: str, tipo: str, attributi: dict[str, Any]) -> None:
        async with self._serratura_comandi:
            try:
                await self._chiedi(
                    cosa,
                    "POST",
                    self._url(v, f"actions/{azione}"),
                    corpo={"data": {"type": tipo, "attributes": attributi}},
                )
            except ErroreKamereon as err:
                motivo = _MOTIVI.get(str(err.codice)) or _MOTIVI.get(str(err.http)) or err.messaggio
                raise ErroreComando(f"{v.titolo} non esegue il comando ({cosa}): {motivo}") from err

    def _serve(self, v: Vettura, servizio: str, cosa: str) -> None:
        if servizio not in servizi_di(v):
            raise ErroreComando(f"{v.titolo} non sa {cosa} da remoto (il servizio non è attivo)")

    async def clima(self, v: Vettura, acceso: bool, temperatura: int = 22) -> None:
        self._serve(v, CLIMA, "accendere il clima")
        if acceso:
            # Come l'app: da 16 a 26 gradi.
            gradi = max(16, min(26, int(temperatura)))
            await self._comanda(
                v, "accendere il clima", "hvac-start", "HvacStart", {"action": "start", "targetTemperature": gradi}
            )
        else:
            await self._comanda(v, "spegnere il clima", "hvac-start", "HvacStart", {"action": "stop"})

    async def ricarica(self, v: Vettura, avvia: bool) -> None:
        if avvia:
            self._serve(v, AVVIA_RICARICA, "avviare la ricarica")
            await self._comanda(v, "avviare la ricarica", "charging-start", "ChargingStart", {"action": "start"})
        else:
            if FERMA_RICARICA not in servizi_di(v):
                raise ErroreComando(f"{v.titolo} non ferma la ricarica da remoto: si ferma dall'auto o dalla colonnina")
            await self._comanda(v, "fermare la ricarica", "charging-start", "ChargingStart", {"action": "stop"})

    async def trova(self, v: Vettura) -> None:
        self._serve(v, LUCI_CLACSON, "accendere luci e clacson")
        await self._comanda(
            v,
            "far trovare l'auto",
            "horn-lights",
            "HornLights",
            {"action": "start", "duration": 5, "target": "horn_lights"},
        )


def _durata(valore: Any) -> float:
    try:
        return max(60.0, float(valore))
    except (TypeError, ValueError):
        return 1800.0


def _attributi(corpo: dict[str, Any]) -> dict[str, Any]:
    dati = corpo.get("data")
    if isinstance(dati, dict) and isinstance(dati.get("attributes"), dict):
        return dict(dati["attributes"])
    return {}
