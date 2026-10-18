# Aggiungere una marca

Una marca entra in GdAAuto quando la si vuole leggere **dal suo cloud**, senza
un'altra integrazione in mezzo. Per le auto che hanno già un'integrazione in
Home Assistant non serve: va bene la strada «È già in Home Assistant».

## Com'è fatta una marca

Una cartella in `custom_components/gdaauto/marche/<marca>/` con un
`__init__.py` che espone:

| Nome | Cosa |
| --- | --- |
| `ID`, `NOME` | `"leapmotor"`, `"Leapmotor"` |
| `CAMPI` | cosa chiedere nella configurazione: `nome`, `obbligatorio`, `segreto`, `lungo` |
| `VUOLE_PIN` | se i comandi vogliono un PIN a parte |
| `INTERVALLO_MINUTI` | ogni quanto leggerla da ferma, se l'utente non sceglie |
| `crea_da_voce(hass, entry, cartella)` | il cliente della marca, dalla configurazione |
| `comandi_di(vettura)` | i comandi che quell'auto accetta, coi nomi di GdAAuto |

Le tre marche di oggi entrano in tre modi diversi, e sono gli esempi da
copiare: **Leapmotor** con un modulo di campi (`CAMPI`), **Tesla** con
l'OAuth di Home Assistant e le credenziali applicative, **Stellantis** con
un OAuth fatto nel browser e il codice incollato.

Il cliente rispetta `Marca` in `marche/__init__.py`: `accedi`, `vetture`,
`stato`, e i comandi `chiudi`, `apri`, `trova`, `clima`, `ricarica`,
`limite_ricarica`, `destinazione`. Restituisce `Vettura` e `StatoVettura` di
`vettura.py`, e nient'altro della marca esce dalla sua cartella.

Gli errori stanno in `marche/errori.py` e sono di tutte: credenziali
sbagliate e certificati che non vanno fanno partire il reauth, un comando
rifiutato arriva a chi l'ha dato con il motivo, tutto il resto è un
aggiornamento non riuscito che si riprova. Un'auto che dorme non è un errore:
il cliente restituisce gli ultimi dati con `in_sonno` acceso.

Se il cloud ruota i token, il cliente riceve una funzione che li salva nella
configurazione (guarda Stellantis): GdAAuto si ricarica solo quando cambiano
le opzioni, non quando cambia un token.

## I passi

1. La cartella, coi nomi qui sopra.
2. Una riga in `MARCHE`, in `marche/__init__.py`.
3. I passi nel `config_flow.py`: `async_step_<marca>` e quelli che servono
   per entrare, più il ramo nel reauth.
4. Le traduzioni del passo in `strings.json` e `translations/`.
5. Le prove: un cloud finto come `tests/finto_cloud.py`, e i dati veri di un
   modello come in `tests/test_segnali.py`.

Le entità, le opzioni, i servizi, la diagnostica e le sorelle (GdAHome e
gdanav) non si toccano: lavorano su `StatoVettura`.
