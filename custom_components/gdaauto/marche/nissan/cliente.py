"""Il cliente Nissan: uno solo per i due cloud, che sceglie per ogni auto.

Nissan ha due cloud che non si parlano, con due account diversi (spesso con
la stessa email):

- **NissanConnect Services** (``kamereon.py``): Ariya, Leaf dal 2019, Micra
  elettrica e le Nissan connesse più nuove, solo in Europa;
- **NissanConnect EV**, il vecchio Carwings (``carwings.py``): le Leaf dal
  2011 al 2019, in Europa, Stati Uniti, Canada, Australia e Giappone.

Con la regione «Europa, riconosci da solo» si prova a entrare in tutti e due,
e si tengono quelli dove ci sono auto: chi ha una Leaf del 2016 e un'Ariya le
vede tutte e due nella stessa voce. Ogni auto sa da che cloud viene
(``extra["via"]``), e il cliente passa a quello giusto.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Any

import aiohttp

from ...vettura import StatoVettura, Vettura
from ..errori import ErroreComando, ErroreCredenziali, ErroreMarca, ErroreRete
from .carwings import REGIONI, URL, ClienteCarwings
from .kamereon import EUROPA, ClienteKamereon, ServerKamereon, comandi_kamereon

_LOGGER = logging.getLogger(__name__)

ID = "nissan"
NOME = "Nissan"

KAMEREON = "kamereon"
CARWINGS = "carwings"

#: la regione scelta → i cloud da provare
AUTO_EUROPA = "auto_eu"
EUROPA_NUOVE = "eu"


def vie_della_regione(regione: str) -> tuple[str, ...]:
    if regione == AUTO_EUROPA:
        return (KAMEREON, CARWINGS)
    if regione == EUROPA_NUOVE:
        return (KAMEREON,)
    return (CARWINGS,)


def regione_carwings(regione: str) -> str:
    return regione if regione in REGIONI else "NE"


def comandi_di(v: Vettura) -> set[str]:
    if v.extra.get("via") == CARWINGS:
        # Il Carwings accende e spegne il clima e avvia la ricarica: nient'altro.
        return {"clima", "ricarica"}
    return comandi_kamereon(v)


class ClienteNissan:
    def __init__(
        self,
        sessione_http: aiohttp.ClientSession,
        email: str,
        password: str,
        regione: str = AUTO_EUROPA,
        token: dict[str, Any] | None = None,
        salva: Callable[[dict[str, Any]], None] | None = None,
        *,
        vie: tuple[str, ...] | list[str] | None = None,
        server_kamereon: ServerKamereon = EUROPA,
        url_carwings: str = URL,
        attesa_carwings: float | None = None,
    ) -> None:
        self.regione = regione
        self.vie: list[str] = list(vie) if vie else list(vie_della_regione(regione))
        self.password = password
        self.kamereon = ClienteKamereon(sessione_http, token, salva, server=server_kamereon)
        carwings: dict[str, Any] = {"url": url_carwings}
        if attesa_carwings is not None:
            carwings["attesa"] = attesa_carwings
        self.carwings = ClienteCarwings(sessione_http, email, password, regione_carwings(regione), **carwings)

    @property
    def token(self) -> dict[str, Any]:
        return self.kamereon.token

    def _parte(self, via: str) -> ClienteKamereon | ClienteCarwings:
        return self.kamereon if via == KAMEREON else self.carwings

    # --- entrare ---------------------------------------------------------------

    async def prova(self, codice: str = "", verifier: str = "") -> list[Vettura]:
        """La prima volta: entra in ogni cloud della regione e tiene quelli con le auto.

        NissanConnect Services entra col ``code`` del login fatto nel browser,
        NissanConnect EV con la password. Se nessuno va: un guasto vince sulle
        credenziali (non si chiede di rientrare per colpa di un server spento);
        se uno entra ma non ha auto, si tiene lo stesso, e il config flow dirà
        «nessuna auto».
        """
        trovate: dict[str, list[Vettura]] = {}
        errori: dict[str, ErroreMarca] = {}
        for via in vie_della_regione(self.regione):
            try:
                if via == KAMEREON:
                    if not codice:
                        continue
                    await self.kamereon.scambia_codice(codice, verifier)
                    await self.kamereon.accedi()
                    trovate[via] = await self.kamereon.vetture()
                else:
                    if not self.password:
                        if self.regione == AUTO_EUROPA:
                            # Senza password il vecchio cloud non si prova: chi ha solo auto nuove non la scrive.
                            continue
                        raise ErroreCredenziali("NissanConnect EV vuole la password dell'app")
                    await self.carwings.accedi()
                    trovate[via] = await self.carwings.vetture()
            except ErroreMarca as err:
                _LOGGER.debug("Nissan: %s non va per questo account: %s", via, err)
                errori[via] = err
        con_auto = [via for via, elenco in trovate.items() if elenco]
        if con_auto:
            self.vie = con_auto
            return [v for via in con_auto for v in trovate[via]]
        guasti = [e for e in errori.values() if not isinstance(e, ErroreCredenziali)]
        if guasti:
            raise guasti[0]
        if trovate:
            self.vie = list(trovate)
            return []
        if len(errori) == 1:
            raise next(iter(errori.values()))
        if not errori:
            raise ErroreCredenziali(
                "Nissan: serve il codice del login nel browser, o la password per le Leaf fino al 2019"
            )
        raise ErroreCredenziali("Nissan rifiuta l'accesso sia su NissanConnect Services sia su NissanConnect EV")

    async def accedi(self) -> None:
        for via in self.vie:
            await self._parte(via).accedi()

    async def vetture(self) -> list[Vettura]:
        elenco: list[Vettura] = []
        for via in self.vie:
            elenco += await self._parte(via).vetture()
        return elenco

    # --- leggere e comandare -------------------------------------------------------

    def _di(self, v: Vettura) -> ClienteKamereon | ClienteCarwings:
        via = str(v.extra.get("via") or KAMEREON)
        if via not in self.vie:
            raise ErroreRete(f"{v.titolo}: il suo cloud ({via}) non è più nella configurazione")
        return self._parte(via)

    async def stato(self, v: Vettura) -> StatoVettura:
        return await self._di(v).stato(v)

    async def clima(self, v: Vettura, acceso: bool, _pin: str | None, temperatura: int = 22) -> None:
        parte = self._di(v)
        if isinstance(parte, ClienteKamereon):
            await parte.clima(v, acceso, temperatura)
        else:
            # Il Carwings usa la temperatura impostata nell'auto.
            await parte.clima(v, acceso)

    async def ricarica(self, v: Vettura, avvia: bool, _pin: str | None) -> None:
        await self._di(v).ricarica(v, avvia)

    async def trova(self, v: Vettura, _pin: str | None) -> None:
        parte = self._di(v)
        if not isinstance(parte, ClienteKamereon):
            raise ErroreComando(f"{v.titolo} non accende luci né clacson da remoto")
        await parte.trova(v)

    async def chiudi(self, v: Vettura, _pin: str | None) -> None:
        raise ErroreComando(f"{v.titolo}: GdAAuto non sa ancora chiudere le Nissan da remoto")

    async def apri(self, v: Vettura, _pin: str | None) -> None:
        raise ErroreComando(f"{v.titolo}: GdAAuto non sa ancora aprire le Nissan da remoto")

    async def limite_ricarica(self, v: Vettura, _percento: int, _pin: str | None) -> None:
        raise ErroreComando(f"{v.titolo}: il cloud Nissan non cambia il limite di carica")

    async def destinazione(self, v: Vettura, nome: str, lat: float, lon: float, indirizzo: str = "") -> None:
        raise ErroreComando(f"{v.titolo}: GdAAuto non manda ancora destinazioni alle Nissan")
