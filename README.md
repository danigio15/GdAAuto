<p align="center">
  <img src="docs/logo/icona_512.png" alt="GdAAuto" width="220">
</p>

<h3 align="center">Tutte le auto elettriche in Home Assistant, con la stessa faccia.<br>E dentro GdAHome e gdanav da sole.</h3>

<p align="center">
  Leapmotor, Tesla e Stellantis dal loro cloud · Kia, BMW, Renault e tutte le altre da Home Assistant
</p>

---

## Cos'è

GdAAuto è un'integrazione per Home Assistant. Prende la tua auto elettrica,
di qualunque marca, e la fa diventare **un dispositivo sempre uguale**: la
batteria si chiama `sensor.<auto>_batteria`, il cavo
`binary_sensor.<auto>_cavo_collegato`, la serratura `lock.<auto>_serratura`,
con le stesse unità e le stesse classi per tutte.

Per questo **la plancia di GdAHome e gdanav la riconoscono da sole**: scegli
il dispositivo GdAAuto e tutte le caselle si riempiono, senza scrivere un
`entity_id`. Una prova automatica lo controlla col codice vero di tutte e due
a ogni modifica.

## Due strade per entrare

| | Come | Per chi |
| --- | --- | --- |
| **La marca** | GdAAuto parla direttamente col cloud del costruttore | **Leapmotor**: T03, B05, B10, C10, C16 e i modelli che verranno · **Tesla**: tutte, con l'API ufficiale · **Stellantis**: Peugeot, Citroën, DS, Opel, Vauxhall |
| **Home Assistant** | L'auto ha già la sua integrazione; GdAAuto la legge da lì | **Kia e Hyundai, BMW, Renault, Volkswagen, Polestar, Fiat, Jeep** e tutte le altre |

Quello che ogni marca nativa sa fare:

| | Legge | Comanda |
| --- | :---: | --- |
| **Leapmotor** | ✓ | chiudi e apri, clima, ricarica, limite, trova, destinazione (col PIN dell'auto) |
| **Tesla** | ✓ | chiudi e apri, clima, ricarica, limite, trova, destinazione |
| **Stellantis** | ✓ | per ora nessuno: i comandi passano da MQTT con un codice via SMS, e arriveranno |

Con la seconda strada GdAAuto guarda le entità del dispositivo dell'auto e
**propone da solo** quale è la batteria, quale l'autonomia, quale la serratura.
Tu controlli e confermi. I comandi passano per le entità vere dell'auto:
chiudere usa la sua serratura, il clima il suo clima, il limite di ricarica il
suo cursore.

## Cosa c'è nel dispositivo

| | Entità |
| --- | --- |
| **Batteria e strada** | batteria, autonomia, contachilometri, velocità, potenza, energia residua, marcia |
| **Ricarica** | in carica, cavo collegato, stato della ricarica, potenza di ricarica, tempo alla fine, limite di ricarica |
| **L'auto** | serratura, portiere, bagagliaio, finestrini, quadro acceso, clima, sentinella, posizione |
| **Diagnostica** | pressione delle quattro gomme, ultimo aggiornamento, pulsante «Aggiorna» |
| **Comandi** | chiudi e apri, clima, avvia e ferma la ricarica, limite di ricarica, trova l'auto (clacson e luci), manda una destinazione al navigatore dell'auto |

Un'entità nasce solo se la tua auto quel dato lo dice: una T03 non ha la
temperatura della batteria, e non avrai un sensore sempre «sconosciuto». Se il
dato arriva più tardi, per esempio alla prima ricarica, l'entità nasce allora.

## Leapmotor

1. **Un account dedicato.** Il cloud Leapmotor tiene una sola sessione per
   account: con lo stesso account l'app sul telefono verrebbe disconnessa.
   Crea un secondo account e, dall'app ufficiale, **condividi l'auto** con lui.
2. **Il certificato dell'app.** Il cloud vuole il certificato dell'app
   Leapmotor (`app_cert.pem`, `app_key.pem`), che non è pubblico e non è in
   questa repository. Lo incolli nella configurazione, oppure metti i due file
   in `/config/gdaauto/`.
3. **Il PIN dell'auto**, nelle opzioni, serve per i comandi. Senza PIN si
   legge tutto e non si comanda niente; la destinazione al navigatore va anche
   senza.

**Quanto spesso legge.** Ogni 5 minuti da ferma, ogni minuto in marcia o in
carica, ogni 15 minuti quando è chiusa e senza cavo. Si cambia nelle opzioni.
Quando qualcuno preme «Aggiorna», o gdanav in viaggio chiede dati freschi, si
legge subito: una lettura sola anche se la chiedono in tanti, mai più di una
ogni 30 secondi.

## Tesla

GdAAuto usa la **Fleet API**, l'API ufficiale di Tesla. Serve un'app
sviluppatore Tesla:

1. **L'app su [developer.tesla.com](https://developer.tesla.com).** Indirizzo
   di ritorno `https://my.home-assistant.io/redirect/oauth`, permessi su dati,
   posizione, comandi e ricarica dell'auto. L'app va registrata nella regione
   dell'auto, con la chiave pubblica pubblicata sul tuo dominio: è lo stesso
   passo che fa l'integrazione Tesla Fleet di Home Assistant. **Se usi già
   Tesla Fleet, l'app ce l'hai: va bene la stessa.**
2. **Le credenziali in Home Assistant.** Impostazioni → Dispositivi e servizi
   → Credenziali applicative → aggiungi, integrazione **GdAAuto**, con il
   Client ID e il Client Secret dell'app.
3. **Aggiungi GdAAuto → Tesla**, ed entra col tuo account Tesla. La regione
   la capisce da sola.

**L'auto che dorme non si sveglia per leggerla**: ogni lettura e ogni
sveglia costano a chi ha l'app sviluppatore. Da addormentata restano gli
ultimi dati, con l'entità «In sonno» accesa; si sveglia solo per un comando.
Da ferma si legge ogni 10 minuti.

**I comandi firmati.** Le Tesla dal 2021 vogliono i comandi firmati con la
chiave dell'app (Vehicle Command Protocol). Indica nelle opzioni l'indirizzo
del proxy ufficiale di Tesla, `tesla-http-proxy`, che li firma: GdAAuto
manda lì i comandi e continua a leggere dalla Fleet API. Senza proxy i
comandi vanno solo sulle auto più vecchie, e GdAAuto dice perché quando
non passano.

## Stellantis

Peugeot, Citroën, DS, Opel e Vauxhall, elettriche e ibride plug-in, col
cloud dell'app del marchio (MyPeugeot, MyCitroën, MyDS, MyOpel, MyVauxhall).
Fiat, Jeep, Alfa Romeo e Lancia usano un altro cloud: per loro c'è la
strada «È già in Home Assistant».

1. **Aggiungi GdAAuto → Stellantis**, scegli l'app e il paese.
2. **Entra nel browser di un computer**, col link che GdAAuto ti mostra e gli
   strumenti per sviluppatori aperti (F12, scheda Rete).
3. Alla fine il browser prova ad aprire un indirizzo come
   `mymap://oauth2redirect/it?code=…` e non ci riesce: **copialo e incollalo**
   in GdAAuto. Va bene anche solo il codice.

Email e password restano nel tuo browser: GdAAuto non le vede e non le
manda a nessuno. Il token si rinnova da solo; se un giorno non va più, Home
Assistant chiede di rifare il login.

## La sicurezza dei comandi

- **Aprire l'auto da lontano è spento** finché non lo accendi nelle opzioni.
  Chiudere si può sempre.
- Ogni comando si accende o si spegne a parte, nelle opzioni.
- Il PIN, la password, i token e i certificati non escono mai da Home
  Assistant. I certificati stanno in `.storage/gdaauto`, leggibili solo da
  Home Assistant.
- La diagnostica toglie email, password, PIN, token, VIN, targa e posizione.

## Come arriva a GdAHome e a gdanav

- **GdAHome.** Nella plancia, quando aggiungi l'auto scegliendo integrazione
  e dispositivo, scegli **GdAAuto** e la tua auto. Batteria, autonomia, cavo,
  potenza, limite, serratura, finestrini, gomme e posizione si riempiono da
  soli.
- **gdanav.** Impostazioni → Dispositivi e servizi → Aggiungi integrazione →
  **gdanav**, e come dispositivo scegli quello **GdAAuto**. gdanav riconosce
  batteria, autonomia, ricarica, potenza, velocità, contachilometri,
  temperatura della batteria e posizione. In viaggio, quando gdanav chiede
  dati freschi, GdAAuto rilegge l'auto.

## Installare

1. **HACS** → i tre puntini → **Repository personalizzati**: incolla
   `https://github.com/danigio15/GdAAuto`, categoria **Integrazione**.
2. Cerca **GdAAuto**, installalo e **riavvia** Home Assistant.
3. **Impostazioni → Dispositivi e servizi → Aggiungi integrazione → GdAAuto**,
   e scegli da dove arriva l'auto.

Serve Home Assistant 2025.3 o più nuovo. Va su ogni installazione, anche
Container e Core: è un'integrazione, non un add-on.

## Servizi

| Servizio | Cosa fa |
| --- | --- |
| `gdaauto.invia_destinazione` | manda nome, latitudine e longitudine al navigatore dell'auto |
| `gdaauto.aggiorna` | rilegge l'auto adesso |

## Rispetto alle integrazioni Leapmotor che ci sono già

GdAAuto nasce guardando [kerniger/leapmotor-ha](https://github.com/kerniger/leapmotor-ha),
[markoceri/leapmotor-api](https://github.com/markoceri/leapmotor-api),
[helix-git/ha-leapmotor-gateway](https://github.com/helix-git/ha-leapmotor-gateway)
e [leapmotor-mate](https://github.com/ProtossBlaster/leapmotor-mate). Cosa fa
di diverso:

- **Più marche, una faccia.** Le altre sono solo Leapmotor; GdAAuto mette
  Leapmotor, Tesla, Stellantis e le auto di Home Assistant sotto le stesse
  entità.
- **Asincrona davvero.** Il TLS reciproco col cloud lo fa `aiohttp`; non si
  lancia `curl` in un processo a parte per ogni richiesta.
- **Il ritmo segue l'auto**: più spesso in marcia e in carica, meno da chiusa.
- **Aprire da lontano è spento di base.**
- **Provata contro un cloud finto col TLS reciproco vero**: login, firme,
  rinnovo del token, ripiego sulla C10, comandi col PIN.

Cosa ha preso da loro, e con quale licenza, è in
[RINGRAZIAMENTI.md](RINGRAZIAMENTI.md).

## Per chi sviluppa

Come aggiungere una marca: [docs/AGGIUNGERE-UNA-MARCA.md](docs/AGGIUNGERE-UNA-MARCA.md).

    pip install -r requirements_test.txt ruff
    ruff check custom_components tests && ruff format --check custom_components tests
    pytest

Le prove di `tests/test_sorelle.py` cercano gdanav e gdahomeapp accanto a
questa cartella, oppure in `GDANAV_DIR` e `GDAHOME_DIR`; senza, si saltano.
Il logo si rifà da `docs/logo/logo.svg` con `node docs/logo/rendi.mjs`.

## Avvertenze

GdAAuto non è prodotto né sostenuto da Leapmotor, Tesla, Stellantis o altri
costruttori. Il cloud Leapmotor non è documentato pubblicamente: può cambiare
senza preavviso, e l'uso resta sotto le sue condizioni. I comandi agiscono su
un'auto vera.

## Licenza

Licenza d'uso di GdAAuto 1.0 ([LICENSE](LICENSE)): il codice è pubblico per
essere letto, ma non è open source. Titolare Giovanni d'Aniello, distributore
CRV Immobiliare S.r.l.
