// Test workera na atrapie KV, karmionej wynikiem `python build.py --synthetic --out out`.
// Sprawdza dokladnie ten ksztalt odpowiedzi, ktory parsuje BreatheService.mc.
//
// Uruchomienie: node worker/test/worker.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import worker from '../src/index.js';

const here = dirname(fileURLToPath(import.meta.url));
const out = join(here, '..', '..', 'out');

let failed = 0;
function check(name, cond, detail = '') {
  if (cond) {
    console.log('  ok   ' + name);
  } else {
    console.log('  FAIL ' + name + '  ' + detail);
    failed++;
  }
}

const index = JSON.parse(readFileSync(join(out, 'index.json'), 'utf8'));

const env = {
  GRID: {
    async get(key, opts) {
      if (key === 'index') return opts && opts.type === 'json' ? index : JSON.stringify(index);
      const file = join(out, key.replace(':', '_') + '.bin');
      try {
        const raw = readFileSync(file);
        return raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength);
      } catch {
        return null;
      }
    },
  },
};

const BASE = 'https://breathe-data.example.workers.dev/v1/air';

async function call(qs) {
  const res = await worker.fetch(new Request(BASE + qs), env);
  const body = await res.json();
  return { status: res.status, body };
}

console.log('zapytanie takie, jakie wysyla zegarek');
const params = '?latitude=52.23&longitude=21.01&current=european_aqi,us_aqi,pm10,pm2_5,' +
  'alder_pollen,birch_pollen,grass_pollen,mugwort_pollen,olive_pollen,ragweed_pollen' +
  '&timeformat=unixtime';
const r = await call(params);
check('status 200', r.status === 200, String(r.status));
check('jest obiekt current', !!r.body.current);
const cur = r.body.current || {};
for (const f of ['time', 'european_aqi', 'us_aqi', 'pm2_5', 'pm10',
  'alder_pollen', 'birch_pollen', 'grass_pollen', 'mugwort_pollen',
  'olive_pollen', 'ragweed_pollen']) {
  check(`pole ${f} obecne`, Object.prototype.hasOwnProperty.call(cur, f));
}
check('european_aqi to liczba w sensownym zakresie',
  typeof cur.european_aqi === 'number' && cur.european_aqi >= 0 && cur.european_aqi <= 200,
  String(cur.european_aqi));
check('znacznik czasu to sekundy uniksowe',
  typeof cur.time === 'number' && cur.time > 1700000000, String(cur.time));
check('podano zrodlo i licencje', !!r.body.source && !!r.body.licence);

const size = JSON.stringify(r.body).length;
check('odpowiedz miesci sie w budzecie zegarka (< 700 B)', size < 700, size + ' B');

console.log('punkt poza domena CAMS');
const far = await call('?latitude=40.71&longitude=-74.00');
check('status 200 zamiast bledu', far.status === 200, String(far.status));
check('wszystkie pola puste',
  far.body.current && far.body.current.european_aqi === null &&
  far.body.current.grass_pollen === null);

console.log('bledne wejscie');
check('brak wspolrzednych to 400', (await call('')).status === 400);
check('szerokosc poza zakresem to 400',
  (await call('?latitude=120&longitude=10')).status === 400);

console.log('health');
const h = await worker.fetch(new Request('https://x/health'), env);
const hb = await h.json();
check('health raportuje liczbe godzin', hb.ok === true && hb.hours === index.hours.length,
  JSON.stringify(hb));

console.log();
if (failed) {
  console.log(`NIEUDANE: ${failed}`);
  process.exit(1);
}
console.log('wszystko przeszlo');
