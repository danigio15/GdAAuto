"""Il filo col cloud Leapmotor: entra, legge, comanda.

È ``aiohttp`` da capo a piedi, con il TLS reciproco (mTLS) che il cloud
pretende: prima col certificato dell'app, per il login, poi con quello
dell'account che il login consegna. Niente ``curl`` lanciato da fuori,
niente thread.

Non sa niente di Home Assistant: gli si passa una sessione, una cartella dove
tenere i certificati (il TLS di Python li vuole su disco) e le credenziali, e
risponde con ``Vettura`` e ``StatoVettura``.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import ssl
import time
from typing import Any
from urllib.parse import quote

import aiohttp
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12

from ...vettura import StatoVettura, Vettura
from . import cripto
from .errori import ErroreApi, ErroreCertificato, ErroreCredenziali, ErrorePin, ErroreRete
from .segnali import interpreta

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://appgateway.leapmotor-international.de"
_PREFISSO = "/carownerservice/oversea"
#: Il consumo medio cambia piano: basta rileggerlo ogni mezz'ora.
_CONSUMO_OGNI_S = 1800
_TIMEOUT = aiohttp.ClientTimeout(total=30)

# Il modello nel segmento dell'indirizzo dello stato. Le B usano quello della
# C10; un modello che non conosciamo prova col suo nome e, se il cloud dice
# 404, ripiega sulla C10 e se lo ricorda.
_PERCORSI_STATO = {"b05": "c10", "b10": "c10", "b11": "c10"}

# I modelli che rispondono con i campi col nome e hanno meno comandi.
MODELLI_SEMPLICI = {"T03", "S01"}

# I comandi (cmdId) che usiamo.
CMD_SERRATURA = "110"
CMD_TROVA = "120"
CMD_CLIMA = "170"
CMD_DESTINAZIONE = "180"
CMD_PIANO_RICARICA = "190"
CMD_RICARICA = "193"

CODICE_LOGIN_RIFIUTATO = "302010108"


ID = "leapmotor"
NOME = "Leapmotor"

# I comandi che questa marca sa dare, con il nome neutro di GdAAuto.
COMANDI = {
    "chiudi": CMD_SERRATURA,
    "trova": CMD_TROVA,
    "clima": CMD_CLIMA,
    "ricarica": CMD_RICARICA,
    "limite_ricarica": CMD_PIANO_RICARICA,
    "destinazione": CMD_DESTINAZIONE,
}


def vettura_da_json(voce: dict[str, Any], condivisa: bool) -> Vettura:
    """L'anagrafica dall'elenco dell'account. Quello che serve solo a
    Leapmotor (carId, diritti, carType) sta in ``extra``."""
    diritti = voce.get("rightList")
    if isinstance(diritti, str):
        elenco = {d.strip() for d in diritti.split(",") if d.strip()}
    elif isinstance(diritti, list):
        elenco = {str(d) for d in diritti}
    else:
        elenco = set()
    anno = voce.get("year")
    car_type = str(voce.get("carType") or "C10").upper()
    return Vettura(
        vin=str(voce["vin"]),
        marca=ID,
        modello=car_type,
        nome=str(voce.get("vinNickname") or voce.get("nickName") or ""),
        anno=int(anno) if isinstance(anno, int | str) and str(anno).isdigit() else None,
        targa=str(voce.get("plateNumber") or ""),
        colore=str(voce.get("outColor") or ""),
        condivisa=condivisa,
        extra={
            "car_id": str(voce["carId"]) if voce.get("carId") is not None else None,
            "diritti": sorted(elenco),
            "car_type": car_type,
        },
    )


def _percorso_stato(v: Vettura) -> str:
    m = v.modello.lower()
    return _PERCORSI_STATO.get(m, m or "c10")


def _semplice(v: Vettura) -> bool:
    return v.modello.upper() in MODELLI_SEMPLICI


def comandi_di(v: Vettura) -> set[str]:
    """I comandi neutri che questa auto accetta, dai diritti dell'account.
    Senza l'elenco dei diritti si dice tutto e decide il cloud."""
    diritti = set(v.extra.get("diritti") or [])
    if not diritti:
        possibili = set(COMANDI)
    else:
        possibili = {nome for nome, cmd in COMANDI.items() if cmd in diritti}
        possibili.add("destinazione")
    if _semplice(v):
        # La T03 non accetta il limite di ricarica da remoto, e non ha il 193.
        possibili.discard("limite_ricarica")
        possibili.discard("ricarica")
    return possibili


@dataclass
class Sessione:
    """Quello che il login consegna e che serve a ogni richiesta dopo."""

    id_utente: str
    token: str
    refresh_token: str | None
    device_id: str
    chiave_firma: bytes
    cert_sincronizzato: bool = False


def _forma(corpo: dict[str, str]) -> str:
    return "&".join(f"{k}={quote(v, safe='')}" for k, v in corpo.items())


class ClienteLeapmotor:
    def __init__(
        self,
        sessione_http: aiohttp.ClientSession,
        *,
        email: str,
        password: str,
        cert_app_pem: str,
        chiave_app_pem: str,
        cartella: Path | str,
        lingua: str = "it-IT",
        base_url: str = BASE_URL,
        device_id: str = cripto.DEVICE_ID_PREDEFINITO,
    ) -> None:
        self._http = sessione_http
        self.email = email
        self.password = password
        self.lingua = lingua
        self.base_url = base_url.rstrip("/")
        self._device_id_iniziale = device_id
        self.cartella = Path(cartella)
        self._cert_app_pem = cert_app_pem
        self._chiave_app_pem = chiave_app_pem
        self._ssl_app: ssl.SSLContext | None = None
        self._ssl_account: ssl.SSLContext | None = None
        self.sessione: Sessione | None = None
        self._percorsi_provati: dict[str, str] = {}
        #: VIN → (consumo medio, quando si è letto)
        self._consumi: dict[str, tuple[float | None, float]] = {}
        self._serratura = asyncio.Lock()

    # --- i certificati --------------------------------------------------

    def _scrivi_privato(self, nome: str, contenuto: bytes) -> Path:
        self.cartella.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(self.cartella, 0o700)
        percorso = self.cartella / nome
        fd = os.open(percorso, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(contenuto)
        return percorso

    def _contesto(self, cert: Path, chiave: Path) -> ssl.SSLContext:
        # Il server del cloud usa un certificato autofirmato: lo verificano
        # tutti disattivando il controllo, e noi con loro. La sicurezza vera
        # qui è il certificato che presentiamo noi, non quello che riceviamo.
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            ctx.load_cert_chain(str(cert), str(chiave))
        except (ssl.SSLError, OSError) as err:
            raise ErroreCertificato(f"Il certificato in {cert.name} non si apre: {err}") from err
        return ctx

    def _prepara_ssl_app(self) -> ssl.SSLContext:
        """Da chiamare fuori dal ciclo degli eventi: scrive e legge file."""
        if self._ssl_app is None:
            if not self._cert_app_pem.strip() or not self._chiave_app_pem.strip():
                raise ErroreCertificato("Mancano il certificato e la chiave dell'app Leapmotor")
            cert = self._scrivi_privato("app_cert.pem", self._cert_app_pem.encode())
            chiave = self._scrivi_privato("app_key.pem", self._chiave_app_pem.encode())
            self._ssl_app = self._contesto(cert, chiave)
        return self._ssl_app

    def _prepara_ssl_account(self, p12_base64: str, id_utente: str, uid: str) -> ssl.SSLContext:
        """Il certificato dell'account, dal PKCS#12 del login. Fuori dal ciclo."""
        try:
            p12 = base64.b64decode(p12_base64)
            password = cripto.password_p12(id_utente, uid)
            chiave, cert, _ = pkcs12.load_key_and_certificates(p12, password.encode())
        except Exception as err:
            raise ErroreCertificato(f"Il certificato dell'account non si decifra: {err}") from err
        if chiave is None or cert is None:
            raise ErroreCertificato("Il certificato dell'account è vuoto")
        percorso_cert = self._scrivi_privato("account_cert.pem", cert.public_bytes(serialization.Encoding.PEM))
        percorso_chiave = self._scrivi_privato(
            "account_key.pem",
            chiave.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()
            ),
        )
        return self._contesto(percorso_cert, percorso_chiave)

    # --- la rete -------------------------------------------------------------

    async def _posta(
        self,
        cosa: str,
        percorso: str,
        *,
        intestazioni: dict[str, str],
        corpo: str,
        ssl_: ssl.SSLContext,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{_PREFISSO}{percorso}"
        try:
            async with self._http.post(url, data=corpo, headers=intestazioni, ssl=ssl_, timeout=_TIMEOUT) as r:
                testo = await r.text()
                http = r.status
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise ErroreRete(f"{cosa}: il cloud non risponde: {err}") from err
        try:
            dati = json.loads(testo)
        except ValueError as err:
            if http != 200:
                # Un 404 del gateway è una pagina, non JSON: è il cloud che
                # dice «questo indirizzo non c'è», e chi chiama ci ripiega.
                raise ErroreApi(cosa, None, testo[:120], http) from err
            raise ErroreRete(f"{cosa}: risposta non JSON (HTTP {http}): {testo[:120]}") from err
        if not isinstance(dati, dict):
            raise ErroreRete(f"{cosa}: risposta inattesa (HTTP {http})")
        codice = dati.get("code")
        if http != 200 or codice != 0:
            raise ErroreApi(cosa, codice, str(dati.get("message") or testo[:120]), http)
        return dati

    def _intestazioni_base(self, nonce: str, timestamp: str, device_id: str) -> dict[str, str]:
        return {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "acceptLanguage": self.lingua,
            "channel": cripto.CANALE,
            "deviceType": cripto.TIPO_DISPOSITIVO,
            "X-P12_ENC_ALG": cripto.ALGORITMO_P12,
            "source": cripto.SORGENTE,
            "version": cripto.VERSIONE_APP,
            "nonce": nonce,
            "deviceId": device_id,
            "timestamp": timestamp,
        }

    def _firmate(self, *, vin: str | None = None, parametri: dict[str, str] | None = None) -> dict[str, str]:
        """Le intestazioni di una richiesta normale, dopo il login."""
        s = self._sessione_o_errore()
        nonce, timestamp = cripto.nonce(), str(int(time.time() * 1000))
        campi = {
            "acceptLanguage": self.lingua,
            "channel": cripto.CANALE,
            "deviceId": s.device_id,
            "deviceType": cripto.TIPO_DISPOSITIVO,
            "nonce": nonce,
            "source": cripto.SORGENTE,
            "timestamp": timestamp,
            "version": cripto.VERSIONE_APP,
            **(parametri or {}),
        }
        if vin:
            campi["vin"] = vin
        h = self._intestazioni_base(nonce, timestamp, s.device_id)
        h["sign"] = cripto.firma_hmac(s.chiave_firma, cripto.testo_firma_standard(campi))
        h["userId"] = s.id_utente
        h["token"] = s.token
        return h

    def _sessione_o_errore(self) -> Sessione:
        if self.sessione is None or self._ssl_account is None:
            raise ErroreCredenziali("Non si è ancora entrati")
        return self.sessione

    # --- entrare --------------------------------------------------------------

    async def accedi(self) -> None:
        """Il login: col certificato dell'app, poi si tiene quello dell'account."""
        loop = asyncio.get_running_loop()
        ssl_app = await loop.run_in_executor(None, self._prepara_ssl_app)
        nonce, timestamp = cripto.nonce(), str(int(time.time() * 1000))
        h = self._intestazioni_base(nonce, timestamp, self._device_id_iniziale)
        h["sign"] = cripto.firma_login(
            lingua=self.lingua,
            device_id=self._device_id_iniziale,
            email=self.email,
            password=self.password,
            nonce_=nonce,
            timestamp=timestamp,
        )
        corpo = _forma(
            {
                "isRecoverAcct": "0",
                "password": self.password,
                "policyId": cripto.POLICY_ID,
                "loginMethod": "1",
                "email": self.email,
            }
        )
        try:
            risposta = await self._posta("login", "/acct/v1/login", intestazioni=h, corpo=corpo, ssl_=ssl_app)
        except ErroreApi as err:
            if str(err.codice) == CODICE_LOGIN_RIFIUTATO or err.http in (401, 403):
                raise ErroreCredenziali(err.messaggio or "Email o password rifiutate") from err
            raise
        d = risposta.get("data") or {}
        try:
            token = str(d["token"])
            id_utente = str(d["id"])
            chiave = cripto.chiave_firma(str(d["signIkm"]), str(d["signSalt"]), str(d["signInfo"]))
            p12 = str(d["base64Cert"])
            uid = str(d["uid"])
        except KeyError as err:
            raise ErroreRete(f"Il login non ha consegnato {err}") from err
        self._ssl_account = await loop.run_in_executor(None, self._prepara_ssl_account, p12, id_utente, uid)
        self.sessione = Sessione(
            id_utente=id_utente,
            token=token,
            refresh_token=str(d.get("refreshToken") or "") or None,
            device_id=cripto.device_id_dal_token(token, self._device_id_iniziale),
            chiave_firma=chiave,
        )
        _LOGGER.debug("Entrati nel cloud Leapmotor")

    async def rinnova_token(self) -> None:
        s = self._sessione_o_errore()
        if not s.refresh_token:
            raise ErroreCredenziali("Nessun refresh token")
        h = self._firmate(parametri={"refreshToken": s.refresh_token})
        r = await self._posta(
            "rinnovo del token",
            "/acct/v1/token/refresh",
            intestazioni=h,
            corpo=_forma({"refreshToken": s.refresh_token}),
            ssl_=self._ssl_account,  # type: ignore[arg-type]
        )
        d = r.get("data") or {}
        if not d.get("token"):
            raise ErroreCredenziali("Il rinnovo non ha consegnato un token")
        s.token = str(d["token"])
        s.refresh_token = str(d.get("refreshToken") or "") or s.refresh_token

    async def _con_sessione(self, azione: Any) -> Any:
        """Esegue ``azione()``; se il token è scaduto lo rinnova, e se non si
        può rientra da capo. Una richiesta alla volta: il cloud vuole un solo
        passo per sessione."""
        async with self._serratura:
            if self.sessione is None:
                await self.accedi()
            try:
                return await azione()
            except ErroreApi as err:
                if not err.token_scaduto:
                    raise
                _LOGGER.debug("Token scaduto: si rinnova")
                try:
                    await self.rinnova_token()
                except (ErroreApi, ErroreCredenziali):
                    await self.accedi()
                return await azione()

    # --- leggere ----------------------------------------------------------

    async def vetture(self) -> list[Vettura]:
        async def leggi() -> list[Vettura]:
            r = await self._posta(
                "elenco delle auto",
                "/vehicle/v1/list",
                intestazioni=self._firmate(),
                corpo="",
                ssl_=self._ssl_account,  # type: ignore[arg-type]
            )
            d = r.get("data") or {}
            elenco = []
            for chiave, condivisa in (("bindcars", False), ("sharedcars", True)):
                for voce in d.get(chiave) or []:
                    if isinstance(voce, dict) and voce.get("vin"):
                        elenco.append(vettura_da_json(voce, condivisa))
            return elenco

        return await self._con_sessione(leggi)

    async def stato(self, vettura: Vettura) -> StatoVettura:
        async def leggi() -> StatoVettura:
            percorso = self._percorsi_provati.get(vettura.vin, _percorso_stato(vettura))
            try:
                r = await self._stato_grezzo(vettura, percorso)
            except ErroreApi as err:
                if err.http != 404 or percorso == "c10":
                    raise
                _LOGGER.info(
                    "Il cloud non conosce /status/get/%s per la %s: si prova con c10", percorso, vettura.modello
                )
                percorso = "c10"
                r = await self._stato_grezzo(vettura, percorso)
            self._percorsi_provati[vettura.vin] = percorso
            d = r.get("data") or {}
            if vettura.condivisa and vettura.extra.get("car_id") and not _ha_segnali(d):
                # Un'auto condivisa a volte risponde vuota senza il carId.
                r2 = await self._stato_grezzo(vettura, percorso, car_id=vettura.extra["car_id"])
                if _ha_segnali(r2.get("data") or {}):
                    d = r2["data"]
            stato = interpreta(d)
            stato.consumo_medio_kwh_100km = await self._consumo_medio(vettura)
            return stato

        return await self._con_sessione(leggi)

    async def _consumo_medio(self, vettura: Vettura) -> float | None:
        """Il consumo medio delle ultime sei settimane (kWh/100 km), riletto ogni mezz'ora.

        È un di più: se il cloud non lo dà, lo stato arriva lo stesso.
        """
        valore, quando = self._consumi.get(vettura.vin, (None, 0.0))
        if time.monotonic() - quando < _CONSUMO_OGNI_S and quando:
            return valore
        try:
            r = await self._posta(
                "consumo medio",
                "/drivingRecord/v1/getLastNweeks100kmECAndRank",
                intestazioni=self._firmate(parametri={"carvin": vettura.vin}),
                corpo=_forma({"carvin": vettura.vin}),
                ssl_=self._ssl_account,  # type: ignore[arg-type]
            )
        except ErroreApi as err:
            if err.token_scaduto:
                raise
            _LOGGER.debug("Consumo medio della %s non letto: %s", vettura.modello, err)
            nuovo = None
        except ErroreRete as err:
            _LOGGER.debug("Consumo medio della %s non letto: %s", vettura.modello, err)
            return valore
        else:
            classifica = (r.get("data") or {}).get("rankResult") if isinstance(r.get("data"), dict) else None
            try:
                nuovo = float(classifica["hundredKmEC"]) if isinstance(classifica, dict) else None
            except (KeyError, TypeError, ValueError):
                nuovo = None
            if nuovo is not None and not 0 < nuovo < 100:
                nuovo = None
        self._consumi[vettura.vin] = (nuovo, time.monotonic())
        return nuovo

    async def _stato_grezzo(self, vettura: Vettura, percorso: str, car_id: str | None = None) -> dict[str, Any]:
        corpo = {"vin": vettura.vin}
        if car_id:
            corpo["carId"] = car_id
        return await self._posta(
            "stato dell'auto",
            f"/vehicle/v1/status/get/{percorso}",
            intestazioni=self._firmate(vin=vettura.vin),
            corpo=_forma(corpo),
            ssl_=self._ssl_account,  # type: ignore[arg-type]
        )

    # --- comandare ---------------------------------------------------------

    async def comanda(self, vettura: Vettura, cmd_id: str, contenuto: dict[str, Any], pin: str | None) -> None:
        """Un comando all'auto. Con il PIN quasi tutti; la destinazione no."""
        testo = json.dumps(contenuto, separators=(",", ":"))

        async def esegui() -> None:
            s = self._sessione_o_errore()
            if cmd_id == CMD_DESTINAZIONE:
                h = self._firmate_a_mano([testo, cmd_id, vettura.vin])
                corpo = {"cmdContent": testo, "vin": vettura.vin, "cmdId": cmd_id}
            else:
                if not pin:
                    raise ErrorePin("Questo comando vuole il PIN dell'auto: scrivilo nelle opzioni di GdAAuto")
                pin_cifrato = cripto.cifra_pin(pin, s.token)
                await self._sincronizza_cert()
                await self._verifica_pin(vettura, pin_cifrato)
                h = self._firmate_a_mano([testo, cmd_id, pin_cifrato, vettura.vin])
                corpo = {"cmdContent": testo, "vin": vettura.vin, "cmdId": cmd_id, "operatePassword": pin_cifrato}
            # Nelle firme dei comandi il VIN sta in fondo, dopo la versione.
            r = await self._posta(
                f"comando {cmd_id}",
                "/vehicle/v1/app/remote/ctl",
                intestazioni=h,
                corpo=_forma(corpo),
                ssl_=self._ssl_account,  # type: ignore[arg-type]
            )
            d = r.get("data") or {}
            if isinstance(d, dict) and d.get("remoteCtlId"):
                await self._aspetta_esito(
                    str(d["remoteCtlId"]),
                    int(d.get("queryRemoteCtlResultTimeout") or 30000),
                    int(d.get("queryInterval") or 2000),
                )

        await self._con_sessione(esegui)

    def _firmate_a_mano(self, pezzi: list[str]) -> dict[str, str]:
        """Le intestazioni dei comandi. La firma è:
        ``language channel [cmdContent cmdId] deviceId deviceType nonce
        [operatePassword] source timestamp version [vin]``."""
        s = self._sessione_o_errore()
        nonce, timestamp = cripto.nonce(), str(int(time.time() * 1000))
        if len(pezzi) == 4:  # comando con PIN
            testo_cmd, cmd_id, pin_cifrato, vin = pezzi
            prima, fra, dopo = [testo_cmd, cmd_id], [pin_cifrato], [vin]
        elif len(pezzi) == 3:  # comando senza PIN
            testo_cmd, cmd_id, vin = pezzi
            prima, fra, dopo = [testo_cmd, cmd_id], [], [vin]
        elif len(pezzi) == 2:  # verifica del PIN: operatePassword, vin
            prima, fra, dopo = [], [pezzi[0]], [pezzi[1]]
        else:  # esito: remoteCtlId
            prima, fra, dopo = [], [pezzi[0]], []
        testo = "".join(
            [
                self.lingua,
                cripto.CANALE,
                *prima,
                s.device_id,
                cripto.TIPO_DISPOSITIVO,
                nonce,
                *fra,
                cripto.SORGENTE,
                timestamp,
                cripto.VERSIONE_APP,
                *dopo,
            ]
        )
        h = self._intestazioni_base(nonce, timestamp, s.device_id)
        h["sign"] = cripto.firma_hmac(s.chiave_firma, testo)
        h["userId"] = s.id_utente
        h["token"] = s.token
        return h

    async def _sincronizza_cert(self) -> None:
        """Una volta per sessione, prima del primo comando, col certificato dell'app."""
        s = self._sessione_o_errore()
        if s.cert_sincronizzato:
            return
        await self._posta(
            "sincronizzazione del certificato",
            "/vehicle/v1/cert/sync",
            intestazioni=self._firmate(),
            corpo="",
            ssl_=self._ssl_app,  # type: ignore[arg-type]
        )
        s.cert_sincronizzato = True

    async def verifica_pin(self, vettura: Vettura, pin: str) -> None:
        """Il PIN è quello giusto? Lo chiede al cloud senza comandare niente."""
        s = self._sessione_o_errore()
        await self._sincronizza_cert()
        await self._verifica_pin(vettura, cripto.cifra_pin(pin, s.token))

    async def _verifica_pin(self, vettura: Vettura, pin_cifrato: str) -> None:
        try:
            await self._posta(
                "verifica del PIN",
                "/vehicle/v1/operPwd/verify",
                intestazioni=self._firmate_a_mano([pin_cifrato, vettura.vin]),
                corpo=_forma({"operatePassword": pin_cifrato, "vin": vettura.vin}),
                ssl_=self._ssl_account,  # type: ignore[arg-type]
            )
        except ErroreApi as err:
            if err.token_scaduto:
                raise
            raise ErrorePin(f"Il cloud non accetta il PIN: {err.messaggio}") from err

    async def _aspetta_esito(self, remote_ctl_id: str, timeout_ms: int, intervallo_ms: int) -> None:
        scadenza = time.monotonic() + max(timeout_ms, 1000) / 1000
        ultimo: Any = None
        while time.monotonic() < scadenza:
            r = await self._posta(
                "esito del comando",
                "/vehicle/v1/app/remote/ctl/result/query",
                intestazioni=self._firmate_a_mano([remote_ctl_id]),
                corpo=_forma({"remoteCtlId": remote_ctl_id}),
                ssl_=self._ssl_account,  # type: ignore[arg-type]
            )
            ultimo = r.get("data")
            if ultimo == 1:
                return
            await asyncio.sleep(max(intervallo_ms, 250) / 1000)
        raise ErroreApi("esito del comando", ultimo, "L'auto non ha confermato in tempo")

    # --- i comandi che conosciamo ------------------------------------------

    async def chiudi(self, v: Vettura, pin: str | None) -> None:
        await self.comanda(v, CMD_SERRATURA, {"value": "lock"}, pin)

    async def apri(self, v: Vettura, pin: str | None) -> None:
        await self.comanda(v, CMD_SERRATURA, {"value": "unlock"}, pin)

    async def trova(self, v: Vettura, pin: str | None) -> None:
        await self.comanda(v, CMD_TROVA, {"value": "true"}, pin)

    async def clima(self, v: Vettura, acceso: bool, pin: str | None, temperatura: int = 22) -> None:
        if acceso:
            contenuto = {
                "circle": "out",
                "mode": "wind",
                "operate": "manual",
                "position": "all",
                "temperature": str(max(18, min(32, temperatura))),
                "windlevel": "3",
                "wshld": "0",
            }
        elif _semplice(v):
            # La T03 ignora lo spegnimento «nudo»: vuole il corpo intero.
            contenuto = {
                "circle": "out",
                "mode": "wind",
                "operate": "off",
                "position": "all",
                "temperature": "26",
                "windlevel": "3",
                "wshld": "0",
            }
        else:
            contenuto = {"operate": "off"}
        await self.comanda(v, CMD_CLIMA, contenuto, pin)

    async def ricarica(self, v: Vettura, avvia: bool, pin: str | None) -> None:
        await self.comanda(v, CMD_RICARICA, {"value": "start" if avvia else "stop"}, pin)

    async def limite_ricarica(self, v: Vettura, percento: int, pin: str | None) -> None:
        """Cambia solo il limite; il resto del piano resta com'è nell'auto."""
        piano = ((v.stato.grezzo.get("config") or {}).get("3") or {}) if v.stato else {}
        contenuto = {
            "chargeEnable": int(piano.get("isEnable") or 0),
            "chargesoc": int(max(1, min(100, percento))),
            "circulation": int(piano.get("circulation") or 0),
            "cycles": str(piano.get("cycles") or "1,1,1,1,1,1,1"),
            "endtime": str(piano.get("endTime") or "08:00"),
            "recharge": int(piano.get("recharge") or 0),
            "starttime": str(piano.get("beginTime") or "22:00"),
        }
        await self.comanda(v, CMD_PIANO_RICARICA, contenuto, pin)

    async def destinazione(self, v: Vettura, nome: str, lat: float, lon: float, indirizzo: str = "") -> None:
        contenuto = {
            "address": indirizzo or nome,
            "addressname": nome,
            "latitude": f"{lat:.6f}",
            "longitude": f"{lon:.6f}",
            "linenum": "0",
        }
        await self.comanda(v, CMD_DESTINAZIONE, contenuto, None)


def _ha_segnali(d: dict[str, Any]) -> bool:
    segnali = d.get("signal")
    if isinstance(segnali, dict):
        return bool(segnali)
    return any(k in d for k in ("soc", "totalMileage", "expectedMileage"))
