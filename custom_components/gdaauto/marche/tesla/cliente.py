"""Il filo con la Fleet API di Tesla: l'API ufficiale, con OAuth.

Il token lo tiene Home Assistant (credenziali applicative): al cliente si
passa una funzione che restituisce un token valido, e lui non sa altro.

Due regole che costano soldi a chi ha l'app sviluppatore:

- **non si sveglia mai l'auto per leggerla.** Si chiede prima com'è
  (``/vehicles/{vin}``): se dorme si tengono gli ultimi dati, e i
  ``vehicle_data`` si chiedono solo da sveglia;
- **si sveglia solo per un comando**, e solo se serve.

Le auto costruite dal 2021 vogliono i comandi firmati (Vehicle Command
Protocol). Il cliente li manda al proxy ufficiale di Tesla
(``tesla-http-proxy``), se c'è, che li firma con la chiave dell'auto; senza
proxy li manda alla Fleet API, che li accetta solo dalle auto più vecchie.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging
from typing import Any

import aiohttp

from ...vettura import StatoVettura, Vettura
from ..errori import AutoAddormentata, ErroreApi, ErroreComando, ErroreCredenziali, ErroreRete
from .dati import interpreta

_LOGGER = logging.getLogger(__name__)

ID = "tesla"
NOME = "Tesla"

SERVER = {
    "na": "https://fleet-api.prd.na.vn.cloud.tesla.com",
    "eu": "https://fleet-api.prd.eu.vn.cloud.tesla.com",
    "cn": "https://fleet-api.prd.cn.vn.cloud.tesla.cn",
}

_ENDPOINTS = "charge_state;climate_state;drive_state;location_data;vehicle_state;vehicle_config"
_TIMEOUT = aiohttp.ClientTimeout(total=30)
_SVEGLIA_ATTESA = 45.0

# Le risposte «no» che vogliono dire «è già così».
_GIA_COSI = {"already_set", "is_charging", "not_charging", "complete", "charging_complete"}

COMANDI = {"chiudi", "trova", "clima", "ricarica", "limite_ricarica", "destinazione"}

FIRMA_RICHIESTA = (
    "Questa Tesla vuole i comandi firmati: indica nelle opzioni l'indirizzo del proxy ufficiale "
    "tesla-http-proxy, oppure comandala dall'integrazione Tesla Fleet di Home Assistant."
)


def comandi_di(_v: Vettura) -> set[str]:
    return set(COMANDI)


def _modello(vin: str, config: dict[str, Any]) -> str:
    tipo = str(config.get("car_type") or "").lower()
    nomi = {
        "models": "Model S",
        "model3": "Model 3",
        "modelx": "Model X",
        "modely": "Model Y",
        "cybertruck": "Cybertruck",
    }
    if tipo in nomi:
        return nomi[tipo]
    # Il quarto carattere del VIN dice il modello.
    return {"S": "Model S", "3": "Model 3", "X": "Model X", "Y": "Model Y", "C": "Cybertruck"}.get(
        vin[3:4].upper(), "Tesla"
    )


class ClienteTesla:
    def __init__(
        self,
        sessione_http: aiohttp.ClientSession,
        token: Callable[[], Awaitable[str]],
        regione: str,
        *,
        proxy: str = "",
        proxy_verifica: bool = True,
    ) -> None:
        self._http = sessione_http
        self._token = token
        self.base_url = SERVER.get(regione, SERVER["eu"])
        self.proxy = proxy.rstrip("/")
        self._proxy_ssl: bool | None = None if proxy_verifica else False
        self._ultimo: dict[str, StatoVettura] = {}

    # --- la rete ---------------------------------------------------------

    async def _chiedi(
        self, cosa: str, metodo: str, percorso: str, *, json: Any = None, comando: bool = False
    ) -> dict[str, Any]:
        base = self.proxy if (comando and self.proxy) else self.base_url
        ssl = self._proxy_ssl if (comando and self.proxy) else None
        try:
            intestazioni = {"Authorization": f"Bearer {await self._token()}", "Content-Type": "application/json"}
        except aiohttp.ClientResponseError as err:
            # Tesla ha detto no al rinnovo: serve rientrare. Un 5xx è solo un guasto.
            if err.status in (400, 401, 403):
                raise ErroreCredenziali(f"Tesla non rinnova il token: {err.status}") from err
            raise ErroreRete(f"Il rinnovo del token non riesce: {err.status}") from err
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise ErroreRete(f"Il rinnovo del token non riesce: {err}") from err
        try:
            async with self._http.request(
                metodo, f"{base}/{percorso}", json=json, headers=intestazioni, timeout=_TIMEOUT, ssl=ssl
            ) as r:
                http = r.status
                try:
                    corpo = await r.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError):
                    corpo = {"error": (await r.text())[:200]}
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise ErroreRete(f"{cosa}: Tesla non risponde: {err}") from err
        if not isinstance(corpo, dict):
            corpo = {"response": corpo}
        if http == 401:
            raise ErroreCredenziali(f"{cosa}: Tesla rifiuta il token")
        if http == 408:
            raise AutoAddormentata(f"{cosa}: l'auto dorme")
        if http >= 400:
            messaggio = str(corpo.get("error") or corpo.get("error_description") or "")
            if "command protocol" in messaggio.lower() or "signed" in messaggio.lower():
                raise ErroreComando(FIRMA_RICHIESTA)
            raise ErroreApi(cosa, corpo.get("error"), messaggio, http)
        return corpo

    # --- leggere ----------------------------------------------------------

    async def accedi(self) -> None:
        await self.vetture()

    async def vetture(self) -> list[Vettura]:
        r = await self._chiedi("elenco delle auto", "GET", "api/1/vehicles")
        elenco = []
        for voce in r.get("response") or []:
            if not isinstance(voce, dict) or not voce.get("vin"):
                continue
            vin = str(voce["vin"])
            elenco.append(
                Vettura(
                    vin=vin,
                    marca=ID,
                    modello=_modello(vin, voce.get("vehicle_config") or {}),
                    nome=str(voce.get("display_name") or ""),
                    extra={"id": voce.get("id"), "stato": voce.get("state")},
                )
            )
        return elenco

    async def stato(self, v: Vettura) -> StatoVettura:
        r = await self._chiedi("stato dell'auto", "GET", f"api/1/vehicles/{v.vin}")
        stato_cloud = str((r.get("response") or {}).get("state") or "")
        if stato_cloud != "online":
            return self._addormentata(v)
        try:
            r = await self._chiedi(
                "dati dell'auto", "GET", f"api/1/vehicles/{v.vin}/vehicle_data?endpoints={_ENDPOINTS}"
            )
        except AutoAddormentata:
            return self._addormentata(v)
        s = interpreta(r.get("response") or {})
        self._ultimo[v.vin] = s
        return s

    def _addormentata(self, v: Vettura) -> StatoVettura:
        """Gli ultimi dati, con l'auto che dorme: niente sveglia per leggerla."""
        ultimo = self._ultimo.get(v.vin) or v.stato
        if ultimo is None:
            # Appena installata, con l'auto che dorme: uno stato vuoto, e le
            # entità nascono alla prima lettura da sveglia.
            return StatoVettura(in_sonno=True)
        ultimo.in_sonno = True
        ultimo.velocita_kmh = 0.0 if ultimo.velocita_kmh is not None else None
        return ultimo

    # --- comandare --------------------------------------------------------

    async def _sveglia(self, v: Vettura) -> None:
        r = await self._chiedi("sveglia", "POST", f"api/1/vehicles/{v.vin}/wake_up")
        scadenza = asyncio.get_running_loop().time() + _SVEGLIA_ATTESA
        while (r.get("response") or {}).get("state") != "online":
            if asyncio.get_running_loop().time() > scadenza:
                raise ErroreComando(f"{v.titolo} non si è svegliata in tempo")
            await asyncio.sleep(3)
            r = await self._chiedi("sveglia", "GET", f"api/1/vehicles/{v.vin}")

    async def comanda(self, v: Vettura, nome: str, json: dict[str, Any] | None = None) -> None:
        percorso = f"api/1/vehicles/{v.vin}/command/{nome}"
        try:
            r = await self._chiedi(f"comando {nome}", "POST", percorso, json=json or {}, comando=True)
        except AutoAddormentata:
            await self._sveglia(v)
            r = await self._chiedi(f"comando {nome}", "POST", percorso, json=json or {}, comando=True)
        esito = r.get("response") or {}
        if esito.get("result") is False and str(esito.get("reason") or "") not in _GIA_COSI:
            raise ErroreComando(f"Tesla non esegue {nome}: {esito.get('reason') or 'nessun motivo'}")

    async def chiudi(self, v: Vettura, _pin: str | None) -> None:
        await self.comanda(v, "door_lock")

    async def apri(self, v: Vettura, _pin: str | None) -> None:
        await self.comanda(v, "door_unlock")

    async def trova(self, v: Vettura, _pin: str | None) -> None:
        await self.comanda(v, "honk_horn")
        await self.comanda(v, "flash_lights")

    async def clima(self, v: Vettura, acceso: bool, _pin: str | None, temperatura: int = 22) -> None:
        if not acceso:
            await self.comanda(v, "auto_conditioning_stop")
            return
        await self.comanda(v, "auto_conditioning_start")
        gradi = max(15, min(28, temperatura))
        await self.comanda(v, "set_temps", {"driver_temp": gradi, "passenger_temp": gradi})

    async def ricarica(self, v: Vettura, avvia: bool, _pin: str | None) -> None:
        await self.comanda(v, "charge_start" if avvia else "charge_stop")

    async def limite_ricarica(self, v: Vettura, percento: int, _pin: str | None) -> None:
        await self.comanda(v, "set_charge_limit", {"percent": int(max(50, min(100, percento)))})

    async def destinazione(self, v: Vettura, nome: str, lat: float, lon: float, indirizzo: str = "") -> None:
        await self.comanda(v, "navigation_gps_request", {"lat": lat, "lon": lon, "order": 0})
