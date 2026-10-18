"""Ogni marca vera rispetta il contratto di ``base.py``, e il catalogo le punta bene.

``test_telaio.py`` prova il config flow con una marca finta; qui si guarda che
le marche vere dichiarino quello che il config flow e ``AutoCloud`` si
aspettano, così una marca nuova sbagliata si ferma qui e non da un utente.
"""

from __future__ import annotations

import inspect

import pytest

from custom_components.gdaauto.marche import MARCHE
from custom_components.gdaauto.marche.base import (
    ACCESSO_CAMPI,
    ACCESSO_CODICE_BROWSER,
    ACCESSO_DISPOSITIVO,
    ACCESSO_OAUTH_HA,
    Campo,
)
from custom_components.gdaauto.marche.catalogo import MARCHI
from custom_components.gdaauto.vettura import Vettura

#: I comandi che le entità di GdAAuto sanno dare.
COMANDI = {"chiudi", "apri", "trova", "clima", "ricarica", "limite_ricarica", "destinazione"}

#: Le funzioni che ogni accesso vuole, oltre a ``crea_da_voce`` e ``comandi_di``.
FUNZIONI = {
    ACCESSO_CAMPI: ("prova_accesso",),
    ACCESSO_DISPOSITIVO: ("avvia_dispositivo", "completa_dispositivo"),
    ACCESSO_OAUTH_HA: (),
    ACCESSO_CODICE_BROWSER: (),
}


@pytest.mark.parametrize("modulo", MARCHE.values(), ids=MARCHE.keys())
def test_la_marca_rispetta_il_contratto(modulo) -> None:
    assert isinstance(modulo.NOME, str) and modulo.NOME
    assert modulo.ACCESSO in FUNZIONI
    for nome in ("crea_da_voce", "comandi_di", *FUNZIONI[modulo.ACCESSO]):
        assert callable(getattr(modulo, nome, None)), nome
    for nome in ("crea_da_voce", *FUNZIONI[modulo.ACCESSO]):
        assert inspect.iscoroutinefunction(getattr(modulo, nome)), f"{nome} va scritta async"

    if modulo.ACCESSO in (ACCESSO_CAMPI, ACCESSO_DISPOSITIVO):
        assert modulo.CAMPI and all(isinstance(c, Campo) for c in modulo.CAMPI)
        nomi = [c.nome for c in modulo.CAMPI]
        assert len(nomi) == len(set(nomi)), "un campo chiesto due volte"

    nota = getattr(modulo, "NOTA", None)
    if nota is not None:
        assert set(nota) == {"it", "en"} and all(nota.values())

    assert isinstance(modulo.VUOLE_PIN, bool)
    assert isinstance(modulo.INTERVALLO_MINUTI, int) and 1 <= modulo.INTERVALLO_MINUTI <= 180
    in_moto = getattr(modulo, "INTERVALLO_IN_MOTO_SECONDI", 60)
    assert isinstance(in_moto, int) and 10 <= in_moto <= 3600


@pytest.mark.parametrize("marchio", [m for m in MARCHI if m.nativo in MARCHE], ids=lambda m: m.id)
def test_il_marchio_nativo_e_configurabile(marchio) -> None:
    modulo = MARCHE[marchio.nativo]
    assert marchio.ha and marchio.link.startswith("https://"), "serve la strada di riserva da Home Assistant"
    campi = {c.nome: c for c in getattr(modulo, "CAMPI", ())}
    for chiave, valore in marchio.preimpostati.items():
        campo = campi.get(chiave)
        if campo is not None and campo.scelte:
            assert valore in {v for v, _ in campo.scelte}, f"{marchio.id}: «{valore}» non è fra le scelte di {chiave}"


def test_ogni_marca_ha_almeno_un_marchio() -> None:
    serviti = {m.nativo for m in MARCHI}
    assert set(MARCHE) <= serviti, f"marche che nessuno può scegliere: {set(MARCHE) - serviti}"


def test_i_marchi_hanno_id_unici_e_ordinati() -> None:
    ids = [m.id for m in MARCHI]
    assert len(ids) == len(set(ids))
    assert ids == sorted(ids)


@pytest.mark.parametrize("modulo", MARCHE.values(), ids=MARCHE.keys())
def test_i_comandi_sono_quelli_di_gdaauto(modulo) -> None:
    """Anche un'auto di cui il cloud non ha detto niente non deve rompere ``comandi_di``."""
    auto = Vettura(vin="ZZZ00000000000001", marca=modulo.ID, modello="", nome="Prova")
    comandi = modulo.comandi_di(auto)
    assert isinstance(comandi, set)
    assert comandi <= COMANDI, comandi - COMANDI
    if getattr(modulo, "SENZA_COMANDI", False):
        assert not comandi - {"destinazione"}
