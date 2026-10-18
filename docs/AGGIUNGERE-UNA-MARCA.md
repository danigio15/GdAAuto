# Aggiungere una marca

Una marca entra in GdAAuto quando la si vuole leggere **dal suo cloud**, senza
un'altra integrazione in mezzo. Per le auto che hanno già un'integrazione in
Home Assistant non serve: basta una riga nel catalogo che dica quale
installare, e GdAAuto la legge da lì.

## Il catalogo

`marche/catalogo.py` elenca tutti i marchi che chi configura può scegliere.
Ogni `Marchio` dice:

| Campo | Cosa |
| --- | --- |
| `id`, `nome` | `"kia"`, `"Kia"` |
| `nativo` | l'`ID` della marca nativa che lo serve, o niente |
| `preimpostati` | i dati già noti del marchio, che il modulo non chiede (`{"marchio": "kia"}`, `{"app": "MyOpel"}`) |
| `ha`, `link` | l'integrazione di Home Assistant da usare se non è nativo |

Un marchio che punta a una marca nativa non ancora presente passa da solo
alla strada di Home Assistant: si può scrivere il catalogo prima della marca.

## Com'è fatta una marca

Una cartella in `custom_components/gdaauto/marche/<marca>/`. Il registro in
`marche/__init__.py` la trova da solo: ogni sottocartella con un `ID` è una
marca. Il suo `__init__.py` dichiara i nomi del contratto, descritti per
intero in `marche/base.py`:

| Nome | Cosa |
| --- | --- |
| `ID`, `NOME` | come si chiama nel codice e sullo schermo |
| `ACCESSO` | come si entra: `ACCESSO_CAMPI`, `ACCESSO_DISPOSITIVO`, `ACCESSO_OAUTH_HA`, `ACCESSO_CODICE_BROWSER` |
| `CAMPI` | le domande del modulo, come `Campo`: solo nomi di `VOCABOLARIO`, che hanno già le traduzioni |
| `NOTA` | due righe per chi configura, `{"it": …, "en": …}` (facoltativa) |
| `VUOLE_PIN` | se i comandi vogliono un PIN a parte, chiesto nelle opzioni |
| `SENZA_COMANDI` | se la marca si legge soltanto: le opzioni dei comandi spariscono |
| `INTERVALLO_MINUTI` | ogni quanto leggerla da ferma, se l'utente non sceglie |
| `INTERVALLO_IN_MOTO_SECONDI` | e in marcia o in carica (facoltativo, se no 60) |
| `comandi_di(vettura)` | i comandi che quell'auto accetta, coi nomi di GdAAuto |
| `crea_da_voce(hass, entry, cartella)` | il cliente della marca, dalla configurazione salvata |

E, secondo l'accesso:

- **`campi`**: `async prova_accesso(hass, dati) -> Accesso`. Entra davvero
  con quello che l'utente ha scritto e restituisce cosa salvare, le auto, un
  `unique_id` e il titolo. Il modulo lo costruisce il config flow da `CAMPI`;
  il reauth lo riusa, con i segreti nascosti. Esempi: Leapmotor, Renault,
  Uconnect, Mercedes, MG, Toyota, Polestar.
- **`dispositivo`**: il login si fa su un altro schermo con un codice.
  `async avvia_dispositivo(hass, dati) -> Dispositivo` dà il link e il codice;
  `async completa_dispositivo(hass, dati, dispositivo) -> Accesso` solleva
  `InAttesa` finché l'utente non ha confermato. Esempio: BMW.
- **`oauth_ha`** e **`codice_browser`** hanno i loro passi nel config flow:
  Tesla (credenziali applicative di Home Assistant) e Stellantis (login nel
  browser e codice incollato). Una marca nuova che entra così va aggiunta lì.

Un codice mandato per email o SMS durante `prova_accesso` si dice sollevando
`CodiceInviato`: il modulo torna indietro chiedendo il campo `codice`. Un
codice sbagliato è `CodiceSbagliato`.

## Il cliente

Quello che `crea_da_voce` restituisce rispetta `Marca` in
`marche/__init__.py`: `accedi`, `vetture`, `stato`, e i comandi `chiudi`,
`apri`, `trova`, `clima`, `ricarica`, `limite_ricarica`, `destinazione`.
Restituisce `Vettura` e `StatoVettura` di `vettura.py`, e nient'altro della
marca esce dalla sua cartella.

Gli errori stanno in `marche/errori.py` e sono di tutte: `ErroreCredenziali`
e `ErroreCertificato` fanno partire il reauth, `ErroreComando` arriva a chi
ha dato il comando con il motivo, `ErrorePin` dice che manca o è sbagliato il
PIN, tutto il resto (`ErroreRete`, `ErroreApi`) è un aggiornamento non
riuscito che si riprova. Un'auto che dorme non è un errore: il cliente
restituisce gli ultimi dati con `in_sonno` acceso, e non la sveglia per
leggerla se svegliarla costa (Tesla) o consuma la batteria a 12 V.

Se il cloud ruota i token, il cliente riceve una funzione `salva` che li
scrive nella configurazione: GdAAuto si ricarica solo quando cambiano le
opzioni, non quando cambia un token.

Per le sessioni HTTP si usa sempre `async_get_clientsession(hass)`, anche
col TLS reciproco: il certificato si passa a ogni richiesta (guarda
Leapmotor).

## I passi

1. La cartella, coi nomi qui sopra.
2. Il marchio (o i marchi) in `marche/catalogo.py`, con `nativo` e
   `preimpostati`.
3. Se serve un campo che non c'è, aggiungerlo a `VOCABOLARIO` e alle
   traduzioni (`strings.json`, `translations/it.json`, `translations/en.json`).
4. Le prove, in `tests/test_<marca>.py`: un cloud finto con `aiohttp` su
   `127.0.0.1` (la fixture `socket_enabled`), i dati veri di un modello in
   `tests/<marca>_campioni.py`, l'accesso, il rinnovo del token, i comandi e
   gli errori. `tests/test_contratto.py` controlla da solo che la marca
   rispetti il contratto e che il catalogo la punti bene;
   `tests/test_telaio.py` prova il config flow con una marca finta per ogni
   tipo di accesso.
5. Se il codice nasce da un progetto MIT o Apache, la sua licenza va in
   `RINGRAZIAMENTI.md`. Da un progetto GPL o AGPL si prendono solo i fatti
   (indirizzi, nomi dei campi), mai il codice.

Le entità, le opzioni, i servizi, la diagnostica e le sorelle (GdAHome e
gdanav) non si toccano: lavorano su `StatoVettura`.
