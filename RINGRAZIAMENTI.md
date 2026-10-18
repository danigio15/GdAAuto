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
- il corpo intero dello spegnimento del clima che la T03 vuole.

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

## markoceri/leapmotor-api — documentazione

<https://github.com/markoceri/leapmotor-api>

La sua documentazione (`docs/api.md`, `docs/vehicles.md`) descrive gli
indirizzi del cloud, i numeri dei segnali delle C10 e B10 e i comandi. Da lì
vengono i **fatti** che GdAAuto usa: quale numero è la batteria, quale
indirizzo legge lo stato. Il codice di quel progetto è sotto AGPL-3.0 e
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
