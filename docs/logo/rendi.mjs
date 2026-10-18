// Il logo: da logo.svg ai PNG che servono (Chromium vero, per i caratteri e i filtri).
// Uso: node docs/logo/rendi.mjs   (cerca Playwright dove l'ha messo npm)
import { chromium } from "playwright";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const qui = process.env.CARTELLA || dirname(fileURLToPath(import.meta.url));
const svg = readFileSync(join(qui, "logo.svg"), "utf8");
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM || undefined });
const pagina = await browser.newPage({ viewport: { width: 1024, height: 1024 } });
await pagina.setContent(
  `<!doctype html><html><head><base href="file://${qui}/"><style>html,body{margin:0;background:#000}</style></head><body>${svg}</body></html>`,
);
await pagina.evaluate(() => document.fonts.ready);
await pagina.waitForTimeout(300);
await pagina.screenshot({ path: join(qui, "logo_1024.png"), omitBackground: false });
await browser.close();
