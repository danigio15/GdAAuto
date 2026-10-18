"""La crittografia del cloud, contro i vettori dell'implementazione di
riferimento (kerniger/leapmotor-ha, MIT): se una sola cifra cambia, il cloud
non ci fa più entrare."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

from custom_components.gdaauto.marche.leapmotor import cripto

TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyX25hbWUiOiAiYSxiLERFVjEyMzQ1NixkIn0.firmaxxxxxxxxxx"


def test_password_del_certificato_dell_account() -> None:
    assert cripto.password_p12(123456, "U1a2b3c4d5e6f7g8") == "/APpmNMgOFVw+K8"
    assert cripto.password_p12("9876543210", "abcdefghijklmnop") == "00ps/S0ZlVxrO0D"


def test_pin_cifrato_col_token() -> None:
    assert cripto.cifra_pin("123456", TOKEN) == "DNEKFDlSSYL5CN6XptoiUg=="


def test_pin_senza_token_usa_la_chiave_di_riserva() -> None:
    assert cripto.cifra_pin("654321", None) == "UIYO/Ib7bQXxyub+fc3Ilg=="


def test_device_id_dal_token() -> None:
    assert cripto.device_id_dal_token(TOKEN) == "DEV123456"
    assert cripto.device_id_dal_token("non.un-jwt.valido") == cripto.DEVICE_ID_PREDEFINITO
    assert cripto.device_id_dal_token("") == cripto.DEVICE_ID_PREDEFINITO


def test_firma_del_login_nell_ordine_giusto() -> None:
    attesa = hashlib.sha256(b"it-IT1dev1a@b.it01123456segreta20260204leapmotor17000000000001.12.3").hexdigest()
    assert (
        cripto.firma_login(
            lingua="it-IT",
            device_id="dev",
            email="a@b.it",
            password="segreta",
            nonce_="123456",
            timestamp="1700000000000",
        )
        == attesa
    )


def test_firma_standard_in_ordine_alfabetico() -> None:
    campi = {"vin": "V", "acceptLanguage": "it-IT", "nonce": "1", "channel": "1"}
    assert cripto.testo_firma_standard(campi) == "it-IT11V"
    chiave = cripto.chiave_firma("ikm", "sale", "info")
    assert len(chiave) == 32
    assert cripto.firma_hmac(chiave, "x") == hmac.new(chiave, b"x", hashlib.sha256).hexdigest()


def test_il_nonce_ha_sei_o_sette_cifre() -> None:
    for _ in range(200):
        n = cripto.nonce()
        assert n.isdigit()
        assert 6 <= len(n) <= 7


def test_il_token_troppo_corto_non_cifra() -> None:
    corto = base64.urlsafe_b64encode(json.dumps({}).encode()).decode()
    try:
        cripto.cifra_pin("1234", corto)
    except ValueError:
        return
    raise AssertionError("un token corto doveva essere rifiutato")
