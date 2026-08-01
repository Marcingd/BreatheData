// Worker Breathe: zamienia zapytanie zegarka na odczyt jednej komorki siatki.
//
// Odpowiedz ma ksztalt identyczny z Open-Meteo, wiec tarcza nie wymaga zmian
// poza adresem. Siatki lezą w KV pod kluczami grid:YYYYMMDDHH, spis w "index".

import { sample, emptyReading, pickHour } from './decode.js';

const INDEX_TTL_MS = 5 * 60 * 1000;
const GRID_CACHE_MAX = 3;

let indexCache = null;
let indexCachedAt = 0;
const gridCache = new Map();

async function loadIndex(env) {
  const now = Date.now();
  if (indexCache && now - indexCachedAt < INDEX_TTL_MS) return indexCache;
  const raw = await env.GRID.get('index', { type: 'json', cacheTtl: 300 });
  if (raw) {
    indexCache = raw;
    indexCachedAt = now;
  }
  return raw || indexCache;
}

async function loadGrid(env, key) {
  if (gridCache.has(key)) return gridCache.get(key);
  const buf = await env.GRID.get(key, { type: 'arrayBuffer', cacheTtl: 3600 });
  if (!buf) return null;
  if (gridCache.size >= GRID_CACHE_MAX) gridCache.delete(gridCache.keys().next().value);
  gridCache.set(key, buf);
  return buf;
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'public, max-age=600',
      'access-control-allow-origin': '*',
    },
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === '/health') {
      const index = await loadIndex(env);
      return json({
        ok: !!index,
        hours: index ? index.hours.length : 0,
        updated: index ? index.updated : null,
      });
    }

    const lat = parseFloat(url.searchParams.get('latitude'));
    const lon = parseFloat(url.searchParams.get('longitude'));
    const nowEpoch = Math.floor(Date.now() / 1000);

    if (!isFinite(lat) || !isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) {
      return json({ error: 'brak poprawnych wspolrzednych' }, 400);
    }

    const index = await loadIndex(env);
    const hour = pickHour(index, nowEpoch);
    if (!hour) return json({ error: 'brak danych' }, 503);

    const buf = await loadGrid(env, hour.key);
    if (!buf) return json({ error: 'brak siatki dla godziny' }, 503);

    // Poza domena CAMS oddajemy puste pola zamiast bledu - tarcza pokaze
    // wtedy "--" i "POLLEN N/A" zamiast wisiec na "WAITING FOR DATA".
    const current = sample(buf, lat, lon) || emptyReading(hour.epoch);

    return json({
      source: 'CAMS / Copernicus Atmosphere Monitoring Service',
      licence: 'CC-BY 4.0',
      current,
    });
  },
};
