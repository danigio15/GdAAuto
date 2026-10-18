"""Il filo col cloud InControl di Jaguar e Land Rover (oggi «JLR Remote»).

Il cloud è uno solo per i due marchi, e ha tre porte:

- ``ifas`` dà i token: email e password (o il token di rinnovo) in cambio di
  un ``access_token`` che dura un giorno, di un ``authorization_token`` e di
  un ``refresh_token``;
- ``ifop`` registra il «telefono»: ogni token nuovo va legato a un
  identificativo del dispositivo (un UUID scelto da noi, fisso per account),
  che poi viaggia in ogni richiesta;
- ``if9`` è tutto il resto: chi è l'utente, le sue auto, gli attributi di
  ognuna, lo stato, la posizione, i viaggi e i comandi.

Lo stato si legge da ``status``: è l'ultima fotografia che l'auto ha mandato
al cloud, e leggerla non sveglia l'auto (lo farebbe solo la richiesta
``healthstatus``, che GdAAuto non usa).

I comandi sono «servizi»: prima ci si fa dare un token del servizio
(``authenticate``), col PIN personale dell'app per chiudere e aprire, con le
ultime quattro cifre del telaio per gli altri; poi si manda il comando, e il
cloud risponde con un numero di pratica che si segue finché l'auto non dice
com'è andata. Un comando alla volta.

Indirizzi, intestazioni, identificativi dell'app e nomi dei servizi vengono da
ardevd/jlrpy e msp1974/homeassistant-jlrincontrol (licenza MIT): vedi
RINGRAZIAMENTI.md. Il codice è scritto da capo per aiohttp.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import logging
import time
from typing import Any
from urllib.parse import quote
import uuid

import aiohttp

from ...vettura import StatoVettura, Vettura
from ..errori import ErroreApi, ErroreComando, ErroreCredenziali, ErrorePin, ErroreRete
from .dati import interpreta, km_dei_viaggi

_LOGGER = logging.getLogger(__name__)

ID = "jlr"
NOME = "Jaguar Land Rover"

#: Il nome sullo schermo di ogni marchio del catalogo che punta qui.
MARCHI = {"jaguar": "Jaguar", "land_rover": "Land Rover"}


@dataclass(frozen=True)
class Indirizzi:
    ifas: str
    ifop: str
    if9: str


REGIONI: dict[str, Indirizzi] = {
    "mondo": Indirizzi(
        ifas="https://ifas.prod-row.jlrmotor.com/ifas/jlr",
        ifop="https://ifop.prod-row.jlrmotor.com/ifop/jlr",
        if9="https://if9.prod-row.jlrmotor.com/if9/jlr",
    ),
    "cina": Indirizzi(
        ifas="https://ifas.prod-chn.jlrmotor.com/ifas/jlr",
        ifop="https://ifop.prod-chn.jlrmotor.com/ifop/jlr",
        if9="https://ifoa.prod-chn.jlrmotor.com/if9/jlr",
    ),
}

# --- l'app, come la descrive jlrpy (MIT) ---------------------------------------------

#: Le credenziali del client OAuth dell'app («as:aspass» in base64).
AUTORIZZAZIONE_TOKEN = "Basic YXM6YXNwYXNz"
ID_APP = "ICR_JAGUAR_ANDROID"
SEGRETO_APP = "7bf6f544-1926-4714-8066-ceceb40d538d"
#: Il programma telematico e l'agente che jlrpy dichiara: sono quelli che il cloud ha sempre accettato.
PROGRAMMA = "jlrpy"
AGENTE = "jlrpy"
#: La durata che si dichiara registrando il dispositivo, in secondi (come l'app).
DURATA_DISPOSITIVO = "86400"

_UTENTE = "application/vnd.wirelesscar.ngtp.if9.User-v3+json"
_ATTRIBUTI = "application/vnd.ngtp.org.VehicleAttributes-v8+json"
_STATO = "application/vnd.ngtp.org.if9.healthstatus-v4+json"
_VIAGGI = "application/vnd.ngtp.org.triplist-v2+json"
_ESITO_V4 = "application/vnd.wirelesscar.ngtp.if9.ServiceStatus-v4+json"
_ESITO_V5 = "application/vnd.wirelesscar.ngtp.if9.ServiceStatus-v5+json"
_AVVIO = "application/vnd.wirelesscar.ngtp.if9.StartServiceConfiguration-v3+json"
_AVVIO_UTF8 = _AVVIO + "; charset=utf-8"
_PHEV = "application/vnd.wirelesscar.ngtp.if9.PhevService-v1+json; charset=utf-8"
_AUTENTICA = "application/vnd.wirelesscar.ngtp.if9.AuthenticateRequest-v2+json; charset=utf-8"

#: I servizi dell'auto (``availableServices``) → i comandi di GdAAuto.
SERVIZI = {
    "RDL": {"chiudi"},
    "RDU": {"apri"},
    "HBLF": {"trova"},
    "ECC": {"clima"},
    "CP": {"ricarica", "limite_ricarica"},
}
#: I servizi che vogliono il PIN personale; gli altri si aprono con le ultime quattro cifre del telaio.
CON_PIN = {"RDL", "RDU"}

#: Gli esiti di un servizio: ancora in corso, andato bene.
_IN_CORSO = {"STARTED", "RUNNING", "QUEUED", "PENDING"}
_RIUSCITO = {"SUCCESSFUL", "MESSAGEDELIVERED", "COMPLETED"}

_TIMEOUT = aiohttp.ClientTimeout(total=30)
#: Il token si rinnova cinque minuti prima che scada.
_MARGINE_S = 300
#: Viaggi e chilometri della settimana: ogni mezz'ora basta.
_STORICO_OGNI_S = 30 * 60


def comandi_di(v: Vettura) -> set[str]:
    """I comandi dei servizi che l'auto ha e che l'abbonamento tiene accesi."""
    fatti: set[str] = set()
    for servizio in v.extra.get("servizi") or ():
        fatti |= SERVIZI.get(str(servizio), set())
    if v.extra.get("carburante") not in (None, "Electric") and v.extra.get("trazione") != "PHEV":
        # Un'auto solo a benzina o gasolio non ha batteria da caricare.
        fatti -= {"ricarica", "limite_ricarica"}
    return fatti


def _temperatura_ecc(gradi: float) -> str:
    """La preclimatizzazione vuole i decimi di grado, da 15,5 a 28,5 °C."""
    return str(min(285, max(155, round(gradi * 10))))


class ClienteJLR:
    def __init__(
        self,
        sessione_http: aiohttp.ClientSession,
        email: str,
        password: str = "",
        regione: str = "mondo",
        dispositivo: str | None = None,
        token: dict[str, Any] | None = None,
        salva: Callable[[dict[str, Any]], None] | None = None,
        *,
        indirizzi: Indirizzi | None = None,
        attesa_comando: float = 90.0,
        passo_comando: float = 3.0,
    ) -> None:
        self._http = sessione_http
        self.email = email.strip()
        self._password = password
        self.indirizzi = indirizzi or REGIONI.get(regione) or REGIONI["mondo"]
        #: L'identificativo del «telefono», fisso per account: ogni token nuovo si lega a lui.
        self.dispositivo = dispositivo or str(uuid.uuid4())
        self.token: dict[str, Any] = dict(token or {})
        self._salva = salva
        self._attesa_comando = attesa_comando
        self._passo_comando = passo_comando
        self._serratura = asyncio.Lock()
        self._un_comando = asyncio.Lock()
        self._storici: dict[str, tuple[dict[str, Any], float]] = {}

    @property
    def id_utente(self) -> str:
        return str(self.token.get("id_utente") or "")

    # --- la rete ---------------------------------------------------------------------

    async def _chiama(
        self,
        cosa: str,
        metodo: str,
        url: str,
        intestazioni: dict[str, str],
        corpo: Any = None,
        parametri: dict[str, str] | None = None,
    ) -> tuple[int, str]:
        try:
            async with self._http.request(
                metodo,
                url,
                headers=intestazioni,
                data=None if corpo is None else json.dumps(corpo),
                params=parametri,
                timeout=_TIMEOUT,
            ) as r:
                return r.status, await r.text()
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise ErroreRete(f"{cosa}: il cloud JLR non risponde: {err}") from err

    @staticmethod
    def _json(testo: str) -> Any:
        if not testo.strip():
            return {}
        try:
            return json.loads(testo)
        except ValueError:
            return None

    @classmethod
    def _messaggio(cls, testo: str) -> str:
        corpo = cls._json(testo)
        if isinstance(corpo, dict):
            parti = [
                str(corpo.get(k))
                for k in ("error", "error_description", "errorLabel", "errorDescription", "message")
                if corpo.get(k)
            ]
            if parti:
                return " · ".join(parti)
        return " ".join(testo.split())[:200]

    def _intestazioni(self, **altre: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token.get('access_token', '')}",
            "X-Device-Id": self.dispositivo,
            "x-telematicsprogramtype": PROGRAMMA,
            "x-App-Id": ID_APP,
            "x-App-Secret": SEGRETO_APP,
            "Content-Type": "application/json",
            **altre,
        }

    async def _api(
        self,
        cosa: str,
        metodo: str,
        url: str,
        intestazioni: dict[str, str] | None = None,
        corpo: Any = None,
        parametri: dict[str, str] | None = None,
        rifiuto: Callable[[str], Exception] = ErroreCredenziali,
    ) -> Any:
        """Una richiesta a ``if9`` col token buono; a un 401 si rinnova il token e si riprova una volta.

        Se anche il token nuovo è rifiutato, l'errore è ``rifiuto`` (le credenziali,
        o il PIN quando si apre un servizio che lo vuole).
        """
        await self._token_valido()
        for tentativo in (1, 2):
            stato, testo = await self._chiama(
                cosa, metodo, url, self._intestazioni(**(intestazioni or {})), corpo, parametri
            )
            if stato == 401 and tentativo == 1:
                _LOGGER.debug("%s: il token non va più, se ne chiede un altro", cosa)
                await self._rinnova(forza=True)
                continue
            if stato >= 500 or stato == 408:
                raise ErroreRete(f"{cosa}: il cloud JLR ha un guasto (HTTP {stato})")
            if stato == 401:
                raise rifiuto(f"{cosa}: il cloud JLR rifiuta l'accesso ({self._messaggio(testo)})")
            if stato >= 400:
                raise ErroreApi(cosa, stato, self._messaggio(testo), stato)
            corpo_r = self._json(testo)
            if corpo_r is None:
                raise ErroreRete(f"{cosa}: il cloud JLR ha risposto una cosa che non si legge")
            return corpo_r
        raise ErroreRete(f"{cosa}: il cloud JLR non risponde")  # pragma: no cover

    # --- i token -----------------------------------------------------------------------

    async def _token_valido(self) -> None:
        if self.token.get("access_token") and float(self.token.get("scade") or 0) - _MARGINE_S > time.time():
            return
        await self._rinnova()

    async def _rinnova(self, forza: bool = False) -> None:
        """Un token nuovo: col token di rinnovo se c'è, se no (o se è scaduto) con la password."""
        vecchio = self.token.get("access_token")
        async with self._serratura:
            if self.token.get("access_token") != vecchio:
                return  # l'ha già rinnovato qualcun altro mentre si aspettava
            if (
                not forza
                and self.token.get("access_token")
                and float(self.token.get("scade") or 0) - _MARGINE_S > (time.time())
            ):
                return
            nuovo: dict[str, Any] | None = None
            if self.token.get("refresh_token"):
                try:
                    nuovo = await self._chiedi_token(
                        {"grant_type": "refresh_token", "refresh_token": self.token["refresh_token"]}
                    )
                except ErroreCredenziali as err:
                    _LOGGER.debug("Il token di rinnovo JLR non va più (%s): si rientra con la password", err)
            if nuovo is None:
                if not self.email or not self._password:
                    raise ErroreCredenziali("Il token JLR è scaduto e manca la password per rientrare")
                nuovo = await self._chiedi_token(
                    {"grant_type": "password", "username": self.email, "password": self._password}
                )
            self.token = {**nuovo, "id_utente": self.token.get("id_utente")}
            await self._registra_dispositivo()
            if not self.id_utente:
                await self._leggi_utente()
            self._salvalo()

    async def _chiedi_token(self, modulo: dict[str, str]) -> dict[str, Any]:
        cosa = "login JLR" if modulo.get("grant_type") == "password" else "rinnovo del token JLR"
        stato, testo = await self._chiama(
            cosa,
            "POST",
            f"{self.indirizzi.ifas}/tokens/tokensSSO",
            {
                "Authorization": AUTORIZZAZIONE_TOKEN,
                "Content-Type": "application/json",
                "X-Device-Id": self.dispositivo,
                "user-agent": AGENTE,
                "Connection": "close",
            },
            modulo,
        )
        if stato >= 500 or stato == 408:
            raise ErroreRete(f"{cosa}: il cloud JLR ha un guasto (HTTP {stato})")
        if stato == 429:
            raise ErroreApi(cosa, stato, "troppe richieste: il cloud JLR chiede di aspettare", stato)
        corpo = self._json(testo)
        if stato == 403 and corpo is None:
            # Una pagina HTML e non un errore OAuth: è il muro davanti al cloud, non la password.
            raise ErroreApi(cosa, stato, "il cloud JLR blocca questo client: " + self._messaggio(testo), stato)
        if 400 <= stato < 500:
            raise ErroreCredenziali(f"{cosa}: email o password rifiutate ({self._messaggio(testo)})")
        if stato >= 300 or not isinstance(corpo, dict) or not corpo.get("access_token"):
            raise ErroreRete(f"{cosa}: risposta che non si capisce (HTTP {stato})")
        try:
            durata = float(corpo.get("expires_in") or 3600)
        except (TypeError, ValueError):
            durata = 3600.0
        return {
            "access_token": str(corpo["access_token"]),
            "authorization_token": str(corpo.get("authorization_token") or ""),
            "refresh_token": str(corpo.get("refresh_token") or ""),
            "scade": time.time() + durata,
        }

    async def _registra_dispositivo(self) -> None:
        """Il token nuovo si lega al nostro dispositivo, se no ``if9`` lo rifiuta."""
        stato, testo = await self._chiama(
            "registrazione del dispositivo JLR",
            "POST",
            f"{self.indirizzi.ifop}/users/{quote(self.email, safe='@')}/clients",
            self._intestazioni(),
            {
                "access_token": self.token["access_token"],
                "authorization_token": self.token.get("authorization_token", ""),
                "expires_in": DURATA_DISPOSITIVO,
                "deviceID": self.dispositivo,
            },
        )
        if stato >= 500 or stato == 408:
            raise ErroreRete(f"registrazione del dispositivo JLR: guasto (HTTP {stato})")
        if stato in (401, 403):
            raise ErroreCredenziali(f"Il cloud JLR non registra il dispositivo ({self._messaggio(testo)})")
        if stato >= 400:
            raise ErroreApi("registrazione del dispositivo JLR", stato, self._messaggio(testo), stato)

    async def _leggi_utente(self) -> None:
        stato, testo = await self._chiama(
            "utente JLR",
            "GET",
            f"{self.indirizzi.if9}/users",
            self._intestazioni(Accept=_UTENTE),
            parametri={"loginName": self.email},
        )
        if stato >= 500 or stato == 408:
            raise ErroreRete(f"utente JLR: guasto (HTTP {stato})")
        if stato in (401, 403):
            raise ErroreCredenziali(f"Il cloud JLR non dice chi è l'utente ({self._messaggio(testo)})")
        corpo = self._json(testo)
        if stato >= 400 or not isinstance(corpo, dict) or not corpo.get("userId"):
            raise ErroreApi("utente JLR", stato, self._messaggio(testo), stato)
        self.token["id_utente"] = str(corpo["userId"])

    def _salvalo(self) -> None:
        if self._salva is not None:
            self._salva(dict(self.token))

    # --- quello che vuole GdAAuto -----------------------------------------------------------

    async def accedi(self) -> None:
        await self._token_valido()
        if not self.id_utente:
            async with self._serratura:
                if not self.id_utente:
                    await self._leggi_utente()
                    self._salvalo()

    async def vetture(self) -> list[Vettura]:
        await self.accedi()
        base = f"{self.indirizzi.if9}/users/{self.id_utente}/vehicles"
        elenco = await self._api("elenco delle auto", "GET", base, parametri={"primaryOnly": "true"})
        auto = (elenco or {}).get("vehicles") if isinstance(elenco, dict) else None
        if not auto:
            # Un account invitato all'auto (utente secondario) non è «primario» di niente.
            elenco = await self._api("elenco delle auto", "GET", base, parametri={"primaryOnly": "false"})
            auto = (elenco or {}).get("vehicles") if isinstance(elenco, dict) else None
        vetture: list[Vettura] = []
        for voce in auto or []:
            if not isinstance(voce, dict) or not voce.get("vin"):
                continue
            vin = str(voce["vin"])
            try:
                attributi = await self._api(
                    "attributi dell'auto",
                    "GET",
                    f"{self.indirizzi.if9}/vehicles/{vin}/attributes",
                    {"Accept": _ATTRIBUTI},
                )
            except ErroreApi as err:
                _LOGGER.debug("Attributi della %s non letti: %s", vin[-4:], err)
                attributi = {}
            vetture.append(_vettura(vin, voce, attributi if isinstance(attributi, dict) else {}))
        return vetture

    async def stato(self, vettura: Vettura) -> StatoVettura:
        base = f"{self.indirizzi.if9}/vehicles/{vettura.vin}"
        status = await self._api(
            "stato dell'auto", "GET", f"{base}/status", {"Accept": _STATO}, parametri={"includeInactive": "true"}
        )
        posizione: Any = None
        try:
            posizione = await self._api("posizione dell'auto", "GET", f"{base}/position")
        except (ErroreApi, ErroreRete) as err:
            # Senza l'abbonamento giusto, o con la privacy accesa, la posizione non c'è.
            _LOGGER.debug("Posizione della %s non letta: %s", vettura.titolo, err)
        stato = interpreta(
            status if isinstance(status, dict) else {},
            posizione if isinstance(posizione, dict) else None,
            elettrica=vettura.extra.get("carburante") == "Electric",
        )
        for campo, valore in (await self._storico(vettura)).items():
            setattr(stato, campo, valore)
        return stato

    async def _storico(self, vettura: Vettura) -> dict[str, Any]:
        """I km degli ultimi sette giorni, dai viaggi registrati; riletti ogni mezz'ora, e facoltativi."""
        campi, quando = self._storici.get(vettura.vin, ({}, 0.0))
        if quando and time.monotonic() - quando < _STORICO_OGNI_S:
            return campi
        campi = dict(campi)
        dal = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=6)
        try:
            viaggi = await self._api(
                "viaggi dell'auto",
                "GET",
                f"{self.indirizzi.if9}/vehicles/{vettura.vin}/trips",
                {"Accept": _VIAGGI},
                parametri={"startDate": dal.strftime("%Y-%m-%dT%H:%M:%S+0000"), "count": "1000"},
            )
            km = km_dei_viaggi(viaggi if isinstance(viaggi, dict) else None, dal)
            if km is not None:
                campi["km_7_giorni"] = km
        except (ErroreApi, ErroreRete) as err:
            # Con la registrazione dei viaggi spenta non ci sono: lo stato arriva lo stesso.
            _LOGGER.debug("Viaggi della %s non letti: %s", vettura.titolo, err)
        self._storici[vettura.vin] = (campi, time.monotonic())
        return campi

    # --- i comandi --------------------------------------------------------------------------

    def _vuole(self, v: Vettura, servizio: str) -> None:
        servizi = v.extra.get("servizi")
        if servizi is not None and servizio not in servizi:
            raise ErroreComando(f"{v.titolo}: il servizio {servizio} non è attivo su questa auto")

    async def _autentica(self, v: Vettura, servizio: str, pin: str | None) -> dict[str, Any]:
        """Il token del servizio: col PIN per chiudere e aprire, con le ultime cifre del telaio per il resto."""
        if servizio in CON_PIN:
            if not (pin or "").strip():
                raise ErrorePin("Per chiudere e aprire serve il PIN personale dell'app InControl, nelle opzioni")
            segreto = str(pin).strip()
            rifiuto: Callable[[str], Exception] = ErrorePin
        else:
            segreto = v.vin[-4:]
            rifiuto = ErroreComando
        try:
            risposta = await self._api(
                f"autorizzazione del servizio {servizio}",
                "POST",
                f"{self.indirizzi.if9}/vehicles/{v.vin}/users/{self.id_utente}/authenticate",
                {"Content-Type": _AUTENTICA},
                {"serviceName": servizio, "pin": segreto},
                rifiuto=rifiuto,
            )
        except ErroreApi as err:
            if servizio in CON_PIN and err.http in (400, 403):
                raise ErrorePin(f"Il cloud JLR non accetta il PIN ({err.messaggio})") from err
            raise ErroreComando(f"{v.titolo}: il servizio {servizio} non si apre ({err.messaggio})") from err
        if not isinstance(risposta, dict) or not risposta.get("token"):
            raise ErroreComando(f"{v.titolo}: il cloud JLR non ha dato il token del servizio {servizio}")
        return risposta

    async def _comanda(
        self,
        v: Vettura,
        servizio: str,
        percorso: str,
        pin: str | None = None,
        intestazioni: dict[str, str] | None = None,
        aggiunte: dict[str, Any] | None = None,
    ) -> None:
        """Apre il servizio, manda il comando, e segue la pratica finché l'auto non risponde."""
        self._vuole(v, servizio)
        async with self._un_comando:
            await self.accedi()
            corpo = {**await self._autentica(v, servizio, pin), **(aggiunte or {})}
            try:
                esito = await self._api(
                    f"comando {servizio}",
                    "POST",
                    f"{self.indirizzi.if9}/vehicles/{v.vin}/{percorso}",
                    intestazioni,
                    corpo,
                    rifiuto=ErroreComando,
                )
            except ErroreApi as err:
                raise ErroreComando(f"{v.titolo}: il cloud JLR rifiuta il comando ({err.messaggio})") from err
            await self._segui(v, servizio, esito if isinstance(esito, dict) else {})

    async def _segui(self, v: Vettura, servizio: str, esito: dict[str, Any]) -> None:
        if esito.get("errorLabel") or esito.get("error"):
            raise ErroreComando(f"{v.titolo}: comando rifiutato ({self._messaggio(json.dumps(esito))})")
        pratica = esito.get("customerServiceId")
        if not pratica:
            return  # il cloud non dà una pratica da seguire: il comando è partito
        fine = time.monotonic() + self._attesa_comando
        while True:
            stato = str(esito.get("status") or "").upper()
            if stato in _RIUSCITO:
                return
            if stato and stato not in _IN_CORSO:
                motivo = esito.get("failureReason") or esito.get("failureDescription") or esito.get("status")
                raise ErroreComando(f"{v.titolo}: l'auto non ha eseguito il comando ({motivo})")
            if time.monotonic() >= fine:
                _LOGGER.info("%s: il comando %s è ancora in corso, si smette di aspettare", v.titolo, servizio)
                return
            await asyncio.sleep(self._passo_comando)
            try:
                letto = await self._api(
                    f"esito del comando {servizio}",
                    "GET",
                    f"{self.indirizzi.if9}/vehicles/{v.vin}/services/{pratica}",
                    {"Accept": _ESITO_V4},
                )
            except (ErroreApi, ErroreRete) as err:
                # Il comando è partito: se l'esito non si legge, lo dirà la prossima lettura dello stato.
                _LOGGER.debug("Esito del comando %s non letto: %s", servizio, err)
                return
            esito = letto if isinstance(letto, dict) else {}

    async def chiudi(self, v: Vettura, pin: str | None) -> None:
        await self._comanda(v, "RDL", "lock", pin, {"Content-Type": _AVVIO})

    async def apri(self, v: Vettura, pin: str | None) -> None:
        await self._comanda(v, "RDU", "unlock", pin, {"Content-Type": _AVVIO})

    async def trova(self, v: Vettura, pin: str | None) -> None:
        await self._comanda(v, "HBLF", "honkBlink", None, {"Accept": _ESITO_V4, "Content-Type": _AVVIO_UTF8})

    async def clima(self, v: Vettura, acceso: bool, pin: str | None, temperatura: int = 22) -> None:
        parametri = [{"key": "PRECONDITIONING", "value": "START" if acceso else "STOP"}]
        if acceso:
            parametri.append({"key": "TARGET_TEMPERATURE_CELSIUS", "value": _temperatura_ecc(temperatura)})
        await self._comanda(
            v,
            "ECC",
            "preconditioning",
            None,
            {"Accept": _ESITO_V5, "Content-Type": _PHEV},
            {"serviceParameters": parametri},
        )

    async def ricarica(self, v: Vettura, avvia: bool, pin: str | None) -> None:
        parametri = [{"key": "CHARGE_NOW_SETTING", "value": "FORCE_ON" if avvia else "FORCE_OFF"}]
        await self._profilo_ricarica(v, parametri)

    async def limite_ricarica(self, v: Vettura, percento: int, pin: str | None) -> None:
        limite = max(1, min(100, int(percento)))
        await self._profilo_ricarica(v, [{"key": "SET_PERMANENT_MAX_SOC", "value": limite}])

    async def _profilo_ricarica(self, v: Vettura, parametri: list[dict[str, Any]]) -> None:
        await self._comanda(
            v,
            "CP",
            "chargeProfile",
            None,
            {"Accept": _ESITO_V5, "Content-Type": _PHEV},
            {"serviceParameters": parametri},
        )

    async def destinazione(self, v: Vettura, nome: str, lat: float, lon: float, indirizzo: str = "") -> None:
        raise ErroreComando("Il cloud JLR non manda destinazioni al navigatore")


def _vettura(vin: str, voce: dict[str, Any], attributi: dict[str, Any]) -> Vettura:
    marchio = str(attributi.get("vehicleBrand") or "").strip()
    modello = str(attributi.get("vehicleType") or attributi.get("vehicleTypeCode") or "").strip() or "InControl"
    soprannome = str(attributi.get("nickname") or "").strip()
    if soprannome:
        nome = soprannome
    elif marchio and not modello.lower().startswith(marchio.lower()):
        nome = f"{marchio} {modello}"
    else:
        nome = modello
    servizi = [
        str(s.get("serviceType"))
        for s in attributi.get("availableServices") or []
        if isinstance(s, dict) and s.get("serviceType") and s.get("vehicleCapable") and s.get("serviceEnabled")
    ]
    anno = attributi.get("modelYear")
    try:
        anno_int = int(anno) if anno else None
    except (TypeError, ValueError):
        anno_int = None
    return Vettura(
        vin=vin,
        marca=ID,
        modello=modello,
        nome=nome,
        anno=anno_int,
        targa=str(attributi.get("registrationNumber") or ""),
        colore=str(attributi.get("exteriorColorName") or ""),
        condivisa=str(voce.get("role") or "Primary").lower() != "primary",
        extra={
            "marchio": marchio,
            "carburante": attributi.get("fuelType"),
            "trazione": attributi.get("powerTrainType"),
            # Senza attributi non si sa che servizi ci sono: nessun comando, piuttosto che comandi a vuoto.
            "servizi": servizi,
        },
    )


__all__ = ["ID", "MARCHI", "NOME", "REGIONI", "ClienteJLR", "Indirizzi", "comandi_di"]
