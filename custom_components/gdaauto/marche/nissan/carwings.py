"""NissanConnect EV (Carwings): il cloud delle Leaf dal 2011 al 2019.

Un cloud vecchio e lento (``gdcportalgw.its-mo.com``), che parla con moduli
POST e risponde in JSON:

1. ``InitialApp_v2.php`` dà una chiave (``baseprm``); la password si cifra
   con Blowfish (ECB, riempimento PKCS#5) con quella chiave e va in base64;
2. ``UserLoginRequest.php`` con email e password cifrata dà la sessione
   (``custom_sessionid``), l'auto (VIN, ``dcmId``), il fuso e la lingua. La
   sessione non ha un rinnovo: quando il cloud non la accetta più si rientra
   con la password, che per questo resta salvata;
3. ``BatteryStatusRecordsRequest.php`` e ``RemoteACRecordsRequest.php`` danno
   l'**ultima** lettura che l'auto ha mandato e l'ultimo comando del clima.
   Non svegliano l'auto: per questo i dati cambiano solo quando la Leaf parla
   da sé (fine viaggio, inizio e fine della carica) o quando l'app le chiede
   un aggiornamento. GdAAuto non glielo chiede mai: ogni richiesta sveglia il
   modulo telematico e consuma la batteria da 12 V;
4. i comandi (clima acceso e spento, avvio della ricarica) tornano una
   ``resultKey``: il risultato si chiede finché ``responseFlag`` è «1».
   ``ELECTRIC_WAVE_ABNORMAL`` vuol dire che l'auto non ha campo.

La posizione («Locate my vehicle») chiederebbe all'auto di rispondere, e in
Europa il cloud non la dà più: qui non c'è. Chiusura, luci e clacson il
Carwings non li ha mai avuti.

Gli indirizzi, i parametri e la cifratura della password vengono da
filcole/pycarwings2 (licenza Apache-2.0, Copyright 2016 Jason Horne) e da
come l'integrazione Nissan Leaf di Home Assistant (Apache-2.0) la usa: vedi
RINGRAZIAMENTI.md.
"""

from __future__ import annotations

import asyncio
import base64
from datetime import UTC, date, datetime
import json
import logging
import time
from typing import Any

import aiohttp

from ...vettura import StatoVettura, Vettura
from ..errori import ErroreApi, ErroreComando, ErroreCredenziali, ErroreRete
from .dati import interpreta_carwings, storico_carwings

try:  # cryptography 43 e dopo: Blowfish sta fra i cifrari «decrepiti»
    from cryptography.hazmat.decrepit.ciphers.algorithms import Blowfish
except ImportError:  # pragma: no cover - versioni vecchie
    from cryptography.hazmat.primitives.ciphers.algorithms import Blowfish  # type: ignore[no-redef,attr-defined]
from cryptography.hazmat.primitives.ciphers import Cipher, modes

_LOGGER = logging.getLogger(__name__)

URL = "https://gdcportalgw.its-mo.com/api_v210707_NE/gdc/"
#: la stringa fissa dell'app, la stessa per tutti (da pycarwings2)
APP = "9s5rfKVuMrT03RtzajWNcA"
#: le regioni del Carwings: Europa, Stati Uniti, Canada, Australia, Giappone
REGIONI = ("NE", "NNA", "NCI", "NMA", "NML")

#: il cloud a volte ci mette molto
_TIMEOUT = aiohttp.ClientTimeout(total=90)
#: l'app chiede il risultato di un comando ogni venti secondi
ATTESA = 20.0
TENTATIVI = 6
OGNI_STORICO = 30 * 60


def cifra_password(password: str, chiave: str) -> str:
    """Blowfish ECB con la ``baseprm`` come chiave, riempimento PKCS#5, in base64."""
    dati = password.encode()
    riempimento = 8 - len(dati) % 8
    dati += bytes([riempimento]) * riempimento
    cifratore = Cipher(Blowfish(chiave.encode()), modes.ECB()).encryptor()
    return base64.standard_b64encode(cifratore.update(dati) + cifratore.finalize()).decode()


def _stato(r: dict[str, Any]) -> int:
    """Lo «status» dentro la risposta: 200 se va, un numero o una sigla («AAS-604») se no."""
    try:
        return int(r.get("status"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


class ClienteCarwings:
    """Un account NissanConnect EV."""

    def __init__(
        self,
        sessione_http: aiohttp.ClientSession,
        email: str,
        password: str,
        regione: str = "NE",
        *,
        url: str = URL,
        attesa: float = ATTESA,
        tentativi: int = TENTATIVI,
    ) -> None:
        self._http = sessione_http
        self.email = email
        self._password = password
        self.regione = regione if regione in REGIONI else "NE"
        self.url = url if url.endswith("/") else url + "/"
        self.attesa = attesa
        self.tentativi = tentativi
        self._sessione = ""
        self._utente_gdc = ""
        self._lingua = "en-US"
        self._fuso = ""
        self._auto: list[dict[str, str]] = []
        self._serratura_login = asyncio.Lock()
        self._serratura_comandi = asyncio.Lock()
        self._storico: dict[str, tuple[float, dict[str, Any]]] = {}

    # --- la rete -------------------------------------------------------------

    async def _posta(self, pagina: str, parametri: dict[str, str], cosa: str) -> dict[str, Any]:
        modulo = {**parametri, "initial_app_str": APP, "custom_sessionid": self._sessione}
        try:
            async with self._http.post(
                self.url + pagina, data=modulo, headers={"User-Agent": ""}, timeout=_TIMEOUT
            ) as r:
                http = r.status
                testo = await r.text()
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise ErroreRete(f"{cosa}: NissanConnect EV non risponde: {err}") from err
        if http >= 500:
            raise ErroreRete(f"{cosa}: NissanConnect EV risponde HTTP {http}")
        try:
            corpo = json.loads(testo)
        except ValueError:
            corpo = None
        if not isinstance(corpo, dict):
            # Quando è in manutenzione risponde una pagina HTML.
            raise ErroreRete(f"{cosa}: NissanConnect EV risponde una cosa che non si capisce (HTTP {http})")
        return corpo

    # --- entrare -------------------------------------------------------------

    async def _entra(self) -> None:
        self._sessione = ""
        r = await self._posta("InitialApp_v2.php", {"RegionCode": self.regione, "lg": "en-US"}, "avvio")
        chiave = r.get("baseprm")
        if not chiave:
            raise ErroreRete("avvio: NissanConnect EV non ha dato la chiave per la password")
        r = await self._posta(
            "UserLoginRequest.php",
            {"RegionCode": self.regione, "UserId": self.email, "Password": cifra_password(self._password, str(chiave))},
            "login",
        )
        stato = _stato(r)
        if stato >= 500:
            raise ErroreRete(f"login: NissanConnect EV ha un guasto (status {r.get('status')})")
        if str(r.get("status")) == "AAS-604":
            raise ErroreCredenziali("NissanConnect EV: l'abbonamento è scaduto, rinnovalo sul sito Nissan")
        profilo = (r.get("vehicle") or {}).get("profile") if isinstance(r.get("vehicle"), dict) else None
        if stato != 200 or r.get("ErrorCode") or r.get("message") == "INVALID PARAMS":
            motivo = r.get("ErrorMessage") or r.get("message") or r.get("status")
            raise ErroreCredenziali(f"NissanConnect EV rifiuta email o password ({motivo})")
        elenco = r.get("VehicleInfoList") or {}
        info = (elenco.get("vehicleInfo") or elenco.get("VehicleInfo")) if isinstance(elenco, dict) else None
        if not info:
            info = r.get("vehicleInfo") or []
        info = [i for i in info if isinstance(i, dict)] if isinstance(info, list) else []
        self._sessione = str(next((i.get("custom_sessionid") for i in info if i.get("custom_sessionid")), "") or "")
        cliente = r.get("CustomerInfo") or {}
        self._lingua = str(cliente.get("Language") or "en-US")
        self._fuso = str(cliente.get("Timezone") or "")
        legato = str((cliente.get("VehicleInfo") or {}).get("UserVehicleBoundTime") or "1970-01-01T00:00:00Z")
        profilo = profilo if isinstance(profilo, dict) else {}
        self._utente_gdc = str(profilo.get("gdcUserId") or "")
        auto: list[dict[str, str]] = []
        for i in info:
            vin = str(i.get("vin") or "").upper()
            if not vin:
                continue
            stesso = vin == str(profilo.get("vin") or "").upper()
            auto.append(
                {
                    "vin": vin,
                    "nome": str(i.get("nickname") or ""),
                    "dcm": str(profilo.get("dcmId") or "") if stesso else "",
                    "legato": legato,
                }
            )
        if not auto and profilo.get("vin"):
            auto.append(
                {
                    "vin": str(profilo["vin"]).upper(),
                    "nome": "",
                    "dcm": str(profilo.get("dcmId") or ""),
                    "legato": legato,
                }
            )
        self._auto = auto

    async def accedi(self) -> None:
        async with self._serratura_login:
            await self._entra()

    async def _chiama(self, pagina: str, parametri: dict[str, str], cosa: str) -> dict[str, Any]:
        """Una chiamata con la sessione; se il cloud non la accetta più, si rientra una volta."""
        if not self._sessione:
            await self.accedi()
        r = await self._posta(pagina, parametri, cosa)
        if _stato(r) != 200:
            _LOGGER.debug("NissanConnect EV: %s risponde %s, si rientra", cosa, r.get("status"))
            await self.accedi()
            r = await self._posta(pagina, parametri, cosa)
        if r.get("ErrorCode") or r.get("ErrorMessage"):
            raise ErroreApi(cosa, r.get("ErrorCode"), str(r.get("ErrorMessage") or ""))
        if _stato(r) != 200:
            raise ErroreApi(cosa, r.get("status"), str(r.get("message") or ""))
        return r

    def _della(self, v: Vettura) -> dict[str, str]:
        return {
            "RegionCode": self.regione,
            "lg": self._lingua,
            "DCMID": str(v.extra.get("dcm") or ""),
            "VIN": v.vin,
            "tz": self._fuso,
        }

    # --- leggere ---------------------------------------------------------------

    async def vetture(self) -> list[Vettura]:
        if not self._sessione:
            await self.accedi()
        return [
            Vettura(
                vin=a["vin"],
                marca="nissan",
                modello="Leaf",
                nome=a["nome"] if a["nome"] and a["nome"].upper() != "LEAF" else "Nissan Leaf",
                extra={"via": "carwings", "dcm": a["dcm"], "legato": a["legato"]},
            )
            for a in self._auto
        ]

    async def stato(self, v: Vettura) -> StatoVettura:
        legato = str(v.extra.get("legato") or "1970-01-01T00:00:00Z")
        r = await self._chiama("BatteryStatusRecordsRequest.php", {**self._della(v), "TimeFrom": legato}, "batteria")
        batteria = r.get("BatteryStatusRecords")
        if not isinstance(batteria, dict):
            raise ErroreApi("batteria", None, f"NissanConnect EV non ha ancora una lettura di {v.titolo}")
        clima: Any = None
        try:
            c = await self._chiama("RemoteACRecordsRequest.php", {**self._della(v), "TimeFrom": legato}, "clima")
            clima = c.get("RemoteACRecords")
        except (ErroreApi, ErroreRete) as err:
            _LOGGER.debug("NissanConnect EV: clima di %s non letto: %s", v.titolo, err)
        s = interpreta_carwings(batteria, clima)
        for campo, valore in (await self._consumi(v)).items():
            setattr(s, campo, valore)
        return s

    async def _consumi(self, v: Vettura) -> dict[str, Any]:
        """Il riepilogo del mese, riletto ogni mezz'ora; facoltativo."""
        quando, campi = self._storico.get(v.vin, (0.0, {}))
        if quando and time.monotonic() - quando < OGNI_STORICO:
            return campi
        try:
            r = await self._chiama(
                "PriceSimulatorDetailInfoRequest.php",
                {**self._della(v), "TargetMonth": datetime.now(UTC).strftime("%Y%m")},
                "consumi del mese",
            )
            campi = storico_carwings(r)
        except (ErroreApi, ErroreRete) as err:
            _LOGGER.debug("NissanConnect EV: consumi di %s non letti: %s", v.titolo, err)
        self._storico[v.vin] = (time.monotonic(), campi)
        return campi

    # --- comandare --------------------------------------------------------------

    async def _attendi(self, v: Vettura, pagina: str, chiave: str, cosa: str) -> dict[str, Any] | None:
        """Chiede il risultato finché l'auto non ha risposto; ``None`` se non risponde in tempo."""
        for _ in range(self.tentativi):
            await asyncio.sleep(self.attesa)
            r = await self._chiama(pagina, {**self._della(v), "UserId": self._utente_gdc, "resultKey": chiave}, cosa)
            if str(r.get("responseFlag")) == "1":
                return r
        return None

    async def clima(self, v: Vettura, acceso: bool) -> None:
        richiesta, risultato = (
            ("ACRemoteRequest.php", "ACRemoteResult.php")
            if acceso
            else ("ACRemoteOffRequest.php", "ACRemoteOffResult.php")
        )
        cosa = "accendere il clima" if acceso else "spegnere il clima"
        async with self._serratura_comandi:
            r = await self._chiama(richiesta, self._della(v), cosa)
            chiave = str(r.get("resultKey") or "")
            if not chiave:
                raise ErroreComando(f"{v.titolo} non esegue il comando ({cosa}): il cloud non l'ha accettato")
            esito = await self._attendi(v, risultato, chiave, cosa)
        if esito is None:
            _LOGGER.info(
                "NissanConnect EV: %s non ha ancora risposto (%s); il comando può arrivare più tardi", v.titolo, cosa
            )
            return
        if str(esito.get("operationResult") or "") == "ELECTRIC_WAVE_ABNORMAL":
            raise ErroreComando(f"{v.titolo} non esegue il comando ({cosa}): l'auto non ha campo")
        stato = str(esito.get("hvacStatus") or "").upper()
        if stato and (stato == "ON") != acceso:
            raise ErroreComando(
                f"{v.titolo} non esegue il comando ({cosa}): l'auto risponde «{esito.get('operationResult')}»"
            )

    async def ricarica(self, v: Vettura, avvia: bool) -> None:
        if not avvia:
            raise ErroreComando(f"{v.titolo} non ferma la ricarica da remoto: si ferma dall'auto o staccando il cavo")
        async with self._serratura_comandi:
            await self._chiama(
                "BatteryRemoteChargingRequest.php",
                {**self._della(v), "ExecuteTime": date.today().isoformat()},
                "avviare la ricarica",
            )
