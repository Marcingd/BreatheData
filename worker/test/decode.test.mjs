// Test zgodnosci dekodera workera z koderem w Pythonie.
// Fixtures powstaja w tests/test_pipeline.py, wiec kazda zmiana formatu
// po jednej stronie od razu wywala ten test.
//
// Uruchomienie: node worker/test/decode.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import { sample, pickHour, readHeader } from '../src/decode.js';

const here = dirname(fileURLToPath(import.meta.url));
const fixtures = join(here, '..', '..', 'tests', 'fixtures');

let failed = 0;
function check(name, cond, detail = '') {
  if (cond) {
    console.log('  ok   ' + name);
  } else {
    console.log('  FAIL ' + name + '  ' + detail);
    failed++;
  }
}

const raw = readFileSync(join(fixtures, 'grid.bin'));
const buf = raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength);
const expected = JSON.parse(readFileSync(join(fixtures, 'expected.json'), 'utf8'));

console.log('naglowek');
const head = readHeader(buf);
check('siatka 169 x 281', head.nlat === 169 && head.nlon === 281,
  `${head.nlat}x${head.nlon}`);
check('poczatek siatki 30 / -25', head.lat0 === 30 && head.lon0 === -25,
  `${head.lat0} / ${head.lon0}`);
check('krok 0.25', Math.abs(head.dlat - 0.25) < 1e-6 && Math.abs(head.dlon - 0.25) < 1e-6);
check('znacznik czasu przeniesiony', head.epoch === 1785355200, String(head.epoch));

console.log('zgodnosc z referencja z Pythona');
for (const probe of expected) {
  const got = sample(buf, probe.lat, probe.lon);
  const want = probe.cell;
  if (want === null) {
    check(`punkt ${probe.lat}/${probe.lon} poza siatka`, got === null);
    continue;
  }
  check(`punkt ${probe.lat}/${probe.lon} zdekodowany`, got !== null);
  if (!got) continue;
  for (const key of Object.keys(want)) {
    const a = got[key];
    const b = want[key];
    const same = (a === null && b === null) ||
      (typeof a === 'number' && typeof b === 'number' && Math.abs(a - b) < 0.051);
    check(`  ${probe.lat}/${probe.lon} ${key} = ${b}`, same, `dostalem ${a}`);
  }
}

console.log('wybor godziny');
const index = {
  hours: [
    { key: 'grid:2026080110', epoch: 1000 },
    { key: 'grid:2026080111', epoch: 2000 },
    { key: 'grid:2026080112', epoch: 3000 },
  ],
};
check('bierze ostatnia godzine nie z przyszlosci',
  pickHour(index, 2500).key === 'grid:2026080111');
check('trafia dokladnie w rowna godzine',
  pickHour(index, 2000).key === 'grid:2026080111');
check('gdy wszystko w przyszlosci, bierze najwczesniejsza',
  pickHour(index, 500).key === 'grid:2026080110');
check('pusty indeks zwraca null', pickHour({ hours: [] }, 1000) === null);

console.log();
if (failed) {
  console.log(`NIEUDANE: ${failed}`);
  process.exit(1);
}
console.log('wszystko przeszlo');
