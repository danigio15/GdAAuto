"""Il filo col Connected Car API v4 di Stellantis (api.groupe-psa.com).

Si entra con l'OAuth dell'app del marchio (MyPeugeot, MyCitroën, MyDS,
MyOpel, MyVauxhall). Il login vero lo fa chi configura, nel suo browser: il
codice che il sito restituisce si incolla in Home Assistant. Nessuna email e
nessuna password passano da GdAAuto o da un server di altri.

Il cloud ruota il refresh token a ogni rinnovo: chi crea il cliente gli dà
una funzione ``salva`` che conserva i token nuovi nella configurazione.

Per ora si legge soltanto. I comandi del gruppo passano da MQTT con un codice
monouso via SMS, e non ci sono ancora.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import aiohttp

from ...vettura import StatoVettura, Vettura
from ..errori import ErroreApi, ErroreCredenziali, ErroreRete
from .dati import interpreta

_LOGGER = logging.getLogger(__name__)

ID = "stellantis"
NOME = "Stellantis"

API = "https://api.groupe-psa.com"
_TIMEOUT = aiohttp.ClientTimeout(total=30)
_MARGINE = timedelta(minutes=2)

APP: dict[str, dict[str, Any]] = json.loads((Path(__file__).parent / "app.json").read_text())

MARCHI = {
    "MyPeugeot": "Peugeot",
    "MyCitroen": "Citroën",
    "MyDS": "DS",
    "MyOpel": "Opel",
    "MyVauxhall": "Vauxhall",
}


def comandi_di(_v: Vettura) -> set[str]:
    return set()


def configurazione(app: str, paese: str) -> dict[str, str]:
    """Indirizzo, realm, schema e credenziali dell'app per quel paese."""
    a = APP[app]
    c = a["paesi"][paese]
    return {
        "oauth_url": a["oauth_url"],
        "realm": a["realm"],
        "redirect_uri": f"{a['scheme']}://oauth2redirect/{paese.lower()}",
        "locale": c["locale"],
        "client_id": c["client_id"],
        "client_secret": c["client_secret"],
    }


def indirizzo_login(app: str, paese: str) -> str:
    c = configurazione(app, paese)
    q = {
        "client_id": c["client_id"],
        "response_type": "code",
        "redirect_uri": c["redirect_uri"],
        "scope": "openid profile email",
        "locale": c["locale"],
    }
    return f"{c['oauth_url']}/am/oauth2/authorize?{urlencode(q)}"


def codice_da(testo: str) -> str:
    """Il codice, sia che si incolli il codice sia l'indirizzo intero
    (``mymap://oauth2redirect/it?code=…``)."""
    testo = testo.strip()
    if "code=" in testo:
        q = parse_qs(urlparse(testo).query or testo.split("?", 1)[-1])
        if q.get("code"):
            return q["code"][0]
    return testo


class ClienteStellantis:
    def __init__(
        self,
        sessione_http: aiohttp.ClientSession,
        app: str,
        paese: str,
        token: dict[str, Any],
        salva: Callable[[dict[str, Any]], None] | None = None,
        *,
        api: str = API,
    ) -> None:
        self._http = sessione_http
        self.api = api
        self.app = app
        self.config = configurazione(app, paese)
        self.token = dict(token)
        self._salva = salva
        self._serratura = asyncio.Lock()
        self._vetture_id: dict[str, str] = {}

    # --- i token ------------------------------------------------------------

    @property
    def _basic(self) -> str:
        coppia = f"{self.config['client_id']}:{self.config['client_secret']}".encode()
        return "Basic " + base64.b64encode(coppia).decode()

    async def _token(self, parametri: dict[str, str]) -> dict[str, Any]:
        url = f"{self.config['oauth_url']}/am/oauth2/access_token?{urlencode(parametri)}"
        intestazioni = {"Content-Type": "application/x-www-form-urlencoded", "Authorization": self._basic}
        try:
            async with self._http.post(url, headers=intestazioni, timeout=_TIMEOUT) as r:
                corpo = await r.json(content_type=None)
                http = r.status
        except (aiohttp.ClientError, TimeoutError, OSError, ValueError) as err:
            raise ErroreRete(f"Il login di Stellantis non risponde: {err}") from err
        if http >= 500:
            raise ErroreRete(f"Il login di Stellantis ha un guasto: {http}")
        if http in (400, 401) or not isinstance(corpo, dict) or "access_token" not in corpo:
            descrizione = corpo.get("error_description") if isinstance(corpo, dict) else ""
            raise ErroreCredenziali(f"Stellantis rifiuta l'accesso: {descrizione or http}")
        scade = datetime.now(UTC) + timedelta(seconds=int(corpo.get("expires_in") or 3600))
        return {
            "access_token": corpo["access_token"],
            "refresh_token": corpo.get("refresh_token") or self.token.get("refresh_token"),
            "scade": scade.isoformat(),
        }

    async def scambia_codice(self, codice: str) -> dict[str, Any]:
        """Dal codice del login ai token. Una volta sola, nella configurazione."""
        self.token = await self._token(
            {"redirect_uri": self.config["redirect_uri"], "grant_type": "authorization_code", "code": codice}
        )
        return self.token

    async def _token_valido(self) -> str:
        async with self._serratura:
            scade = self.token.get("scade")
            if not scade or datetime.fromisoformat(scade) - _MARGINE < datetime.now(UTC):
                await self._rinnova()
            return str(self.token["access_token"])

    async def _rinnova(self) -> None:
        if not self.token.get("refresh_token"):
            raise ErroreCredenziali("Manca il refresh token di Stellantis")
        self.token = await self._token({"grant_type": "refresh_token", "refresh_token": self.token["refresh_token"]})
        if self._salva:
            self._salva(dict(self.token))

    # --- la rete -------------------------------------------------------------

    async def _leggi(self, cosa: str, percorso: str, ripeti: bool = True) -> dict[str, Any]:
        intestazioni = {
            "Authorization": f"Bearer {await self._token_valido()}",
            "x-introspect-realm": self.config["realm"],
            "Accept": "application/hal+json",
        }
        url = f"{self.api}/connectedcar/v4/user{percorso}?{urlencode({'client_id': self.config['client_id']})}"
        try:
            async with self._http.get(url, headers=intestazioni, timeout=_TIMEOUT) as r:
                http = r.status
                testo = await r.text()
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise ErroreRete(f"{cosa}: Stellantis non risponde: {err}") from err
        try:
            corpo = json.loads(testo) if testo else {}
        except ValueError:
            corpo = {"message": testo[:200]}
        if http == 401 and ripeti:
            # Un token appena ruotato a volte non vale ancora: si rinnova e si riprova una volta.
            async with self._serratura:
                await self._rinnova()
            return await self._leggi(cosa, percorso, ripeti=False)
        if http == 401:
            raise ErroreCredenziali(f"{cosa}: Stellantis rifiuta il token")
        if http == 404 and str(corpo.get("code")) == "40400":
            return {}  # lo stato di quell'auto, per ora, non c'è
        if http >= 400:
            raise ErroreApi(cosa, corpo.get("code"), str(corpo.get("message") or corpo), http)
        return corpo if isinstance(corpo, dict) else {}

    # --- leggere -----------------------------------------------------------------

    async def accedi(self) -> None:
        await self._token_valido()

    async def vetture(self) -> list[Vettura]:
        r = await self._leggi("elenco delle auto", "/vehicles")
        elenco = []
        for voce in (r.get("_embedded") or {}).get("vehicles") or []:
            if not isinstance(voce, dict) or not voce.get("vin"):
                continue
            self._vetture_id[str(voce["vin"])] = str(voce["id"])
            marchio = str(voce.get("brand") or MARCHI.get(self.app, "Stellantis"))
            elenco.append(
                Vettura(
                    vin=str(voce["vin"]),
                    marca=ID,
                    modello=marchio,
                    # Il cloud non dà né il nome né il modello: si parte dal marchio, come l'app.
                    nome=marchio,
                    extra={"id": voce["id"], "motorizzazione": voce.get("motorization")},
                )
            )
        return elenco

    async def stato(self, v: Vettura) -> StatoVettura:
        id_ = v.extra.get("id") or self._vetture_id.get(v.vin)
        return interpreta(await self._leggi("stato dell'auto", f"/vehicles/{id_}/status"))
