"""GdAHome e gdanav riconoscono un'auto GdAAuto da sole?

È la promessa di GdAAuto: scegli il dispositivo, e la plancia di GdAHome e
l'integrazione gdanav riempiono tutte le caselle senza che tu tocchi niente.
Qui si prova col codice VERO delle due sorelle, letto dalle loro repository
(mai copiato, mai modificato): se una delle due cambia il modo di riconoscere
le auto, questa prova lo dice.

Le cartelle si trovano da ``GDANAV_DIR`` e ``GDAHOME_DIR``, oppure accanto a
questa repository. Senza, le prove si saltano.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import ModuleType
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest

from custom_components.gdaauto.const import DOMAIN

from .test_integrazione import B10_VIN, _installa, _voce_cloud, cliente  # noqa: F401 - la fixture

QUI = Path(__file__).resolve().parents[1]


def _cartella(variabile: str, nome: str) -> Path | None:
    for candidata in (os.environ.get(variabile), QUI.parent / nome, QUI / "sorelle" / nome):
        if candidata and Path(candidata).is_dir():
            return Path(candidata)
    return None


GDANAV = _cartella("GDANAV_DIR", "gdanav")
GDAHOME = _cartella("GDAHOME_DIR", "gdahomeapp")


def _riconosci_di_gdanav() -> ModuleType:
    """Il ``riconosci.py`` di gdanav, caricato senza il suo ``__init__``
    (che vorrebbe tutta l'integrazione)."""
    assert GDANAV is not None
    cartella = GDANAV / "custom_components" / "gdanav"
    pacchetto = ModuleType("gdanav_sorella")
    pacchetto.__path__ = [str(cartella)]
    sys.modules["gdanav_sorella"] = pacchetto
    for nome in ("const", "riconosci"):
        spec = importlib.util.spec_from_file_location(f"gdanav_sorella.{nome}", cartella / f"{nome}.py")
        assert spec and spec.loader
        modulo = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = modulo
        spec.loader.exec_module(modulo)
    return sys.modules["gdanav_sorella.riconosci"]


def _entita_del_dispositivo(hass: HomeAssistant, vin: str) -> list[tuple[er.RegistryEntry, Any]]:
    voce = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, vin)})
    assert voce is not None
    return [(e, hass.states.get(e.entity_id)) for e in er.async_entries_for_device(er.async_get(hass), voce.id)]


@pytest.mark.skipif(GDANAV is None, reason="la repository di gdanav non c'è")
@pytest.mark.parametrize("lingua", ["it", "en"])
async def test_gdanav_riconosce_l_auto(hass: HomeAssistant, cliente, lingua: str) -> None:  # noqa: F811
    hass.config.language = lingua
    await _installa(hass, _voce_cloud())
    riconosci = _riconosci_di_gdanav()
    # Come fa il config flow di gdanav: nome dal registro, unità e classe dallo stato.
    entita = [
        riconosci.Entita(
            e.entity_id,
            nome=e.name or e.original_name or (s.name if s else ""),
            unita=(s.attributes.get("unit_of_measurement") if s else None) or e.unit_of_measurement,
            classe=(s.attributes.get("device_class") if s else None) or e.device_class or e.original_device_class,
        )
        for e, s in _entita_del_dispositivo(hass, B10_VIN)
    ]
    assert riconosci.proponi(entita) == {
        "entita_batteria": "sensor.grande_batteria",
        "entita_autonomia": "sensor.grande_autonomia",
        "entita_in_carica": "binary_sensor.grande_in_carica",
        "entita_potenza_carica": "sensor.grande_potenza_ricarica",
        "entita_temperatura_batteria": "sensor.grande_temperatura_batteria",
        "entita_posizione": "device_tracker.grande_posizione",
        "entita_velocita": "sensor.grande_velocita",
        "entita_potenza": "sensor.grande_potenza",
        "entita_odometro": "sensor.grande_odometro",
    }


@pytest.mark.skipif(
    GDAHOME is None or shutil.which("node") is None, reason="la repository di GdAHome o node non ci sono"
)
@pytest.mark.parametrize("lingua", ["it", "en"])
async def test_la_plancia_di_gdahome_riconosce_l_auto(hass: HomeAssistant, cliente, lingua: str) -> None:  # noqa: F811
    hass.config.language = lingua
    await _installa(hass, _voce_cloud(pin="1234"))
    coppie = _entita_del_dispositivo(hass, B10_VIN)
    # Come la plancia vede un dispositivo: l'elenco delle entità e gli stati.
    dispositivo = {
        "entities": [
            {
                "entity_id": e.entity_id,
                "name": e.name or e.original_name or "",
                "device_class": e.device_class or e.original_device_class or "",
                "unit": e.unit_of_measurement or "",
                "category": e.entity_category.value if e.entity_category else "",
                "disabled": e.disabled,
            }
            for e, _ in coppie
        ],
        "states": {s.entity_id: {"state": s.state, "attributes": dict(s.attributes)} for _, s in coppie if s},
    }
    esito = subprocess.run(
        ["node", str(QUI / "tests" / "plancia" / "lega.mjs"), str(GDAHOME)],
        input=json.dumps(dispositivo, default=str),
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    lega = json.loads(esito.stdout)
    mappa = lega["mappa"]
    assert lega["tipo"] == ""  # elettrica
    assert mappa["dm.ev_batteria_auto"] == "sensor.grande_batteria"
    assert mappa["dm.ev_autonomia"] == "sensor.grande_autonomia"
    assert mappa["dm.ev_odometro"] == "sensor.grande_odometro"
    assert mappa["dm.ev_cavo_collegato"] == "binary_sensor.grande_cavo_collegato"
    assert mappa["dm.ev_potenza_ricarica"] == "sensor.grande_potenza_ricarica"
    assert mappa["dm.ev_target_soc"] == "number.grande_limite_ricarica"
    assert mappa["dm.ev_portiere"] == "lock.grande_serratura"
    assert mappa["dm.ev_finestrini"] == "binary_sensor.grande_finestrini"
    assert mappa["dm.ev_posizione"] == "device_tracker.grande_posizione"
    assert mappa["dm.ev_motore"] == "binary_sensor.grande_quadro"
    assert mappa["dm.ev_stato_ricarica"] in ("binary_sensor.grande_in_carica", "sensor.grande_stato_ricarica")
    for ruota in ("ant_sx", "ant_dx", "post_sx", "post_dx"):
        assert mappa[f"dm.ev_pneumatico_{ruota}"].startswith("sensor.grande_pneumatici_")
