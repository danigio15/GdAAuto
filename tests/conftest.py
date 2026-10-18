"""Le prove girano dentro un Home Assistant vero; il cloud è finto."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Senza questo Home Assistant ignora custom_components."""
