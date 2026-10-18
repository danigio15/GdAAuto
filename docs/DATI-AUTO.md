# I dati dell'auto: un nome solo per GdAAuto, gdanav e GdAHome

Questo è il contratto fra le tre. GdAAuto crea un'entità per ogni dato che
l'auto manda; gdanav e GdAHome la riconoscono **dalla chiave**, non dal nome
mostrato, e la usano per la stessa cosa.

**Come si riconosce un'entità GdAAuto.** Nel registro delle entità di Home
Assistant ha `platform: gdaauto` e `translation_key` uguale alla chiave della
tabella. L'`entity_id` è `<dominio>.<nome_auto>_<chiave>`, ma il nome
dell'auto lo sceglie chi la configura: la chiave sicura è `translation_key`.
Per un'entità di un'altra integrazione restano le regole che gdanav e GdAHome
usano già (classe, unità, parole).

Le unità sono quelle di GdAAuto. Home Assistant può mostrarle in un'altra
unità (miglia, °F, bar): chi legge converte dall'unità dello stato.

## Batteria, autonomia, energia — servono al consumo

| Chiave | Dominio | Unità | Cosa | gdanav (`stato_auto`) | GdAHome |
| --- | --- | --- | --- | --- | --- |
| `batteria` | sensor | % | carica della batteria di trazione | `batteria` | `dm.ev_batteria_auto` |
| `autonomia` | sensor | km | autonomia stimata dall'auto | `autonomia_km` | `dm.ev_autonomia` |
| `energia_residua` | sensor | kWh | energia che resta nella batteria | `energia_kwh` | `dm.ev_energia_residua` |
| `capacita_batteria` | sensor | kWh | capacità utile della batteria | `capacita_kwh` | `dm.ev_capacita_batteria` (e la capacità della scheda auto) |
| `consumo_medio` | sensor | kWh/100 km | consumo medio misurato dall'auto (ultime settimane) | `consumo_medio_kwh100` | `dm.ev_consumo_medio` |
| `temperatura_esterna` | sensor | °C | temperatura fuori | `temperatura_esterna_c` | `dm.ev_temperatura_esterna` |
| `temperatura_batteria` | sensor | °C | temperatura della batteria di trazione | `temperatura_batteria_c` | `dm.ev_temperatura_batteria` |
| `temperatura_interna` | sensor | °C | temperatura nell'abitacolo | `temperatura_interna_c` | `dm.ev_temperatura_interna` |
| `potenza_riscaldatore` | sensor | W | potenza del riscaldatore (abitacolo e batteria) | `potenza_clima_w` | `dm.ev_potenza_riscaldatore` |
| `clima_acceso` | binary_sensor | on/off | clima acceso | `clima_acceso` | `dm.ev_clima` |
| `batteria_in_riscaldamento` | binary_sensor | on/off | la batteria si sta scaldando | `batteria_in_riscaldamento` | `dm.ev_batteria_in_riscaldamento` |
| `velocita` | sensor | km/h | velocità | `velocita_kmh` | `dm.ev_velocita` |
| `potenza` | sensor | kW | potenza usata per muoversi (0 da ferma) | `potenza_kw` | `dm.ev_potenza_istantanea` |
| `odometro` | sensor | km | contachilometri | `odometro_km` | `dm.ev_odometro` |
| `autonomia_nominale` | sensor | km | autonomia di omologazione (WLTP/CLTC) con la carica di adesso | — | `dm.ev_autonomia_nominale` |

## Ricarica

| Chiave | Dominio | Unità | Cosa | gdanav | GdAHome |
| --- | --- | --- | --- | --- | --- |
| `in_carica` | binary_sensor | on/off | sta caricando | `in_carica` | `dm.ev_stato_ricarica` (se manca `stato_ricarica`) |
| `stato_ricarica` | sensor (enum) | `non_in_carica`, `in_carica`, `completata`, `errore`, `programmata`, `rigenerazione`, `in_pausa` | stato della ricarica | — | `dm.ev_stato_ricarica` |
| `cavo_collegato` | binary_sensor | on/off | cavo collegato | — | `dm.ev_cavo_collegato` |
| `potenza_ricarica` | sensor | kW | potenza che entra in batteria | `potenza_carica_kw` | `dm.ev_potenza_ricarica` |
| `tempo_ricarica` | sensor | min | minuti alla fine della ricarica | — | `dm.ev_tempo_ricarica` |
| `limite_ricarica` | number | % | limite di ricarica (si comanda) | — | `dm.ev_target_soc` |
| `ricarica_rapida` | binary_sensor | on/off | ricarica in corrente continua | — | `dm.ev_ricarica_rapida` |
| `ricarica_programmata` | binary_sensor | on/off | ricarica programmata accesa | — | `dm.ev_ricarica_programmata` |
| `ricarica_inizio`, `ricarica_fine` | sensor | «HH:MM» | orari della ricarica programmata | — | `dm.ev_ricarica_inizio`, `dm.ev_ricarica_fine` |
| `v2l` | binary_sensor | on/off | l'auto dà corrente fuori (V2L) | — | `dm.ev_v2l` |
| `comando_ricarica` | switch | on/off | avvia e ferma la ricarica | — | `dm.ev_comando_ricarica` |

## Storico (riletto ogni mezz'ora)

| Chiave | Dominio | Unità | Cosa | gdanav | GdAHome |
| --- | --- | --- | --- | --- | --- |
| `energia_oggi_guida`, `energia_oggi_clima`, `energia_oggi_altro` | sensor | kWh | energia da mezzanotte, divisa per uso | — | `dm.ev_energia_oggi_guida`, `…_clima`, `…_altro` |
| `km_7_giorni`, `energia_7_giorni` | sensor | km, kWh | ultimi sette giorni, oggi compreso | — | `dm.ev_km_7_giorni`, `dm.ev_energia_7_giorni` |
| `ultima_ricarica_energia` | sensor | kWh | energia dell'ultima ricarica | — | `dm.ev_ultima_ricarica_energia` |
| `ultima_ricarica_tipo` | sensor (enum) | `ac`, `dc` | tipo dell'ultima ricarica | — | `dm.ev_ultima_ricarica_tipo` |
| `ultima_ricarica_inizio`, `ultima_ricarica_fine` | sensor | data e ora | quando | — | `dm.ev_ultima_ricarica_inizio`, `…_fine` |

## L'auto

| Chiave | Dominio | Cosa | GdAHome |
| --- | --- | --- | --- |
| `serratura` | lock | serratura (si comanda) | `dm.ev_portiere` |
| `serratura` | binary_sensor | serratura, quando l'auto non la fa comandare (acceso = aperta) | `dm.ev_portiere` |
| `portiere` | binary_sensor | una portiera aperta | `dm.ev_portiere` (se manca `serratura`) |
| `bagagliaio` | binary_sensor | bagagliaio aperto | `dm.ev_bagagliaio` |
| `finestrini` | binary_sensor | un finestrino aperto | `dm.ev_finestrini` |
| `quadro` | binary_sensor | quadro acceso (pronta a partire) | `dm.ev_motore` |
| `marcia` | sensor (enum) | `parcheggio`, `avanti`, `folle`, `retromarcia` | `dm.ev_marcia` |
| `posizione` | device_tracker | dov'è | `dm.ev_posizione` |
| `pneumatici_ant_sx`, `…_ant_dx`, `…_post_sx`, `…_post_dx` | sensor (kPa) | pressione di ogni gomma | `dm.ev_pneumatico_ant_sx` … |
| `pneumatici_allarme` | binary_sensor | una gomma in allarme | `dm.ev_pneumatici` |
| `antifurto` | binary_sensor | antifurto inserito | `dm.ev_allarme` |
| `sentinella` | binary_sensor | modalità sentinella | `dm.ev_sentinella` |
| `freno_stazionamento` | binary_sensor | freno di stazionamento tirato | `dm.ev_freno_stazionamento` |
| `sbrinamento` | binary_sensor | sbrinamento del parabrezza | `dm.ev_sbrinamento` |
| `in_sonno` | binary_sensor | l'auto dorme: i dati sono di quando si è addormentata | `dm.ev_in_sonno` |
| `ultimo_aggiornamento` | sensor (timestamp) | quando l'auto ha mandato i dati | `dm.ev_ultimo_aggiornamento` |
| `clima` | switch | accende e spegne il clima | `dm.ev_comando_clima` |
| `carburante` | sensor (%) | serbatoio (ibride col range extender) | `dm.ev_carburante` |
| `autonomia_carburante`, `autonomia_totale` | sensor (km) | autonomia a carburante e totale | `dm.ev_autonomia_carburante`, `dm.ev_autonomia_totale` |

## Come gdanav usa i dati del consumo

- **`energia_kwh`** al posto della percentuale: un decimo di kWh invece
  dell'1 %, così il consumo vero si misura dopo pochi km.
- **`capacita_kwh`**: la capacità vera dell'auto al posto di quella del
  catalogo, in ogni conto fra percentuale e kWh.
- **`consumo_medio_kwh100`**: il consumo che l'auto ha misurato, per partire
  giusti prima del primo viaggio e correggere il fattore imparato.
- **`temperatura_esterna_c`** aggiornata anche durante il viaggio, quando si
  ricalcola.
- **`potenza_clima_w`**, **`clima_acceso`**, **`batteria_in_riscaldamento`**:
  il carico del clima misurato invece di quello stimato dalla temperatura.
- **`temperatura_batteria_c`**: ricarica più lenta con la batteria fredda.

Un dato che manca lascia il calcolo com'era: niente si inventa.

## Regola per chi aggiunge un dato

Un dato nuovo entra **qui prima**, poi nelle tre: in GdAAuto con la sua
chiave, in gdanav nel riconoscimento e nel messaggio `stato_auto`, in GdAHome
nella casella `dm.ev_<chiave>`. La prova `tests/test_dati_auto.py` di GdAAuto
controlla che ogni chiave della tabella esista davvero.
