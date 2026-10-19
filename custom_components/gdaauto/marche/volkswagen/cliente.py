"""Il cliente del cloud europeo Volkswagen (il BFF di CARIAD, ``emea.bff.cariad.digital``).

È il cloud dell'app Volkswagen per le auto vendute in Europa: la famiglia ID
(ID.3, ID.4, ID.5, ID.7, ID.Buzz), le e-Golf ed e-up! e le ibride plug-in.

- ``/vehicle/v1/vehicles`` elenca le auto dell'account, ognuna coi suoi
  servizi («capabilities»): un servizio con uno ``status`` (licenza scaduta,
  disattivato…) non si usa;
- ``/vehicle/v1/vehicles/<vin>/selectivestatus?jobs=…`` dà lo stato, un
  servizio per «job»: è quello che l'auto ha mandato l'ultima volta, e
  leggerlo non la sveglia (GdAAuto non usa mai ``vehiclewakeuptrigger``);
- ``/parkingposition`` dà dove è parcheggiata (nessuna risposta mentre va);
- ``/vehicle/v1/trips/<vin>/longterm/last`` il consumo medio, letto ogni mezz'ora.

I comandi sono dello stesso cloud: serratura (con l'S-PIN), clima, ricarica,
limite di ricarica, clacson e luci. Uno alla volta: l'auto ne esegue uno solo.

Indirizzi, corpi dei comandi e servizi vengono da
tillsteinbach/CarConnectivity-connector-volkswagen (``connector.py``),
licenza MIT: vedi RINGRAZIAMENTI.md. Il codice è scritto da capo, su ``aiohttp``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
import logging
import secrets
import time
from typing import Any

import aiohttp

from ...vettura import StatoVettura, Vettura
from ..errori import ErroreApi, ErroreComando, ErroreCredenziali, ErrorePin, ErroreRete
from . import accesso, dati
from .accesso import TokenVolkswagen

_LOGGER = logging.getLogger(__name__)

ID = "volkswagen"
NOME = "Volkswagen"

_TIMEOUT = aiohttp.ClientTimeout(total=40)
#: Il token si rinnova un po' prima che scada.
_MARGINE_S = 300
_STORICO_OGNI_S = 1800

#: I servizi che il ``selectivestatus`` sa leggere e che GdAAuto usa.
LAVORI = (
    "access",
    "charging",
    "chargingProfiles",
    "batteryChargingCare",
    "climatisation",
    "climatisationTimers",
    "departureTimers",
    "fuelStatus",
    "vehicleLights",
    "lvBattery",
    "readiness",
    "vehicleHealthInspection",
    "vehicleHealthWarnings",
    "measurements",
    "batterySupport",
)
#: Quando il cloud non ha detto quali servizi ha l'auto, si chiedono questi.
_LAVORI_DI_BASE = ("access", "charging", "climatisation", "fuelStatus", "measurements", "readiness")

#: Il servizio che serve a ogni comando neutro.
_SERVIZI_DEI_COMANDI = {
    "chiudi": "access",
    "trova": "honkAndFlash",
    "clima": "climatisation",
    "ricarica": "charging",
    "limite_ricarica": "charging",
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
    return {comando for comando, servizio in _SERVIZI_DEI_COMANDI.items() if servizio in servizi}


def lavori_di(v: Vettura) -> list[str]:
    servizi = v.extra.get("servizi")
    if not isinstance(servizi, list):
        return list(_LAVORI_DI_BASE)
    return [lavoro for lavoro in LAVORI if lavoro in servizi] or list(_LAVORI_DI_BASE)


def vettura_da_json(voce: dict[str, Any]) -> Vettura | None:
    """Una voce di ``/vehicle/v1/vehicles``; ``None`` se l'auto non si ricarica (a benzina o diesel)."""
    vin = str(voce.get("vin") or "").strip()
    if not vin:
        return None
    capacita = voce.get("capabilities")
    servizi: list[str] | None = None
    if isinstance(capacita, list) and capacita:
        tutti = {str(c.get("id")) for c in capacita if isinstance(c, dict) and c.get("id")}
        if "charging" not in tutti:
            return None
        # Uno «status» (licenza scaduta, servizio spento…) vuol dire che il servizio non si può usare.
        servizi = sorted(str(c["id"]) for c in capacita if isinstance(c, dict) and c.get("id") and not c.get("status"))
    modello = str(voce.get("model") or "").strip() or NOME
    ruolo = str(voce.get("role") or "")
    extra: dict[str, Any] = {"ruolo": ruolo}
    if servizi is not None:
        extra["servizi"] = servizi
    return Vettura(
        vin=vin,
        marca=ID,
        modello=modello,
        nome=str(voce.get("nickname") or "").strip() or modello,
        condivisa=bool(ruolo) and ruolo != "PRIMARY_USER",
        extra=extra,
    )


def _errore_di(r: Any) -> tuple[Any, str]:
    """Il codice e il messaggio dentro ``{"error": {"code": …, "message": …, "info": …}}``."""
    errore = r.get("error") if isinstance(r, dict) else None
    if isinstance(errore, list) and errore:
        errore = errore[0]
    if not isinstance(errore, dict):
        return None, ""
    messaggio = " — ".join(str(errore[k]) for k in ("message", "info") if errore.get(k))
    return errore.get("code"), messaggio


class ClienteVolkswagen:
    def __init__(
        self,
        sessione_http: aiohttp.ClientSession,
        sessione: dict[str, Any] | None = None,
        salva: Callable[[dict[str, Any]], None] | None = None,
        *,
        bff: str | None = None,
        identita: str | None = None,
    ) -> None:
        self._http = sessione_http
        self._bff = (bff or accesso.BFF).rstrip("/")
        self._token = TokenVolkswagen(sessione_http, bff=self._bff, identita=identita)
        #: i token: access, id, refresh e quando scade l'access
        self.sessione: dict[str, Any] = dict(sessione or {})
        self._salva = salva
        self._entrata = asyncio.Lock()
        self._comando = asyncio.Lock()
        self._grezzi: dict[str, dict[str, Any]] = {}
        self._storici: dict[str, tuple[dict[str, Any], float]] = {}
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
            refresh = str(self.sessione.get("refresh_token") or "")
            punto = str(self.sessione.get("punto") or accesso.PUNTO_BFF)
            self._conserva(await self._token.rinnova(refresh, punto))

    def usa(self, token: dict[str, Any]) -> None:
        """I token appena avuti dal login nel browser."""
        self._conserva(token)

    # --- le chiamate ----------------------------------------------------------------------

    def _intestazioni(self) -> dict[str, str]:
        traccia = secrets.token_hex(16).upper()
        return {
            "Authorization": f"Bearer {self.sessione.get('access_token') or ''}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Content-Version": "1",
            "User-Agent": accesso.USER_AGENT,
            "x-android-package-name": accesso.PACCHETTO_APP,
            "weconnect-trace-id": f"{traccia[:8]}-{traccia[8:12]}-{traccia[12:16]}-{traccia[16:20]}-{traccia[20:]}",
        }

    async def _chiama(
        self,
        metodo: str,
        percorso: str,
        cosa: str,
        *,
        corpo: dict[str, Any] | None = None,
        attesi: tuple[int, ...] = (200,),
        _ripeti: bool = True,
    ) -> tuple[int, Any]:
        await self.accedi()
        token = str(self.sessione.get("access_token") or "")
        try:
            async with self._http.request(
                metodo,
                f"{self._bff}{percorso}",
                headers=self._intestazioni(),
                data=json.dumps(corpo) if corpo is not None else None,
                timeout=_TIMEOUT,
                allow_redirects=False,
            ) as risposta:
                http, testo = risposta.status, await risposta.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ErroreRete(f"{cosa}: il cloud Volkswagen non risponde: {err}") from err
        if http == 401:
            if not _ripeti:
                raise ErroreCredenziali(f"Volkswagen non accetta più l'accesso ({cosa})")
            await self.accedi(scaduto=token)
            return await self._chiama(metodo, percorso, cosa, corpo=corpo, attesi=attesi, _ripeti=False)
        if http == 429:
            raise ErroreRete(f"{cosa}: il cloud Volkswagen chiede di rallentare (troppe richieste)")
        if http >= 500:
            raise ErroreRete(f"{cosa}: il cloud Volkswagen risponde HTTP {http}")
        try:
            r = json.loads(testo) if testo.strip() else None
        except ValueError:
            r = None
        if http not in attesi:
            codice, messaggio = _errore_di(r)
            raise ErroreApi(cosa, codice, messaggio or _MESSAGGI_HTTP.get(http, testo[:160]), http)
        return http, r

    # --- le auto e lo stato -----------------------------------------------------------------

    async def vetture(self) -> list[Vettura]:
        _, r = await self._chiama("GET", "/vehicle/v1/vehicles", "elenco delle auto")
        voci = r.get("data") if isinstance(r, dict) else None
        trovate = [vettura_da_json(voce) for voce in voci or [] if isinstance(voce, dict)]
        return [v for v in trovate if v is not None]

    async def _facoltativa(self, v: Vettura, cosa: str, percorso: str) -> Any:
        """Una lettura in più, che può mancare: se l'auto non ha quel servizio non si chiede più."""
        if (v.vin, cosa) in self._mancanti:
            return None
        try:
            _, r = await self._chiama("GET", percorso, cosa, attesi=(200, 204))
        except ErroreApi as err:
            if err.http in (403, 404):
                self._mancanti.add((v.vin, cosa))
            _LOGGER.debug("%s della Volkswagen non letto: %s", cosa, err)
        except ErroreRete as err:
            _LOGGER.debug("%s della Volkswagen non letto: %s", cosa, err)
        else:
            return r
        return None

    async def stato(self, v: Vettura) -> StatoVettura:
        lavori = ",".join(lavori_di(v))
        http, r = await self._chiama(
            "GET",
            f"/vehicle/v1/vehicles/{v.vin}/selectivestatus?jobs={lavori}",
            "stato dell'auto",
            attesi=(200, 207),
        )
        if not isinstance(r, dict) or not r:
            raise ErroreApi("stato dell'auto", None, "il cloud non ha dati per questa auto", http)
        self._grezzi[v.vin] = r
        s = dati.interpreta(r)
        # Mentre va il cloud non dice dove (204): niente posizione, non quella vecchia.
        parcheggio = await self._facoltativa(v, "posizione", f"/vehicle/v1/vehicles/{v.vin}/parkingposition")
        if punto := dati.posizione(parcheggio):
            s.latitudine, s.longitudine = punto
            s.posizione_con_segno = True
        for campo, valore in (await self._storico(v)).items():
            setattr(s, campo, valore)
        return s

    async def _storico(self, v: Vettura) -> dict[str, Any]:
        """Il consumo medio, riletto ogni mezz'ora; se non va, lo stato arriva lo stesso."""
        campi, quando = self._storici.get(v.vin, ({}, 0.0))
        if quando and time.monotonic() - quando < _STORICO_OGNI_S:
            return campi
        campi = dict(campi)
        viaggi = await self._facoltativa(v, "consumo medio", f"/vehicle/v1/trips/{v.vin}/longterm/last")
        if (consumo := dati.consumo_medio(viaggi)) is not None:
            campi["consumo_medio_kwh_100km"] = consumo
        self._storici[v.vin] = (campi, time.monotonic())
        return campi

    # --- i comandi ----------------------------------------------------------------------------

    async def _comanda(
        self, v: Vettura, nome: str, metodo: str, percorso: str, corpo: dict[str, Any], col_pin: bool = False
    ) -> None:
        async with self._comando:
            try:
                await self._chiama(
                    metodo, f"/vehicle/v1/vehicles/{v.vin}/{percorso}", nome, corpo=corpo, attesi=(200, 202, 204)
                )
            except ErroreApi as err:
                motivo = err.messaggio or f"HTTP {err.http}"
                if col_pin and ("spin" in motivo.lower() or "s-pin" in motivo.lower()):
                    raise ErrorePin(f"Volkswagen non accetta l'S-PIN: {motivo}") from err
                raise ErroreComando(f"Volkswagen non ha eseguito «{nome}»: {motivo}") from err

    @staticmethod
    def _spin(pin: str | None) -> str:
        if not pin or not str(pin).strip():
            raise ErrorePin("Per chiudere e aprire una Volkswagen serve l'S-PIN dell'app: scrivilo nelle opzioni")
        return str(pin).strip()

    async def verifica_pin(self, pin: str) -> None:
        """L'S-PIN si prova una volta, alla configurazione: tre errori di fila lo bloccano."""
        try:
            await self._chiama("POST", "/vehicle/v1/spin/verify", "S-PIN", corpo={"spin": pin}, attesi=(200, 204))
        except ErroreApi as err:
            raise ErrorePin(f"Volkswagen non accetta l'S-PIN: {err.messaggio or f'HTTP {err.http}'}") from err

    async def chiudi(self, v: Vettura, pin: str | None) -> None:
        await self._comanda(v, "chiudi", "POST", "access/lock", {"spin": self._spin(pin)}, col_pin=True)

    async def apri(self, v: Vettura, pin: str | None) -> None:
        await self._comanda(v, "apri", "POST", "access/unlock", {"spin": self._spin(pin)}, col_pin=True)

    async def trova(self, v: Vettura, pin: str | None) -> None:
        """Clacson e luci per dieci secondi: il cloud vuole sapere dov'è chi lo chiede, e si usa l'auto."""
        s = v.stato
        if s is None or s.latitudine is None or s.longitudine is None:
            raise ErroreComando("Volkswagen suona e lampeggia solo se si sa dov'è l'auto: aspetta che parcheggi")
        corpo = {
            "mode": "honk-and-flash",
            "duration_s": 10,
            "userPosition": {"latitude": s.latitudine, "longitude": s.longitudine},
        }
        await self._comanda(v, "trova", "POST", "honkandflash", corpo)

    async def clima(self, v: Vettura, acceso: bool, pin: str | None, temperatura: int = 22) -> None:
        if not acceso:
            await self._comanda(v, "clima", "POST", "climatisation/stop", {})
            return
        gradi = min(30.0, max(16.0, round(float(temperatura) * 2) / 2))
        corpo = {
            "targetTemperature": gradi,
            "targetTemperatureUnit": "celsius",
            **dati.impostazioni_clima(self._grezzi.get(v.vin) or {}),
        }
        await self._comanda(v, "clima", "POST", "climatisation/start", corpo)

    async def ricarica(self, v: Vettura, avvia: bool, pin: str | None) -> None:
        await self._comanda(v, "ricarica", "POST", "charging/start" if avvia else "charging/stop", {})

    async def limite_ricarica(self, v: Vettura, percento: int, pin: str | None) -> None:
        """Il limite va a decine, fra 50 e 100; il resto delle impostazioni resta com'era."""
        limite = max(50, min(100, round(percento / 10) * 10))
        corpo = {**dati.impostazioni_ricarica(self._grezzi.get(v.vin) or {}), "targetSOC_pct": limite}
        await self._comanda(v, "limite di ricarica", "PUT", "charging/settings", corpo)
