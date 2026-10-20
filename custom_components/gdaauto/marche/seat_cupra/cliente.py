"""Il cliente del cloud «OLA» di SEAT e CUPRA (``ola.prod.code.seat.cloud.vwgroup.com``).

Si entra come l'app My CUPRA o My SEAT, ma il login lo fa chi configura nel
suo browser (vedi ``login.py``): qui arriva solo il ``code`` del ritorno, che
col ``code_verifier`` della sfida PKCE diventa i token, da OLA
(``/authorization/api/v1/token``). Lo stesso indirizzo li rinnova col
``refresh_token``, che ruota a ogni rinnovo: per questo ogni token nuovo si
consegna alla callback ``salva``. Se il rinnovo non va, serve rientrare dal
browser (``ErroreCredenziali``): la password GdAAuto non l'ha mai avuta.

Lo stato è sparso su più indirizzi (vedi ``dati.py``): ogni lettura chiede
quelli che cambiano spesso; impostazioni, autonomie, disponibilità e storico
dei viaggi si rileggono ogni mezz'ora. Il cloud ha quello che l'auto ha
mandato: leggerlo non la sveglia (la sveglia, ``vehicle-wakeup``, non si usa).

I comandi sono quelli dell'app: chiudere e aprire vogliono l'S-PIN, che
diventa un ``SecToken``; clima, ricarica, limite, clacson e luci e la
destinazione no. Uno alla volta.

**Rischio noto.** Dall'8 giugno 2026 il cloud OLA chiede alle app un
gettone Firebase App Check, che un client non ufficiale non sa fare: molti
indirizzi possono rispondere 403. Quel 403 diventa un ``ErroreApi`` con un
messaggio chiaro, mai un ``ErroreCredenziali``: rientrare non servirebbe.

Indirizzi, campi e ordine dei passi vengono da
tillsteinbach/CarConnectivity-connector-seatcupra (MIT) e da WulfgarW/pycupra
(Apache 2.0): vedi RINGRAZIAMENTI.md. Il codice è scritto da capo,
asincrono, su ``aiohttp``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
import json
import logging
import time
from typing import Any

import aiohttp

from ...vettura import StatoVettura, Vettura
from ..errori import CodiceSbagliato, ErroreApi, ErroreComando, ErroreCredenziali, ErrorePin, ErroreRete
from . import dati
from .app import APP, OLA, PERCORSO_TOKEN, AppMarchio, lingua_cloud
from .login import rivendicazioni, scadenza_del_jwt, sub_del_jwt

_LOGGER = logging.getLogger(__name__)

ID = "seat_cupra"
NOME = "SEAT · CUPRA"

#: I comandi neutri, e le capacità del cloud che ognuno vuole (ne basta una).
COMANDI = {"chiudi", "apri", "trova", "clima", "ricarica", "limite_ricarica", "destinazione"}
_CAPACITA: dict[str, tuple[str, ...]] = {
    "chiudi": ("access", "transactionHistoryLockUnlock", "lockUnlockEnabled"),
    "apri": ("access", "transactionHistoryLockUnlock", "lockUnlockEnabled"),
    "trova": ("honkAndFlash",),
    "clima": ("climatisation",),
    "ricarica": ("charging",),
    "limite_ricarica": ("charging",),
}

#: Quello che il cloud risponde quando il blocco App Check ferma GdAAuto.
MESSAGGIO_APP_CHECK = (
    "il cloud di SEAT e CUPRA rifiuta le app non ufficiali (Firebase App Check, attivo da giugno 2026): "
    "GdAAuto non può leggere né comandare l'auto finché il blocco resta. Non è la password: rientrare non serve"
)
_SEGNI_APP_CHECK = ("app check", "appcheck", "app-check", "app_check", "firebase")

_TIMEOUT = aiohttp.ClientTimeout(total=30)
#: Il token si rinnova un po' prima che scada.
_MARGINE = timedelta(minutes=3)
#: Impostazioni, autonomie, disponibilità e viaggi cambiano piano: si rileggono ogni mezz'ora.
_LENTI_OGNI_S = 30 * 60
#: I giorni di viaggi chiesti al cloud, per il consumo medio.
_GIORNI_STORICO = 25
_OK = {200, 201, 202, 204, 207}


def comandi_di(v: Vettura) -> set[str]:
    """I comandi che il cloud dà a quell'auto; se le capacità non si conoscono, tutti."""
    capacita = v.extra.get("capacita")
    if capacita is None:
        return set(COMANDI)
    attive = set(capacita)
    comandi = {c for c, servono in _CAPACITA.items() if attive.intersection(servono)}
    if v.extra.get("obiettivo_soc") is False:
        comandi.discard("limite_ricarica")
    # La destinazione non ha una capacità sua: l'app la manda a tutte le auto connesse.
    comandi.add("destinazione")
    return comandi


def _ha(v: Vettura, capacita: str) -> bool:
    attive = v.extra.get("capacita")
    return attive is None or capacita in attive


def capacita_da_json(r: Any) -> tuple[list[str], list[str], dict[str, dict[str, Any]], str]:
    """Le capacità dell'auto: tutte, quelle attive, i loro parametri, la piattaforma (MOD3, MOD4…)."""
    tutte: list[str] = []
    attive: list[str] = []
    parametri_: dict[str, dict[str, Any]] = {}
    voci = r.get("capabilities") if isinstance(r, dict) else None
    for voce in voci if isinstance(voci, list) else []:
        if not isinstance(voce, dict) or not voce.get("id"):
            continue
        nome = str(voce["id"])
        tutte.append(nome)
        # «active» quando c'è; se no, attiva se nessuno stato la blocca.
        attiva = voce["active"] is not False if "active" in voce else not voce.get("status")
        if attiva:
            attive.append(nome)
        if isinstance(voce.get("parameters"), dict):
            parametri_[nome] = voce["parameters"]
    piattaforma = str(r.get("platform") or "") if isinstance(r, dict) else ""
    return tutte, attive, parametri_, piattaforma


def vettura_da_json(voce: dict[str, Any], app: AppMarchio, capacita: Any = None) -> Vettura | None:
    """Una voce del garage; ``None`` se il cloud dice che non si ricarica (benzina o diesel)."""
    vin = str(voce.get("vin") or "").strip()
    if not vin:
        return None
    extra: dict[str, Any] = {"marchio": app.marchio}
    if capacita is not None:
        tutte, attive, param, piattaforma = capacita_da_json(capacita)
        if tutte and "charging" not in tutte:
            return None
        extra["capacita"] = attive
        extra["piattaforma"] = piattaforma
        obiettivo = str(param.get("charging", {}).get("supportsTargetStateOfCharge", "")).lower()
        extra["obiettivo_soc"] = obiettivo != "false"
    modello_ = dati._dict(dati._dict(voce.get("specifications")).get("factoryModel"))
    modello = str(modello_.get("vehicleModel") or "").strip()
    marca = str(modello_.get("vehicleBrand") or app.nome).strip()
    anno = modello_.get("modYear")
    ruolo = str(voce.get("userRole") or "")
    return Vettura(
        vin=vin,
        marca=ID,
        modello=modello,
        nome=str(voce.get("vehicleNickname") or "").strip() or f"{marca} {modello}".strip(),
        anno=int(anno) if isinstance(anno, int | str) and str(anno).isdigit() else None,
        condivisa=bool(ruolo) and ruolo.lower() not in ("primaryuser", "primary", "owner"),
        extra=extra,
    )


def _token_da_json(r: Any) -> dict[str, Any]:
    """I token della risposta, coi nomi di OAuth anche quando il cloud scrive «accessToken»."""
    if not isinstance(r, dict):
        return {}
    nomi = {
        "accessToken": "access_token",
        "refreshToken": "refresh_token",
        "idToken": "id_token",
        "expiresIn": "expires_in",
    }
    return {nomi.get(k, k): v for k, v in r.items()}


def _json(testo: str) -> Any:
    if not testo.strip():
        return None
    try:
        return json.loads(testo)
    except ValueError:
        return None


def _motivo(testo: str) -> str:
    """Il perché di un errore, da come il cloud lo scrive (``message``, ``detail``, ``errors``…)."""
    r = _json(testo)
    if isinstance(r, dict):
        errori = r.get("errors")
        if isinstance(errori, list) and errori and isinstance(errori[0], dict):
            r = {**errori[0], **{k: v for k, v in r.items() if k != "errors"}}
        for chiave in ("message", "detail", "error_description", "title", "description", "error", "code"):
            valore = r.get(chiave)
            if isinstance(valore, str) and valore.strip():
                return valore.strip()
            if isinstance(valore, dict) and valore.get("message"):
                return str(valore["message"])
    return testo.strip()[:200]


def e_app_check(http: int, testo: str) -> bool:
    """Il 403 del blocco Firebase App Check (dal giugno 2026), riconosciuto dal messaggio."""
    return http == 403 and any(segno in testo.lower() for segno in _SEGNI_APP_CHECK)


class ClienteSeatCupra:
    def __init__(
        self,
        sessione_http: aiohttp.ClientSession,
        marchio: str = "cupra",
        sessione: dict[str, Any] | None = None,
        salva: Callable[[dict[str, Any]], None] | None = None,
        lingua: str = "en",
        ola: str | None = None,
    ) -> None:
        if marchio not in APP:
            raise ValueError(f"Marchio sconosciuto: {marchio}")
        self._http = sessione_http
        self.marchio = marchio
        self.app = APP[marchio]
        self.lingua = lingua_cloud(lingua)
        #: quello che si salva: i token, la scadenza, l'id dell'utente
        self.sessione: dict[str, Any] = dict(sessione or {})
        self._salva = salva
        self._ola = (ola or OLA).rstrip("/")
        self._comando = asyncio.Lock()
        self._accesso = asyncio.Lock()
        #: per ogni VIN: i dati lenti, e quando si sono letti
        self._lenti: dict[str, tuple[dict[str, Any], float]] = {}
        self._posizioni: dict[str, tuple[float, float]] = {}

    # --- l'accesso ------------------------------------------------------------

    @property
    def utente(self) -> str:
        return str(self.sessione.get("user_id") or "")

    def _token_valido(self) -> bool:
        try:
            scade = datetime.fromisoformat(str(self.sessione["scade"]))
        except (KeyError, ValueError):
            return False
        return bool(self.sessione.get("access_token")) and scade - _MARGINE > datetime.now(UTC)

    async def scambia_codice(self, codice: str, verifier: str) -> dict[str, Any]:
        """Il ``code`` del login nel browser, col ``code_verifier`` della sfida, diventa i token."""
        corpo = {
            "grant_type": "authorization_code",
            "client_id": self.app.client_id,
            "redirect_uri": self.app.redirect_uri,
            "code": codice,
            "code_verifier": verifier,
        }
        token = await self._token(corpo, "scambio del codice", codice=True)
        self._conserva(token, nuovo=True)
        return dict(self.sessione)

    async def accedi(self) -> None:
        async with self._accesso:
            if self._token_valido() and self.utente:
                return
            if not self.sessione.get("refresh_token") or not self.utente:
                raise ErroreCredenziali(f"{self.app.nome}: serve entrare di nuovo dal browser")
            await self._rinnova()

    async def _rinnova(self) -> None:
        corpo = {
            "grant_type": "refresh_token",
            "client_id": self.app.client_id,
            "refresh_token": str(self.sessione["refresh_token"]),
        }
        if self.app.client_secret:
            corpo["client_secret"] = self.app.client_secret
        self._conserva(await self._token(corpo, "rinnovo dei token"))

    async def _token(self, corpo: dict[str, str], cosa: str, *, codice: bool = False) -> dict[str, Any]:
        """Una domanda all'indirizzo dei token di OLA; gli errori, chiamati per quello che vogliono dire."""
        intestazioni = {
            **self._intestazioni_app(),
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            "X-Platform": "Android",
        }
        http, testo = await self._richiesta(
            "POST", f"{self._ola}{PERCORSO_TOKEN}", cosa, headers=intestazioni, data=corpo
        )
        if http == 200:
            token = _token_da_json(_json(testo))
            if not token.get("access_token"):
                raise ErroreRete(f"{cosa}: {self.app.nome} non ha dato i token")
            return token
        if http == 403:
            # Il punto dei token non dice 403 per una password: è il blocco delle app non ufficiali.
            raise ErroreApi(cosa, "app_check", MESSAGGIO_APP_CHECK, http)
        if http >= 500 or http == 429:
            raise ErroreRete(f"{cosa}: il cloud {self.app.nome} ha un guasto (HTTP {http})")
        if http in (400, 401):
            motivo = _motivo(testo)
            if codice:
                raise CodiceSbagliato(f"{self.app.nome} non accetta il codice del login ({motivo}): rifai il login")
            raise ErroreCredenziali(f"{self.app.nome} non rinnova più l'accesso ({motivo}): rientra dal browser")
        raise ErroreApi(cosa, None, _motivo(testo), http)

    def _conserva(self, token: dict[str, Any], *, nuovo: bool = False) -> None:
        accesso = str(token.get("access_token") or "")
        durata = dati._numero(token.get("expires_in"))
        if durata is None:
            exp = scadenza_del_jwt(accesso)
            durata = exp - time.time() if exp else 3600
        id_token = str(token.get("id_token") or "")
        utente = (
            str(token.get("userId") or token.get("user_id") or "")
            or ("" if nuovo else self.utente)
            or sub_del_jwt(id_token)
            or sub_del_jwt(accesso)
            or self.utente
        )
        if not utente:
            raise ErroreApi("token", None, f"{self.app.nome} non ha detto chi è l'utente", 200)
        sessione = {
            "access_token": accesso,
            # Il refresh token ruota; se il cloud non ne dà uno nuovo, vale ancora il vecchio.
            "refresh_token": str(token.get("refresh_token") or self.sessione.get("refresh_token") or ""),
            "scade": (datetime.now(UTC) + timedelta(seconds=max(60.0, durata))).isoformat(),
            "user_id": utente,
        }
        chi = rivendicazioni(id_token)
        if nome := str(chi.get("email") or chi.get("nickname") or chi.get("name") or self.sessione.get("chi") or ""):
            sessione["chi"] = nome
        self.sessione = sessione
        if self._salva:
            self._salva(dict(self.sessione))

    # --- le domande al cloud -------------------------------------------------

    async def _richiesta(self, metodo: str, url: str, cosa: str, **altro: Any) -> tuple[int, str]:
        try:
            async with self._http.request(metodo, url, timeout=_TIMEOUT, allow_redirects=False, **altro) as r:
                return r.status, await r.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ErroreRete(f"{cosa}: il cloud {self.app.nome} non risponde: {err}") from err

    def _intestazioni_app(self) -> dict[str, str]:
        """Come si presenta l'app al cloud OLA."""
        return {
            "Accept": "application/json",
            "Accept-Charset": "UTF-8",
            "Accept-Language": self.lingua,
            "User-Agent": self.app.user_agent,
            "app-version": self.app.versione,
            "app-brand": self.app.marchio,
            "app-market": "android",
            "origin": "app",
        }

    async def _api(
        self,
        metodo: str,
        percorso: str,
        cosa: str,
        *,
        corpo: Any = None,
        intestazioni: dict[str, str] | None = None,
        comando: bool = False,
    ) -> tuple[int, Any]:
        """Una domanda al cloud col token; un 401 rinnova il token e riprova una volta."""
        await self.accedi()
        http, testo = 0, ""
        for tentativo in range(2):
            h = {
                **self._intestazioni_app(),
                "Authorization": f"Bearer {self.sessione['access_token']}",
                "User-ID": self.utente,
                **(intestazioni or {}),
            }
            altro: dict[str, Any] = {"headers": h}
            if corpo is not None:
                altro["json"] = corpo
            http, testo = await self._richiesta(metodo, f"{self._ola}{percorso}", cosa, **altro)
            if http != 401 or tentativo:
                break
            async with self._accesso:
                await self._rinnova()
        if http in _OK:
            r = _json(testo)
            if testo.strip() and r is None and http != 204:
                raise ErroreRete(f"{cosa}: {self.app.nome} ha risposto una cosa che non si capisce")
            return http, r
        raise self._errore(http, testo, cosa, comando)

    def _errore(self, http: int, testo: str, cosa: str, comando: bool) -> Exception:
        # Una lettura rifiutata con 403 è il blocco App Check (pycupra lo riconosce così):
        # un comando rifiutato con 403 può essere anche l'account che non è l'utente principale.
        if e_app_check(http, testo) or (http == 403 and not comando):
            return ErroreApi(cosa, "app_check", MESSAGGIO_APP_CHECK, http)
        if http == 401:
            return ErroreCredenziali(f"{self.app.nome} non accetta più l'accesso: rientra dal browser")
        if http >= 500:
            return ErroreRete(f"{cosa}: il cloud {self.app.nome} ha un guasto (HTTP {http})")
        motivo = _motivo(testo)
        if comando:
            if http == 429:
                return ErroreComando(
                    f"{self.app.nome}: troppi comandi; il cloud ne accetta altri dopo il prossimo viaggio"
                )
            if http == 403:
                return ErroreComando(f"{self.app.nome} non lascia dare «{cosa}» a questo account ({motivo or 403})")
            return ErroreComando(f"{self.app.nome} non esegue «{cosa}»: {motivo or f'HTTP {http}'}")
        if http == 429:
            return ErroreRete(f"{cosa}: il cloud {self.app.nome} chiede di rallentare (HTTP 429)")
        return ErroreApi(cosa, None, motivo, http)

    # --- le auto ---------------------------------------------------------------

    async def vetture(self) -> list[Vettura]:
        await self.accedi()
        _http, r = await self._api("GET", f"/v2/users/{self.utente}/garage/vehicles", "elenco delle auto")
        voci = r.get("vehicles") if isinstance(r, dict) else None
        trovate: list[Vettura] = []
        for voce in voci if isinstance(voci, list) else []:
            if not isinstance(voce, dict) or not voce.get("vin"):
                continue
            capacita: Any = None
            try:
                _http, capacita = await self._api(
                    "GET", f"/v1/user/{self.utente}/vehicle/{voce['vin']}/capabilities", "capacità dell'auto"
                )
            except (ErroreApi, ErroreRete) as err:
                # Senza capacità l'auto si tiene, coi comandi tutti possibili.
                _LOGGER.debug("Capacità di %s non lette: %s", voce["vin"][-4:], err)
            if v := vettura_da_json(voce, self.app, capacita):
                trovate.append(v)
        return trovate

    # --- lo stato --------------------------------------------------------------

    async def _leggi(self, percorso: str, cosa: str) -> dict[str, Any] | None:
        _http, r = await self._api("GET", percorso, cosa)
        return r if isinstance(r, dict) else None

    async def stato(self, v: Vettura) -> StatoVettura:
        """Lo stato dell'auto, da quello che ha mandato al cloud: non la sveglia.

        Ogni pezzo è facoltativo: se uno non va, gli altri arrivano lo stesso;
        se non va nessuno, si dice perché. Le credenziali rifiutate si dicono subito.
        """
        await self.accedi()
        vin = v.vin
        letture: dict[str, str] = {
            "stato": f"/v2/vehicles/{vin}/status",
            "mycar": f"/v5/users/{self.utente}/vehicles/{vin}/mycar",
            "chilometri": f"/v1/vehicles/{vin}/mileage",
        }
        if _ha(v, "charging"):
            letture["carica"] = f"/v1/vehicles/{vin}/charging/status"
        if _ha(v, "climatisation"):
            letture["clima"] = f"/v1/vehicles/{vin}/climatisation/status"
        if _ha(v, "parkingPosition"):
            letture["parcheggio"] = f"/v1/vehicles/{vin}/parkingposition"
        pezzi: dict[str, Any] = {}
        primo_errore: Exception | None = None
        for nome, percorso in letture.items():
            try:
                pezzi[nome] = await self._leggi(percorso, nome)
            except ErroreCredenziali:
                raise
            except (ErroreApi, ErroreRete) as err:
                _LOGGER.debug("%s: %s di %s non letto: %s", self.app.nome, nome, vin[-4:], err)
                primo_errore = primo_errore or err
        if not any(pezzi.values()) and primo_errore is not None:
            raise primo_errore
        lenti = await self._dati_lenti(v)
        s = dati.interpreta(
            **pezzi,
            impostazioni_carica=lenti.get("impostazioni_carica"),
            impostazioni_clima=lenti.get("impostazioni_clima"),
            autonomie=lenti.get("autonomie"),
            disponibilita=lenti.get("disponibilita"),
        )
        for campo, valore in dati._dict(lenti.get("viaggi")).items():
            setattr(s, campo, valore)
        if s.latitudine is not None and s.longitudine is not None:
            self._posizioni[vin] = (s.latitudine, s.longitudine)
        return s

    async def _dati_lenti(self, v: Vettura, *, subito: bool = False) -> dict[str, Any]:
        """Quello che cambia piano, riletto ogni mezz'ora; ogni lettura è facoltativa."""
        vin = v.vin
        lenti, quando = self._lenti.get(vin, ({}, 0.0))
        if not subito and quando and time.monotonic() - quando < _LENTI_OGNI_S:
            return lenti
        lenti = dict(lenti)
        letture: dict[str, str] = {
            "autonomie": f"/v1/vehicles/{vin}/ranges",
            "disponibilita": f"/v1/vehicles/{vin}/remote-availability",
        }
        if _ha(v, "charging"):
            letture["impostazioni_carica"] = f"/v1/vehicles/{vin}/charging/info"
        if _ha(v, "climatisation"):
            letture["impostazioni_clima"] = f"/v2/vehicles/{vin}/climatisation/settings"
        for nome, percorso in letture.items():
            try:
                lenti[nome] = await self._leggi(percorso, nome)
            except ErroreCredenziali:
                raise
            except (ErroreApi, ErroreRete) as err:
                _LOGGER.debug("%s: %s di %s non letto: %s", self.app.nome, nome, vin[-4:], err)
        if _ha(v, "tripStatistics"):
            try:
                lenti["viaggi"] = dati.storico_viaggi(await self._leggi(self._percorso_viaggi(vin), "viaggi"), _oggi())
            except ErroreCredenziali:
                raise
            except (ErroreApi, ErroreRete) as err:
                _LOGGER.debug("%s: viaggi di %s non letti: %s", self.app.nome, vin[-4:], err)
        self._lenti[vin] = (lenti, time.monotonic())
        return lenti

    @staticmethod
    def _percorso_viaggi(vin: str) -> str:
        oggi = _oggi()
        da = oggi - timedelta(days=_GIORNI_STORICO)
        return (
            f"/v2/vehicles/{vin}/driving-data/CUSTOM?from={da.isoformat()}T00:00:00Z"
            f"&to={oggi.isoformat()}T23:59:59Z&distanceUnit=km&speedUnit=kmph"
        )

    def _rileggi_lenti(self, vin: str) -> None:
        """Dopo un comando che cambia un'impostazione, la prossima lettura la rilegge."""
        if vin in self._lenti:
            self._lenti[vin] = (self._lenti[vin][0], 0.0)

    # --- i comandi ---------------------------------------------------------------

    async def _comanda(self, metodo: str, percorso: str, cosa: str, corpo: Any = None, **altro: Any) -> Any:
        async with self._comando:
            _http, r = await self._api(metodo, percorso, cosa, corpo=corpo, comando=True, **altro)
            return r

    async def _sec_token(self, pin: str | None) -> str:
        """L'S-PIN diventa un ``SecToken``, che vale per un comando."""
        if not pin:
            raise ErrorePin(f"Per chiudere e aprire una {self.app.nome} serve l'S-PIN dell'app, nelle opzioni")
        try:
            # Come un comando: un 403 qui può essere l'S-PIN sbagliato o bloccato, non solo App Check.
            _http, r = await self._api(
                "POST", f"/v2/users/{self.utente}/spin/verify", "S-PIN", corpo={"spin": pin}, comando=True
            )
        except ErroreComando as err:
            raise ErrorePin(f"{self.app.nome} non accetta l'S-PIN: {err}") from err
        token = r.get("securityToken") if isinstance(r, dict) else None
        if not token:
            raise ErrorePin(f"{self.app.nome} non ha confermato l'S-PIN")
        return str(token)

    async def verifica_pin(self, pin: str) -> None:
        """Prova l'S-PIN (``ErrorePin`` se il cloud non lo accetta), senza dare comandi."""
        async with self._comando:
            await self._sec_token(pin)

    async def _serratura(self, v: Vettura, pin: str | None, azione: str, cosa: str) -> None:
        async with self._comando:
            sec = await self._sec_token(pin)
            await self._api(
                "POST",
                f"/v1/vehicles/{v.vin}/access/{azione}",
                cosa,
                corpo={},
                intestazioni={"SecToken": sec},
                comando=True,
            )

    async def chiudi(self, v: Vettura, pin: str | None) -> None:
        await self._serratura(v, pin, "lock", "chiudi")

    async def apri(self, v: Vettura, pin: str | None) -> None:
        await self._serratura(v, pin, "unlock", "apri")

    async def trova(self, v: Vettura, _pin: str | None = None) -> None:
        """Clacson e luci: il cloud vuole sapere dov'è chi lo chiede; si dà la posizione dell'auto."""
        posizione = self._posizioni.get(v.vin)
        if posizione is None and v.stato and v.stato.latitudine is not None and v.stato.longitudine is not None:
            posizione = (v.stato.latitudine, v.stato.longitudine)
        if posizione is None:
            raise ErroreComando(f"{self.app.nome}: clacson e luci vogliono la posizione dell'auto, che non è nota")
        lat, lon = (int(x * 10000) / 10000 for x in posizione)
        corpo = {"mode": "honkandflash", "userPosition": {"latitude": lat, "longitude": lon}}
        await self._comanda("POST", f"/v1/vehicles/{v.vin}/honk-and-flash", "clacson e luci", corpo)

    async def clima(self, v: Vettura, acceso: bool, _pin: str | None = None, temperatura: int = 22) -> None:
        if acceso:
            gradi = round(min(max(float(temperatura), 16.0), 30.0) * 2) / 2
            corpo = {"targetTemperature": gradi, "targetTemperatureUnit": "celsius"}
            await self._comanda("POST", f"/v2/vehicles/{v.vin}/climatisation/start", "accendi il clima", corpo)
        else:
            await self._comanda("POST", f"/vehicles/{v.vin}/climatisation/requests/stop", "spegni il clima")
        self._rileggi_lenti(v.vin)

    async def ricarica(self, v: Vettura, avvia: bool, _pin: str | None = None) -> None:
        azione, cosa = ("start", "avvia la ricarica") if avvia else ("stop", "ferma la ricarica")
        await self._comanda("POST", f"/vehicles/{v.vin}/charging/requests/{azione}", cosa)

    async def limite_ricarica(self, v: Vettura, percento: int, _pin: str | None = None) -> None:
        if not 1 <= int(percento) <= 100:
            raise ErroreComando(f"Il limite di ricarica va da 1 a 100 %, non {percento}")
        if v.extra.get("piattaforma") == "MOD4":
            # Le auto MOD4 (Born, Tavascan…) cambiano il limite con un'azione a sé.
            corpo: dict[str, Any] = {"targetSocPercentage": int(percento)}
            percorso = f"/v1/vehicles/{v.vin}/charging/actions/update-settings"
        else:
            # Le altre vogliono tutte le impostazioni di ricarica, col limite nuovo.
            impostazioni = dati._dict(
                dati._dict((await self._dati_lenti(v)).get("impostazioni_carica")).get("settings")
            )
            corpo = {**impostazioni, "targetSoc": int(percento)}
            percorso = f"/v1/vehicles/{v.vin}/charging/settings"
        await self._comanda("POST", percorso, "limite di ricarica", corpo)
        self._rileggi_lenti(v.vin)

    async def destinazione(self, v: Vettura, nome: str, lat: float, lon: float, indirizzo: str = "") -> None:
        meta: dict[str, Any] = {
            "destinationName": (nome or indirizzo or "GdAAuto").strip(),
            "poiProvider": "google",
            "geoCoordinate": {"latitude": float(lat), "longitude": float(lon)},
        }
        await self._comanda("PUT", f"/v1/users/vehicles/{v.vin}/destination", "manda la destinazione", [meta])


def _oggi() -> date:
    return datetime.now(UTC).date()
