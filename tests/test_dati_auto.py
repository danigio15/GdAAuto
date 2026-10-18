"""Il contratto con gdanav e GdAHome (``docs/DATI-AUTO.md``) dice il vero.

Ogni chiave della tabella è un'entità che GdAAuto sa creare, nel dominio
detto, con la traduzione in italiano e in inglese; e ogni entità di GdAAuto
è nella tabella, così nessun dato resta senza un posto nelle sorelle.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

from custom_components.gdaauto import binary_sensor, sensor

QUI = Path(__file__).resolve().parents[1]
CONTRATTO = QUI / "docs" / "DATI-AUTO.md"
TRADUZIONI = QUI / "custom_components" / "gdaauto" / "translations"

#: Le entità che non sono dati dell'auto: i pulsanti per rileggere e trovare.
FUORI_DAL_CONTRATTO = {("button", "aggiorna"), ("button", "trova")}


def _righe() -> list[tuple[list[str], str]]:
    """(chiavi, dominio) di ogni riga delle tabelle; «…_ant_dx» completa la chiave prima."""
    righe = []
    for riga in CONTRATTO.read_text(encoding="utf-8").splitlines():
        celle = [c.strip() for c in riga.strip().strip("|").split("|")]
        if len(celle) < 3 or not celle[0].startswith("`"):
            continue
        chiavi: list[str] = []
        for nome in re.findall(r"`([^`]+)`", celle[0]):
            if nome.startswith("…"):
                base = chiavi[-1].rsplit("_", 2 if nome.count("_") > 1 else 1)[0]
                nome = base + nome.removeprefix("…")
            chiavi.append(nome)
        righe.append((chiavi, celle[1].split()[0]))
    return righe


def _entita_di_gdaauto() -> set[tuple[str, str]]:
    tutte = {("sensor", d.key) for d in sensor.DESCRIZIONI} | {
        ("binary_sensor", d.key) for d in binary_sensor.DESCRIZIONI
    }
    traduzioni = json.loads((TRADUZIONI / "it.json").read_text(encoding="utf-8"))["entity"]
    for dominio in ("lock", "switch", "number", "button", "device_tracker"):
        tutte |= {(dominio, chiave) for chiave in traduzioni.get(dominio, {})}
    return tutte


def test_ogni_chiave_del_contratto_e_un_entita() -> None:
    entita = _entita_di_gdaauto()
    mancano = [(d, k) for chiavi, d in _righe() for k in chiavi if (d, k) not in entita]
    assert not mancano, f"nel contratto ma non in GdAAuto: {mancano}"


def test_ogni_entita_e_nel_contratto() -> None:
    nel_contratto = {(d, k) for chiavi, d in _righe() for k in chiavi}
    fuori = _entita_di_gdaauto() - nel_contratto - FUORI_DAL_CONTRATTO
    assert not fuori, f"in GdAAuto ma senza un posto nel contratto: {sorted(fuori)}"


def test_ogni_chiave_ha_il_nome_in_italiano_e_in_inglese() -> None:
    for lingua in ("it", "en"):
        traduzioni = json.loads((TRADUZIONI / f"{lingua}.json").read_text(encoding="utf-8"))["entity"]
        for chiavi, dominio in _righe():
            for chiave in chiavi:
                assert traduzioni[dominio][chiave]["name"], (lingua, dominio, chiave)
