"""Le costanti di GdAAuto."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "gdaauto"

# Da dove arriva l'auto.
CONF_FONTE = "fonte"
FONTE_CLOUD = "cloud"  # GdAAuto parla col cloud della marca
FONTE_HA = "home_assistant"  # l'auto c'è già in Home Assistant, con la sua integrazione

CONF_MARCA = "marca"
CONF_DISPOSITIVO = "dispositivo"
CONF_MAPPA = "mappa"
CONF_NOME = "nome"

# Le opzioni.
CONF_PIN = "pin"
CONF_INTERVALLO = "intervallo_minuti"
CONF_INTERVALLO_IN_MOTO = "intervallo_in_moto_secondi"
CONF_RISPARMIO = "risparmio"
CONF_COMANDI_ATTIVI = "comandi_attivi"

INTERVALLO_PREDEFINITO = 5  # minuti, da ferma
INTERVALLO_IN_MOTO_PREDEFINITO = 60  # secondi, in marcia o in carica
INTERVALLO_RISPARMIO = timedelta(minutes=15)  # chiusa, ferma, senza cavo
INTERVALLO_MINIMO = timedelta(seconds=30)
AGGIORNA_A_MANO_OGNI = timedelta(seconds=30)

# Dopo un comando si rilegge l'auto: il cloud ci mette un po' a saperlo.
RILEGGI_DOPO_COMANDO = 8.0

# I comandi che GdAAuto conosce, con lo stesso nome per tutte le marche.
COMANDO_CHIUDI = "chiudi"
COMANDO_TROVA = "trova"
COMANDO_CLIMA = "clima"
COMANDO_RICARICA = "ricarica"
COMANDO_LIMITE = "limite_ricarica"
COMANDO_DESTINAZIONE = "destinazione"
COMANDI = (COMANDO_CHIUDI, COMANDO_TROVA, COMANDO_CLIMA, COMANDO_RICARICA, COMANDO_LIMITE, COMANDO_DESTINAZIONE)

# Aprire l'auto da lontano è il comando che fa più danni se lo dà la persona
# sbagliata: di base è spento, si accende nelle opzioni.
COMANDI_ATTIVI_PREDEFINITI = [COMANDO_TROVA, COMANDO_CLIMA, COMANDO_RICARICA, COMANDO_LIMITE, COMANDO_DESTINAZIONE]
COMANDO_APRI = "apri"

SERVIZIO_DESTINAZIONE = "invia_destinazione"
SERVIZIO_AGGIORNA = "aggiorna"
