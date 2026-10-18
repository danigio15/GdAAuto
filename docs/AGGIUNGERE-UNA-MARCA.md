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
| `crea(sessione_http, dati, cartella, lingua)` | il cliente della marca |
| `comandi_di(vettura)` | i comandi che quell'auto accetta, coi nomi di GdAAuto |

Il cliente rispetta `Marca` in `marche/__init__.py`: `accedi`, `vetture`,
`stato`, e i comandi `chiudi`, `apri`, `trova`, `clima`, `ricarica`,
`limite_ricarica`, `destinazione`. Restituisce `Vettura` e `StatoVettura` di
`vettura.py`, e nient'altro della marca esce dalla sua cartella.

Gli errori: credenziali sbagliate e certificati che non vanno fanno partire il
reauth; tutto il resto è un aggiornamento non riuscito che si riprova. Oggi
`auto.py` riconosce le classi di errore di Leapmotor; una marca nuova le
eredita o se ne aggiunge una base comune.

## I passi

1. La cartella, coi nomi qui sopra.
2. Una riga in `MARCHE`, in `marche/__init__.py`.
3. Un passo nel `config_flow.py`, uguale a quello di Leapmotor:
   `async_step_<marca>` che chiama `_passo_marca("<marca>", user_input)`.
4. Le traduzioni del passo in `strings.json` e `translations/`.
5. Le prove: un cloud finto come `tests/finto_cloud.py`, e i dati veri di un
   modello come in `tests/test_segnali.py`.

Le entità, le opzioni, i servizi, la diagnostica e le sorelle (GdAHome e
gdanav) non si toccano: lavorano su `StatoVettura`.
