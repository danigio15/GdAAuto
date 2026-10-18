"""Le marche: ogni auto elettrica entra in GdAAuto da qui.

Una marca è un modulo con dentro:

- ``ID`` e ``NOME``: come si chiama nel codice e sullo schermo;
- ``CAMPI``: cosa chiedere a chi la configura (email, password, certificati…);
- ``VUOLE_PIN``: se i comandi all'auto vogliono un PIN a parte;
- ``crea(sessione_http, dati, cartella, lingua)``: il suo cliente;
- ``comandi_di(vettura)``: i comandi neutri che quell'auto accetta.

E il cliente che ``crea`` restituisce rispetta ``Marca`` qui sotto: entra,
elenca le auto, legge lo stato, e comanda con i nomi di GdAAuto. Tutto quello
che è della marca (segnali, firme, cloud) resta nella sua cartella: fuori si
vedono solo ``Vettura`` e ``StatoVettura``.

Per aggiungerne una: una cartella qui dentro con questi nomi, e una riga in
``MARCHE``. Il resto dell'integrazione non si tocca.
"""

from __future__ import annotations

from types import ModuleType
from typing import Protocol

from ..vettura import StatoVettura, Vettura
from . import leapmotor

MARCHE: dict[str, ModuleType] = {leapmotor.ID: leapmotor}


class Marca(Protocol):
    """Quello che ogni cliente di marca sa fare."""

    async def accedi(self) -> None: ...

    async def vetture(self) -> list[Vettura]: ...

    async def stato(self, vettura: Vettura) -> StatoVettura: ...

    async def chiudi(self, v: Vettura, pin: str | None) -> None: ...

    async def apri(self, v: Vettura, pin: str | None) -> None: ...

    async def trova(self, v: Vettura, pin: str | None) -> None: ...

    async def clima(self, v: Vettura, acceso: bool, pin: str | None, temperatura: int = 22) -> None: ...

    async def ricarica(self, v: Vettura, avvia: bool, pin: str | None) -> None: ...

    async def limite_ricarica(self, v: Vettura, percento: int, pin: str | None) -> None: ...

    async def destinazione(self, v: Vettura, nome: str, lat: float, lon: float, indirizzo: str = "") -> None: ...


def marca(id_: str) -> ModuleType:
    try:
        return MARCHE[id_]
    except KeyError as err:
        raise ValueError(f"Marca sconosciuta: {id_}") from err
