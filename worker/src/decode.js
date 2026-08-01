// Dekoder siatki "BRT1". Stale MUSZA byc identyczne jak w src/grid.py -
// test worker/test/decode.test.mjs porownuje wynik z referencja z Pythona.

export const HEADER_SIZE = 28;
export const CELL_SIZE = 10;
export const MISSING = 255;
export const LOG_SCALE = 24;

export const FIELDS = [
  'european_aqi', 'us_aqi', 'pm2_5', 'pm10',
  'alder_pollen', 'birch_pollen', 'grass_pollen',
  'mugwort_pollen', 'olive_pollen', 'ragweed_pollen',
];

export function readHeader(buf) {
  const dv = new DataView(buf);
  const magic = String.fromCharCode(dv.getUint8(0), dv.getUint8(1), dv.getUint8(2), dv.getUint8(3));
  if (magic !== 'BRT1') throw new Error('zly naglowek siatki: ' + magic);
  return {
    lat0: dv.getFloat32(4, true),
    lon0: dv.getFloat32(8, true),
    dlat: dv.getFloat32(12, true),
    dlon: dv.getFloat32(16, true),
    nlat: dv.getUint16(20, true),
    nlon: dv.getUint16(22, true),
    epoch: dv.getUint32(24, true),
  };
}

// Odwrotnosc kwantowania logarytmicznego z grid.py
function dequant(q) {
  return Math.expm1(q / LOG_SCALE);
}

export function sample(buf, lat, lon) {
  const h = readHeader(buf);
  const r = Math.round((lat - h.lat0) / h.dlat);
  const c = Math.round((lon - h.lon0) / h.dlon);
  if (r < 0 || r >= h.nlat || c < 0 || c >= h.nlon) return null;

  const off = HEADER_SIZE + (r * h.nlon + c) * CELL_SIZE;
  const cell = new Uint8Array(buf, off, CELL_SIZE);
  const out = { time: h.epoch };
  for (let i = 0; i < FIELDS.length; i++) {
    const v = cell[i];
    if (v === MISSING) {
      out[FIELDS[i]] = null;
    } else if (i === 0) {
      out[FIELDS[i]] = v;
    } else if (i === 1) {
      out[FIELDS[i]] = v * 2;
    } else {
      out[FIELDS[i]] = Math.round(dequant(v) * 10) / 10;
    }
  }
  return out;
}

export function emptyReading(epoch) {
  const out = { time: epoch };
  for (const f of FIELDS) out[f] = null;
  return out;
}

// Godzina z indeksu najblizsza biezacemu czasowi, ale nie z przyszlosci,
// dopoki jakakolwiek przeszla jest dostepna.
export function pickHour(index, nowEpoch) {
  const hours = (index && index.hours) || [];
  if (!hours.length) return null;
  let best = null;
  for (const h of hours) {
    if (h.epoch <= nowEpoch && (!best || h.epoch > best.epoch)) best = h;
  }
  if (best) return best;
  return hours.reduce((a, b) => (a.epoch <= b.epoch ? a : b));
}
