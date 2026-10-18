// La plancia di GdAHome riconosce un'auto GdAAuto?
//
// Legge da stdin il dispositivo come lo descrive Home Assistant ({entities,
// states}), lo passa a `legaLAutoAlDispositivo` della plancia VERA (il file
// di gdahomeapp, non una copia) e stampa la mappa casella → entity_id.
// Uso: node lega.mjs <cartella di gdahomeapp> < dispositivo.json
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

// La plancia sta in ponte/plancia/src; quella dell'officina è un collegamento.
const gdahome = process.argv[2];
const modulo = [
  "ponte/plancia/src/core/auto-device-binding.js",
  "officina/custom_components/dashboardmodern/frontend/src/core/auto-device-binding.js",
]
  .map((percorso) => join(gdahome, percorso))
  .find((percorso) => existsSync(percorso));
if (!modulo) throw new Error(`La plancia di GdAHome non c'è in ${gdahome}`);
const { legaLAutoAlDispositivo } = await import(pathToFileURL(modulo).href);
const dispositivo = JSON.parse(readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify(legaLAutoAlDispositivo(dispositivo)));
