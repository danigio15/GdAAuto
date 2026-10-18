# Ringraziamenti e codice di altri

GdAAuto parla col cloud Leapmotor grazie al lavoro di chi l'ha capito prima,
leggendo l'app ufficiale. Quello che è venuto da loro, e con quale licenza.

## kerniger/leapmotor-ha — licenza MIT

<https://github.com/kerniger/leapmotor-ha>

Da qui vengono, riscritti in `custom_components/gdaauto/marche/leapmotor/cripto.py`:

- la tabella S-box e le 32 chiavi di giro di SM4 con cui si ricava la password
  del certificato dell'account;
- la chiave e l'IV di riserva per cifrare il PIN, e il modo di ricavarli dal token;
- l'ordine dei campi nelle firme del login, delle richieste e dei comandi;
- il corpo intero dello spegnimento del clima che la T03 vuole;
- come leggere davvero la ricarica sulle auto europee (il codice 1149 da solo
  non basta: si guarda anche se entra corrente), la serratura solo con 1 o 0,
  l'ordine delle gomme, la marcia (1 retromarcia, 3 avanti), i dati vecchi di
  più di 15 minuti come auto addormentata, e i segnali di autonomia di
  omologazione, ricarica rapida, riscaldamento della batteria, freno di
  stazionamento, sbrinamento, allarme gomme e range extender;
- gli indirizzi e le firme dello storico: l'energia di oggi per guida, clima
  e resto, i km e l'energia degli ultimi sette giorni, le sessioni di
  ricarica (fuori da `/oversea`, col corpo in JSON), e la potenza del
  riscaldatore (1348).

La licenza MIT chiede che il suo avviso accompagni il codice:

```
MIT License

Copyright (c) 2026 kerniger

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## ProtossBlaster/leapmotor-mate — fatti osservati

<https://github.com/ProtossBlaster/leapmotor-mate>

Licenza AGPL-3.0: GdAAuto non ne prende codice, solo **fatti** osservati su
auto vere. Che leggere il cloud non sveglia l'auto (e quindi si può leggere
spesso), che una C10 manda 0 km di autonomia quando si addormenta, che una B10
tiene acceso il segnale della presa per qualche minuto dopo averla staccata e
che 2 vuol dire V2L, che i finestrini delle B10 dicono 2 quando sono aperti, e
la conferma dell'ordine delle gomme.

## markoceri/leapmotor-certs — il certificato dell'app

<https://github.com/markoceri/leapmotor-certs>

Tiene il certificato e la chiave dell'app Leapmotor, estratti dall'app e
uguali per tutti. Non ha una licenza, e sono materiale di Leapmotor: GdAAuto
non li contiene e non li ridistribuisce. Quando chi configura non dà i suoi,
Home Assistant li scarica da lì, ne controlla nome, scadenza e chiave, e li
salva nella configurazione di chi li ha chiesti.

## markoceri/leapmotor-api — documentazione

<https://github.com/markoceri/leapmotor-api>

La sua documentazione (`docs/api.md`, `docs/vehicles.md`) descrive gli
indirizzi del cloud, i numeri dei segnali delle C10 e B10 e i comandi. Da lì
vengono i **fatti** che GdAAuto usa: quale numero è la batteria, quale
indirizzo legge lo stato, l'indirizzo del consumo medio delle ultime
settimane. Il codice di quel progetto è sotto AGPL-3.0 e
**non è stato copiato**: il cliente di GdAAuto è scritto da capo.

## andreadegiovine/homeassistant-stellantis-vehicles — licenza MIT

<https://github.com/andreadegiovine/homeassistant-stellantis-vehicles>

Da qui vengono:

- `custom_components/gdaauto/marche/stellantis/app.json`: indirizzi, realm,
  schemi di ritorno e credenziali delle app MyPeugeot, MyCitroën, MyDS,
  MyOpel e MyVauxhall, paese per paese (il loro `configs.json`, ridotto ai
  campi che servono);
- gli indirizzi del Connected Car API v4 e la forma dei parametri del login;
- dove stanno, nello stato dell'auto, batteria, autonomia, ricarica, porte,
  posizione.

Il codice di GdAAuto è scritto da capo, e il login non passa dal loro
servizio remoto: chi configura entra nel suo browser.

```
MIT License

Copyright (c) 2023

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## pypolestar/pypolestar — licenza MIT

<https://github.com/pypolestar/pypolestar>

Da qui vengono i fatti del protocollo Polestar: l'indirizzo di Polestar ID, il
client id dell'app e il suo indirizzo di ritorno, lo scope, il flusso PKCE col
modulo del login, l'indirizzo GraphQL e il testo delle query
`GetConsumerCarsV2` e `CarTelematicsV2` (ridotte a batteria e contachilometri),
i nomi dei campi e gli stati della ricarica. I campioni delle prove
(`tests/polestar_campioni.py`) sono i suoi fixture anonimi, senza immagini. Il
codice di GdAAuto è scritto da capo. pypolestar/polestar_api (MIT, stesso
autore) è stato guardato per l'uso, senza prenderne codice.

```
MIT License

Copyright (c) 2023 Tuen Lee

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## pytoyoda/pytoyoda e pytoyoda/ha_toyota — licenza MIT

<https://github.com/pytoyoda/pytoyoda> · <https://github.com/pytoyoda/ha_toyota>

Da pytoyoda vengono gli indirizzi del login ForgeRock (realm `tme`) e di
`ctpa-oneapi`, il client `oneapp` con il suo indirizzo di ritorno, la chiave
pubblica dell'app (`x-api-key`) e la sua versione, le intestazioni (`x-guid`,
`x-client-ref`, `x-brand`) e la differenza fra Toyota e Lexus, i nomi dei
campi delle risposte e i payload d'esempio delle prove
(`tests/toyota_campioni.py`). Da ha_toyota, quali capacità dell'auto abilitano
serratura e clima e il codice di esito del clima. Il codice di GdAAuto è
scritto da capo.

```
MIT License

Copyright (c) 2021 Simon Hansen

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## MG / SAIC — licenza MIT

<https://github.com/SAIC-iSmart-API/saic-python-client-ng> ·
<https://github.com/SAIC-iSmart-API/saic-python-mqtt-gateway> ·
<https://github.com/ad-ha/mg-saic-ha>

Da saic-python-client-ng vengono gli indirizzi e i percorsi dell'API iSMART,
la derivazione delle chiavi AES e della firma `APP-VERIFICATION-STRING`, le
intestazioni, il login (le credenziali dell'app, il tenant, il formato del
deviceId), il meccanismo dell'event-id, i corpi dei comandi e le scale dei
campi; i vettori di prova della firma e i payload d'esempio delle prove
(`tests/mg_campioni.py`) vengono dai suoi test. Da saic-python-mqtt-gateway le
scale e i valori non validi, l'indice della temperatura del clima e quando il
limite di ricarica non c'è. Da mg-saic-ha gli indirizzi delle regioni, gli
intervalli prudenti e il significato di alcuni codici. Il codice di GdAAuto è
scritto da capo.

```
MIT License

Copyright (c) 2024 The SAIC-iSmart-API project

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

```
MIT License

Copyright (c) 2022 ReverseEngineeringDE

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

```
MIT License

Copyright (c) 2024 Alvaro Duarte

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## BMW CarData — la documentazione ufficiale

BMW CarData è l'API ufficiale e gratuita di BMW per i propri clienti: il
cliente di GdAAuto è scritto da capo sulla sua documentazione (guida, swagger
«Customer API v1» e «Device Code Flow»). Per capire come si comporta davvero
si sono letti, senza prenderne codice né tabelle:

- JustChr/BavarianData (MIT, © 2026 Chris Krammer and contributors):
  indirizzi, scope, l'intestazione `x-version`, l'attesa dopo `access_denied`,
  il rischio di un container con troppi descrittori, e quali descrittori e
  unità usano le auto vere;
- kvanbiesen/bmw-cardata-ha (BSD 2-Clause, © 2025 Kris Van Biesen; alcuni
  file © 2025 Renaud Allard): la copia della documentazione BMW, i codici di
  errore CU-xxx, il limite di 50 chiamate al giorno e la forma delle risposte;
- JjyKsi/bmw-cardata-ha (pubblico dominio): solo consultato.

## hacf-fr/renault-api — licenza MIT

<https://github.com/hacf-fr/renault-api>

Da qui vengono gli indirizzi e le chiavi pubbliche Gigya e Kamereon per
paese (in `marche/renault/locali.py`), la sequenza del login Gigya, del JWT e
della verifica in due passaggi, i percorsi Kamereon KCA e KCM con i corpi dei
comandi e la tabella delle strade per modello (in `marche/renault/modelli.json`),
la mappa degli stati di ricarica e del cavo, e i payload d'esempio delle prove
(`tests/renault_campioni.py`). Il codice di GdAAuto è scritto da capo.
L'integrazione Renault di Home Assistant (Apache-2.0) è stata guardata per come
legge cavo e ricarica e per il limite di 60 chiamate l'ora, senza copiarne codice.

```
MIT License

Copyright © 2020 epenet

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Uconnect — py-uconnect e hass-uconnect (Apache 2.0), evcc (MIT)

<https://github.com/hass-uconnect/py-uconnect> ·
<https://github.com/hass-uconnect/hass-uconnect> ·
<https://github.com/evcc-io/evcc>

Da py-uconnect e hass-uconnect (Apache License 2.0,
<http://www.apache.org/licenses/LICENSE-2.0>, copyright dei rispettivi
autori) vengono i fatti del protocollo Uconnect, riscritti da capo: gli
indirizzi Gigya per marchio e regione e le loro chiavi pubbliche, i codici di
marchio, gli indirizzi authz, channels e mfa con le loro x-api-key, le
intestazioni, i percorsi di stato, posizione, serrature, PIN e comandi, e la
scelta del tempo di ricarica per tipo di caricatore.

Da evcc (`vehicle/fiat`, MIT) vengono il flusso di login da Gigya a Cognito
senza l'SDK di Amazon, la firma SigV4 (riscritta in Python) con i suoi valori
di prova registrati dall'SDK, la versione v2 dello stato e l'avviso sul
«deep refresh», che sveglia l'auto e GdAAuto non usa.

```
MIT License

Copyright (c) evcc.io (andig, naltatis, premultiply)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## ReneNulschDE/mbapi2020 — licenza MIT

<https://github.com/ReneNulschDE/mbapi2020>

Da qui vengono i fatti del protocollo Mercedes me: i passi del login,
l'identificativo pubblico dell'app, gli indirizzi per regione, le
intestazioni e le versioni dell'app, gli indirizzi REST, il websocket dei
comandi con le sue conferme, i numeri di campo dei messaggi protobuf e i
significati dei codici di stato. Il lettore protobuf di GdAAuto è scritto da
capo; i campioni delle prove (`tests/mercedes_campioni.py`) sono stati
prodotti una volta con i messaggi di mbapi2020 e la libreria protobuf, che
GdAAuto non usa.

```
MIT License

Copyright (c) 2026 Rene Nulsch

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Hyundai-Kia-Connect/hyundai_kia_connect_api — licenza MIT

<https://github.com/Hyundai-Kia-Connect/hyundai_kia_connect_api>

Da qui vengono i fatti del cloud europeo di Kia, Hyundai e Genesis: gli
identificativi e le chiavi pubbliche delle app, gli indirizzi, i passi del
login della OneApp con la password cifrata, la firma `X-Fingerprint` con le
sue chiavi e il timbro `Stamp`, gli indirizzi dello stato (CCS2 e formato
vecchio) e dei comandi, e la posizione dei campi nello stato. Il cliente di
GdAAuto è scritto da capo, asincrono. I due stati delle prove
(`tests/hyundai_kia_campioni/`) sono campioni del progetto, già anonimizzati;
le due impronte di prova sono i suoi vettori verificati.

```
MIT License

Copyright (c) 2021, Fuat Akgun

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Tesla — la documentazione ufficiale

La Fleet API è documentata da Tesla su <https://developer.tesla.com/docs/fleet-api>.
Il cliente di GdAAuto è scritto da capo su quella. Per la forma delle
risposte si sono guardati anche Teslemetry/python-tesla-fleet-api (Apache-2.0)
e l'integrazione Tesla Fleet di Home Assistant (Apache-2.0), senza copiarne
il codice; il proxy dei comandi firmati è teslamotors/vehicle-command
(Apache-2.0), che si usa così com'è, accanto a GdAAuto.

## Gli altri progetti guardati

- helix-git/ha-leapmotor-gateway: l'idea dei comandi pericolosi spenti di base.
- ProtossBlaster/leapmotor-mate: le note sul consumo della batteria da 12 V e
  sull'account dedicato.
- flobz/psa_car_controller (GPL-3.0): guardato per capire il cloud
  Stellantis, non copiato.

## Il carattere del logo

Archivo, © The Archivo Project Authors, SIL Open Font License 1.1: il testo è
in `docs/logo/LICENZA-Archivo.txt`.
