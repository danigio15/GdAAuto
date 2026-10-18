"""Certificati e chiavi caricati in ogni forma: PEM, DER, PKCS#12. E il modulo che li accetta come file."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.gdaauto.const import DOMAIN
from custom_components.gdaauto.marche import MARCHE
from custom_components.gdaauto.marche.base import ACCESSO_CAMPI, Accesso, Campo
from custom_components.gdaauto.marche.catalogo import PER_ID
from custom_components.gdaauto.marche.errori import ErroreCertificato
from custom_components.gdaauto.pem import CERTIFICATO, CHIAVE, FormatoSconosciuto, da_pkcs12, in_pem, tipo_di

from .test_telaio import AUTO, MARCHIO, _modulo

CHIAVE_RSA = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_NOME = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "LeapmotorAppCrtCN")])
CERT = (
    x509.CertificateBuilder()
    .subject_name(_NOME)
    .issuer_name(_NOME)
    .public_key(CHIAVE_RSA.public_key())
    .serial_number(1)
    .not_valid_before(datetime.now(UTC) - timedelta(days=1))
    .not_valid_after(datetime.now(UTC) + timedelta(days=30))
    .sign(CHIAVE_RSA, hashes.SHA256())
)
CERT_PEM = CERT.public_bytes(serialization.Encoding.PEM)
CERT_DER = CERT.public_bytes(serialization.Encoding.DER)
CHIAVE_PEM = CHIAVE_RSA.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
)
CHIAVE_DER = CHIAVE_RSA.private_bytes(
    serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
)
P12 = pkcs12.serialize_key_and_certificates(b"app", CHIAVE_RSA, CERT, None, serialization.NoEncryption())
# Gli id dei file caricati sono uuid, come quelli veri di file_upload.
F1, F2 = "0f1a2b3c4d5e6f708192a3b4c5d6e7f8", "1f1a2b3c4d5e6f708192a3b4c5d6e7f8"
P12_CHIUSO = pkcs12.serialize_key_and_certificates(
    b"app", CHIAVE_RSA, CERT, None, serialization.BestAvailableEncryption(b"segreta")
)


def _e_lo_stesso_cert(testo: str) -> bool:
    return x509.load_pem_x509_certificate(testo.encode()) == CERT


def _e_la_stessa_chiave(testo: str) -> bool:
    chiave = serialization.load_pem_private_key(testo.encode(), None)
    return chiave.private_numbers() == CHIAVE_RSA.private_numbers()  # type: ignore[union-attr]


@pytest.mark.parametrize("grezzo", [CERT_PEM, CERT_DER, P12], ids=["pem", "der", "p12"])
def test_il_certificato_in_ogni_forma(grezzo: bytes) -> None:
    assert _e_lo_stesso_cert(in_pem(grezzo, CERTIFICATO))


@pytest.mark.parametrize("grezzo", [CHIAVE_PEM, CHIAVE_DER, P12], ids=["pem", "der", "p12"])
def test_la_chiave_in_ogni_forma(grezzo: bytes) -> None:
    assert _e_la_stessa_chiave(in_pem(grezzo, CHIAVE))


def test_quello_che_non_si_apre() -> None:
    for grezzo, tipo in (
        (b"\x00\x01binario a caso", CERTIFICATO),
        (b"", CHIAVE),
        (b"-----BEGIN CERTIFICATE-----\nrotto\n-----END CERTIFICATE-----\n", CERTIFICATO),
        (CERT_PEM, CHIAVE),  # un certificato dove serve la chiave
        (P12_CHIUSO, CERTIFICATO),
    ):
        with pytest.raises(FormatoSconosciuto):
            in_pem(grezzo, tipo)
    cert, chiave = da_pkcs12(P12_CHIUSO, "segreta")
    assert cert and chiave and _e_lo_stesso_cert(cert)
    assert tipo_di("cert_app") == CERTIFICATO and tipo_di("chiave_app") == CHIAVE


# --- nel modulo di configurazione ----------------------------------------------------------


@pytest.fixture
def caricati(tmp_path: Path) -> Iterator[dict[str, bytes]]:
    """I file «caricati» dal browser: id → contenuto, come li dà file_upload."""
    file: dict[str, bytes] = {}

    @contextmanager
    def finto(_hass: HomeAssistant, id_file: str) -> Iterator[Path]:
        percorso = tmp_path / id_file
        percorso.write_bytes(file[id_file])
        yield percorso

    with patch("custom_components.gdaauto.config_flow.process_uploaded_file", finto):
        yield file


def _con_certificati() -> Any:
    ricevuti: list[dict[str, Any]] = []
    modulo = _modulo(ACCESSO_CAMPI)

    async def prova_accesso(_hass: HomeAssistant, dati: dict[str, Any]) -> Accesso:
        ricevuti.append(dict(dati))
        return Accesso(dict(dati), [AUTO], "finta_cert", "Finta · cert")

    modulo.prova_accesso = prova_accesso
    modulo.CAMPI = (
        Campo("email"),
        Campo("cert_app", obbligatorio=False, lungo=True, carica=".pem,.der,.p12"),
        Campo("chiave_app", obbligatorio=False, lungo=True, segreto=True, carica=".pem,.der,.p12"),
    )
    modulo.ricevuti = ricevuti
    return modulo


async def _fino_al_modulo(hass: HomeAssistant) -> dict[str, Any]:
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    return await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "finta_brand"})


async def test_si_caricano_un_der_e_una_chiave_binaria(hass: HomeAssistant, caricati: dict[str, bytes]) -> None:
    modulo = _con_certificati()
    caricati.update({F1: CERT_DER, F2: CHIAVE_DER})
    with patch.dict(MARCHE, {"finta": modulo}), patch.dict(PER_ID, {"finta_brand": MARCHIO}):
        r = await _fino_al_modulo(hass)
        assert {str(k) for k in r["data_schema"].schema} == {
            "email",
            "cert_app",
            "cert_app_file",
            "chiave_app",
            "chiave_app_file",
        }
        r = await hass.config_entries.flow.async_configure(
            r["flow_id"], {"email": "a@b.it", "cert_app_file": F1, "chiave_app_file": F2}
        )
        await hass.async_block_till_done()
    assert r["type"] is FlowResultType.CREATE_ENTRY
    (dati,) = modulo.ricevuti
    assert "cert_app_file" not in dati and "chiave_app_file" not in dati
    assert _e_lo_stesso_cert(dati["cert_app"]) and _e_la_stessa_chiave(dati["chiave_app"])


async def test_un_p12_riempie_tutti_e_due(hass: HomeAssistant, caricati: dict[str, bytes]) -> None:
    modulo = _con_certificati()
    caricati[F1] = P12
    with patch.dict(MARCHE, {"finta": modulo}), patch.dict(PER_ID, {"finta_brand": MARCHIO}):
        r = await _fino_al_modulo(hass)
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"email": "a@b.it", "cert_app_file": F1})
        await hass.async_block_till_done()
    assert r["type"] is FlowResultType.CREATE_ENTRY
    (dati,) = modulo.ricevuti
    assert _e_lo_stesso_cert(dati["cert_app"]) and _e_la_stessa_chiave(dati["chiave_app"])


async def test_un_file_che_non_si_apre(hass: HomeAssistant, caricati: dict[str, bytes]) -> None:
    modulo = _con_certificati()
    caricati[F1] = b"\x00\x01 non sono un certificato"
    with patch.dict(MARCHE, {"finta": modulo}), patch.dict(PER_ID, {"finta_brand": MARCHIO}):
        r = await _fino_al_modulo(hass)
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"email": "a@b.it", "cert_app_file": F1})
    assert r["errors"] == {"base": "file"}
    assert modulo.ricevuti == []
    # L'id del file, ormai usato, non si ripropone nel modulo.
    suggeriti = {str(k): (k.description or {}).get("suggested_value") for k in r["data_schema"].schema}
    assert suggeriti["email"] == "a@b.it"
    assert suggeriti["cert_app_file"] is None


async def test_leapmotor_legge_anche_i_der_nella_cartella(hass: HomeAssistant) -> None:
    from custom_components.gdaauto.marche.leapmotor import _pem_da_file

    cartella = Path(hass.config.path("gdaauto"))
    cartella.mkdir(exist_ok=True)
    (cartella / "app_cert.pem").write_bytes(CERT_DER)
    (cartella / "app_key.pem").write_bytes(CHIAVE_DER)
    dati = await hass.async_add_executor_job(_pem_da_file, hass, {})
    assert _e_lo_stesso_cert(dati["cert_app"]) and _e_la_stessa_chiave(dati["chiave_app"])
    (cartella / "app_key.pem").write_bytes(b"\x00 rotta")
    with pytest.raises(ErroreCertificato, match=r"app_key\.pem"):
        await hass.async_add_executor_job(_pem_da_file, hass, {})
