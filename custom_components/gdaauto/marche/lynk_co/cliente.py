"""Il filo col cloud dell'app Lynk & Co per le auto del 2025 in poi (01 nuova, 02, 08).

Il login è quello dell'app: Azure AD B2C (``login.lynkco.com``) con email,
password e un codice per SMS. Lo fa chi configura **nel suo browser**: alla
fine il sito prova ad aprire l'app con un indirizzo ``msauth://…?code=…``, che
il browser non sa aprire; quell'indirizzo (o il solo codice) si incolla in
Home Assistant. GdAAuto lo scambia coi token (PKCE) e poi rinnova da sola col
refresh token.

Ogni richiesta all'API porta una firma: SHA-256 di id del cliente (nel token),
nonce e percorso. Il cloud tiene **un solo dispositivo per account**: entrare
da Home Assistant scollega l'app sul telefono, e viceversa.

Indirizzi, firma e campi vengono da ha_lynkco_2025 (b12e, licenza MIT): vedi
RINGRAZIAMENTI.md.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import hashlib
import json
import logging
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, quote, urlparse
import uuid

import aiohttp

from ...vettura import StatoVettura, Vettura
from ..errori import ErroreApi, ErroreComando, ErroreCredenziali, ErroreRete
from .dati import interpreta

_LOGGER = logging.getLogger(__name__)

ID = "lynk_co"
NOME = "Lynk & Co"

# L'app Lynk & Co (Azure AD B2C), da ha_lynkco_2025 (MIT).
CLIENT_ID = "c3e13a0c-8ba7-4ea5-9a21-ecd75830b9e9"
TENANT = "lynkcoprod.onmicrosoft.com"
SCOPE = (
    f"https://{TENANT}/mobile-app-web-api/mobile.read "
    f"https://{TENANT}/mobile-app-web-api/mobile.write "
    "profile offline_access"
)
REDIRECT_URI = "msauth://prod.lynkco.app.crisp.prod/2jmj7l5rSw0yVb%2FvlWAYkK%2FYBwk%3D"

INDIRIZZI = {
    "login": f"https://login.lynkco.com/{TENANT}/b2c_1a_signin_mfa/",
    "api": "https://agw.mobile-app.lynkco.biz",
}

#: I modelli, dal codice dell'elenco (ha_lynkco_2025).
MODELLI = {"CX11_A1": "01", "CX11_A3": "01", "E335": "02", "DX11": "08"}

_TIMEOUT = aiohttp.ClientTimeout(total=30)
_MARGINE = timedelta(minutes=2)
_METADATI_OGNI_S = 1800

CLIMA_MIN, CLIMA_MAX = 16, 28


def comandi_di(_v: Vettura) -> set[str]:
    return {"chiudi", "apri", "trova", "clima", "ricarica", "limite_ricarica"}


def pkce() -> tuple[str, str]:
    """Il verificatore e la sfida S256."""
    verificatore = secrets.token_urlsafe(64)[:96]
    sfida = base64.urlsafe_b64encode(hashlib.sha256(verificatore.encode()).digest()).decode().rstrip("=")
    return verificatore, sfida


def indirizzo_login(sfida: str, login: str | None = None) -> str:
    return (
        f"{login or INDIRIZZI['login']}oauth2/v2.0/authorize"
        f"?response_type=code&client_id={CLIENT_ID}"
        f"&scope={quote(SCOPE, safe='')}"
        f"&code_challenge={sfida}&code_challenge_method=S256"
        f"&redirect_uri={quote(REDIRECT_URI, safe='')}"
    )


def codice_da(incollato: str) -> str:
    """Il codice, dall'indirizzo ``msauth://…?code=…`` o da solo."""
    testo = incollato.strip()
    if "code=" in testo:
        for parte in (urlparse(testo).query, testo.split("?", 1)[-1], testo):
            q = parse_qs(parte)
            if q.get("code"):
                return q["code"][0]
    if testo and "://" not in testo and " " not in testo:
        return testo
    raise ErroreCredenziali("Nell'indirizzo incollato non c'è il codice di Lynk & Co")


def rivendicazioni(token: str) -> dict[str, Any]:
    """Il corpo del JWT (senza verificarlo: serve solo a leggere chi è)."""
    try:
        corpo = token.split(".")[1]
        corpo += "=" * (-len(corpo) % 4)
        dati = json.loads(base64.urlsafe_b64decode(corpo))
    except (IndexError, ValueError):
        return {}
    return dati if isinstance(dati, dict) else {}


def firma(segreto: str, nonce: str, percorso: str) -> str:
    return hashlib.sha256((segreto + nonce + percorso).encode()).hexdigest()


async def scambia_codice(
    sessione: aiohttp.ClientSession, codice: str, verificatore: str, login: str | None = None
) -> dict[str, Any]:
    """Dal codice del browser ai token."""
    return await _token(
        sessione,
        {
            "client_info": "1",
            "scope": SCOPE,
            "code": codice,
            "grant_type": "authorization_code",
            "code_verifier": verificatore,
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
        },
        login,
    )


async def _token(sessione: aiohttp.ClientSession, modulo: dict[str, str], login: str | None) -> dict[str, Any]:
    url = f"{login or INDIRIZZI['login']}oauth2/v2.0/token"
    try:
        async with sessione.post(url, data=modulo, headers={"Accept": "application/json"}, timeout=_TIMEOUT) as r:
            http = r.status
            corpo = await r.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, OSError, ValueError) as err:
        raise ErroreRete(f"Il login di Lynk & Co non risponde: {err}") from err
    if http >= 500:
        raise ErroreRete(f"Il login di Lynk & Co ha un guasto (HTTP {http})")
    if http != 200 or not isinstance(corpo, dict) or not corpo.get("access_token"):
        descrizione = corpo.get("error_description") or corpo.get("error") if isinstance(corpo, dict) else ""
        raise ErroreCredenziali(f"Lynk & Co rifiuta l'accesso: {descrizione or http}")
    scade = rivendicazioni(corpo["access_token"]).get("exp")
    if not isinstance(scade, (int, float)):
        scade = time.time() + float(corpo.get("expires_in") or 3600)
    return {
        "access_token": corpo["access_token"],
        "refresh_token": corpo.get("refresh_token") or modulo.get("refresh_token"),
        "scade": datetime.fromtimestamp(scade, UTC).isoformat(),
    }


class ClienteLynk:
    def __init__(
        self,
        sessione_http: aiohttp.ClientSession,
        token: dict[str, Any],
        dispositivo: str | None = None,
        salva: Callable[[dict[str, Any]], None] | None = None,
        *,
        indirizzi: dict[str, str] | None = None,
    ) -> None:
        self._http = sessione_http
        self.indirizzi = {**INDIRIZZI, **(indirizzi or {})}
        self.token = dict(token)
        self.dispositivo = dispositivo or str(uuid.uuid4())
        self._salva = salva
        self._serratura = asyncio.Lock()
        self._comando = asyncio.Lock()
        self._metadati: dict[str, tuple[dict[str, Any], float]] = {}
        self._propulsione: dict[str, str] = {}

    @property
    def _love(self) -> str:
        return f"{self.indirizzi['api']}/mobile-app/love/v1"

    @property
    def _comandi(self) -> str:
        return f"{self.indirizzi['api']}/mobile-app/love-remote-command/v1"

    @property
    def chi(self) -> dict[str, Any]:
        return rivendicazioni(str(self.token.get("access_token") or ""))

    # --- i token --------------------------------------------------------------------

    async def _rinnova(self) -> None:
        if not self.token.get("refresh_token"):
            raise ErroreCredenziali("Manca il refresh token di Lynk & Co: rientra dal browser")
        self.token = await _token(
            self._http,
            {
                "client_id": CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": str(self.token["refresh_token"]),
                "scope": SCOPE,
                "redirect_uri": REDIRECT_URI,
            },
            self.indirizzi["login"],
        )
        if self._salva:
            self._salva(dict(self.token))

    async def _token_valido(self) -> str:
        async with self._serratura:
            scade = self.token.get("scade")
            if not self.token.get("access_token") or (
                scade and datetime.fromisoformat(scade) - _MARGINE < datetime.now(UTC)
            ):
                await self._rinnova()
            return str(self.token["access_token"])

    # --- la rete ---------------------------------------------------------------------

    def _intestazioni(self, token: str, percorso: str) -> dict[str, str]:
        chi = rivendicazioni(token)
        nonce = str(uuid.uuid4())
        return {
            "Authorization": f"Bearer {token}",
            "X-Auth-Token": token,
            "X-DeviceId": self.dispositivo,
            "X-CustomerNumber": str(chi.get("customerNumber") or ""),
            "X-CustomerId": str(chi.get("snowflakeId") or ""),
            "User-Agent": "Android",
            "api-version": "1",
            "X-NONCE": nonce,
            "X-SIGNATURE-VERSION": "v2",
            "X-SIGNATURE": firma(str(chi.get("snowflakeId") or ""), nonce, percorso),
            "X-App-Name": "Lynk&Co",
            "X-App-Version": "2.55.0",
            "X-App-Build-Number": "8284",
            "X-Device-OS-Version": "14",
            "X-Device-Model": "Pixel 8",
            "X-Device-Language": "en",
            "Content-Type": "application/json",
        }

    def _percorso_firmato(self, url: str) -> str:
        """Il percorso dopo la radice del servizio (love, comandi…), o quello intero."""
        for radice in ("/mobile-app/love/v1", "/mobile-app/love-remote-command/v1"):
            dove = url.find(radice + "/")
            if dove >= 0:
                return "/" + url[dove + len(radice) :].lstrip("/")
        return urlparse(url).path

    async def _chiama(
        self,
        cosa: str,
        metodo: str,
        url: str,
        *,
        parametri: dict[str, Any] | None = None,
        corpo: Any = None,
        ripeti: bool = True,
    ) -> Any:
        token = await self._token_valido()
        try:
            async with self._http.request(
                metodo,
                url,
                headers=self._intestazioni(token, self._percorso_firmato(url)),
                params=parametri,
                json=corpo,
                timeout=_TIMEOUT,
            ) as r:
                http = r.status
                testo = await r.text()
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise ErroreRete(f"{cosa}: Lynk & Co non risponde: {err}") from err
        if http == 401 and ripeti:
            async with self._serratura:
                await self._rinnova()
            return await self._chiama(cosa, metodo, url, parametri=parametri, corpo=corpo, ripeti=False)
        if http == 401:
            raise ErroreCredenziali(f"{cosa}: Lynk & Co rifiuta il token")
        if http >= 500 or http == 429:
            raise ErroreRete(f"{cosa}: Lynk & Co ha un guasto o frena (HTTP {http})")
        try:
            dati = json.loads(testo) if testo else {}
        except ValueError:
            dati = {"message": testo[:200]}
        if http >= 400:
            messaggio = dati.get("message") or dati.get("error") or testo[:200] if isinstance(dati, dict) else testo
            raise ErroreApi(cosa, dati.get("code") if isinstance(dati, dict) else None, str(messaggio), http)
        return dati if dati is not None else {}

    # --- leggere ------------------------------------------------------------------------

    async def accedi(self) -> None:
        """Il token, e la sessione del dispositivo come la apre l'app."""
        await self._token_valido()
        try:
            await self._chiama(
                "sessione",
                "POST",
                f"{self.indirizzi['api']}/mobile-app/iamservice/v1/validate-session",
                corpo={"deviceUuid": self.dispositivo, "isLogin": True},
            )
        except ErroreApi as err:
            _LOGGER.debug("La sessione di Lynk & Co non si conferma: %s", err)

    async def vetture(self) -> list[Vettura]:
        r = await self._chiama("elenco delle auto", "GET", f"{self._love}/list/vehicles")
        elenco: list[Vettura] = []
        for voce in (r.get("listOfVehicles") if isinstance(r, dict) else None) or []:
            auto = voce.get("vehicle") if isinstance(voce, dict) else None
            if not isinstance(auto, dict) or not auto.get("vin"):
                continue
            codice = str(auto.get("model") or "")
            modello = MODELLI.get(codice, codice or "Lynk & Co")
            elenco.append(
                Vettura(
                    vin=str(auto["vin"]),
                    marca=ID,
                    modello=modello,
                    nome=f"Lynk & Co {modello}" if codice in MODELLI else "Lynk & Co",
                    extra={"codice_modello": codice},
                )
            )
        return elenco

    async def _leggi(self, v: Vettura, pezzo: str) -> dict[str, Any]:
        r = await self._chiama(pezzo, "GET", f"{self._love}/vehicle/{v.vin}/{pezzo}")
        return r if isinstance(r, dict) else {}

    async def _facoltativo(self, v: Vettura, pezzo: str) -> dict[str, Any]:
        try:
            return await self._leggi(v, pezzo)
        except ErroreApi as err:
            _LOGGER.debug("%s della Lynk & Co non letto: %s", pezzo, err)
        except ErroreRete as err:
            _LOGGER.debug("%s della Lynk & Co non letto: %s", pezzo, err)
        return {}

    async def _meta(self, v: Vettura) -> dict[str, Any]:
        """Contachilometri, capacità e motorizzazione: cambiano piano, si rileggono ogni mezz'ora."""
        meta, quando = self._metadati.get(v.vin, ({}, 0.0))
        if quando and time.monotonic() - quando < _METADATI_OGNI_S:
            return meta
        nuovo = await self._facoltativo(v, "vehicle_metadata")
        meta = nuovo or meta
        self._metadati[v.vin] = (meta, time.monotonic())
        if tipo := (meta.get("vehicle") or {}).get("propulsionType"):
            self._propulsione[v.vin] = str(tipo).upper()
        return meta

    async def stato(self, v: Vettura) -> StatoVettura:
        pezzi: dict[str, Any] = {"charge": await self._leggi(v, "charge_state")}
        pezzi["metadata"] = await self._meta(v)
        for chiave, pezzo in (
            ("vehicle_data", "vehicle_data"),
            ("location", "location_state"),
            ("climate", "climate_state"),
            ("doors", "doors_windows_state"),
        ):
            pezzi[chiave] = await self._facoltativo(v, pezzo)
        if self._propulsione.get(v.vin) != "BEV":
            pezzi["fuel"] = await self._facoltativo(v, "fuel_state")
        return interpreta(pezzi)

    # --- comandare ---------------------------------------------------------------------------

    async def _comanda(self, v: Vettura, cosa: str, comando: str, parametri: dict[str, Any] | None = None) -> None:
        async with self._comando:
            try:
                r = await self._chiama(
                    cosa, "POST", f"{self._comandi}/vehicle/{v.vin}/command/{comando}", parametri=parametri
                )
            except ErroreApi as err:
                raise ErroreComando(f"La Lynk & Co non esegue «{cosa}»: {err.messaggio or err.http}") from err
        esito = str(r.get("status") or r.get("result") or "") if isinstance(r, dict) else ""
        if esito.upper() in ("FAILED", "REJECTED", "ERROR"):
            raise ErroreComando(f"La Lynk & Co non esegue «{cosa}»: {esito}")

    async def chiudi(self, v: Vettura, _pin: str | None) -> None:
        await self._comanda(v, "chiudi", "door_lock")

    async def apri(self, v: Vettura, _pin: str | None) -> None:
        await self._comanda(v, "apri", "door_unlock")

    async def trova(self, v: Vettura, _pin: str | None) -> None:
        await self._comanda(v, "trova", "flash_lights")

    async def clima(self, v: Vettura, acceso: bool, _pin: str | None, temperatura: int = 22) -> None:
        if acceso:
            gradi = min(max(int(temperatura), CLIMA_MIN), CLIMA_MAX)
            await self._comanda(v, "clima", "auto_conditioning_start", {"temp": gradi})
        else:
            await self._comanda(v, "clima", "auto_conditioning_stop")

    async def ricarica(self, v: Vettura, avvia: bool, _pin: str | None) -> None:
        await self._comanda(v, "ricarica", "charge_start" if avvia else "charge_stop")

    async def limite_ricarica(self, v: Vettura, percento: int, _pin: str | None) -> None:
        if not 50 <= percento <= 100:
            raise ErroreComando("La Lynk & Co accetta un limite fra 50 e 100 %")
        # Il cloud vuole passi di 5 (ha_lynkco_2025).
        await self._comanda(v, "limite di ricarica", "set_charge_limit", {"percent": round(percento / 5) * 5})
