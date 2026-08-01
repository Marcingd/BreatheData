# -*- coding: utf-8 -*-
"""Budowa siatek jakosci powietrza dla tarczy Breathe.

  ADS (CAMS Europe)  ->  indeksy i pylki na siatce 0,25 stopnia  ->  Cloudflare KV

Szereg godzinowy powstaje z dwoch zrodel:
  * ANALIZA za wczorajsza dobe - najlepsze odtworzenie przeszlosci, potrzebne
    do sredniej 24-godzinnej PM, na ktorej stoi europejski indeks i US AQI,
  * PROGNOZA z ostatniego dostepnego przebiegu 00 UTC - godziny biezaca i przyszle.
Analiza nadpisuje prognoze tam, gdzie obie pokrywaja te sama godzine.

Bez tego srednia 24 h liczyla sie z prognozy o duzym wyprzedzeniu i zanizala
US AQI o kilka punktow (sprawdzone wobec pomiarow GIOS w Zyrardowie).

Uzycie:
    python build.py                 # pelny przebieg (wymaga kluczy w srodowisku)
    python build.py --synthetic     # bez ADS, dane zastepcze do testow
    python build.py --out ./out     # zapis na dysk zamiast do KV
"""
import argparse
import datetime as dt
import json
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import aqi          # noqa: E402
import grid         # noqa: E402

PUBLISH_HOURS = 12          # ile godzin do przodu ladujemy do KV
HISTORY_HOURS = 24          # ile godzin wstecz na srednie kroczace
RUN_READY_HOUR = 9          # o tej godzinie UTC przebieg 00 UTC jest juz opublikowany
KEY_PREFIX = "grid:"
INDEX_KEY = "index"

POLLUTANTS = ["pm2_5", "pm10", "no2", "o3", "so2"]
POLLEN = ["alder_pollen", "birch_pollen", "grass_pollen",
          "mugwort_pollen", "olive_pollen", "ragweed_pollen"]
ALL_FIELDS = POLLUTANTS + POLLEN


def pick_run(now):
    """Data przebiegu modelu 00 UTC, ktory na pewno jest juz dostepny."""
    day = now.date() if now.hour >= RUN_READY_HOUR else (now - dt.timedelta(days=1)).date()
    return dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc)


def hour_series(now):
    """Godziny, dla ktorych trzymamy dane: historia + publikowane."""
    first = now - dt.timedelta(hours=HISTORY_HOURS - 1)
    last = now + dt.timedelta(hours=PUBLISH_HOURS - 1)
    out = []
    t = first
    while t <= last:
        out.append(t)
        t += dt.timedelta(hours=1)
    return out


def empty_stack(n):
    return {f: np.full((n, grid.NLAT, grid.NLON), np.nan, dtype=np.float32)
            for f in ALL_FIELDS}


def synthetic_stack(series):
    """Powtarzalne dane zastepcze - gradient plus dobowy oddech, bez losowosci."""
    lats, lons = grid.target_axes()
    la = lats[:, None]
    lo = lons[None, :]
    base = 8.0 + 14.0 * np.exp(-((la - 51.0) ** 2) / 120.0 - ((lo - 18.0) ** 2) / 400.0)
    stack = {}
    for name, mult in (("pm2_5", 1.0), ("pm10", 1.7), ("no2", 2.2), ("o3", 3.4), ("so2", 0.8)):
        stack[name] = np.stack([base * mult * (1.0 + 0.25 * np.sin(i / 3.8))
                                for i in range(len(series))]).astype(np.float32)
    for i, name in enumerate(POLLEN):
        peak = 4.0 + 60.0 * i
        stack[name] = np.stack([np.clip(base - 6.0, 0, None) * peak / 12.0
                                for _ in series]).astype(np.float32)
    return stack


def _ingest(stack, series, path, stamps, fields, overwrite):
    """Wczytanie pliku CAMS do szeregu; stamps to czasy kolejnych krokow w pliku."""
    import cams
    ds = cams.open_netcdf(path)
    src_lat, src_lon, lat_order, lon_order = cams.axes(ds)
    dst_lat, dst_lon = grid.target_axes()
    print("  zrodlo: lat %.2f..%.2f (%d), lon %.2f..%.2f (%d)"
          % (src_lat[0], src_lat[-1], len(src_lat), src_lon[0], src_lon[-1], len(src_lon)))

    slot = {t: i for i, t in enumerate(series)}
    for ads_name, (field, fragments) in cams.VARIABLES.items():
        if field not in fields:
            continue
        var = cams.find_var(ds, fragments)
        a = cams.read_layer(ds, var, lat_order, lon_order)
        a = cams.resample(a, src_lat, src_lon, dst_lat, dst_lon)
        if a.shape[0] != len(stamps):
            raise RuntimeError("%s: %d krokow w pliku, oczekiwano %d"
                               % (field, a.shape[0], len(stamps)))
        used = 0
        for k, t in enumerate(stamps):
            i = slot.get(t)
            if i is None:
                continue
            if overwrite:
                stack[field][i] = a[k]
            else:
                gap = np.isnan(stack[field][i])
                stack[field][i][gap] = a[k][gap]
            used += 1
        print("    %-16s <- %-14s %d/%d krokow" % (field, var, used, len(stamps)))
    ds.close()


def _leads(run, first_time, last_time):
    """Zakres leadtime_hour pokrywajacy zadany przedzial czasu."""
    first = max(0, int((first_time - run).total_seconds() // 3600))
    last = int((last_time - run).total_seconds() // 3600)
    if last < first or first > 96:
        return []
    return list(range(first, min(96, last) + 1))


def download(stack, series, now, run_start):
    """Trzy zrodla, kazde nadpisuje poprzednie tam, gdzie ma lepsze dane:
    poprzedni przebieg (godziny sprzed biezacego), biezacy przebieg, analiza."""
    import cams
    tmp = tempfile.gettempdir()

    # 1. Poprzedni przebieg - bez niego poranne uruchomienia mialyby urwana
    #    historie, bo biezacy przebieg zaczyna sie dopiero o polnocy.
    if series[0] < run_start:
        prev_run = run_start - dt.timedelta(days=1)
        leads = _leads(prev_run, series[0], run_start - dt.timedelta(hours=1))
        if leads:
            stamps = [prev_run + dt.timedelta(hours=h) for h in leads]
            ads_names = [n for n, (f, _) in cams.VARIABLES.items() if f in POLLUTANTS]
            print("historia: przebieg %s, leadtime %d..%d"
                  % (prev_run.date(), leads[0], leads[-1]))
            try:
                path = os.path.join(tmp, "cams_prev.zip")
                cams.request_hours(path, "forecast", prev_run.strftime("%Y-%m-%d"),
                                   ["00:00"], [str(h) for h in leads], ads_names)
                _ingest(stack, series, path, stamps, POLLUTANTS, overwrite=True)
            except Exception as exc:
                print("historia niedostepna (%s)" % exc)

    first_lead = max(0, int((series[0] - run_start).total_seconds() // 3600))
    last_lead = int((series[-1] - run_start).total_seconds() // 3600)
    last_lead = min(96, max(last_lead, first_lead))
    leads = list(range(first_lead, last_lead + 1))
    stamps = [run_start + dt.timedelta(hours=h) for h in leads]

    print("prognoza: przebieg %s, leadtime %d..%d" % (run_start.date(), leads[0], leads[-1]))
    path = os.path.join(tmp, "cams_fc.zip")
    cams.request_hours(path, "forecast", run_start.strftime("%Y-%m-%d"), ["00:00"],
                       [str(h) for h in leads], list(cams.VARIABLES.keys()))
    _ingest(stack, series, path, stamps, ALL_FIELDS, overwrite=True)

    # Analiza obejmuje tylko zanieczyszczenia - pylkow do srednich nie liczymy.
    # Bywa niedostepna: CAMS publikuje ja okolo poludnia dnia nastepnego, wiec
    # poranne przebiegi dostana 400 i zostana przy prognozie. To jest w porzadku.
    day = (now - dt.timedelta(days=1)).date()
    an_start = dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc)
    times = ["%02d:00" % h for h in range(24)]
    an_stamps = [an_start + dt.timedelta(hours=h) for h in range(24)]
    if not any(t in series for t in an_stamps):
        print("analiza: pominieta, okno jej nie obejmuje")
        return
    ads_names = [n for n, (f, _) in cams.VARIABLES.items() if f in POLLUTANTS]
    print("analiza: %s, 24 godziny" % day)
    try:
        path = os.path.join(tmp, "cams_an.zip")
        cams.request_hours(path, "analysis", an_start.strftime("%Y-%m-%d"), times,
                           ["0"], ads_names)
        _ingest(stack, series, path, an_stamps, POLLUTANTS, overwrite=True)
    except Exception as exc:
        # brak analizy nie moze wywalic publikacji - zostaje sama prognoza
        print("analiza niedostepna (%s), zostaje prognoza" % exc)


def build_hour(stack, idx):
    """Warstwy dla jednej godziny: indeksy z ostatnich wartosci i srednich kroczacych."""
    pm25_24 = aqi.trailing_mean(stack["pm2_5"], idx, 24)
    pm10_24 = aqi.trailing_mean(stack["pm10"], idx, 24)
    o3_8 = aqi.trailing_mean(stack["o3"], idx, 8)
    no2 = stack["no2"][idx]
    o3 = stack["o3"][idx]
    so2 = stack["so2"][idx]

    layers = {
        "european_aqi": aqi.european_aqi(pm25_24, pm10_24, no2, o3, so2),
        "us_aqi": aqi.us_aqi(pm25_24, pm10_24, o3_8, no2, so2),
        "pm2_5": stack["pm2_5"][idx],
        "pm10": stack["pm10"][idx],
    }
    for name in POLLEN:
        layers[name] = stack[name][idx]
    return layers


def probe(stack, idx, lat, lon):
    """Rozbicie indeksow w jednym punkcie - do porownania z pomiarem naziemnym."""
    r = int(round((lat - grid.LAT0) / grid.STEP))
    c = int(round((lon - grid.LON0) / grid.STEP))
    if r < 0 or r >= grid.NLAT or c < 0 or c >= grid.NLON:
        return
    cell = lambda a: float(a[idx, r, c])
    pm25_24 = float(aqi.trailing_mean(stack["pm2_5"], idx, 24)[r, c])
    pm10_24 = float(aqi.trailing_mean(stack["pm10"], idx, 24)[r, c])
    o3_8 = float(aqi.trailing_mean(stack["o3"], idx, 8)[r, c])
    print("sonda %.3f/%.3f: pm2.5 %.1f (24h %.1f) | pm10 %.1f (24h %.1f) | o3 %.1f (8h %.1f)"
          % (lat, lon, cell(stack["pm2_5"]), pm25_24, cell(stack["pm10"]), pm10_24,
             cell(stack["o3"]), o3_8))
    one = lambda v: np.full((1, 1), v, dtype=np.float32)
    total, sub = aqi.us_aqi(one(pm25_24), one(pm10_24), one(o3_8),
                            one(cell(stack["no2"])), one(cell(stack["so2"])), parts=True)
    print("  US AQI %.0f = max(%s)"
          % (float(total[0, 0]), ", ".join("%s %.0f" % (k, float(v[0, 0]))
                                           for k, v in sub.items())))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true", help="bez ADS, dane zastepcze")
    ap.add_argument("--out", help="katalog na pliki zamiast wysylki do KV")
    ap.add_argument("--now", help="nadpisanie czasu, format YYYY-MM-DDTHH")
    args = ap.parse_args()

    if args.now:
        now = dt.datetime.strptime(args.now, "%Y-%m-%dT%H").replace(tzinfo=dt.timezone.utc)
    else:
        now = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)

    run_start = pick_run(now)
    series = hour_series(now)
    print("teraz %s | szereg %s .. %s (%d h)"
          % (now.isoformat(), series[0].isoformat(), series[-1].isoformat(), len(series)))

    if args.synthetic:
        stack = synthetic_stack(series)
    else:
        stack = empty_stack(len(series))
        download(stack, series, now, run_start)

    publish_idx = [i for i, t in enumerate(series) if t >= now][:PUBLISH_HOURS]
    if not publish_idx:
        raise RuntimeError("okno nie zawiera zadnej godziny do publikacji")

    # ile godzin historii faktycznie mamy pod pierwsza publikowana godzina
    have = int(np.sum(np.isfinite(stack["pm2_5"][:publish_idx[0] + 1, grid.NLAT // 2,
                                                 grid.NLON // 2])))
    print("historia PM pod pierwsza publikowana godzina: %d/%d h" % (have, HISTORY_HOURS))

    # kontrolka na Skierniewicach - pozwala porownac skladniki z pomiarem GIOS
    probe(stack, publish_idx[0], 51.955, 20.15)

    sink = None
    if not args.out:
        import publish
        sink = publish.KV()
    elif not os.path.isdir(args.out):
        os.makedirs(args.out)

    keys = []
    for i in publish_idx:
        stamp = series[i]
        key = KEY_PREFIX + stamp.strftime("%Y%m%d%H")
        blob = grid.encode(build_hour(stack, i), stamp.timestamp())
        if sink:
            sink.put(key, blob)
        else:
            with open(os.path.join(args.out, key.replace(":", "_") + ".bin"), "wb") as fh:
                fh.write(blob)
        keys.append({"key": key, "epoch": int(stamp.timestamp())})
        print("  %s  %d B" % (key, len(blob)))

    index = {
        "version": 1,
        "updated": int(now.timestamp()),
        "source": "CAMS European air quality forecasts (Copernicus), CC-BY 4.0",
        "grid": {"lat0": grid.LAT0, "lon0": grid.LON0, "step": grid.STEP,
                 "nlat": grid.NLAT, "nlon": grid.NLON},
        "hours": keys,
    }
    if sink:
        sink.put_json(INDEX_KEY, index)
    else:
        with open(os.path.join(args.out, "index.json"), "w") as fh:
            json.dump(index, fh, indent=2)
    print("opublikowano godzin: %d" % len(keys))


if __name__ == "__main__":
    main()
