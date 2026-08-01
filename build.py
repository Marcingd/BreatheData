# -*- coding: utf-8 -*-
"""Budowa siatek jakosci powietrza dla tarczy Breathe.

  ADS (CAMS Europe)  ->  indeksy i pylki na siatce 0,25 stopnia  ->  Cloudflare KV

Jedno zadanie do ADS obejmuje okno [teraz - 24 h, teraz + PUBLISH_HOURS], bo
srednia 24-godzinna dla pylow zawieszonych potrzebuje przeszlosci, a publikujemy
kilkanascie godzin do przodu, zeby zegarek zawsze trafil na aktualna godzine
nawet miedzy przebiegami.

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


def pick_run(now):
    """Data przebiegu modelu 00 UTC, ktory na pewno jest juz dostepny."""
    day = now.date() if now.hour >= RUN_READY_HOUR else (now - dt.timedelta(days=1)).date()
    return dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc)


def leadtime_window(run_start, now):
    """Zakres leadtime_hour pokrywajacy historie i publikowane godziny."""
    first = int((now - run_start).total_seconds() // 3600) - HISTORY_HOURS + 1
    last = int((now - run_start).total_seconds() // 3600) + PUBLISH_HOURS - 1
    first = max(0, first)
    last = min(96, max(last, first))
    return list(range(first, last + 1))


def synthetic_stack(hours):
    """Powtarzalne dane zastepcze - gradient plus dobowy oddech, bez losowosci."""
    lats, lons = grid.target_axes()
    la = lats[:, None]
    lo = lons[None, :]
    base = 8.0 + 14.0 * np.exp(-((la - 51.0) ** 2) / 120.0 - ((lo - 18.0) ** 2) / 400.0)
    out = {}
    for name, mult in (("pm2_5", 1.0), ("pm10", 1.7), ("no2", 2.2), ("o3", 3.4), ("so2", 0.8)):
        out[name] = np.stack([base * mult * (1.0 + 0.25 * np.sin((h % 24) / 3.8))
                              for h in range(hours)]).astype(np.float32)
    for i, name in enumerate(["alder_pollen", "birch_pollen", "grass_pollen",
                              "mugwort_pollen", "olive_pollen", "ragweed_pollen"]):
        peak = 4.0 + 60.0 * i
        out[name] = np.stack([np.clip(base - 6.0, 0, None) * peak / 12.0
                              for _ in range(hours)]).astype(np.float32)
    return out


def download_stack(run_start, leadtimes):
    """Pobranie z ADS i przerzedzenie do siatki docelowej."""
    import cams
    tmp = os.path.join(tempfile.gettempdir(), "cams_%s.zip" % run_start.strftime("%Y%m%d"))
    cams.request_hours(
        tmp, "forecast",
        run_start.strftime("%Y-%m-%d"),
        ["00:00"],
        [str(h) for h in leadtimes],
        list(cams.VARIABLES.keys()),
    )
    ds = cams.open_netcdf(tmp)
    src_lat, src_lon, lat_order, lon_order = cams.axes(ds)
    dst_lat, dst_lon = grid.target_axes()
    print("zrodlo: lat %.2f..%.2f (%d), lon %.2f..%.2f (%d)"
          % (src_lat[0], src_lat[-1], len(src_lat), src_lon[0], src_lon[-1], len(src_lon)))

    stack = {}
    for _, (field, fragments) in cams.VARIABLES.items():
        var = cams.find_var(ds, fragments)
        a = cams.read_layer(ds, var, lat_order, lon_order)
        stack[field] = cams.resample(a, src_lat, src_lon, dst_lat, dst_lon)
        print("  %-16s <- %-14s %s" % (field, var, stack[field].shape))
    ds.close()
    return stack


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
    for name in ["alder_pollen", "birch_pollen", "grass_pollen",
                 "mugwort_pollen", "olive_pollen", "ragweed_pollen"]:
        layers[name] = stack[name][idx]
    return layers


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
    leadtimes = leadtime_window(run_start, now)
    print("teraz %s | przebieg %s | leadtime %d..%d"
          % (now.isoformat(), run_start.date(), leadtimes[0], leadtimes[-1]))

    if args.synthetic:
        stack = synthetic_stack(len(leadtimes))
    else:
        stack = download_stack(run_start, leadtimes)

    hours = [run_start + dt.timedelta(hours=h) for h in leadtimes]
    publish_idx = [i for i, h in enumerate(hours) if h >= now][:PUBLISH_HOURS]
    if not publish_idx:
        raise RuntimeError("okno nie zawiera zadnej godziny do publikacji")

    sink = None
    if not args.out:
        import publish
        sink = publish.KV()
    elif not os.path.isdir(args.out):
        os.makedirs(args.out)

    keys = []
    for i in publish_idx:
        stamp = hours[i]
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
