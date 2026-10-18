"""BMW e MINI, da BMW CarData: l'API ufficiale e gratuita di BMW per i clienti.

Si entra col Client ID che chi configura crea nel portale MyBMW / MINI
(sezione CarData) e con un codice confermato sul sito di BMW: nessuna password
passa da GdAAuto. Si legge soltanto, con al massimo 50 chiamate al giorno per
account: vedi ``cliente.py``.

Gli indirizzi e il protocollo sono quelli della documentazione di BMW
CarData; per i dettagli si sono letti JustChr/BavarianData (MIT) e
kvanbiesen/bmw-cardata-ha (BSD): vedi RINGRAZIAMENTI.md.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..base import ACCESSO_DISPOSITIVO, Accesso, Campo, Dispositivo
from .cliente import ID, NOME, ClienteBmw, chi_e, chiedi_codice, comandi_di, nota_di, scambia_codice

ACCESSO = ACCESSO_DISPOSITIVO
CAMPI: tuple[Campo, ...] = (Campo("client_id"),)
NOTA = {
    "it": (
        "Nel portale MyBMW (o MINI) apri «CarData», crea un Client ID e attiva sia «CarData API» sia "
        "«CarData Stream», poi incollalo qui. BMW concede 50 letture al giorno per account: "
        "GdAAuto legge ogni mezz'ora e da sola rallenta se le auto sono più di una."
    ),
    "en": (
        "In the MyBMW (or MINI) portal open “CarData”, create a Client ID and subscribe it to both "
        "“CarData API” and “CarData Stream”, then paste it here. BMW allows 50 reads a day per account: "
        "GdAAuto reads every half hour and slows down by itself when there is more than one car."
    ),
}
VUOLE_PIN = False
#: CarData si legge soltanto.
SENZA_COMANDI = True
#: 50 chiamate al giorno: una lettura ogni mezz'ora ne usa 48 per un'auto.
INTERVALLO_MINUTI = 30
#: In marcia o in carica non si legge più spesso: la quota è la stessa.
INTERVALLO_IN_MOTO_SECONDI = INTERVALLO_MINUTI * 60

CONF_CLIENT_ID = "client_id"
CONF_TOKEN = "token"
CONF_CONTAINER = "container_id"
CONF_RIDOTTO = "container_ridotto"
CONF_VETTURE = "vetture"

_MARCHI = {"bmw": "BMW", "mini": "MINI"}


async def avvia_dispositivo(hass: HomeAssistant, dati: dict[str, Any]) -> Dispositivo:
    codice = await chiedi_codice(async_get_clientsession(hass), str(dati[CONF_CLIENT_ID]).strip())
    return Dispositivo(codice.pop("link"), codice.pop("codice"), codice)


async def completa_dispositivo(hass: HomeAssistant, dati: dict[str, Any], dispositivo: Dispositivo) -> Accesso:
    """Si prova lo scambio una volta (``InAttesa`` se non è confermato), poi le auto e il container."""
    http = async_get_clientsession(hass)
    client_id = str(dati[CONF_CLIENT_ID]).strip()
    token = await scambia_codice(http, client_id, dispositivo.stato)
    cliente = ClienteBmw(
        http,
        client_id,
        token,
        container_id=dati.get(CONF_CONTAINER),
        ridotto=bool(dati.get(CONF_RIDOTTO)),
    )
    vetture = await cliente.leggi_vetture()
    if vetture:
        await cliente.prepara_container()
    chi = chi_e(token)
    marchio = _MARCHI.get(str(dati.get("marchio") or ""), "BMW")
    account = chi.get("gcid") or chi.get("email") or client_id
    return Accesso(
        dati={
            **dati,
            CONF_CLIENT_ID: client_id,
            CONF_TOKEN: cliente.token,
            CONF_CONTAINER: cliente.container_id,
            CONF_RIDOTTO: cliente.ridotto,
            CONF_VETTURE: [nota_di(v) for v in vetture],
        },
        vetture=vetture,
        unique_id=f"{ID}_{account.lower()}",
        titolo=f"{marchio} · {chi.get('email') or f'{len(vetture)} auto'}",
    )


async def crea_da_voce(hass: HomeAssistant, entry: ConfigEntry, _cartella: str) -> ClienteBmw:
    @callback
    def salva(cambi: dict[str, Any]) -> None:
        # Token che ruotano e id del container: cambiano i dati, non le opzioni, e non si ricarica.
        hass.config_entries.async_update_entry(entry, data={**entry.data, **cambi})

    return ClienteBmw(
        async_get_clientsession(hass),
        entry.data[CONF_CLIENT_ID],
        entry.data[CONF_TOKEN],
        salva,
        container_id=entry.data.get(CONF_CONTAINER),
        ridotto=bool(entry.data.get(CONF_RIDOTTO)),
        vetture_note=entry.data.get(CONF_VETTURE),
    )


__all__ = [
    "ACCESSO",
    "CAMPI",
    "ID",
    "INTERVALLO_IN_MOTO_SECONDI",
    "INTERVALLO_MINUTI",
    "NOME",
    "NOTA",
    "SENZA_COMANDI",
    "VUOLE_PIN",
    "ClienteBmw",
    "avvia_dispositivo",
    "comandi_di",
    "completa_dispositivo",
    "crea_da_voce",
]
