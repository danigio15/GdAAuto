"""BYD: l'app «overseas» (DiLink) per Dolphin, Atto 3, Seal, Seal U, Sealion, Han, Tang.

Il cloud BYD avvolge ogni richiesta in una busta cifrata da una libreria
offuscata dell'app («Bangcle»), un AES a scatola bianca che non usa una chiave
ma otto grandi tabelle di ricerca estratte dalla libreria nativa dell'app
(``libencrypt.so``). L'algoritmo è un fatto del protocollo, riscritto in
``busta.py``; **le tabelle no**: sono materiale crittografico di BYD, estratto
dall'app, e non esiste una licenza per ridistribuirle. Per questo GdAAuto non le
contiene — come non contiene il certificato dell'app Leapmotor.

Se in ``/config/gdaauto/`` non c'è già il file delle tabelle, GdAAuto lo
scarica dal repository pubblico di pyBYD (``bangcle_tables.bin``, a un commit
fisso e con l'impronta SHA-256 controllata) e lo salva lì; chi preferisce il
proprio, estratto dalla propria app, lo mette in
``/config/gdaauto/bangcle_tables.bin``.

Tutto il resto è nativo e da fatti noti: il login JSON con email e password
(non una pagina web), il rinnovo della sessione, la lettura di tutti gli
endpoint e i comandi.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from ..base import ACCESSO_CAMPI, Accesso, Campo
from ..errori import ErroreCertificato
from .busta import Busta, ErroreTabelle, carica_tabelle
from .cliente import BASE_URL, ID, NOME, ClienteByd, comandi_di, nodo_di_paese

ACCESSO = ACCESSO_CAMPI

#: Dove l'utente mette il file delle tabelle Bangcle (GdAAuto non lo contiene).
CARTELLA = "gdaauto"
NOMI_FILE = ("bangcle_tables.bin", "byd_bangcle_tables.bin")
#: Le tabelle pubblicate da jkaberg/pyBYD, a un commit fisso, e la loro impronta.
URL_TABELLE = (
    "https://raw.githubusercontent.com/jkaberg/pyBYD/995ef70a2b6bd287d58c1f8564e6b50eabc7f868"
    "/src/pybyd/data/bangcle_tables.bin"
)
SHA256_TABELLE = "5d7577f6298441a393898a6283b043495f3918f80d86c41191d9cf48a1c99a1f"

CONF_TOKEN = "token"

#: Le regioni DiLink principali; fuori da queste si usa il nodo europeo.
_PAESI = (
    ("IT", "Italia"),
    ("DE", "Germania"),
    ("FR", "Francia"),
    ("ES", "Spagna"),
    ("GB", "Regno Unito"),
    ("NL", "Paesi Bassi"),
    ("BE", "Belgio"),
    ("NO", "Norvegia"),
    ("SE", "Svezia"),
    ("DK", "Danimarca"),
    ("PL", "Polonia"),
    ("AT", "Austria"),
    ("PT", "Portogallo"),
    ("IE", "Irlanda"),
    ("CH", "Svizzera"),
    ("AU", "Australia"),
    ("SG", "Singapore"),
    ("BR", "Brasile"),
    ("JP", "Giappone"),
    ("MX", "Messico"),
    ("ID", "Indonesia"),
    ("TR", "Turchia"),
    ("KR", "Corea del Sud"),
    ("IN", "India"),
)

CAMPI = (
    Campo("email"),
    Campo("password", segreto=True),
    Campo("paese", scelte=_PAESI, predefinito="IT"),
    Campo("pin", obbligatorio=False, segreto=True),
)

NOTA = {
    "it": (
        "Funziona con l'app **BYD** internazionale (fuori dalla Cina). Le tabelle crittografiche dell'app "
        "GdAAuto le scarica da sola dal repository pubblico di pyBYD e le salva in "
        "`/config/gdaauto/bangcle_tables.bin`; se preferisci le tue, estratte dalla tua app, mettile lì. "
        "Usa un account **dedicato**, con l'auto "
        "condivisa dall'account principale: il cloud BYD può tenere una sola sessione per account. "
        "Il **PIN** è quello dei comandi dell'app (chiudi/apri/clima/ricarica): senza, GdAAuto legge "
        "soltanto. Leggere l'auto la risveglia (interroga il T-Box): l'intervallo è tenuto prudente."
    ),
    "en": (
        "Works with the international **BYD** app (outside China). GdAAuto downloads the app's "
        "cryptographic tables by itself from the public pyBYD repository and saves them in "
        "`/config/gdaauto/bangcle_tables.bin`; if you prefer your own, extracted from your app, put them "
        "there. Use a **dedicated** account with the car "
        "shared from your main account: the BYD cloud keeps a single session per account. The **PIN** "
        "is the app's command PIN (lock/unlock/climate/charge): without it GdAAuto only reads. Reading "
        "the car wakes it up (it queries the T-Box), so the interval is kept conservative."
    ),
}

VUOLE_PIN = True
#: Leggere l'auto interroga il T-Box: prudenza, per non svegliarla di continuo né scaricarla.
INTERVALLO_MINUTI = 5
#: In marcia o in carica si guarda più spesso, ma mai sotto il minuto.
INTERVALLO_IN_MOTO_SECONDI = 60
INTERVALLO_MINIMO_SECONDI = 60


def _percorso_tabelle(hass: HomeAssistant) -> Path | None:
    cartella = Path(hass.config.path(CARTELLA))
    for nome in NOMI_FILE:
        if (cartella / nome).is_file():
            return cartella / nome
    return None


def _leggi_busta(percorso: Path) -> Busta:
    """Legge il file delle tabelle e prepara la busta. Bloccante: va nell'executor."""
    try:
        return Busta(carica_tabelle(percorso.read_bytes()))
    except (OSError, ErroreTabelle) as err:
        raise ErroreCertificato(f"Il file delle tabelle BYD non si apre: {err}") from err


def _scrivi(percorso: Path, contenuto: bytes) -> None:
    percorso.parent.mkdir(parents=True, exist_ok=True)
    provvisorio = percorso.with_suffix(".scarico")
    provvisorio.write_bytes(contenuto)
    provvisorio.replace(percorso)


async def _scarica_tabelle(hass: HomeAssistant) -> Path:
    """Le tabelle dal repository pubblico di pyBYD, salvate in /config/gdaauto/."""
    try:
        async with async_get_clientsession(hass).get(URL_TABELLE, timeout=aiohttp.ClientTimeout(total=60)) as r:
            r.raise_for_status()
            contenuto = await r.read()
    except (aiohttp.ClientError, TimeoutError) as err:
        raise ErroreCertificato(
            f"Le tabelle BYD non si scaricano ({err}): riprova, o mettile in /config/gdaauto/bangcle_tables.bin"
        ) from err
    if hashlib.sha256(contenuto).hexdigest() != SHA256_TABELLE:
        raise ErroreCertificato("Le tabelle BYD scaricate non sono quelle attese: non si usano")
    percorso = Path(hass.config.path(CARTELLA)) / NOMI_FILE[0]
    await hass.async_add_executor_job(_scrivi, percorso, contenuto)
    return percorso


async def _busta_da_file(hass: HomeAssistant) -> Busta:
    percorso = _percorso_tabelle(hass) or await _scarica_tabelle(hass)
    return await hass.async_add_executor_job(_leggi_busta, percorso)


def _crea(
    hass: HomeAssistant,
    dati: dict[str, Any],
    busta: Busta,
    salva: Any = None,
) -> ClienteByd:
    return ClienteByd(
        async_get_clientsession(hass),
        email=dati["email"],
        password=dati["password"],
        busta=busta,
        pin=dati.get("pin") or "",
        paese=dati.get("paese") or "",
        fuso=str(dt_util.get_default_time_zone()),
        base_url=dati.get("base_url") or nodo_di_paese(dati.get("paese") or ""),
        token=dati.get(CONF_TOKEN),
        salva=salva,
        esecutore=hass.async_add_executor_job,
    )


async def prova_accesso(hass: HomeAssistant, dati: dict[str, Any]) -> Accesso:
    """Entra davvero, elenca le auto, e (se c'è un PIN) lo verifica subito."""
    busta = await _busta_da_file(hass)
    dati = {k: v for k, v in dati.items() if k != CONF_TOKEN}  # la prova non riusa vecchie sessioni
    cliente = _crea(hass, dati, busta)
    await cliente.accedi()
    vetture = await cliente.vetture()
    if (pin := str(dati.get("pin") or "").strip()) and vetture:
        await cliente.verifica_pin(_vettura_con_comandi(vetture), pin)
    email = str(dati["email"]).strip()
    dati[CONF_TOKEN] = cliente.sessione
    return Accesso(dati=dati, vetture=vetture, unique_id=f"{ID}_{email.lower()}", titolo=f"{NOME} · {email}")


def _vettura_con_comandi(vetture: list[Any]) -> Any:
    return next((v for v in vetture if comandi_di(v)), vetture[0])


async def crea_da_voce(hass: HomeAssistant, entry: ConfigEntry, _cartella: str) -> ClienteByd:
    busta = await _busta_da_file(hass)

    @callback
    def salva(token: dict[str, Any]) -> None:
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_TOKEN: token})

    return _crea(hass, dict(entry.data), busta, salva)


__all__ = [
    "ACCESSO",
    "BASE_URL",
    "CAMPI",
    "ID",
    "INTERVALLO_IN_MOTO_SECONDI",
    "INTERVALLO_MINUTI",
    "NOME",
    "NOTA",
    "VUOLE_PIN",
    "ClienteByd",
    "comandi_di",
    "crea_da_voce",
    "prova_accesso",
]
