"""La capacità della batteria: quella della marca, o ricavata da energia e carica."""

from __future__ import annotations

from custom_components.gdaauto.vettura import StatoVettura


def test_la_capacita_della_marca_vince() -> None:
    assert StatoVettura(capacita_batteria_kwh=77.4, energia_residua_kwh=10, batteria=50).capacita_kwh == 77.4


def test_la_capacita_si_ricava_da_energia_e_carica() -> None:
    assert StatoVettura(energia_residua_kwh=45.0, batteria=85).capacita_kwh == 52.9


def test_quando_non_si_ricava() -> None:
    # Sotto il 10 % l'arrotondamento della percentuale pesa troppo.
    assert StatoVettura(energia_residua_kwh=3.0, batteria=5).capacita_kwh is None
    # Un risultato che non è una batteria d'auto.
    assert StatoVettura(energia_residua_kwh=1.0, batteria=50).capacita_kwh is None
    assert StatoVettura(batteria=50).capacita_kwh is None
