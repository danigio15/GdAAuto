"""Gigya: l'account MyRenault, MyDacia e MyAlpine.

L'app non ha un suo server di login: usa Gigya (SAP Customer Data Cloud).
Il giro è questo:

1. ``accounts.login`` con email e password → un ``login_token`` che non
   scade da solo. È l'unica volta che serve la password: GdAAuto non la salva;
2. ``accounts.getAccountInfo`` col ``login_token`` → chi è (``personId``);
3. ``accounts.getJWT`` col ``login_token`` → un JWT di un quarto d'ora, da
   mandare a Kamereon. Quando scade se ne chiede un altro, sempre dal
   ``login_token``. Se Gigya non lo accetta più, bisogna rientrare.

Dal 2026 Gigya può chiedere la verifica in due passaggi (errore 403101): un
codice mandato per email. Il codice si scrive nel campo «codice» e si
conferma di nuovo; Gigya ricorda poi il dispositivo (i cookie ``gmid`` e
``ucid``, salvati col token) per una trentina di giorni.

Gli indirizzi e la sequenza della verifica vengono da hacf-fr/renault-api
(licenza MIT): vedi RINGRAZIAMENTI.md.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import json
import time
from typing import Any

import aiohttp

from ..errori import (
    CodiceInviato as _CodiceInviato,
    CodiceSbagliato as _CodiceSbagliato,
    ErroreApi,
    ErroreCredenziali,
    ErroreRete,
)

_TIMEOUT = aiohttp.ClientTimeout(total=30)

#: login sbagliato, token scaduto o revocato, account bloccato o da verificare
_CREDENZIALI_RIFIUTATE = {403042, 403005, 403013, 403120, 401030, 403041}
_VERIFICA_RICHIESTA = 403101
_CODICE_SBAGLIATO = 400006
#: i cookie che identificano il dispositivo ricordato dopo la verifica
DISPOSITIVO = ("gmid", "ucid")
#: quanto tempo si aspetta il codice, prima di ricominciare
_ATTESA_CODICE = 15 * 60


class VerificaRichiesta(ErroreCredenziali):
    """Gigya vuole la verifica in due passaggi prima di dare il login_token."""

    def __init__(self, messaggio: str, reg_token: str) -> None:
        super().__init__(messaggio)
        self.reg_token = reg_token


# Gli errori del codice sono di tutte le marche: il config flow li riconosce.
CodiceInviato = _CodiceInviato
CodiceSbagliato = _CodiceSbagliato


def scadenza_jwt(jwt: str, adesso: float | None = None) -> float:
    """Quando scade il JWT (secondi epoch), letto dal suo ``exp`` senza
    verificarne la firma; se non si legge, fra un quarto d'ora."""
    adesso = time.time() if adesso is None else adesso
    try:
        corpo = jwt.split(".")[1]
        corpo += "=" * (-len(corpo) % 4)
        exp = json.loads(base64.urlsafe_b64decode(corpo)).get("exp")
        return float(exp) if exp else adesso + 900
    except (IndexError, ValueError, TypeError, AttributeError):
        return adesso + 900


class Gigya:
    def __init__(self, sessione_http: aiohttp.ClientSession, url: str, chiave: str) -> None:
        self._http = sessione_http
        self.url = url.rstrip("/")
        self.chiave = chiave

    async def _chiedi(
        self,
        cosa: str,
        metodo: str,
        percorso: str,
        *,
        dati: dict[str, Any] | None = None,
        parametri: dict[str, Any] | None = None,
        dispositivo: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            async with self._http.request(
                metodo,
                f"{self.url}/{percorso}",
                data=dati,
                params=parametri,
                cookies=dispositivo or None,
                timeout=_TIMEOUT,
            ) as r:
                http = r.status
                testo = await r.text()
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise ErroreRete(f"{cosa}: Gigya non risponde: {err}") from err
        try:
            corpo = json.loads(testo)
        except ValueError:
            corpo = None
        if not isinstance(corpo, dict):
            raise ErroreRete(f"{cosa}: Gigya risponde una cosa che non si capisce (HTTP {http})")
        codice = int(corpo.get("errorCode") or 0)
        if codice:
            dettagli = str(corpo.get("errorDetails") or corpo.get("errorMessage") or "")
            if codice == _VERIFICA_RICHIESTA and corpo.get("regToken"):
                raise VerificaRichiesta(f"{cosa}: serve la verifica in due passaggi", str(corpo["regToken"]))
            if codice == _CODICE_SBAGLIATO and percorso.endswith("completeVerification"):
                raise CodiceSbagliato("Il codice di verifica non va: controlla l'ultima email di Renault")
            if codice in _CREDENZIALI_RIFIUTATE:
                raise ErroreCredenziali(f"{cosa}: Gigya rifiuta l'accesso ({codice}: {dettagli})")
            if codice >= 500000 or http >= 500:
                raise ErroreRete(f"{cosa}: Gigya ha un guasto ({codice}: {dettagli})")
            raise ErroreApi(cosa, codice, dettagli, http)
        if http >= 500:
            raise ErroreRete(f"{cosa}: Gigya risponde HTTP {http}")
        return corpo

    # --- il login --------------------------------------------------------

    async def login(self, email: str, password: str, dispositivo: dict[str, str] | None = None) -> str:
        r = await self._chiedi(
            "login",
            "POST",
            "accounts.login",
            dati={"ApiKey": self.chiave, "loginID": email, "password": password},
            dispositivo=dispositivo,
        )
        token = (r.get("sessionInfo") or {}).get("cookieValue")
        if not token:
            raise ErroreApi("login", None, "Gigya non ha dato il login_token")
        return str(token)

    async def persona(self, login_token: str) -> str:
        r = await self._chiedi(
            "account", "POST", "accounts.getAccountInfo", dati={"ApiKey": self.chiave, "login_token": login_token}
        )
        persona = (r.get("data") or {}).get("personId")
        if not persona:
            raise ErroreApi("account", None, "Gigya non dice chi è l'account (personId)")
        return str(persona)

    async def jwt(self, login_token: str) -> str:
        r = await self._chiedi(
            "rinnovo del JWT",
            "POST",
            "accounts.getJWT",
            dati={
                "ApiKey": self.chiave,
                "login_token": login_token,
                "fields": "data.personId,data.gigyaDataCenter",
                "expiration": 900,
            },
        )
        if not r.get("id_token"):
            raise ErroreApi("rinnovo del JWT", None, "Gigya non ha dato il JWT")
        return str(r["id_token"])

    # --- la verifica in due passaggi ---------------------------------------

    async def nuovo_dispositivo(self) -> dict[str, str]:
        """I cookie ``gmid``/``ucid`` di un dispositivo nuovo (``webSdkBootstrap``)."""
        try:
            async with self._http.get(
                f"{self.url}/accounts.webSdkBootstrap", params={"APIKey": self.chiave}, timeout=_TIMEOUT
            ) as r:
                if r.status >= 400:
                    raise ErroreRete(f"verifica: Gigya risponde HTTP {r.status}")
                return {nome: r.cookies[nome].value for nome in DISPOSITIVO if nome in r.cookies}
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise ErroreRete(f"verifica: Gigya non risponde: {err}") from err

    async def avvia_verifica(self, reg_token: str, dispositivo: dict[str, str]) -> str:
        r = await self._chiedi(
            "verifica",
            "GET",
            "accounts.tfa.initTFA",
            parametri={
                "APIKey": self.chiave,
                "regToken": reg_token,
                "provider": "gigyaEmail",
                "mode": "verify",
                **dispositivo,
            },
            dispositivo=dispositivo,
        )
        if not r.get("gigyaAssertion"):
            raise ErroreApi("verifica", None, "Gigya non ha avviato la verifica")
        return str(r["gigyaAssertion"])

    async def email_verifica(self, assertion: str, dispositivo: dict[str, str]) -> tuple[str, str]:
        """La prima email registrata per la verifica: (id, indirizzo mascherato)."""
        r = await self._chiedi(
            "verifica",
            "GET",
            "accounts.tfa.email.getEmails",
            parametri={"APIKey": self.chiave, "gigyaAssertion": assertion},
            dispositivo=dispositivo,
        )
        for voce in r.get("emails") or []:
            if isinstance(voce, dict) and voce.get("id"):
                return str(voce["id"]), str(voce.get("obfuscated") or "")
        raise ErroreApi("verifica", None, "Nessuna email registrata per la verifica in due passaggi")

    async def manda_codice(self, assertion: str, email_id: str, lingua: str, dispositivo: dict[str, str]) -> str:
        r = await self._chiedi(
            "verifica",
            "GET",
            "accounts.tfa.email.sendVerificationCode",
            parametri={"APIKey": self.chiave, "gigyaAssertion": assertion, "emailID": email_id, "lang": lingua},
            dispositivo=dispositivo,
        )
        if not r.get("phvToken"):
            raise ErroreApi("verifica", None, "Gigya non ha mandato il codice")
        return str(r["phvToken"])

    async def conferma_codice(self, assertion: str, phv: str, codice: str, dispositivo: dict[str, str]) -> str:
        r = await self._chiedi(
            "verifica",
            "GET",
            "accounts.tfa.email.completeVerification",
            parametri={"APIKey": self.chiave, "gigyaAssertion": assertion, "phvToken": phv, "code": codice},
            dispositivo=dispositivo,
        )
        if not r.get("providerAssertion"):
            raise ErroreApi("verifica", None, "Gigya non ha confermato il codice")
        return str(r["providerAssertion"])

    async def chiudi_verifica(self, assertion: str, prova: str, reg_token: str, dispositivo: dict[str, str]) -> None:
        await self._chiedi(
            "verifica",
            "GET",
            "accounts.tfa.finalizeTFA",
            parametri={
                "APIKey": self.chiave,
                "gigyaAssertion": assertion,
                "providerAssertion": prova,
                "regToken": reg_token,
                # Il dispositivo va ricordato: niente codice ai login dopo.
                "tempDevice": "false",
            },
            dispositivo=dispositivo,
        )


@dataclass
class _InSospeso:
    """Una verifica avviata, che aspetta il codice dall'email."""

    reg_token: str = field(repr=False)
    assertion: str = field(repr=False)
    phv: str = field(repr=False)
    dispositivo: dict[str, str] = field(repr=False)
    scade: float


#: Le verifiche in attesa del codice, fra un invio del modulo e il successivo.
#: Restano solo in memoria: niente password, solo i gettoni di Gigya.
_IN_SOSPESO: dict[str, _InSospeso] = {}


async def entra(
    gigya: Gigya,
    email: str,
    password: str,
    *,
    codice: str = "",
    dispositivo: dict[str, str] | None = None,
    lingua: str = "it",
) -> dict[str, str]:
    """Il login con la password, verifica in due passaggi compresa.

    Restituisce il ``login_token`` e il dispositivo (``gmid``/``ucid``) da
    salvare. Se Gigya chiede la verifica, manda il codice per email e solleva
    ``CodiceInviato``: si richiama con lo stesso account e il ``codice``.
    """
    chiave = f"{gigya.url}|{gigya.chiave}|{email.strip().lower()}"
    adesso = time.monotonic()
    for k in [k for k, s in _IN_SOSPESO.items() if s.scade < adesso]:
        del _IN_SOSPESO[k]
    disp = {k: v for k, v in (dispositivo or {}).items() if k in DISPOSITIVO and v}
    sospeso = _IN_SOSPESO.get(chiave)
    codice = codice.strip()
    if sospeso and codice:
        prova = await gigya.conferma_codice(sospeso.assertion, sospeso.phv, codice, sospeso.dispositivo)
        await gigya.chiudi_verifica(sospeso.assertion, prova, sospeso.reg_token, sospeso.dispositivo)
        del _IN_SOSPESO[chiave]
        disp = dict(sospeso.dispositivo)
    try:
        login_token = await gigya.login(email.strip(), password, disp)
    except VerificaRichiesta as err:
        if not disp:
            disp = await gigya.nuovo_dispositivo()
        assertion = await gigya.avvia_verifica(err.reg_token, disp)
        email_id, mascherata = await gigya.email_verifica(assertion, disp)
        phv = await gigya.manda_codice(assertion, email_id, lingua, disp)
        _IN_SOSPESO[chiave] = _InSospeso(err.reg_token, assertion, phv, disp, adesso + _ATTESA_CODICE)
        raise CodiceInviato(
            f"Renault chiede la verifica in due passaggi: ha mandato un codice a {mascherata or 'la tua email'}. "
            "Scrivilo nel campo «codice» e conferma di nuovo."
        ) from None
    return {"login_token": login_token, **disp}
