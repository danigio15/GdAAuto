"""Gli errori del cloud Leapmotor, chiamati per quello che vogliono dire."""

from __future__ import annotations


class ErroreLeapmotor(Exception):
    """Qualunque cosa vada storta parlando col cloud."""


class ErroreRete(ErroreLeapmotor):
    """Il cloud non risponde, o risponde una cosa che non è JSON."""


class ErroreApi(ErroreLeapmotor):
    """Il cloud ha risposto, ma con un codice diverso da zero."""

    def __init__(self, cosa: str, codice: object = None, messaggio: str = "", http: int = 0) -> None:
        self.cosa = cosa
        self.codice = codice
        self.messaggio = messaggio
        self.http = http
        super().__init__(f"{cosa}: codice {codice}, HTTP {http}: {messaggio}")

    @property
    def token_scaduto(self) -> bool:
        testo = self.messaggio.lower()
        return "token" in testo and any(
            p in testo for p in ("invalid", "expired", "expire", "unauthorized", "not valid")
        )


class ErroreCredenziali(ErroreLeapmotor):
    """Email o password sbagliate, o l'account bloccato: non si riprova da soli."""


class ErroreCertificato(ErroreLeapmotor):
    """Il certificato dell'app manca o non si apre, o quello dell'account non si decifra."""


class ErrorePin(ErroreLeapmotor):
    """Un comando chiede il PIN dell'auto e non c'è, o il cloud non lo accetta."""
