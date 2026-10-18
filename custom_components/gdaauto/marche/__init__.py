"""Le marche: ogni auto elettrica che GdAAuto sa leggere dal suo cloud.

Ogni marca è una cartella qui dentro, che rispetta il contratto di
``base.py``; il registro le trova da solo. Il catalogo di tutti i marchi, e
quale marca nativa serve ognuno, è in ``catalogo.py``.

Il cliente che ``crea_da_voce`` restituisce rispetta ``Marca`` qui sotto:
entra, elenca le auto, legge lo stato, e comanda con i nomi di GdAAuto. Tutto
quello che è della marca (segnali, firme, cloud) resta nella sua cartella:
fuori si vedono solo ``Vettura`` e ``StatoVettura``.
"""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType
from typing import Protocol

from ..vettura import StatoVettura, Vettura
from . import leapmotor, stellantis, tesla


def _trova() -> dict[str, ModuleType]:
    """Ogni sottocartella con un ``ID`` è una marca."""
    trovate: dict[str, ModuleType] = {}
    for info in pkgutil.iter_modules(__path__):
        if not info.ispkg:
            continue
        modulo = importlib.import_module(f"{__name__}.{info.name}")
        if isinstance(getattr(modulo, "ID", None), str):
            trovate[modulo.ID] = modulo
    return dict(sorted(trovate.items()))


MARCHE: dict[str, ModuleType] = _trova()


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


__all__ = ["MARCHE", "Marca", "leapmotor", "marca", "stellantis", "tesla"]


def marca(id_: str) -> ModuleType:
    try:
        return MARCHE[id_]
    except KeyError as err:
        raise ValueError(f"Marca sconosciuta: {id_}") from err
