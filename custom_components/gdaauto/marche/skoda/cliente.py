"""Il cliente del cloud dell'app MyŠkoda (``mysmob.api.connect.skoda-auto.cz``).

È il cloud dell'app Škoda per le auto vendute in Europa: Enyaq ed Elroq
elettriche, le Superb, Octavia e Kodiaq iV plug-in, la Citigo-e iV. Si entra
col login del gruppo Volkswagen (vedi ``accesso.py``).

- ``/v2/garage`` elenca le auto dell'account; ``/v2/garage/vehicles/<vin>``
  dà di ognuna modello, targa, batteria e i servizi («capabilities»): un
  servizio con uno ``statuses`` (licenza scaduta, ospite…) non si usa;
- lo stato si legge a pezzi (ricarica, porte, autonomia, clima, connessione,
  posizione, tagliando: vedi ``dati.py``), e ogni pezzo è facoltativo: un
  pezzo che non va lascia gli altri. Leggere non sveglia l'auto (GdAAuto non
  chiama mai ``vehicle-wakeup``);
- ogni mezz'ora i viaggi (consumo medio, km ed energia degli ultimi sette
  giorni) e l'ultima ricarica; ogni sei ore le spie, che su alcune auto
  costano batteria a 12 V.

I comandi sono dello stesso cloud: serratura (con l'S-PIN), clacson e luci,
clima, ricarica e limite di ricarica. Uno alla volta: l'auto ne esegue uno
solo, e il cloud risponde «accettato» prima che l'auto lo faccia.

Indirizzi, corpi dei comandi e servizi vengono da skodaconnect/myskoda
(``rest_api.py``, ``myskoda.py``, ``models/``) e skodaconnect/homeassistant-myskoda,
licenza MIT: vedi RINGRAZIAMENTI.md. Il codice è scritto da capo, su ``aiohttp``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import json
import logging
import time
from typing import Any
from urllib.parse import quote

import aiohttp

from ...vettura import StatoVettura, Vettura
from ..errori import ErroreApi, ErroreComando, ErroreCredenziali, ErrorePin, ErroreRete
from . import accesso, dati
from .accesso import TokenSkoda

_LOGGER = logging.getLogger(__name__)

ID = "skoda"
NOME = "Škoda"

#: Le statistiche di ricarica stanno su un cloud di CARIAD, con lo stesso token.
RICARICHE = "https://prod.emea.mobile.charging.cariad.digital"

_TIMEOUT = aiohttp.ClientTimeout(total=40)
#: Il token si rinnova un po' prima che scada.
_MARGINE_S = 300
_STORICO_OGNI_S = 1800
#: Le spie si leggono di rado: su alcune auto (Citigo-e, Karoq) la lettura costa batteria a 12 V.
_SALUTE_OGNI_S = 6 * 3600
#: I giorni di statistiche di ricarica in cui cercare l'ultima.
_GIORNI_RICARICHE = 60

_GENERAZIONI = "connectivityGenerations=MOD1&connectivityGenerations=MOD2&connectivityGenerations=MOD3" + (
    "&connectivityGenerations=MOD4"
)

#: I pezzi dello stato: nome, servizio che serve, percorso (``{vin}`` dentro).
_PEZZI = (
    ("ricarica", "CHARGING", "/v1/charging/{vin}"),
    ("stato", "STATE", "/v2/vehicle-status/{vin}"),
    ("autonomia", "STATE", "/v2/vehicle-status/{vin}/driving-range"),
    ("clima", "AIR_CONDITIONING", "/v2/air-conditioning/{vin}"),
    ("connessione", "READINESS", "/v2/connection-status/{vin}/readiness"),
    ("posizione", "PARKING_POSITION", "/v1/maps/positions?vin={vin}"),
    ("manutenzione", "", "/v3/vehicle-maintenance/vehicles/{vin}"),
)
#: Quando il cloud non ha detto quali servizi ha l'auto, si leggono questi.
_PEZZI_DI_BASE = {"ricarica", "stato", "autonomia", "clima", "posizione", "manutenzione"}

#: Il servizio che serve a ogni comando neutro.
_SERVIZI_DEI_COMANDI = {
    "chiudi": "ACCESS",
    "trova": "HONK_AND_FLASH",
    "clima": "AIR_CONDITIONING",
    "ricarica": "CHARGING",
}
_COMANDI_DI_BASE = {"clima", "ricarica", "limite_ricarica"}

_MESSAGGI_HTTP = {
    403: "l'account non può dare questo comando (serve l'utente principale, o il servizio non è attivo)",
    404: "l'auto non ha questo servizio",
    409: "c'è già un comando in corso",
}


def comandi_di(v: Vettura) -> set[str]:
    """I comandi dei servizi che l'auto ha attivi; senza notizie, quelli che tutte le elettriche hanno."""
    servizi = v.extra.get("servizi")
    if not isinstance(servizi, list):
        return set(_COMANDI_DI_BASE)
    comandi = {comando for comando, servizio in _SERVIZI_DEI_COMANDI.items() if servizio in servizi}
    # Il limite si cambia con le impostazioni estese; le plug-in vecchie (MQB) non lo cambiano.
    if "EXTENDED_CHARGING_SETTINGS" in servizi or ("CHARGING" in servizi and "CHARGING_MQB" not in servizi):
        comandi.add("limite_ricarica")
    return comandi


def pezzi_di(v: Vettura) -> list[tuple[str, str]]:
    """I pezzi dello stato da leggere per quest'auto: (nome, percorso)."""
    servizi = v.extra.get("servizi")
    vin = quote(v.vin)
    return [
        (nome, percorso.format(vin=vin))
        for nome, servizio, percorso in _PEZZI
        if (nome in _PEZZI_DI_BASE if not isinstance(servizi, list) else (not servizio or servizio in servizi))
    ]


def _ha(v: Vettura, servizio: str) -> bool:
    servizi = v.extra.get("servizi")
    return not isinstance(servizi, list) or servizio in servizi


def vettura_da_json(voce: dict[str, Any], info: dict[str, Any] | None = None) -> Vettura | None:
    """Una voce del garage, con le sue informazioni; ``None`` se l'auto non si ricarica (benzina o diesel)."""
    info = info or {}
    vin = str(info.get("vin") or voce.get("vin") or "").strip()
    if not vin:
        return None
    capacita = info.get("capabilities") or {}
    elenco = capacita.get("capabilities") if isinstance(capacita, dict) else None
    servizi: list[str] | None = None
    # Con un errore nell'elenco (il cloud a volte risponde 429 dentro la risposta) i servizi non si sanno.
    if isinstance(elenco, list) and elenco and not capacita.get("errors"):
        tutti = {str(c.get("id")) for c in elenco if isinstance(c, dict) and c.get("id")}
        if "CHARGING" not in tutti:
            return None
        servizi = sorted(str(c["id"]) for c in elenco if isinstance(c, dict) and c.get("id") and not c.get("statuses"))
    specifica = info.get("specification") or {}
    titolo = str(specifica.get("title") or voce.get("title") or "").strip()
    modello = str(specifica.get("model") or "").strip() or titolo.removeprefix("Škoda").strip() or NOME
    extra: dict[str, Any] = {"stato_account": str(info.get("state") or voce.get("state") or "")}
    if servizi is not None:
        extra["servizi"] = servizi
    if (kwh := dati.numero(dati.valore(specifica, "battery.capacityInKWh"))) and kwh > 0:
        extra["capacita_kwh"] = kwh
    anno = dati.numero(specifica.get("modelYear"))
    return Vettura(
        vin=vin,
        marca=ID,
        modello=modello,
        nome=str(info.get("name") or voce.get("name") or "").strip() or modello,
        anno=int(anno) if anno else None,
        targa=str(info.get("licensePlate") or "").strip(),
        condivisa=extra["stato_account"] == "GUEST_USER",
        extra=extra,
    )


def _errore_di(r: Any) -> str:
    """Il messaggio d'errore, in qualunque delle forme che usa il cloud."""
    if not isinstance(r, dict):
        return ""
    parti: list[str] = []
    for chiave in ("title", "detail", "message", "description", "error", "errorCode", "type"):
        x = r.get(chiave)
        if isinstance(x, dict):
            x = x.get("message") or x.get("description") or x.get("code")
        if x and isinstance(x, str | int) and str(x) not in parti:
            parti.append(str(x))
    return " — ".join(parti)


class ClienteSkoda:
    def __init__(
        self,
        sessione_http: aiohttp.ClientSession,
        sessione: dict[str, Any] | None = None,
        salva: Callable[[dict[str, Any]], None] | None = None,
        *,
        base: str | None = None,
        ricariche: str | None = None,
        fuso: str = "UTC",
    ) -> None:
        self._http = sessione_http
        self._base = (base or accesso.BASE).rstrip("/")
        self._ricariche = (ricariche or RICARICHE).rstrip("/")
        self._token = TokenSkoda(sessione_http, base=self._base)
        #: il fuso orario di casa, per i giorni dei viaggi
        self._fuso = fuso or "UTC"
        #: i token: access, id, refresh e quando scade l'access
        self.sessione: dict[str, Any] = dict(sessione or {})
        self._salva = salva
        self._entrata = asyncio.Lock()
        self._comando = asyncio.Lock()
        self._storici: dict[str, tuple[dict[str, Any], float]] = {}
        self._salute: dict[str, tuple[Any, float]] = {}
        #: le letture facoltative che un'auto non ha (vin, cosa)
        self._mancanti: set[tuple[str, str]] = set()

    # --- l'accesso ------------------------------------------------------------------------

    def _token_valido(self) -> bool:
        try:
            scade = float(self.sessione.get("scade") or 0)
        except (TypeError, ValueError):
            scade = 0
        return bool(self.sessione.get("access_token")) and scade - _MARGINE_S > time.time()

    def _conserva(self, token: dict[str, Any]) -> None:
        self.sessione = dict(token)
        if self._salva:
            self._salva(dict(self.sessione))

    async def accedi(self, scaduto: str | None = None) -> None:
        """Un token valido: quello salvato, o uno rinnovato col refresh token.

        ``scaduto`` è il token che il cloud ha appena rifiutato: se nel frattempo
        un'altra lettura ne ha già preso uno nuovo, si usa quello. Se il rinnovo
        non va più, ``ErroreCredenziali``: il login si rifà nel browser.
        """
        async with self._entrata:
            attuale = self.sessione.get("access_token")
            if self._token_valido() and (scaduto is None or attuale != scaduto):
                return
            self._conserva(await self._token.rinnova(str(self.sessione.get("refresh_token") or "")))

    def usa(self, token: dict[str, Any]) -> None:
        """I token appena avuti dal login nel browser."""
        self._conserva(token)

    # --- le chiamate ----------------------------------------------------------------------

    def _intestazioni(self, ricariche: bool) -> dict[str, str]:
        intestazioni = {
            "Authorization": f"Bearer {self.sessione.get('access_token') or ''}",
            "Accept": "application/json",
        }
        if ricariche:
            intestazioni |= {"Accept-Language": "en-US", "X-Brand": "skoda", "X-Api-Version": "1"}
        return intestazioni

    async def _chiama(
        self,
        metodo: str,
        percorso: str,
        cosa: str,
        *,
        corpo: dict[str, Any] | None = None,
        attesi: tuple[int, ...] = (200,),
        ricariche: bool = False,
        _ripeti: bool = True,
    ) -> tuple[int, Any]:
        await self.accedi()
        token = str(self.sessione.get("access_token") or "")
        indirizzo = f"{self._ricariche}{percorso}" if ricariche else f"{self._base}/api{percorso}"
        try:
            async with self._http.request(
                metodo,
                indirizzo,
                headers=self._intestazioni(ricariche),
                json=corpo,
                timeout=_TIMEOUT,
                allow_redirects=False,
            ) as risposta:
                http, testo = risposta.status, await risposta.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ErroreRete(f"{cosa}: il cloud Škoda non risponde: {err}") from err
        if http == 401:
            if not _ripeti:
                raise ErroreCredenziali(f"Škoda non accetta più l'accesso ({cosa})")
            await self.accedi(scaduto=token)
            return await self._chiama(
                metodo, percorso, cosa, corpo=corpo, attesi=attesi, ricariche=ricariche, _ripeti=False
            )
        if http == 429:
            raise ErroreRete(f"{cosa}: il cloud Škoda chiede di rallentare (troppe richieste)")
        if http >= 500:
            raise ErroreRete(f"{cosa}: il cloud Škoda risponde HTTP {http}")
        try:
            r = json.loads(testo) if testo.strip() else None
        except ValueError:
            r = None
        if http not in attesi:
            raise ErroreApi(cosa, http, _errore_di(r) or _MESSAGGI_HTTP.get(http, testo[:160]), http)
        return http, r

    async def _facoltativa(
        self, v: Vettura, cosa: str, metodo: str, percorso: str, *, ricorda: bool = True, **altro: Any
    ) -> Any:
        """Una lettura che può mancare. Se l'auto non ha quel servizio (403, 404), con ``ricorda`` non si chiede più."""
        if (v.vin, cosa) in self._mancanti:
            return None
        try:
            _, r = await self._chiama(metodo, percorso, cosa, attesi=(200,), **altro)
        except ErroreApi as err:
            if ricorda and err.http in (403, 404):
                self._mancanti.add((v.vin, cosa))
            _LOGGER.debug("%s della Škoda non letto: %s", cosa, err)
        except ErroreRete as err:
            _LOGGER.debug("%s della Škoda non letto: %s", cosa, err)
        else:
            return r
        return None

    # --- l'account, le auto e lo stato -----------------------------------------------------------

    async def utente(self) -> dict[str, Any]:
        """Chi è l'account: ``{"id", "email", …}``."""
        _, r = await self._chiama("GET", "/v1/users", "account")
        return r if isinstance(r, dict) else {}

    async def vetture(self) -> list[Vettura]:
        _, r = await self._chiama("GET", f"/v2/garage?{_GENERAZIONI}", "elenco delle auto")
        voci = r.get("vehicles") if isinstance(r, dict) else None
        trovate: list[Vettura] = []
        for voce in voci or []:
            if not isinstance(voce, dict) or not voce.get("vin"):
                continue
            vin = quote(str(voce["vin"]))
            try:
                _, info = await self._chiama("GET", f"/v2/garage/vehicles/{vin}?{_GENERAZIONI}", "dati dell'auto")
            except (ErroreApi, ErroreRete) as err:
                # Senza le informazioni l'auto c'è lo stesso: i servizi si scopriranno leggendo.
                _LOGGER.warning("Škoda: i dati di un'auto non si leggono, si usa l'elenco: %s", err)
                info = None
            if (v := vettura_da_json(voce, info if isinstance(info, dict) else None)) is not None:
                trovate.append(v)
        return trovate

    async def stato(self, v: Vettura) -> StatoVettura:
        sezioni: dict[str, Any] = {}
        guasti: list[Exception] = []
        for nome, percorso in pezzi_di(v):
            try:
                _, sezioni[nome] = await self._chiama("GET", percorso, nome, attesi=(200,))
            except (ErroreApi, ErroreRete) as err:
                # Un pezzo che non va (il cloud risponde spesso 500 a uno solo) lascia gli altri.
                _LOGGER.debug("%s della Škoda non letto: %s", nome, err)
                guasti.append(err)
        if not any(isinstance(sezioni.get(n), dict) for n in ("ricarica", "stato", "autonomia", "clima")):
            if guasti and all(isinstance(e, ErroreRete) for e in guasti):
                raise ErroreRete(f"Il cloud Škoda non ha dato lo stato dell'auto: {guasti[0]}")
            raise ErroreApi("stato dell'auto", None, "il cloud non ha dati per questa auto")
        sezioni["salute"] = await self._spie(v)
        capacita = dati.numero(v.extra.get("capacita_kwh"))
        s = dati.interpreta(sezioni, capacita)
        # Mentre va il cloud non dice dove: niente posizione, non quella vecchia.
        if punto := dati.posizione(sezioni.get("posizione")):
            s.latitudine, s.longitudine = punto
            s.posizione_con_segno = True
        for campo, val in (await self._storico(v)).items():
            setattr(s, campo, val)
        return s

    async def _spie(self, v: Vettura) -> Any:
        """Le spie (con quella delle gomme), rilette ogni sei ore; se non vanno, quelle di prima."""
        spie, quando = self._salute.get(v.vin, (None, 0.0))
        if (quando and time.monotonic() - quando < _SALUTE_OGNI_S) or not _ha(v, "VEHICLE_HEALTH_INSPECTION"):
            return spie
        nuove = await self._facoltativa(v, "spie", "GET", f"/v1/vehicle-health-report/warning-lights/{quote(v.vin)}")
        spie = nuove if isinstance(nuove, dict) else spie
        self._salute[v.vin] = (spie, time.monotonic())
        return spie

    async def _storico(self, v: Vettura) -> dict[str, Any]:
        """Consumo medio, viaggi della settimana e ultima ricarica, riletti ogni mezz'ora.

        Sono letture in più, e tutte facoltative: se una non va, restano i dati
        di prima e lo stato arriva lo stesso.
        """
        campi, quando = self._storici.get(v.vin, ({}, 0.0))
        if quando and time.monotonic() - quando < _STORICO_OGNI_S:
            return campi
        campi = dict(campi)
        for leggi in (self._consumo_medio, self._ultimi_7_giorni, self._ultima_ricarica):
            try:
                campi.update(await leggi(v))
            except ErroreCredenziali:
                raise
            except Exception as err:  # lo storico non deve mai fermare lo stato
                _LOGGER.debug("Storico della Škoda non letto (%s): %s", leggi.__name__, err)
        self._storici[v.vin] = (campi, time.monotonic())
        return campi

    async def _consumo_medio(self, v: Vettura) -> dict[str, Any]:
        if not _ha(v, "TRIP_STATISTICS"):
            return {}
        domanda = f"offsetType=week&offset=0&timezone={quote(self._fuso, safe='')}"
        r = await self._facoltativa(v, "viaggi", "GET", f"/v1/trip-statistics/{quote(v.vin)}?{domanda}")
        consumo = dati.consumo_medio(r)
        return {"consumo_medio_kwh_100km": consumo} if consumo is not None else {}

    async def _ultimi_7_giorni(self, v: Vettura) -> dict[str, Any]:
        if not _ha(v, "TRIP_STATISTICS"):
            return {}
        adesso = datetime.now(UTC)
        dal = (adesso - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        al = adesso.strftime("%Y-%m-%dT%H:%M:%SZ")
        domanda = f"timezone={quote(self._fuso, safe='')}&from={quote(dal)}&to={quote(al)}"
        r = await self._facoltativa(
            v, "viaggi singoli", "GET", f"/v1/trip-statistics/{quote(v.vin)}/single-trips?{domanda}"
        )
        km, energia = dati.ultimi_viaggi(r)
        campi: dict[str, Any] = {}
        if km is not None:
            campi["km_7_giorni"] = km
        if energia is not None:
            campi["energia_7_giorni_kwh"] = energia
        return campi

    async def _ultima_ricarica(self, v: Vettura) -> dict[str, Any]:
        if not _ha(v, "CHARGING"):
            return {}
        oggi = datetime.now(UTC).date()
        corpo = {
            "startedAfter": (oggi - timedelta(days=_GIORNI_RICARICHE)).isoformat(),
            "startedBefore": (oggi + timedelta(days=1)).isoformat(),
            "selectedFilterOptions": [{"filterType": "VEHICLE", "vin": v.vin}],
            "capabilities": [],
            "fetchFilterOptions": True,
        }
        r = await self._facoltativa(
            v, "statistiche di ricarica", "POST", "/charging_statistics", corpo=corpo, ricariche=True
        )
        if campi := dati.ultima_ricarica(r):
            return campi
        # Il vecchio storico, per chi non ha le statistiche nuove.
        vecchio = await self._facoltativa(
            v, "storico di ricarica", "GET", f"/v1/charging/{quote(v.vin)}/history?userTimezone=UTC&limit=20"
        )
        return dati.ultima_ricarica_storica(vecchio)

    # --- i comandi ----------------------------------------------------------------------------

    async def _comanda(
        self,
        v: Vettura,
        nome: str,
        metodo: str,
        percorso: str,
        corpo: dict[str, Any] | None = None,
        col_pin: bool = False,
    ) -> None:
        async with self._comando:
            try:
                await self._chiama(metodo, percorso, nome, corpo=corpo, attesi=(200, 202, 204))
            except ErroreApi as err:
                motivo = err.messaggio or f"HTTP {err.http}"
                if col_pin and "spin" in motivo.lower().replace("-", ""):
                    raise ErrorePin(f"Škoda non accetta l'S-PIN: {motivo}") from err
                raise ErroreComando(f"Škoda non ha eseguito «{nome}»: {motivo}") from err

    @staticmethod
    def _spin(pin: str | None) -> str:
        if not pin or not str(pin).strip():
            raise ErrorePin("Per chiudere e aprire una Škoda serve l'S-PIN dell'app: scrivilo nelle opzioni")
        return str(pin).strip()

    async def verifica_pin(self, pin: str) -> None:
        """L'S-PIN si prova una volta, alla configurazione: dopo tre errori di fila l'app lo blocca."""
        try:
            _, r = await self._chiama("POST", "/v1/spin/verify", "S-PIN", corpo={"currentSpin": pin})
        except ErroreApi as err:
            raise ErrorePin(f"Škoda non accetta l'S-PIN: {err.messaggio or f'HTTP {err.http}'}") from err
        giusto, restano = dati.verifica_spin(r)
        if not giusto:
            dopo = f" (restano {restano} tentativi prima del blocco)" if restano is not None else ""
            raise ErrorePin(f"L'S-PIN Škoda è sbagliato{dopo}")

    async def chiudi(self, v: Vettura, pin: str | None) -> None:
        corpo = {"currentSpin": self._spin(pin)}
        await self._comanda(v, "chiudi", "POST", f"/v1/vehicle-access/{quote(v.vin)}/lock", corpo, col_pin=True)

    async def apri(self, v: Vettura, pin: str | None) -> None:
        corpo = {"currentSpin": self._spin(pin)}
        await self._comanda(v, "apri", "POST", f"/v1/vehicle-access/{quote(v.vin)}/unlock", corpo, col_pin=True)

    async def trova(self, v: Vettura, pin: str | None) -> None:
        """Clacson e luci: il cloud vuole sapere dov'è l'auto, e si usa l'ultima posizione letta."""
        s = v.stato
        if s is None or s.latitudine is None or s.longitudine is None:
            raise ErroreComando("Škoda suona e lampeggia solo se si sa dov'è l'auto: aspetta che parcheggi")
        corpo = {
            "mode": "HONK_AND_FLASH",
            "vehiclePosition": {"latitude": s.latitudine, "longitude": s.longitudine},
        }
        await self._comanda(v, "trova", "POST", f"/v1/vehicle-access/{quote(v.vin)}/honk-and-flash", corpo)

    async def clima(self, v: Vettura, acceso: bool, pin: str | None, temperatura: int = 22) -> None:
        percorso = f"/v2/air-conditioning/{quote(v.vin)}"
        if not acceso:
            await self._comanda(v, "clima", "POST", f"{percorso}/stop")
            return
        gradi = min(30.0, max(16.0, round(float(temperatura) * 2) / 2))
        corpo = {
            "heaterSource": "ELECTRIC",
            "targetTemperature": {"temperatureValue": gradi, "unitInCar": "CELSIUS"},
        }
        await self._comanda(v, "clima", "POST", f"{percorso}/start", corpo)

    async def ricarica(self, v: Vettura, avvia: bool, pin: str | None) -> None:
        await self._comanda(v, "ricarica", "POST", f"/v1/charging/{quote(v.vin)}/{'start' if avvia else 'stop'}")

    async def limite_ricarica(self, v: Vettura, percento: int, pin: str | None) -> None:
        """Il limite va a decine, fra 50 e 100, come nell'app."""
        limite = max(50, min(100, round(percento / 10) * 10))
        corpo = {"targetSOCInPercent": limite}
        await self._comanda(v, "limite di ricarica", "PUT", f"/v1/charging/{quote(v.vin)}/set-charge-limit", corpo)
