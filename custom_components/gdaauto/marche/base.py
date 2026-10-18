"""Il contratto delle marche: come si entra, e cosa si consegna.

Ogni marca nativa è una cartella in ``marche/`` con un ``__init__.py`` che
dichiara questi nomi:

- ``ID``, ``NOME``: come si chiama nel codice e sullo schermo;
- ``ACCESSO``: come si entra, uno di ``ACCESSO_*`` qui sotto;
- ``CAMPI``: cosa chiedere a chi configura (per ``campi`` e ``dispositivo``);
- ``NOTA``: due righe per chi configura, ``{"it": …, "en": …}`` (facoltativa);
- ``VUOLE_PIN``: se i comandi vogliono un PIN a parte, chiesto nelle opzioni;
- ``INTERVALLO_MINUTI``: ogni quanto leggerla da ferma, se l'utente non sceglie;
- ``INTERVALLO_IN_MOTO_SECONDI``: e in marcia o in carica (facoltativo);
- ``comandi_di(vettura)``: i comandi neutri che quell'auto accetta;
- ``crea_da_voce(hass, entry, cartella)``: il cliente, dalla configurazione.

E, secondo l'accesso:

- ``campi``: ``async prova_accesso(hass, dati) -> Accesso``. Entra davvero
  con quello che l'utente ha scritto, e restituisce cosa salvare;
- ``dispositivo`` (il login fatto su un altro schermo con un codice):
  ``async avvia_dispositivo(hass, dati) -> Dispositivo`` e
  ``async completa_dispositivo(hass, dati, dispositivo) -> Accesso``, che
  solleva ``InAttesa`` finché l'utente non ha confermato;
- ``oauth_ha`` e ``codice_browser`` hanno i loro passi nel config flow
  (Tesla e Stellantis).

I nomi dei campi sono quelli di ``VOCABOLARIO``: hanno già le traduzioni. Una
marca che ha bisogno di un campo nuovo lo aggiunge lì e nelle traduzioni.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..vettura import Vettura
from .errori import ErroreMarca

ACCESSO_CAMPI = "campi"
ACCESSO_DISPOSITIVO = "dispositivo"
ACCESSO_OAUTH_HA = "oauth_ha"
ACCESSO_CODICE_BROWSER = "codice_browser"

#: I campi che il config flow sa chiedere, già tradotti.
VOCABOLARIO = (
    "email",
    "username",
    "password",
    "pin",
    "paese",
    "regione",
    "marchio",
    "client_id",
    "client_secret",
    "api_key",
    "vin",
    "telefono",
    "cert_app",
    "chiave_app",
    "codice",
)


@dataclass(frozen=True)
class Campo:
    """Una domanda del modulo di configurazione."""

    nome: str
    obbligatorio: bool = True
    segreto: bool = False
    lungo: bool = False
    #: per una tendina: (valore, etichetta)
    scelte: tuple[tuple[str, str], ...] = ()
    predefinito: str | None = None

    def __post_init__(self) -> None:
        if self.nome not in VOCABOLARIO:
            raise ValueError(f"Il campo «{self.nome}» non è nel vocabolario di GdAAuto")


@dataclass
class Accesso:
    """Quello che un accesso riuscito consegna al config flow."""

    #: cosa salvare nella configurazione (credenziali, token, regione…)
    dati: dict[str, Any]
    vetture: list[Vettura]
    #: chi è, per non configurare due volte lo stesso account
    unique_id: str
    titolo: str


@dataclass
class Dispositivo:
    """Un login da fare su un altro schermo: un link e un codice."""

    link: str
    codice_utente: str
    #: quello che serve alla marca per completare (device_code, verifier…)
    stato: dict[str, Any] = field(default_factory=dict)


class InAttesa(ErroreMarca):
    """L'utente non ha ancora confermato sull'altro schermo."""
