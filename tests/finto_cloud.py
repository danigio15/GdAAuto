"""Un cloud Leapmotor finto, ma col TLS reciproco vero.

Controlla quello che il cloud vero controlla: il certificato giusto per ogni
indirizzo (quello dell'app per il login e la sincronizzazione, quello
dell'account per il resto), le firme nell'ordine che la documentazione della
comunità descrive, il deviceId della sessione, il PIN cifrato col token.
Le firme qui sono scritte di nuovo, campo per campo, dalla documentazione:
non riusano l'ordine del cliente.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import datetime as dt
import hashlib
import hmac
import json
from pathlib import Path
import ssl
from typing import Any
from urllib.parse import parse_qsl

from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from custom_components.gdaauto.marche.leapmotor import cripto

ID_UTENTE = "4242"
UID = "U9z8y7x6w5v4u3t2"
DEVICE_SESSIONE = "DEVSESSIONE"


def certificato(cn: str) -> tuple[bytes, bytes, Any, Any]:
    """Un certificato autofermato: PEM del certificato, PEM della chiave, e gli oggetti."""
    chiave = ec.generate_private_key(ec.SECP256R1())
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    ora = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(nome)
        .issuer_name(nome)
        .public_key(chiave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ora - dt.timedelta(days=1))
        .not_valid_after(ora + dt.timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(chiave, hashes.SHA256())
    )
    return (
        cert.public_bytes(serialization.Encoding.PEM),
        chiave.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ),
        cert,
        chiave,
    )


def token(n: int) -> str:
    corpo = base64.urlsafe_b64encode(json.dumps({"user_name": f"a,b,{DEVICE_SESSIONE},d", "n": n}).encode())
    return "eyJhbGciOiJIUzI1NiJ9." + corpo.decode().rstrip("=") + "." + "f" * 64


@dataclass
class FintoCloud:
    cartella: Path
    password: str = "segreta"
    pin: str = "1234"
    stati: dict[str, dict[str, Any]] = field(default_factory=dict)
    vetture: list[dict[str, Any]] = field(default_factory=list)
    percorsi_che_non_esistono: set[str] = field(default_factory=set)
    token_scade_alla: int | None = None
    rinnovo_rotto: bool = False
    esiti_prima_del_si: int = 1
    chiamate: list[str] = field(default_factory=list)
    comandi: list[dict[str, str]] = field(default_factory=list)
    login: int = 0
    #: il consumo medio delle ultime settimane (kWh/100 km, come stringa); None: il cloud non lo dà
    consumo: str | None = "15.8"
    letture_consumo: int = 0

    def __post_init__(self) -> None:
        self.cert_app, self.chiave_app, *_ = certificato("app")
        _, _, self._cert_account, self._chiave_account = certificato("account")
        cert_server, chiave_server, *_ = certificato("server")
        (self.cartella / "server.pem").write_bytes(cert_server + chiave_server)
        (self.cartella / "fidati.pem").write_bytes(
            self.cert_app + self._cert_account.public_bytes(serialization.Encoding.PEM)
        )
        self.sale, self.info, self.ikm = "sale", "info", "ikm"
        self.chiave = cripto.chiave_firma(self.ikm, self.sale, self.info)
        self.token = token(0)
        self.refresh = "refresh-0"
        self._richieste_stato = 0

    # --- il server ------------------------------------------------------

    def ssl(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(self.cartella / "server.pem")
        ctx.load_verify_locations(self.cartella / "fidati.pem")
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/carownerservice/oversea/{resto:.*}", self._gestisci)
        return app

    @staticmethod
    def _risposta(data: Any = None, code: int = 0, message: str = "ok", http: int = 200) -> web.Response:
        return web.json_response({"code": code, "message": message, "data": data}, status=http)

    async def _gestisci(self, r: web.Request) -> web.Response:
        resto = r.match_info["resto"]
        self.chiamate.append(resto)
        peer = r.transport.get_extra_info("peercert") if r.transport else None
        cn = dict(x[0] for x in peer["subject"])["commonName"] if peer else None
        corpo = dict(parse_qsl(await r.text(), keep_blank_values=True))
        h = r.headers
        vuole_app = resto in ("acct/v1/login", "vehicle/v1/cert/sync")
        if cn != ("app" if vuole_app else "account"):
            return self._risposta(code=495, message=f"certificato sbagliato: {cn}", http=495)

        if resto == "acct/v1/login":
            return self._login(h, corpo)

        if h.get("deviceId") != DEVICE_SESSIONE or h.get("userId") != ID_UTENTE:
            return self._risposta(code=1, message="deviceId o userId sbagliati")
        # Il rinnovo arriva per forza col token scaduto: conta il refresh token.
        if resto != "acct/v1/token/refresh" and h.get("token") != self.token:
            return self._risposta(code=401, message="token invalid")

        if resto == "acct/v1/token/refresh":
            self._firma_standard(h, {"refreshToken": corpo["refreshToken"]})
            if self.rinnovo_rotto or corpo["refreshToken"] != self.refresh:
                return self._risposta(code=1, message="refresh token invalid")
            self.token, self.refresh = token(len(self.chiamate)), f"refresh-{len(self.chiamate)}"
            return self._risposta({"token": self.token, "refreshToken": self.refresh})

        if resto == "vehicle/v1/list":
            self._firma_standard(h, {})
            proprie = [v for v in self.vetture if not v.get("_condivisa")]
            condivise = [v for v in self.vetture if v.get("_condivisa")]
            return self._risposta({"bindcars": proprie, "sharedcars": condivise})

        if resto.startswith("vehicle/v1/status/get/"):
            percorso = resto.rsplit("/", 1)[-1]
            if percorso in self.percorsi_che_non_esistono:
                return web.Response(status=404, text="<html>404 Not Found</html>")
            self._firma_standard(h, {"vin": corpo["vin"]})
            self._richieste_stato += 1
            if self.token_scade_alla and self._richieste_stato == self.token_scade_alla:
                self.token = "scaduto-" + self.token
                return self._risposta(code=401, message="token expired")
            return self._risposta(self.stati[corpo["vin"]])

        if resto == "drivingRecord/v1/getLastNweeks100kmECAndRank":
            self._firma_standard(h, {"carvin": corpo["carvin"]})
            self.letture_consumo += 1
            if self.consumo is None:
                return self._risposta(code=1, message="no data")
            return self._risposta({"rankResult": {"hundredKmEC": self.consumo, "rank": "40%"}, "weeklyEC": []})

        if resto == "vehicle/v1/cert/sync":
            self._firma_standard(h, {})
            return self._risposta(None)

        if resto == "vehicle/v1/operPwd/verify":
            self._firma(
                h,
                [
                    *(h["acceptLanguage"], "1", h["deviceId"], "1", h["nonce"], corpo["operatePassword"]),
                    *("leapmotor", h["timestamp"], "1.12.3", corpo["vin"]),
                ],
            )
            if corpo["operatePassword"] != cripto.cifra_pin(self.pin, self.token):
                return self._risposta(code=7, message="wrong operate password")
            return self._risposta(None)

        if resto == "vehicle/v1/app/remote/ctl":
            pin = [corpo["operatePassword"]] if "operatePassword" in corpo else []
            self._firma(
                h,
                [
                    *(h["acceptLanguage"], "1", corpo["cmdContent"], corpo["cmdId"], h["deviceId"], "1", h["nonce"]),
                    *pin,
                    *("leapmotor", h["timestamp"], "1.12.3", corpo["vin"]),
                ],
            )
            self.comandi.append(corpo)
            self._attese = self.esiti_prima_del_si
            return self._risposta({"remoteCtlId": "RC1", "queryRemoteCtlResultTimeout": 5000, "queryInterval": 10})

        if resto == "vehicle/v1/app/remote/ctl/result/query":
            self._firma(
                h,
                [
                    *(h["acceptLanguage"], "1", h["deviceId"], "1", h["nonce"], corpo["remoteCtlId"]),
                    *("leapmotor", h["timestamp"], "1.12.3"),
                ],
            )
            if self._attese > 0:
                self._attese -= 1
                return self._risposta(0)
            return self._risposta(1)

        return web.Response(status=404, text="non c'è")

    def _login(self, h: Any, corpo: dict[str, str]) -> web.Response:
        attesa = cripto.firma_login(
            lingua=h["acceptLanguage"],
            device_id=h["deviceId"],
            email=corpo["email"],
            password=corpo["password"],
            nonce_=h["nonce"],
            timestamp=h["timestamp"],
        )
        assert h["sign"] == attesa, "firma del login sbagliata"
        assert corpo["policyId"] == "20260204" and corpo["loginMethod"] == "1"
        if corpo["password"] != self.password:
            return self._risposta(code=302010108, message="account or password error")
        self.login += 1
        self.token = token(100 + self.login)
        self.refresh = f"refresh-login-{self.login}"
        p12 = pkcs12.serialize_key_and_certificates(
            b"account",
            self._chiave_account,
            self._cert_account,
            None,
            serialization.BestAvailableEncryption(cripto.password_p12(ID_UTENTE, UID).encode()),
        )
        return self._risposta(
            {
                "id": int(ID_UTENTE),
                "uid": UID,
                "token": self.token,
                "refreshToken": self.refresh,
                "signIkm": self.ikm,
                "signSalt": self.sale,
                "signInfo": self.info,
                "base64Cert": base64.b64encode(p12).decode(),
            }
        )

    def _firma_standard(self, h: Any, extra: dict[str, str]) -> None:
        campi = {
            k: h[k]
            for k in ("acceptLanguage", "channel", "deviceId", "deviceType", "nonce", "source", "timestamp", "version")
        }
        campi.update(extra)
        self._firma(h, [v for _, v in sorted(campi.items())])

    def _firma(self, h: Any, pezzi: list[str]) -> None:
        attesa = hmac.new(self.chiave, "".join(pezzi).encode(), hashlib.sha256).hexdigest()
        assert h["sign"] == attesa, f"firma sbagliata: {pezzi}"
