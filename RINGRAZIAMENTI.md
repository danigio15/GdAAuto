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

## Gli altri progetti guardati

- helix-git/ha-leapmotor-gateway: l'idea dei comandi pericolosi spenti di base.
- ProtossBlaster/leapmotor-mate: le note sul consumo della batteria da 12 V e
  sull'account dedicato.

## Il carattere del logo

Archivo, © The Archivo Project Authors, SIL Open Font License 1.1: il testo è
in `docs/logo/LICENZA-Archivo.txt`.
