"""Il filo col cloud myAudi: il BFF di CARIAD, quello dell'app per le e-tron.

Tutto passa da ``https://<regione>.bff.cariad.digital/vehicle/v1/…`` col token
dell'identità del gruppo (vedi ``identita.py``):

- ``vehicles``: le auto dell'account, col modello, il nome e le capacità;
- ``vehicles/{vin}/selectivestatus?jobs=…``: lo stato, in una chiamata sola.
  È l'ultima fotografia che l'auto ha mandato al cloud: leggerla non sveglia
  l'auto (svegliarla è ``vehiclewakeup``, che GdAAuto non usa);
- ``vehicles/{vin}/parkingposition``: dov'è parcheggiata (in marcia non c'è);
- i comandi: ``access/lock`` e ``access/unlock`` con l'S-PIN,
  ``climatisation/start|stop``, ``charging/start|stop``, ``charging/settings``
  per il limite, ``honkandflash`` per far lampeggiare le frecce. Il cloud dice
  «ricevuto» subito; l'esito vero arriva dopo in ``pendingrequests``, che si
  guarda per qualche secondo.

Il cloud conta le chiamate (l'intestazione ``Vcf-Remaining-Calls``): Audi dà
un numero di chiamate al giorno per auto, e l'app di riferimento non legge più
spesso di ogni quarto d'ora. Il consumo e i km della settimana vengono dalle
statistiche di viaggio del vecchio cloud MBB (``mal-*.vwg-connect.com``), con
un suo token ricavato dall'``id_token``: si leggono ogni mezz'ora, e se non
vanno lo stato arriva lo stesso.

Indirizzi, corpi dei comandi e lavori vengono da audiconnect/audi_connect_ha e
da acfischer42/CarConnectivity-connector-audi (licenza MIT): vedi
RINGRAZIAMENTI.md.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import json
import logging
import time
from typing import Any

import aiohttp

from ...vettura import StatoVettura, Vettura
from ..errori import ErroreApi, ErroreComando, ErroreCredenziali, ErrorePin, ErroreRete
from .dati import ID, LAVORI, errori_in, interpreta, riassumi_viaggi, vettura_da
from .identita import REGIONI, USER_AGENT, VERSIONE_APP, Identita, chi_e, scaduto

_LOGGER = logging.getLogger(__name__)

NOME = "Audi"

_TIMEOUT = aiohttp.ClientTimeout(total=30)
#: Le statistiche di viaggio cambiano piano: si rileggono ogni mezz'ora.
_STORICO_OGNI_S = 1800
#: Sotto queste chiamate rimaste nella giornata si lasciano perdere le letture facoltative.
_CHIAMATE_DA_RISPARMIARE = 20

#: Il vecchio cloud MBB, per le statistiche di viaggio.
MBB_OAUTH = "https://mbboauth-1d.prd.ece.vwg-connect.com/mbbcoauth"
#: In Europa le e-tron rispondono da qui (audi_connect_ha: l'indirizzo che dà ``homeRegion`` non è valido).
MAL_EUROPA = "https://mal-3a.prd.eu.dp.vwg-connect.com"
MAL_MONDO = "https://mal-1a.prd.ece.vwg-connect.com"


def comandi_di(v: Vettura) -> set[str]:
    """I comandi che l'auto dichiara fra le sue capacità; se non ne dice, quelli di ogni e-tron."""
    capacita = set(v.extra.get("capacita") or ())
    if not capacita:
        return {"chiudi", "apri", "clima", "ricarica", "limite_ricarica"}
    comandi: set[str] = set()
    if "access" in capacita:
        comandi |= {"chiudi", "apri"}
    if "honkAndFlash" in capacita:
        comandi.add("trova")
    if "climatisation" in capacita:
        comandi.add("clima")
    if "charging" in capacita:
        comandi |= {"ricarica", "limite_ricarica"}
    return comandi


def _messaggio(testo: str) -> str:
    """Il motivo leggibile di un errore del BFF (``{"error": {"message": …, "info": …}}``)."""
    try:
        r = json.loads(testo)
    except ValueError:
        return testo[:200]
    errore = r.get("error") if isinstance(r, dict) else None
    if isinstance(errore, dict):
        return str(errore.get("message") or errore.get("info") or errore.get("errorCode") or errore)[:200]
    if isinstance(errore, str):
        return str(r.get("error_description") or errore)[:200]
    return testo[:200]


class ClienteAudi:
    def __init__(
        self,
        http: aiohttp.ClientSession,
        *,
        regione: str,
        token: dict[str, Any] | None = None,
        salva: Callable[[dict[str, Any]], None] | None = None,
        bff: str | None = None,
        mbb: str | None = None,
        mal: str | None = None,
        attesa_s: float = 5.0,
        tentativi: int = 4,
    ) -> None:
        self._http = http
        self.regione = regione if regione in REGIONI else "europa"
        self._bff = (bff or REGIONI[self.regione].bff).rstrip("/")
        self._mbb = (mbb or MBB_OAUTH).rstrip("/")
        self._mal = (mal or (MAL_MONDO if self.regione == "usa" else MAL_EUROPA)).rstrip("/")
        self.identita = Identita(http, self._bff)
        self.token: dict[str, Any] = dict(token or {})
        self._salva = salva
        self._attesa_s = attesa_s
        self._tentativi = tentativi
        self._serratura_token = asyncio.Lock()
        #: un comando alla volta: il cloud ne tiene uno per auto in coda
        self._serratura_comandi = asyncio.Lock()
        #: le impostazioni del clima com'erano all'ultima lettura (il comando le rimanda)
        self._clima: dict[str, dict[str, Any]] = {}
        #: VIN → (i campi dello storico, quando si sono letti)
        self._storici: dict[str, tuple[dict[str, Any], float]] = {}
        self._mbb_token: dict[str, Any] = {}
        self._mbb_client: str | None = None
        #: quante chiamate restano oggi, se il cloud lo dice
        self.chiamate_rimaste: int | None = None

    # --- i token ---------------------------------------------------------------------------

    def _salva_token(self, token: dict[str, Any]) -> None:
        self.token = token
        if self._salva:
            self._salva(dict(token))

    async def accedi(self, *, forza: bool = False) -> None:
        """Un token valido: quello che c'è, o rinnovato. Un refresh rifiutato chiede di rientrare dal browser."""
        async with self._serratura_token:
            if not forza and not scaduto(self.token):
                return
            self._salva_token(await self.identita.rinnova(self.token))

    @property
    def account(self) -> str:
        """Chi è: l'email dell'``id_token``, o il suo ``sub``."""
        chi = chi_e(self.token)
        return str(chi.get("email") or chi.get("sub") or "")

    # --- le chiamate al BFF ------------------------------------------------------------------

    def _intestazioni(self, token: str) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Accept-Charset": "utf-8",
            "X-App-Name": "myAudi",
            "X-App-Version": VERSIONE_APP,
            "User-Agent": USER_AGENT,
            "Authorization": f"Bearer {token}",
        }

    async def _grezza(
        self, metodo: str, url: str, cosa: str, intestazioni: dict[str, str], **altro: Any
    ) -> tuple[int, str]:
        try:
            async with self._http.request(
                metodo, url, headers=intestazioni, timeout=_TIMEOUT, allow_redirects=False, **altro
            ) as risposta:
                rimaste = risposta.headers.get("Vcf-Remaining-Calls")
                if rimaste and rimaste.lstrip("-").isdigit():
                    self.chiamate_rimaste = int(rimaste)
                    if self.chiamate_rimaste < 10:
                        _LOGGER.warning("Audi: restano %s chiamate per oggi", rimaste)
                return risposta.status, await risposta.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ErroreRete(f"{cosa}: il cloud Audi non risponde: {err}") from err

    async def _chiama(
        self,
        metodo: str,
        percorso: str,
        cosa: str,
        *,
        corpo: Any = None,
        ammessi: tuple[int, ...] = (200, 201, 202, 204, 207),
        _ripeti: bool = True,
    ) -> tuple[int, Any]:
        """Una chiamata al BFF: ``(http, json o None)``. Un 401 rinnova il token e riprova una volta."""
        await self.accedi()
        intestazioni = self._intestazioni(str(self.token["access_token"]))
        altro: dict[str, Any] = {}
        if corpo is not None:
            intestazioni["Content-Type"] = "application/json; charset=utf-8"
            altro["data"] = json.dumps(corpo)
        http, testo = await self._grezza(metodo, f"{self._bff}/vehicle/v1/{percorso}", cosa, intestazioni, **altro)
        if http == 401 and _ripeti:
            await self.accedi(forza=True)
            return await self._chiama(metodo, percorso, cosa, corpo=corpo, ammessi=ammessi, _ripeti=False)
        if http == 401:
            raise ErroreCredenziali(f"{cosa}: Audi rifiuta il token anche dopo il rinnovo")
        if http == 429:
            raise ErroreRete(f"{cosa}: troppe chiamate al cloud Audi per oggi, si riprova più tardi")
        if http >= 500:
            raise ErroreRete(f"{cosa}: il cloud Audi ha un guasto (HTTP {http})")
        if http not in ammessi:
            raise ErroreApi(cosa, http, _messaggio(testo), http)
        if not testo.strip():
            return http, None
        try:
            return http, json.loads(testo)
        except ValueError as err:
            raise ErroreRete(f"{cosa}: risposta che non si capisce (HTTP {http})") from err

    # --- le auto e lo stato ------------------------------------------------------------------

    async def vetture(self) -> list[Vettura]:
        _, r = await self._chiama("GET", "vehicles", "elenco delle auto")
        voci = (r or {}).get("data") if isinstance(r, dict) else None
        return [v for voce in voci or [] if isinstance(voce, dict) and (v := vettura_da(voce))]

    async def stato(self, vettura: Vettura) -> StatoVettura:
        vin = vettura.vin
        _, dati = await self._chiama("GET", f"vehicles/{vin}/selectivestatus?jobs={','.join(LAVORI)}", "stato")
        if not isinstance(dati, dict):
            raise ErroreRete("stato: il cloud Audi ha risposto senza dati")
        stato = interpreta(dati)
        if stato.letto is None and errori_in(dati):
            # Ogni lavoro è tornato con un errore: un guasto di CARIAD, non un'auto senza dati.
            raise ErroreRete("stato: il cloud Audi non ha dati dell'auto, per ora (tutti i lavori in errore)")
        clima = ((dati.get("climatisation") or {}).get("climatisationSettings") or {}).get("value")
        if isinstance(clima, dict):
            self._clima[vin] = clima

        posizione = await self._posizione(vin)
        if posizione:
            stato = interpreta(dati, posizione)
        for campo, valore in (await self._storico(vettura)).items():
            setattr(stato, campo, valore)
        if self.chiamate_rimaste is not None:
            stato.grezzo["chiamate_rimaste"] = self.chiamate_rimaste
        return stato

    async def _posizione(self, vin: str) -> dict[str, Any] | None:
        """Dov'è parcheggiata. In marcia il cloud risponde 204 (nessuna posizione): non è un errore."""
        try:
            http, r = await self._chiama("GET", f"vehicles/{vin}/parkingposition", "posizione", ammessi=(200, 204, 404))
        except ErroreApi as err:
            _LOGGER.debug("Posizione dell'Audi %s non letta: %s", vin[-4:], err)
            return None
        dati = r.get("data") if http == 200 and isinstance(r, dict) else None
        return dati if isinstance(dati, dict) else None

    # --- lo storico, dal vecchio cloud MBB ----------------------------------------------------

    async def _storico(self, vettura: Vettura) -> dict[str, Any]:
        """Consumo medio e settimana, riletti ogni mezz'ora; facoltativi: se non vanno resta quello di prima."""
        campi, quando = self._storici.get(vettura.vin, ({}, 0.0))
        if quando and time.monotonic() - quando < _STORICO_OGNI_S:
            return campi
        if self.chiamate_rimaste is not None and self.chiamate_rimaste < _CHIAMATE_DA_RISPARMIARE:
            return campi
        try:
            lungo = await self._viaggi(vettura.vin, "longTerm")
            corto = await self._viaggi(vettura.vin, "shortTerm")
            campi = {**campi, **riassumi_viaggi(lungo, corto)}
        except Exception as err:  # lo storico non deve mai fermare lo stato
            _LOGGER.debug("Statistiche di viaggio dell'Audi %s non lette: %s", vettura.vin[-4:], err)
        self._storici[vettura.vin] = (campi, time.monotonic())
        return campi

    async def _token_mbb(self) -> str:
        """Il token del vecchio cloud MBB, dall'``id_token``: un client registrato, poi lo scambio."""
        if self._mbb_token and not scaduto(self._mbb_token):
            return str(self._mbb_token["access_token"])
        await self.accedi()
        id_token = str(self.token.get("id_token") or "")
        if not id_token:
            raise ErroreApi("token MBB", "id_token", "manca l'id_token", 0)
        base = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if not self._mbb_client:
            corpo = {
                "client_name": "SM-A405FN",
                "platform": "google",
                "client_brand": "Audi",
                "appName": "myAudi",
                "appVersion": VERSIONE_APP,
                "appId": "de.myaudi.mobile.assistant",
            }
            http, testo = await self._grezza(
                "POST",
                f"{self._mbb}/mobile/register/v1",
                "registrazione MBB",
                {**base, "Content-Type": "application/json; charset=utf-8"},
                data=json.dumps(corpo),
            )
            if http not in (200, 201):
                raise ErroreApi("registrazione MBB", http, testo[:200], http)
            self._mbb_client = str(json.loads(testo)["client_id"])
        http, testo = await self._grezza(
            "POST",
            f"{self._mbb}/mobile/oauth2/v1/token",
            "token MBB",
            {**base, "Content-Type": "application/x-www-form-urlencoded", "X-Client-ID": self._mbb_client},
            data={"grant_type": "id_token", "token": id_token, "scope": "sc2:fal"},
        )
        r = json.loads(testo) if testo.strip() else {}
        if http != 200 or not isinstance(r, dict) or not r.get("access_token"):
            raise ErroreApi("token MBB", http, str((r or {}).get("error_description") or testo[:200]), http)
        durata = int(r.get("expires_in") or 3600)
        self._mbb_token = {
            "access_token": r["access_token"],
            "scade": (datetime.now(UTC) + timedelta(seconds=durata)).isoformat(),
        }
        return str(r["access_token"])

    async def _viaggi(self, vin: str, tipo: str) -> Any:
        token = await self._token_mbb()
        intestazioni = {
            "Accept": "application/json",
            "X-App-Name": "myAudi",
            "X-App-Version": VERSIONE_APP,
            "User-Agent": USER_AGENT,
            "X-Client-ID": str(self._mbb_client or ""),
            "Authorization": f"Bearer {token}",
        }
        dopo = (datetime.now(UTC) - timedelta(days=30 if tipo == "shortTerm" else 3650)).strftime("%Y-%m-%dT%H:%M:%SZ")
        fino = (datetime.now(UTC) + timedelta(minutes=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
        http, testo = await self._grezza(
            "GET",
            f"{self._mal}/api/bs/tripstatistics/v1/vehicles/{vin}/tripdata/{tipo}",
            "statistiche di viaggio",
            intestazioni,
            params={"type": "list", "from": dopo, "to": fino},
        )
        if http == 401:
            self._mbb_token = {}
        if http == 204:
            return {}
        if http != 200:
            raise ErroreApi("statistiche di viaggio", http, testo[:200], http)
        return json.loads(testo)

    # --- i comandi ---------------------------------------------------------------------------

    async def _comanda(
        self, vettura: Vettura, cosa: str, percorso: str, corpo: Any = None, metodo: str = "POST"
    ) -> Any:
        """Un comando, uno alla volta: la richiesta, e poi l'esito in ``pendingrequests``."""
        async with self._serratura_comandi:
            try:
                _, r = await self._chiama(metodo, f"vehicles/{vettura.vin}/{percorso}", cosa, corpo=corpo)
            except ErroreApi as err:
                raise ErroreComando(f"{cosa}: Audi rifiuta il comando ({err.messaggio or f'HTTP {err.http}'})") from err
            richiesta = ((r or {}).get("data") or {}).get("requestID") if isinstance(r, dict) else None
            if richiesta:
                await self._conferma(vettura, cosa, str(richiesta))
            return r

    async def _conferma(self, vettura: Vettura, cosa: str, richiesta: str) -> None:
        """Si guarda per qualche secondo se l'auto ha eseguito. Senza risposta il comando si dà per partito."""
        for _ in range(self._tentativi):
            await asyncio.sleep(self._attesa_s)
            try:
                _, r = await self._chiama("GET", f"vehicles/{vettura.vin}/pendingrequests", "esito del comando")
            except (ErroreApi, ErroreRete) as err:
                _LOGGER.debug("Esito di «%s» non letto: %s", cosa, err)
                return
            voci = (r or {}).get("data") if isinstance(r, dict) else None
            voce = next((v for v in voci or [] if isinstance(v, dict) and v.get("id") == richiesta), None)
            if voce is None:
                # Uscita dalla coda: il cloud tiene solo quelle in corso.
                return
            esito = str(voce.get("status") or "")
            if esito == "successful":
                return
            if esito not in ("in_progress", "queued", ""):
                raise ErroreComando(f"{cosa}: l'auto non l'ha eseguito (esito «{esito}»)")
        _LOGGER.info("«%s» per l'Audi %s accettato ma non ancora confermato dall'auto", cosa, vettura.vin[-4:])

    async def _serratura(self, vettura: Vettura, chiudi: bool, pin: str | None) -> None:
        cosa = "chiusura" if chiudi else "apertura"
        pin = str(pin or "").strip()
        if not pin:
            raise ErrorePin("Serve l'S-PIN di myAudi (quattro cifre), nelle opzioni")
        try:
            await self._comanda(vettura, cosa, "access/lock" if chiudi else "access/unlock", {"spin": pin})
        except ErroreComando as err:
            testo = str(err).lower()
            if "spin" in testo or "s-pin" in testo or "pin" in testo.split():
                raise ErrorePin(f"Audi non accetta l'S-PIN: {err}") from err
            raise

    async def chiudi(self, v: Vettura, pin: str | None) -> None:
        await self._serratura(v, True, pin)

    async def apri(self, v: Vettura, pin: str | None) -> None:
        await self._serratura(v, False, pin)

    async def trova(self, v: Vettura, pin: str | None) -> None:
        """Le frecce lampeggiano dieci secondi (mai il clacson). Il cloud vuole sapere dov'è chi lo chiede."""
        lat = v.stato.latitudine if v.stato else None
        lon = v.stato.longitudine if v.stato else None
        if lat is None or lon is None:
            posizione = await self._posizione(v.vin) or {}
            lat, lon = posizione.get("lat"), posizione.get("lon")
        if lat is None or lon is None:
            raise ErroreComando("Per far lampeggiare le frecce Audi vuole la posizione dell'auto, che ora non c'è")
        corpo = {"duration_s": 10, "mode": "flash", "userPosition": {"latitude": lat, "longitude": lon}}
        await self._comanda(v, "lampeggio", "honkandflash", corpo)

    async def clima(self, v: Vettura, acceso: bool, pin: str | None, temperatura: int = 22) -> None:
        if not acceso:
            await self._comanda(v, "spegnimento del clima", "climatisation/stop")
            return
        salvate = self._clima.get(v.vin) or {}
        attuale = salvate.get("targetTemperature_C")
        if isinstance(attuale, int | float) and int(attuale) == int(temperatura):
            # Con la temperatura di sempre non si manda niente: l'auto usa le sue impostazioni,
            # come l'app (alcune, come la Q6, rifiutano un corpo con le impostazioni).
            await self._comanda(v, "accensione del clima", "climatisation/start")
            return
        corpo = self.corpo_clima(salvate, temperatura)
        await self._comanda(v, "accensione del clima", "climatisation/start", corpo)

    @staticmethod
    def corpo_clima(salvate: dict[str, Any], temperatura: int) -> dict[str, Any]:
        """Le impostazioni dell'auto, rimandate com'erano, con la temperatura nuova (il comando le sostituisce)."""
        if not salvate:
            salvate = {
                "climatisationWithoutExternalPower": True,
                "climatizationAtUnlock": False,
                "windowHeatingEnabled": False,
                "zoneFrontLeftEnabled": False,
                "zoneFrontRightEnabled": False,
            }
        corpo = {
            k: salvate[k]
            for k in (
                "climatisationWithoutExternalPower",
                "climatizationAtUnlock",
                "windowHeatingEnabled",
                "zoneFrontLeftEnabled",
                "zoneFrontRightEnabled",
                "zoneRearLeftEnabled",
                "zoneRearRightEnabled",
                "heaterSource",
            )
            if k in salvate
        }
        corpo["targetTemperature"] = max(16, min(30, int(temperatura)))
        corpo["targetTemperatureUnit"] = "celsius"
        corpo["climatisationMode"] = str(salvate.get("climatisationMode") or "comfort")
        return corpo

    async def ricarica(self, v: Vettura, avvia: bool, pin: str | None) -> None:
        azione = "start" if avvia else "stop"
        cosa = "avvio della ricarica" if avvia else "stop della ricarica"
        try:
            await self._comanda(v, cosa, f"charging/{azione}", {})
        except ErroreComando as err:
            if not isinstance(err.__cause__, ErroreApi) or err.__cause__.http != 404:
                raise
            # Le auto più vecchie hanno la strada unica.
            await self._comanda(v, cosa, "charging/start-stop", {"action": azione})

    async def limite_ricarica(self, v: Vettura, percento: int, pin: str | None) -> None:
        if not 20 <= int(percento) <= 100:
            raise ErroreComando("Audi accetta un limite di ricarica fra il 20 e il 100 %")
        await self._comanda(v, "limite di ricarica", "charging/settings", {"targetSOC_pct": int(percento)}, "PUT")

    async def destinazione(self, v: Vettura, nome: str, lat: float, lon: float, indirizzo: str = "") -> None:
        raise ErroreComando("Il cloud Audi non riceve destinazioni da GdAAuto")


__all__ = ["ID", "NOME", "ClienteAudi", "comandi_di"]
