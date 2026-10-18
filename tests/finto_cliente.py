"""Un cliente di marca finto, per le prove dentro Home Assistant."""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any

from custom_components.gdaauto.marche.leapmotor.cliente import vettura_da_json
from custom_components.gdaauto.marche.leapmotor.errori import ErroreCredenziali, ErrorePin
from custom_components.gdaauto.marche.leapmotor.segnali import interpreta
from custom_components.gdaauto.vettura import StatoVettura, Vettura


@dataclass
class FintoCliente:
    elenco: list[dict[str, Any]]
    stati: dict[str, dict[str, Any]]
    password_giusta: bool = True
    chiamate: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)
    letture: int = 0

    async def accedi(self) -> None:
        if not self.password_giusta:
            raise ErroreCredenziali("account or password error")

    async def vetture(self) -> list[Vettura]:
        await self.accedi()
        return [vettura_da_json(v, bool(v.get("_condivisa"))) for v in self.elenco]

    async def stato(self, v: Vettura) -> StatoVettura:
        self.letture += 1
        dati = dict(self.stati[v.vin])
        if isinstance(dati.get("signal"), dict):
            # Come il cloud vero quando l'auto è sveglia: l'ultimo dato è di adesso.
            dati["signal"] = {**dati["signal"], "sts": int(time.time() * 1000)}
        stato = interpreta(dati)
        if isinstance(dati.get("signal"), dict):
            # Lo storico che il cliente vero legge ogni mezz'ora: c'è, così le sorelle lo vedono.
            stato.consumo_medio_kwh_100km = 15.8
            stato.energia_oggi_guida_kwh, stato.energia_oggi_clima_kwh, stato.energia_oggi_altro_kwh = 3.2, 0.8, 0.1
            stato.km_7_giorni, stato.energia_7_giorni_kwh = 60.5, 10.0
            stato.ultima_ricarica_kwh, stato.ultima_ricarica_tipo = 31.0, "dc"
        return stato

    async def verifica_pin(self, v: Vettura, pin: str) -> None:
        self.chiamate.append(("verifica_pin", (v.vin,)))
        if pin != "1234":
            raise ErrorePin("PIN sbagliato")

    async def _comando(self, nome: str, v: Vettura, pin: str | None, *resto: Any) -> None:
        if nome != "destinazione" and not pin:
            raise ErrorePin("serve il PIN")
        self.chiamate.append((nome, (v.vin, *resto)))

    async def chiudi(self, v: Vettura, pin: str | None) -> None:
        await self._comando("chiudi", v, pin)

    async def apri(self, v: Vettura, pin: str | None) -> None:
        await self._comando("apri", v, pin)

    async def trova(self, v: Vettura, pin: str | None) -> None:
        await self._comando("trova", v, pin)

    async def clima(self, v: Vettura, acceso: bool, pin: str | None, temperatura: int = 22) -> None:
        await self._comando("clima", v, pin, acceso, temperatura)

    async def ricarica(self, v: Vettura, avvia: bool, pin: str | None) -> None:
        await self._comando("ricarica", v, pin, avvia)

    async def limite_ricarica(self, v: Vettura, percento: int, pin: str | None) -> None:
        await self._comando("limite", v, pin, percento)

    async def destinazione(self, v: Vettura, nome: str, lat: float, lon: float, indirizzo: str = "") -> None:
        await self._comando("destinazione", v, None, nome, lat, lon)
