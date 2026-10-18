"""Il filo con Kamereon, il cloud delle auto Renault, Dacia e Alpine.

Kamereon (``api-wired-prod-1-<regione>.wrd-aws.com``) vuole a ogni richiesta
la chiave pubblica dell'app (``apikey``), il JWT di Gigya
(``x-gigya-id_token``) e il paese (``?country=IT``). Il percorso:

- ``/commerce/v1/persons/{persona}`` → gli account (MYRENAULT, MYDACIA…);
- ``/commerce/v1/accounts/{account}/vehicles`` → le auto, con modello ed energia;
- ``…/kamereon/kca/car-adapter/v{1,2}/cars/{vin}/<lettura>`` per leggere,
  ``…/actions/<comando>`` per comandare; i modelli più nuovi usano anche
  ``…/kamereon/kcm/v1/vehicles/{vin}/…`` (KCM).

Non tutti i modelli hanno tutte le letture e tutti i comandi, e alcuni li
hanno su strade diverse (la Spring mette in pausa la ricarica con KCM, la
Renault 5 la avvia riscrivendo le impostazioni di ricarica…). La tabella dei
modelli (``modelli.json``) viene da hacf-fr/renault-api
(``kamereon/models.py``, licenza MIT): un modello che non c'è prova le
strade di base.

Il cloud conta le richieste: l'integrazione Renault di Home Assistant ne fa
al più sessanta l'ora per account, e se Kamereon dice «quota» si ferma un
quarto d'ora. Qui si fa lo stesso, e le letture che cambiano piano
(chilometri, gomme, limite di carica) si chiedono ogni mezz'ora. Kamereon
risponde con l'ultimo dato che l'auto ha mandato: leggere non la sveglia.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
import logging
from pathlib import Path
import time
from typing import Any

import aiohttp

from ...vettura import StatoVettura, Vettura
from ..errori import ErroreApi, ErroreComando, ErroreCredenziali, ErroreRete
from .dati import interpreta
from .gigya import Gigya, scadenza_jwt
from .locali import Server, nazione, server_di

_LOGGER = logging.getLogger(__name__)

ID = "renault"
NOME = "Renault"

#: il marchio del catalogo → come si chiama sullo schermo
MARCHI = {"renault": "Renault", "dacia": "Dacia", "alpine": "Alpine"}

_TIMEOUT = aiohttp.ClientTimeout(total=30)
_MARGINE_JWT = 60
#: dopo un «quota superata», quanto si sta zitti
FERMO_QUOTA = 15 * 60
#: ogni quanto si rileggono le cose che cambiano piano
OGNI_LENTE = 30 * 60

_TABELLA: dict[str, Any] = json.loads((Path(__file__).parent / "modelli.json").read_text())
MODELLI: dict[str, dict[str, str | None]] = _TABELLA["modelli"]
IN_WATT = set(_TABELLA["in_watt"])

#: le letture, in ordine: prima quelle che contano
LETTURE = ("battery-status", "hvac-status", "location", "lock-status", "cockpit", "pressure", "soc-levels")
LENTE = {"cockpit", "pressure", "soc-levels"}
#: per un modello che la tabella non conosce: le letture di base, senza le
#: gomme e i limiti (che pochi modelli hanno)
_PREDEFINITE_IGNOTO = {"battery-status", "hvac-status", "location", "lock-status", "cockpit"}
_COMANDI_IGNOTO = {"actions/hvac-start", "actions/hvac-stop", "actions/charge-start", "actions/charge-stop"}

_PERCORSI = {
    ("battery-status", "default"): "/kca/car-adapter/v2/cars/{vin}/battery-status",
    ("soc-levels", "default"): "/kcm/v1/vehicles/{vin}/ev/soc-levels",
    ("actions/hvac-start", "default"): "/kca/car-adapter/v1/cars/{vin}/actions/hvac-start",
    ("actions/hvac-stop", "default"): "/kca/car-adapter/v1/cars/{vin}/actions/hvac-start",
    ("actions/hvac-stop", "kca-stop"): "/kca/car-adapter/v1/cars/{vin}/actions/hvac-start",
    ("actions/charge-start", "default"): "/kca/car-adapter/v1/cars/{vin}/actions/charging-start",
    ("actions/charge-start", "kcm"): "/kcm/v1/vehicles/{vin}/charge/start",
    ("actions/charge-start", "kcm-pause-resume"): "/kcm/v1/vehicles/{vin}/charge/pause-resume",
    ("actions/charge-start", "kcm-settings"): "/kcm/v1/vehicles/{vin}/ev/settings",
    ("actions/charge-stop", "default"): "/kca/car-adapter/v1/cars/{vin}/actions/charging-start",
    ("actions/charge-stop", "kcm-pause-resume"): "/kcm/v1/vehicles/{vin}/charge/pause-resume",
    ("actions/horn-start", "default"): "/kca/car-adapter/v1/cars/{vin}/actions/horn-lights",
    ("actions/lights-start", "default"): "/kca/car-adapter/v1/cars/{vin}/actions/horn-lights",
}

#: le risposte che vogliono dire «questa auto non ce l'ha»
_NON_C_E = {
    "err.func.wired.notFound",
    "err.func.wired.not-found",
    "err.func.wired.forbidden",
    "err.func.403",
    "err.tech.501",
    "err.func.privacy.on",
    "err.func.wired.methodNotAllowed",
}
_MOTIVI = {
    "err.func.wired.forbidden": "l'auto non lo permette da remoto",
    "err.func.403": "l'auto non lo permette da remoto",
    "err.tech.501": "l'auto non lo sa fare",
    "err.func.wired.notFound": "l'auto non lo sa fare",
    "err.func.wired.not-found": "l'auto non lo sa fare",
    "err.func.privacy.on": "nell'auto è accesa la modalità privacy",
    "409001": "un altro comando è ancora in corso",
    "err.func.wired.overloaded": "troppe richieste al cloud: riprova fra un quarto d'ora",
}


class ErroreKamereon(ErroreApi):
    """Kamereon ha risposto con un errore suo (``errors[0].errorCode``)."""

    @property
    def non_c_e(self) -> bool:
        return str(self.codice) in _NON_C_E


def strada(codice_modello: str, chiave: str) -> str | None:
    """Come quel modello fa quella cosa: ``"default"``, ``"kcm"``…, o ``None``
    se non la fa. Un modello documentato senza quella voce usa la strada di
    base, come in renault-api; un modello sconosciuto solo le letture di base."""
    tabella = MODELLI.get(codice_modello)
    if tabella is None:
        return "default" if chiave in _PREDEFINITE_IGNOTO or chiave in _COMANDI_IGNOTO else None
    if chiave in tabella:
        return tabella[chiave]
    return "default"


def _esplicita(codice_modello: str, chiave: str) -> bool:
    """Vero solo se la tabella dice che quel modello lo fa davvero."""
    return bool((MODELLI.get(codice_modello) or {}).get(chiave))


def comandi_di(v: Vettura) -> set[str]:
    codice = str(v.extra.get("modello") or "")
    if codice not in MODELLI:
        # Un modello che nessuno ha ancora provato: le due cose che quasi tutte le elettriche fanno.
        return {"clima", "ricarica"}
    comandi: set[str] = set()
    if strada(codice, "actions/hvac-start"):
        comandi.add("clima")
    if strada(codice, "actions/charge-start") or strada(codice, "actions/charge-stop"):
        comandi.add("ricarica")
    if _esplicita(codice, "soc-levels"):
        comandi.add("limite_ricarica")
    if _esplicita(codice, "actions/horn-start") or _esplicita(codice, "actions/lights-start"):
        comandi.add("trova")
    return comandi


def _dettagli(errore: dict[str, Any]) -> str:
    """Il messaggio di Kamereon, che a volte è un JSON dentro una stringa."""
    testo = str(errore.get("errorMessage") or "")
    try:
        interno = json.loads(testo)
    except ValueError:
        return testo
    if not isinstance(interno, dict):
        return testo
    parti = []
    for e in interno.get("errors") or []:
        if isinstance(e, dict):
            parti.append(" ".join(str(x) for x in (e.get("title"), e.get("detail")) if x))
    return ", ".join(p for p in parti if p) or testo


def _attributi(corpo: dict[str, Any]) -> dict[str, Any]:
    """Le letture KCA hanno ``data.attributes``; quelle KCM il JSON nudo."""
    dati = corpo.get("data")
    if isinstance(dati, dict):
        attributi = dati.get("attributes")
        return dict(attributi) if isinstance(attributi, dict) else {}
    return {k: v for k, v in corpo.items() if k != "errors"}


def _nome(marca: str, modello: str) -> tuple[str, str]:
    """(modello, nome) da «RENAULT» e «MEGANE E-TECH»: «Megane E-Tech», «Renault Megane E-Tech»."""
    marca = marca.strip().title()
    modello = modello.strip().title()
    if not modello:
        return marca or NOME, marca or NOME
    if modello.lower().startswith(marca.lower()):
        return modello, modello
    return modello, f"{marca} {modello}".strip()


class ClienteRenault:
    def __init__(
        self,
        sessione_http: aiohttp.ClientSession,
        paese: str,
        token: dict[str, Any],
        salva: Callable[[dict[str, Any]], None] | None = None,
        *,
        marchio: str = "renault",
        server: Server | None = None,
    ) -> None:
        self._http = sessione_http
        self.server: Server = server or server_di(paese)
        self.nazione = nazione(paese)
        self.marchio = marchio if marchio in MARCHI else "renault"
        self.token: dict[str, Any] = dict(token)
        self._salva = salva
        self._gigya = Gigya(sessione_http, self.server["gigya_url"], self.server["gigya_chiave"])
        self._jwt = ""
        self._jwt_scade = 0.0
        self._serratura_jwt = asyncio.Lock()
        self._serratura_comandi = asyncio.Lock()
        self._conti: list[str] = []
        #: per auto: le letture che l'auto non ha, e quelle che hanno funzionato almeno una volta
        self._mancanti: dict[str, set[str]] = {}
        self._riuscite: dict[str, set[str]] = {}
        #: (vin, lettura) → (quando, attributes) per le letture lente
        self._lente: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
        self._ultimo: dict[str, StatoVettura] = {}
        self._fermo_fino = 0.0

    # --- i token ------------------------------------------------------------

    async def _jwt_valido(self, *, nuovo: bool = False) -> str:
        async with self._serratura_jwt:
            if not nuovo and self._jwt and time.time() < self._jwt_scade - _MARGINE_JWT:
                return self._jwt
            login_token = self.token.get("login_token")
            if not login_token:
                raise ErroreCredenziali("Manca il login_token di Gigya: serve rientrare")
            self._jwt = await self._gigya.jwt(str(login_token))
            self._jwt_scade = scadenza_jwt(self._jwt)
            return self._jwt

    # --- la rete ------------------------------------------------------------

    async def _kamereon(
        self, cosa: str, metodo: str, percorso: str, *, json_: Any = None, di_nuovo: bool = True
    ) -> dict[str, Any]:
        jwt = await self._jwt_valido()
        intestazioni = {
            "Content-type": "application/vnd.api+json",
            "apikey": self.server["kamereon_chiave"],
            "x-gigya-id_token": jwt,
        }
        try:
            async with self._http.request(
                metodo,
                f"{self.server['kamereon_url']}{percorso}",
                headers=intestazioni,
                params={"country": self.nazione},
                json=json_,
                timeout=_TIMEOUT,
            ) as r:
                http = r.status
                testo = await r.text()
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise ErroreRete(f"{cosa}: Kamereon non risponde: {err}") from err
        if testo.startswith("["):
            testo = f'{{"data": {testo}}}'
        try:
            corpo = json.loads(testo) if testo.strip() else {}
        except ValueError:
            corpo = None
        errori = (corpo.get("errors") if isinstance(corpo, dict) else None) or []
        errore = errori[0] if errori and isinstance(errori[0], dict) else {}
        codice = str(errore.get("errorCode") or "")
        if http == 401 or codice == "err.func.wired.unauthorized":
            if di_nuovo:
                # Il JWT è scaduto prima del previsto: se ne chiede un altro e si riprova una volta.
                await self._jwt_valido(nuovo=True)
                return await self._kamereon(cosa, metodo, percorso, json_=json_, di_nuovo=False)
            # Gigya ha appena dato un JWT nuovo: rientrare non servirebbe.
            raise ErroreKamereon(cosa, codice or 401, "Kamereon rifiuta un JWT appena rinnovato", http)
        if codice == "err.func.wired.overloaded" or http == 429:
            self._fermo_fino = time.monotonic() + FERMO_QUOTA
            raise ErroreKamereon(cosa, "err.func.wired.overloaded", _MOTIVI["err.func.wired.overloaded"], http)
        if codice:
            if codice.startswith("err.tech.") and codice != "err.tech.501":
                raise ErroreRete(f"{cosa}: Kamereon ha un guasto ({codice}: {_dettagli(errore)})")
            raise ErroreKamereon(cosa, codice, _dettagli(errore), http)
        if http >= 500 or corpo is None:
            raise ErroreRete(f"{cosa}: Kamereon risponde HTTP {http}")
        if http >= 400:
            raise ErroreKamereon(cosa, None, testo[:200], http)
        return corpo if isinstance(corpo, dict) else {"data": corpo}

    def _percorso_auto(self, v: Vettura, chiave: str, modo: str) -> str:
        coda = _PERCORSI.get((chiave, modo)) or f"/kca/car-adapter/v1/cars/{{vin}}/{chiave}"
        conto = v.extra.get("account") or (self._conti[0] if self._conti else "")
        return f"/commerce/v1/accounts/{conto}/kamereon" + coda.replace("{vin}", v.vin)

    # --- entrare ---------------------------------------------------------------

    async def accedi(self) -> None:
        await self._jwt_valido()
        cambiato = False
        if not self.token.get("person_id"):
            self.token["person_id"] = await self._gigya.persona(str(self.token["login_token"]))
            cambiato = True
        persona = await self._kamereon("account", "GET", f"/commerce/v1/persons/{self.token['person_id']}")
        mio = f"MY{self.marchio.upper()}"
        conti = [
            a
            for a in persona.get("accounts") or []
            if isinstance(a, dict)
            and a.get("accountId")
            # SFDC è il gestionale dei concessionari: lì non ci sono auto connesse.
            and a.get("accountType") != "SFDC"
            and str(a.get("accountStatus") or "ACTIVE").upper() == "ACTIVE"
        ]
        conti.sort(key=lambda a: a.get("accountType") != mio)
        self._conti = [str(a["accountId"]) for a in conti]
        if cambiato and self._salva:
            self._salva(dict(self.token))

    # --- leggere ----------------------------------------------------------------

    async def vetture(self) -> list[Vettura]:
        if not self._conti:
            await self.accedi()
        elenco: list[Vettura] = []
        visti: set[str] = set()
        ultimo_errore: ErroreKamereon | None = None
        for conto in self._conti:
            try:
                r = await self._kamereon("elenco delle auto", "GET", f"/commerce/v1/accounts/{conto}/vehicles")
            except ErroreKamereon as err:
                # Un account senza auto connesse risponde con un errore: si passa al prossimo.
                _LOGGER.debug("Account Renault %s senza auto: %s", conto, err)
                ultimo_errore = err
                continue
            for link in r.get("vehicleLinks") or []:
                if not isinstance(link, dict):
                    continue
                dettagli = link.get("vehicleDetails")
                vin = str(link.get("vin") or (dettagli or {}).get("vin") or "")
                if not vin or vin in visti or not isinstance(dettagli, dict):
                    # Senza dettagli (un'auto appena comprata, o un guasto del cloud) non si sa che modello è.
                    continue
                visti.add(vin)
                modello, nome = _nome(
                    str((dettagli.get("brand") or {}).get("label") or link.get("brand") or ""),
                    str((dettagli.get("model") or {}).get("label") or ""),
                )
                data = str(dettagli.get("firstRegistrationDate") or dettagli.get("registrationDate") or "")
                elenco.append(
                    Vettura(
                        vin=vin,
                        marca=ID,
                        modello=modello,
                        nome=nome,
                        anno=int(data[:4]) if data[:4].isdigit() else None,
                        targa=str(dettagli.get("registrationNumber") or ""),
                        condivisa=str(link.get("linkType") or "OWNER").upper() != "OWNER",
                        extra={
                            "account": conto,
                            "modello": str((dettagli.get("model") or {}).get("code") or ""),
                            "energia": str(dettagli.get("engineEnergyType") or ""),
                        },
                    )
                )
        if not elenco and ultimo_errore is not None and len(self._conti) == 1:
            raise ultimo_errore
        return elenco

    async def stato(self, v: Vettura) -> StatoVettura:
        if time.monotonic() < self._fermo_fino:
            # Kamereon ha detto «quota»: per un quarto d'ora si tiene l'ultima lettura.
            if (ultimo := self._ultimo.get(v.vin) or v.stato) is not None:
                return ultimo
            raise ErroreApi("stato dell'auto", "err.func.wired.overloaded", _MOTIVI["err.func.wired.overloaded"])
        codice = str(v.extra.get("modello") or "")
        mancanti = self._mancanti.setdefault(v.vin, set())
        riuscite = self._riuscite.setdefault(v.vin, set())
        parti: dict[str, dict[str, Any]] = {}
        adesso = time.monotonic()
        for chiave in LETTURE:
            modo = strada(codice, chiave)
            if modo is None or chiave in mancanti:
                continue
            if chiave in LENTE and (vecchia := self._lente.get((v.vin, chiave))) and adesso - vecchia[0] < OGNI_LENTE:
                parti[chiave] = vecchia[1]
                continue
            try:
                corpo = await self._kamereon(f"lettura {chiave}", "GET", self._percorso_auto(v, chiave, modo))
            except ErroreKamereon as err:
                if err.non_c_e and chiave not in riuscite:
                    # Mai riuscita: l'auto non ce l'ha, e non la si chiede più.
                    _LOGGER.debug("%s non ha %s: %s", v.titolo, chiave, err)
                    mancanti.add(chiave)
                    continue
                if str(err.codice) == "err.func.wired.overloaded" or chiave == "battery-status":
                    raise
                _LOGGER.debug("Lettura %s di %s non riuscita: %s", chiave, v.titolo, err)
                continue
            riuscite.add(chiave)
            parti[chiave] = _attributi(corpo)
            if chiave in LENTE:
                self._lente[(v.vin, chiave)] = (adesso, parti[chiave])
        if not parti:
            raise ErroreApi("stato dell'auto", None, f"Kamereon non dà nessuna lettura per {v.titolo}")
        s = interpreta(parti, potenza_in_watt=codice in IN_WATT)
        self._ultimo[v.vin] = s
        return s

    # --- comandare ---------------------------------------------------------------

    async def _comanda(self, v: Vettura, cosa: str, chiave: str, corpo: dict[str, Any] | None = None) -> None:
        modo = strada(str(v.extra.get("modello") or ""), chiave)
        if modo is None:
            raise ErroreComando(f"{v.titolo} non sa {cosa} da remoto")
        async with self._serratura_comandi:
            percorso = self._percorso_auto(v, chiave, modo)
            try:
                if modo == "kcm-settings":
                    # Come fa l'app (renault-api #1348): si rileggono le impostazioni di
                    # ricarica, si spengono i programmi e si riscrivono: l'auto carica subito.
                    impostazioni = _attributi(await self._kamereon(cosa, "GET", percorso))
                    for programma in impostazioni.get("programs") or []:
                        if isinstance(programma, dict):
                            programma["programActivationStatus"] = False
                    corpo = impostazioni
                await self._kamereon(cosa, "POST", percorso, json_=corpo or {})
            except ErroreKamereon as err:
                motivo = _MOTIVI.get(str(err.codice)) or err.messaggio or f"errore {err.codice}"
                raise ErroreComando(f"{v.titolo} non esegue il comando ({cosa}): {motivo}") from err

    def _modo(self, v: Vettura, chiave: str) -> str | None:
        return strada(str(v.extra.get("modello") or ""), chiave)

    async def clima(self, v: Vettura, acceso: bool, _pin: str | None, temperatura: int = 22) -> None:
        if acceso:
            gradi = float(max(16, min(26, temperatura)))
            corpo = {"data": {"type": "HvacStart", "attributes": {"action": "start", "targetTemperature": gradi}}}
            await self._comanda(v, "accendere il clima", "actions/hvac-start", corpo)
        else:
            # Alcuni modelli (Spring, Megane E-Tech, R5, A290) lo fermano con «stop», gli altri con «cancel».
            azione = "stop" if self._modo(v, "actions/hvac-stop") == "kca-stop" else "cancel"
            corpo = {"data": {"type": "HvacStart", "attributes": {"action": azione}}}
            await self._comanda(v, "spegnere il clima", "actions/hvac-stop", corpo)

    async def ricarica(self, v: Vettura, avvia: bool, _pin: str | None) -> None:
        chiave = "actions/charge-start" if avvia else "actions/charge-stop"
        modo = self._modo(v, chiave)
        if not avvia and modo is None:
            raise ErroreComando(f"{v.titolo} non ferma la ricarica da remoto: si ferma dall'auto o dalla colonnina")
        if modo == "kcm-pause-resume":
            corpo = {"data": {"type": "ChargePauseResume", "attributes": {"action": "resume" if avvia else "pause"}}}
        else:
            corpo = {"data": {"type": "ChargingStart", "attributes": {"action": "start" if avvia else "stop"}}}
        await self._comanda(v, "avviare la ricarica" if avvia else "fermare la ricarica", chiave, corpo)

    async def limite_ricarica(self, v: Vettura, percento: int, _pin: str | None) -> None:
        # Come l'app: il limite va da 55 a 100 a passi di 5, la soglia minima da 15 a 45.
        obiettivo = max(55, min(100, round(percento / 5) * 5))
        letti = self._lente.get((v.vin, "soc-levels"))
        minimo = (letti[1] if letti else {}).get("socMin")
        if minimo is None:
            corpo = await self._kamereon("lettura soc-levels", "GET", self._percorso_auto(v, "soc-levels", "default"))
            minimo = _attributi(corpo).get("socMin")
        try:
            soglia = max(15, min(45, round(float(minimo) / 5) * 5))
        except (TypeError, ValueError):
            soglia = 15
        await self._comanda(v, "cambiare il limite di carica", "soc-levels", {"socMin": soglia, "socTarget": obiettivo})
        self._lente.pop((v.vin, "soc-levels"), None)

    async def trova(self, v: Vettura, _pin: str | None) -> None:
        codice = str(v.extra.get("modello") or "")
        fatti = 0
        for chiave, bersaglio in (("actions/lights-start", "lights"), ("actions/horn-start", "horn")):
            if _esplicita(codice, chiave):
                corpo = {"data": {"type": "HornLights", "attributes": {"action": "start", "target": bersaglio}}}
                await self._comanda(v, "far trovare l'auto", chiave, corpo)
                fatti += 1
        if not fatti:
            raise ErroreComando(f"{v.titolo} non accende luci né clacson da remoto")
