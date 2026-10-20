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

## Jaguar Land Rover — ardevd/jlrpy e msp1974/homeassistant-jlrincontrol, licenza MIT

<https://github.com/ardevd/jlrpy> · <https://github.com/msp1974/homeassistant-jlrincontrol>

Da jlrpy: gli indirizzi ifas/ifop/if9 (resto del mondo e Cina), il giro
token → registrazione del dispositivo → utente, le intestazioni e gli
identificativi dell'app (`ICR_JAGUAR_ANDROID`, l'`x-App-Secret`), i tipi MIME,
i nomi dei servizi (RDL, RDU, HBLF, ECC, CP) e i loro parametri. Da
homeassistant-jlrincontrol: i nomi delle chiavi di stato e il loro
significato, le gomme in decimi di kPa, la temperatura della
preclimatizzazione, gli esiti delle pratiche e i servizi attivi. Il codice di
GdAAuto (`marche/jlr/`) è scritto da capo.

```
MIT License

Copyright (c) 2018 ardevd
Copyright (c) 2020 Mark Parker

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

## Volvo — l'integrazione Volvo di Home Assistant (Apache 2.0) e la documentazione di Volvo Cars

<https://github.com/home-assistant/core/tree/dev/homeassistant/components/volvo> ·
<https://developer.volvocars.com>

Dall'integrazione `volvo` di Home Assistant (Apache License 2.0,
<http://www.apache.org/licenses/LICENSE-2.0>, copyright degli autori di Home
Assistant) vengono il giro del login OAuth2 con PKCE e le credenziali
dell'applicazione dell'utente, l'elenco degli scope, la mappa dei campi e dei
valori (porte `OPEN`/`AJAR`, gomme `*_PRESSURE`, stati della ricarica, avvisi,
accessibilità) e i ritmi di lettura; i payload d'esempio di
`tests/volvo_campioni.py` derivano dai suoi fixture di prova (EX30, XC40, XC90
plug-in). Dalla documentazione pubblica di Volvo Cars vengono gli indirizzi
delle Connected Vehicle API v2, delle Energy API v1 e v2, della Location API
v1 e del Volvo ID. Il codice di GdAAuto (`marche/volvo/`) è scritto da capo.
La libreria `volvocarsapi` (thomasddn) e `ha-volvo-cars`, entrambe GPL-3.0,
sono state consultate solo per i fatti del protocollo (indirizzi, nomi dei
campi, scope): nessun codice copiato o tradotto.

```
Copyright Home Assistant authors

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

Modifiche: i fixture sono stati ridotti e tradotti nella forma delle prove di
GdAAuto.

## Porsche — CJNE/pyporscheconnectapi e CJNE/ha-porscheconnect, licenza MIT

<https://github.com/CJNE/pyporscheconnectapi> · <https://github.com/CJNE/ha-porscheconnect>

Da pyporscheconnectapi: gli indirizzi (`identity.porsche.com`,
`api.ppa.porsche.com/app`, `/connect/v1/vehicles…`, `/commands`), il client id,
l'X-Client-ID, lo User-Agent dell'app, l'indirizzo di ritorno, l'audience e gli
scope, le chiavi delle misure e dei comandi, i nomi dei campi, la forma dei
comandi e del PIN (SHA-512 di PIN e sfida). Da ha-porscheconnect: i valori
degli stati della ricarica, l'intervallo di lettura e la forma dei dati nei
fixture delle prove. Il codice di GdAAuto (`marche/porsche/`) è scritto da
capo per aiohttp.

```
MIT License

Copyright (c) 2020 Johan Isacsson
Copyright (c) 2021 CJNE

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

## Ford — marq24/ha-fordpass (Apache 2.0)

<https://github.com/marq24/ha-fordpass>

Da ha-fordpass (Apache License 2.0, Copyright 2025 Matthias Marquardt)
vengono la tabella delle regioni (`marche/ford/regioni.py`: tenant OAuth,
client id, Application-Id per continente, indirizzi del login, lingue e
codici dei paesi), gli indirizzi delle API (login B2C,
`cat-with-b2c-access-token`, `cat-with-refresh-token`, lo scambio OIDC su
Autonomic, il garage, il WebSocket e il REST della telemetria, i comandi v1 e
v1beta, energy-transfer-logs, preferred-charge-times, rcc/profile) e i nomi
dei comandi e dei campi della telemetria. Il codice di GdAAuto
(`marche/ford/`) è scritto da capo. Modifiche: la tabella delle regioni è
rinominata e ridotta. itchannel/fordpass-ha, senza licenza dichiarata, è
stato guardato solo per i nomi dei campi; nessun codice copiato.

```
Copyright 2025 Matthias Marquardt

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

## Volkswagen — tillsteinbach/CarConnectivity-connector-volkswagen, licenza MIT

<https://github.com/tillsteinbach/CarConnectivity-connector-volkswagen> ·
<https://github.com/tillsteinbach/CarConnectivity>

Da CarConnectivity e dal suo connettore Volkswagen: gli indirizzi del BFF
CARIAD e del login identity.vwgroup.io, il client id dell'app, lo scope,
l'indirizzo di ritorno `weconnect://authenticated`, lo User-Agent, il rinnovo
dei token su `/login/v1/idk/token`, i percorsi di `vehicles`,
`selectivestatus`, `parkingposition`, dei comandi e dell'S-PIN, i nomi dei
campi dello stato e i corpi dei comandi. Il codice di GdAAuto
(`marche/volkswagen/`) è scritto da capo per aiohttp. robinostlund/
volkswagencarnet (GPL-3.0) è stato guardato solo per alcuni nomi di campi e
per il percorso dei viaggi; nessun codice copiato.

```
MIT License

Copyright (c) 2021 Till Steinbach

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

## Audi — audiconnect/audi_connect_ha e acfischer42/CarConnectivity-connector-audi, licenza MIT

<https://github.com/audiconnect/audi_connect_ha> ·
<https://github.com/acfischer42/CarConnectivity-connector-audi>

Da audi_connect_ha: il client id e l'indirizzo di ritorno dell'app myAudi, gli
scope, gli indirizzi dell'IDK e del BFF di CARIAD, i lavori di
`selectivestatus` e i nomi dei campi, i corpi del clima e della ricarica,
`pendingrequests`, il token MBB e `tripstatistics`, la chiave HMAC
dell'intestazione `X-QMAuth`, il login col codice del dispositivo per Europa e
Canada e la classificazione dei rifiuti del rinnovo; nelle prove, la forma
dei dati di una Q8 e-tron. Dal connettore Audi di CarConnectivity: `vehicles`
con le capacità, `access/lock|unlock` con l'S-PIN, `honkandflash`,
`parkingposition`, le temperature in kelvin, `readiness` e gli stati della
ricarica. Il codice di GdAAuto (`marche/audi/`) è scritto da capo.

```
MIT License

Copyright (c) 2019 Arjen van Rhijn @arjenvrh
Copyright (c) 2025 Achim Fischer

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

## Nissan — dan-r/HomeAssistant-NissanConnect (MIT) e filcole/pycarwings2 (Apache 2.0)

<https://github.com/dan-r/HomeAssistant-NissanConnect> · <https://github.com/filcole/pycarwings2>

Da HomeAssistant-NissanConnect (MIT; alcune parti ri-licenziate da
Apache-2.0 col permesso di @mitchellrj e @Tobiaswk): gli indirizzi del login
WSO2 e di Kamereon (car-adapter, user-adapter, bff-web), il client id
pubblico dell'app, lo scope, l'indirizzo di ritorno e i parametri di
`authorize`, lo scambio verso i token Kamereon e il loro rinnovo, i numeri
dei servizi, i percorsi delle letture e dei comandi, i nomi dei campi; nelle
prove, la forma dei dati dei suoi test. Da pycarwings2 (Apache License 2.0,
<http://www.apache.org/licenses/LICENSE-2.0>): l'indirizzo del vecchio cloud
NissanConnect EV, la stringa `initial_app_str`, la cifratura Blowfish della
password con `baseprm`, le pagine e i loro parametri, il significato dei
campi. Il codice di GdAAuto (`marche/nissan/`) è scritto da capo; il
riempimento della cifratura è fatto sui byte invece che sui caratteri.
L'integrazione `nissan_leaf` di Home Assistant (Apache-2.0) è stata guardata
solo per le regioni e gli intervalli.

```
MIT License

Copyright (c) 2024 Daniel Raper

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
Copyright 2016 Jason Horne

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

## BYD — jkaberg/pyBYD e TA2k/ioBroker.byd, licenza MIT

<https://github.com/jkaberg/pyBYD> · <https://github.com/TA2k/ioBroker.byd>

Da pyBYD: gli indirizzi del cloud «overseas» per paese, il login JSON, la
busta cifrata (l'algoritmo dell'AES a scatola bianca, riscritto in
`marche/byd/busta.py`, e il formato del file delle tabelle), i modelli dei
dati e i comandi. Da ioBroker.byd: la forma di alcune risposte e i nomi dei
campi. Il codice di GdAAuto (`marche/byd/`) è scritto da capo. Le tabelle
`bangcle_tables.bin` non sono in GdAAuto: si scaricano al primo avvio dal
repository di pyBYD, a un commit fisso e con l'impronta controllata.

```
MIT License

Copyright (c) 2026 Joel Kåberg
Copyright (c) 2026 TA2k

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

## Rivian — bretterer/rivian-python-client (MIT) e bretterer/home-assistant-rivian (Apache 2.0)

<https://github.com/bretterer/rivian-python-client> ·
<https://github.com/bretterer/home-assistant-rivian>

Da rivian-python-client: il gateway GraphQL dell'app, la sessione
(`createCsrfToken`, login con la verifica in due passaggi), le query dello
stato e dei comandi firmati con la chiave del telefono abbinato. Da
home-assistant-rivian: il significato dei campi dello stato e i loro valori.
Il codice di GdAAuto (`marche/rivian/`) è scritto da capo.

```
MIT License

Copyright (c) Brian Retterer, J.R. Gutierrez, Nathan Spencer

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
Copyright Brian Retterer e gli autori di home-assistant-rivian

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

## Honda — enricobattocchi/pymyhondaplus, licenza MIT

<https://github.com/enricobattocchi/pymyhondaplus>

Gli indirizzi del cloud My Honda+, il login con la registrazione del
dispositivo e la sua chiave, la sfida da firmare, i campi dello stato, i
viaggi e i comandi. Il codice di GdAAuto (`marche/honda/`) è scritto da capo.

```
MIT License

Copyright (c) 2026 Enrico Battocchi

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

## Lynk & Co — b12e/ha_lynkco_2025, licenza MIT

<https://github.com/b12e/ha_lynkco_2025>

Gli indirizzi del login Azure AD B2C dell'app e del cloud europeo, la firma
delle richieste, i campi dello stato e i comandi. Il codice di GdAAuto
(`marche/lynk_co/`) è scritto da capo.

```
MIT License

Copyright (c) 2026 Bram Vandeperre

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

## smart — DasBasti/pySmartHashtag e DasBasti/SmartHashtag, licenza MIT

<https://github.com/DasBasti/pySmartHashtag> · <https://github.com/DasBasti/SmartHashtag>

Gli indirizzi del login smart ID e dell'API Hello smart europea, la chiave
pubblica dell'app, le firme delle richieste, i codici dei comandi e i campi
dello stato. Il codice di GdAAuto (`marche/smart/`) è scritto da capo.

```
MIT License

Copyright (c) 2023-2025 Bastian Neumann

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

## Subaru — G-Two/subarulink e l'integrazione Subaru di Home Assistant (Apache 2.0)

<https://github.com/G-Two/subarulink> ·
<https://github.com/home-assistant/core/tree/dev/homeassistant/components/subaru>

Da subarulink (Apache License 2.0, copyright degli autori di subarulink):
gli indirizzi di MySubaru per Stati Uniti e Canada, il login con la
registrazione del dispositivo e il codice di verifica, `validateSession`,
`selectVehicle`, le letture e i comandi con il PIN, il cambio di versione
delle strade e il significato dei campi. Dall'integrazione `subaru` di Home
Assistant (Apache License 2.0, copyright degli autori di Home Assistant): le
unità e gli intervalli. Il codice di GdAAuto (`marche/subaru/`) è scritto da
capo.

```
Copyright gli autori di G-Two/subarulink
Copyright Home Assistant authors

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

## Seat e Cupra — CarConnectivity-connector-seatcupra (MIT) e WulfgarW/pycupra (Apache 2.0)

<https://github.com/tillsteinbach/CarConnectivity-connector-seatcupra> · <https://github.com/WulfgarW/pycupra>

Dal connettore Seat/Cupra di CarConnectivity: gli indirizzi del cloud OLA, i
nomi dei campi (stato, mycar, ricarica, clima, autonomie, capacità), i
comandi con l'S-PIN e il SecToken, le costanti dell'app (client id e segreto
pubblico dell'app CUPRA) e la forma delle risposte usate nelle prove. Da
pycupra (Apache License 2.0, <http://www.apache.org/licenses/LICENSE-2.0>,
copyright degli autori di pycupra): client id, scope, indirizzi di ritorno,
versioni e User-Agent delle app SEAT e CUPRA, il punto dei token di OLA,
`charging/info` e il limite di ricarica, clacson e luci, la destinazione,
`driving-data` e il riconoscimento del blocco App Check. Il codice di GdAAuto
(`marche/seat_cupra/`) è scritto da capo.

```
MIT License

Copyright (c) 2021 Till Steinbach

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
