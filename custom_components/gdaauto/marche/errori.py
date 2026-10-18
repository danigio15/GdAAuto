"""Gli errori delle marche, chiamati per quello che vogliono dire.

Sono gli stessi per tutte: ``auto.py`` li traduce in Home Assistant senza
sapere da quale cloud arrivano.
"""

from __future__ import annotations


class ErroreMarca(Exception):
    """Qualunque cosa vada storta parlando col cloud di una marca."""


class ErroreRete(ErroreMarca):
    """Il cloud non risponde, o risponde una cosa che non si capisce."""


class ErroreApi(ErroreMarca):
    """Il cloud ha risposto, ma con un errore."""

    def __init__(self, cosa: str, codice: object = None, messaggio: str = "", http: int = 0) -> None:
        self.cosa = cosa
        self.codice = codice
        self.messaggio = messaggio
        self.http = http
        super().__init__(f"{cosa}: codice {codice}, HTTP {http}: {messaggio}")

    @property
    def token_scaduto(self) -> bool:
        testo = self.messaggio.lower()
        segni = ("invalid", "expired", "expire", "unauthorized", "not valid")
        return "token" in testo and any(p in testo for p in segni)


class ErroreCredenziali(ErroreMarca):
    """Credenziali rifiutate o scadute: serve rientrare, non si riprova da soli."""


class ErroreCertificato(ErroreMarca):
    """Un certificato manca, non si apre o il cloud lo rifiuta."""


class ErrorePin(ErroreMarca):
    """Un comando chiede un PIN che non c'è, o che il cloud non accetta."""


class ErroreComando(ErroreMarca):
    """L'auto o il cloud non eseguono il comando, e dicono perché."""


class AutoAddormentata(ErroreMarca):
    """L'auto dorme: il cloud non ha dati freschi e non la si sveglia per leggerla."""
