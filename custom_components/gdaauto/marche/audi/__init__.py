"""Audi: le e-tron (Q4, Q6, Q8, A6, e-tron GT) e le TFSI e, dal cloud dell'app myAudi.

Si entra dal browser, come l'app: GdAAuto prepara la pagina di login di Audi
(PKCE), chi configura entra col suo account myAudi nel suo browser e incolla
l'indirizzo finale ``myaudi:///?code=…`` (o solo il codice). GdAAuto scambia
il codice coi token e poi rinnova da sola il refresh token, che ruota e si
salva nella voce senza ricaricarla. Email e password restano nel browser.

Lo stato si legge ogni quarto d'ora dal BFF di CARIAD (una chiamata per lo
stato, una per la posizione), senza svegliare l'auto; i comandi sono chiudi e
apri con l'S-PIN di myAudi, clima, ricarica, limite di ricarica e lampeggio
delle frecce. Europa, Canada e Stati Uniti; la Cina no.

Il protocollo è quello di audiconnect/audi_connect_ha e di
acfischer42/CarConnectivity-connector-audi (licenza MIT): vedi
RINGRAZIAMENTI.md.

C'è anche il login col codice del dispositivo (``avvia_dispositivo`` e
``completa_dispositivo``): audi_connect_ha dice che in Europa e in Canada Audi
rifiuta ormai lo scambio del codice di login senza la verifica dell'app, e il
codice del dispositivo no. Basta cambiare ``ACCESSO`` per usarlo.
"""

from __future__ import annotations

import secrets
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..base import ACCESSO_CODICE_BROWSER, ACCESSO_DISPOSITIVO, Accesso, Campo, Dispositivo
from ..errori import CodiceSbagliato
from .cliente import ID, NOME, ClienteAudi, comandi_di
from .identita import REGIONI, Identita, chi_e, leggi_incollato, nuovo_pkce

ACCESSO = ACCESSO_CODICE_BROWSER

CONF_REGIONE = "regione"
CONF_TOKEN = "token"

CAMPI = (
    Campo(
        CONF_REGIONE,
        scelte=(
            ("europa", "Europa / Europe"),
            ("canada", "Canada"),
            ("usa", "Stati Uniti / United States"),
        ),
        predefinito="europa",
    ),
    Campo("pin", obbligatorio=False, segreto=True),
)

NOTA = {
    "it": (
        "Entri col tuo account myAudi nel tuo browser (in Europa e in Canada confermando un codice, negli "
        "Stati Uniti incollando l'indirizzo finale): GdAAuto non vede email e password. Il PIN è l'S-PIN di "
        "quattro cifre dell'app, e serve solo per chiudere e aprire (puoi lasciarlo vuoto). Audi conta le "
        "chiamate al giorno: GdAAuto legge ogni quarto d'ora e non sveglia l'auto."
    ),
    "en": (
        "You sign in to your myAudi account in your own browser (in Europe and Canada by confirming a code, in "
        "the United States by pasting the final address): GdAAuto never sees your email or password. The "
        "PIN is the app's four-digit S-PIN, only needed to lock and unlock (you can leave it empty). Audi counts "
        "the daily calls: GdAAuto reads every fifteen minutes and never wakes the car."
    ),
}

#: L'S-PIN di myAudi: il cloud lo vuole per chiudere e aprire.
VUOLE_PIN = True
#: Il cloud conta le chiamate al giorno; audi_connect_ha non scende sotto il quarto d'ora.
INTERVALLO_MINUTI = 15
#: In marcia o in carica ogni cinque minuti: due chiamate a lettura.
INTERVALLO_IN_MOTO_SECONDI = 300


def _regione(dati: dict[str, Any]) -> str:
    regione = str(dati.get(CONF_REGIONE) or "europa")
    return regione if regione in REGIONI else "europa"


#: In Europa e in Canada Audi vuole la verifica dell'app per scambiare il codice
#: del browser: lì si entra col codice del dispositivo; negli Stati Uniti dal browser.
_COL_DISPOSITIVO = frozenset({"europa", "canada"})


def accesso_per(dati: dict[str, Any]) -> str:
    return ACCESSO_DISPOSITIVO if _regione(dati) in _COL_DISPOSITIVO else ACCESSO_CODICE_BROWSER


def _cliente(hass: HomeAssistant, dati: dict[str, Any], salva: Any = None) -> ClienteAudi:
    return ClienteAudi(
        async_get_clientsession(hass), regione=_regione(dati), token=dati.get(CONF_TOKEN) or {}, salva=salva
    )


async def _accesso(hass: HomeAssistant, dati: dict[str, Any], token: dict[str, Any]) -> Accesso:
    """Coi token in mano: le auto dell'account, e cosa salvare."""
    dati = {k: v for k, v in dati.items() if k not in ("codice", CONF_TOKEN)}
    dati[CONF_REGIONE] = _regione(dati)
    cliente = _cliente(hass, {**dati, CONF_TOKEN: token})
    vetture = await cliente.vetture()
    chi = chi_e(cliente.token)
    account = str(chi.get("sub") or chi.get("email") or ",".join(sorted(v.vin for v in vetture)))
    email = str(chi.get("email") or "")
    return Accesso(
        dati={**dati, CONF_TOKEN: dict(cliente.token)},
        vetture=vetture,
        unique_id=f"{ID}_{account.lower()}",
        titolo=f"{NOME} · {email or ', '.join(v.titolo for v in vetture) or 'myAudi'}",
    )


# --- il login dal browser (quello della configurazione) ----------------------------------------


async def avvia_browser(hass: HomeAssistant, dati: dict[str, Any]) -> Dispositivo:
    """La pagina di login di myAudi; nello stato il verifier PKCE e lo ``state`` per riconoscere il ritorno."""
    regione = _regione(dati)
    identita = Identita(async_get_clientsession(hass), REGIONI[regione].bff)
    verifier, sfida = nuovo_pkce()
    state = secrets.token_urlsafe(24)
    link = await identita.indirizzo_login(sfida, state, secrets.token_urlsafe(16), REGIONI[regione].lingua)
    return Dispositivo(link=link, codice_utente="", stato={"verifier": verifier, "state": state})


async def completa_browser(
    hass: HomeAssistant, dati: dict[str, Any], dispositivo: Dispositivo, incollato: str
) -> Accesso:
    """Dall'indirizzo finale (o dal solo codice) ai token, e poi le auto."""
    codice, state = leggi_incollato(incollato)
    if state is not None and state != dispositivo.stato.get("state"):
        raise CodiceSbagliato("Questo indirizzo è di un altro login: riapri il link e riprova")
    identita = Identita(async_get_clientsession(hass), REGIONI[_regione(dati)].bff)
    token = await identita.scambia_codice_login(codice, str(dispositivo.stato["verifier"]))
    return await _accesso(hass, dati, token)


# --- il login col codice del dispositivo (Europa e Canada) ---------------------------------


async def avvia_dispositivo(hass: HomeAssistant, dati: dict[str, Any]) -> Dispositivo:
    identita = Identita(async_get_clientsession(hass), REGIONI[_regione(dati)].bff)
    codice = await identita.chiedi_codice()
    return Dispositivo(codice.pop("link"), codice.pop("codice"), codice)


async def completa_dispositivo(hass: HomeAssistant, dati: dict[str, Any], dispositivo: Dispositivo) -> Accesso:
    """Un tentativo di ritirare i token (``InAttesa`` se il codice non è confermato), poi le auto."""
    identita = Identita(async_get_clientsession(hass), REGIONI[_regione(dati)].bff)
    token = await identita.scambia_codice_dispositivo(str(dispositivo.stato["device_code"]))
    return await _accesso(hass, dati, token)


async def crea_da_voce(hass: HomeAssistant, entry: ConfigEntry, _cartella: str) -> ClienteAudi:
    @callback
    def salva(token: dict[str, Any]) -> None:
        # Il refresh token ruota: cambiano i dati, non le opzioni, e non si ricarica.
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_TOKEN: token})

    return _cliente(hass, dict(entry.data), salva)


__all__ = [
    "ACCESSO",
    "CAMPI",
    "ID",
    "INTERVALLO_IN_MOTO_SECONDI",
    "INTERVALLO_MINUTI",
    "NOME",
    "NOTA",
    "VUOLE_PIN",
    "ClienteAudi",
    "accesso_per",
    "avvia_browser",
    "avvia_dispositivo",
    "comandi_di",
    "completa_browser",
    "completa_dispositivo",
    "crea_da_voce",
]
