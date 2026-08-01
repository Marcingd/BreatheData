# -*- coding: utf-8 -*-
"""Testy indeksow i formatu siatki. Uruchomienie: python tests/test_pipeline.py"""
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import aqi     # noqa: E402
import grid    # noqa: E402

fails = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s  %s" % (name, detail))
        fails.append(name)


def scalar(a):
    return float(np.ravel(np.asarray(a))[0])


def close(a, b, tol=0.51):
    return abs(scalar(a) - scalar(b)) <= tol


print("indeks europejski")
z = np.zeros((1, 1), dtype=np.float32)
check("PM2.5 10 ug/m3 to granica pasma dobre/umiarkowane (20)",
      close(aqi.european_aqi(np.full((1, 1), 10.0), z, z, z, z), 20.0))
check("PM2.5 5 ug/m3 daje polowe pierwszego pasma (10)",
      close(aqi.european_aqi(np.full((1, 1), 5.0), z, z, z, z), 10.0))
check("decyduje najgorszy skladnik",
      close(aqi.european_aqi(np.full((1, 1), 5.0), z, z, np.full((1, 1), 130.0), z), 60.0))
check("ponad ostatni prog wysyca sie na 120",
      close(aqi.european_aqi(np.full((1, 1), 5000.0), z, z, z, z), 120.0))

print("indeks US EPA")
check("PM2.5 12.0 ug/m3 to 50", close(aqi.us_aqi(np.full((1, 1), 12.0), z, z, z, z), 50.0, 1.0))
check("PM2.5 35.4 ug/m3 to 100", close(aqi.us_aqi(np.full((1, 1), 35.4), z, z, z, z), 100.0, 1.0))
# 100 ug/m3 ozonu to 50,9 ppb, czyli koniec pierwszego pasma EPA (54 ppb)
check("ozon 100 ug/m3 (50,9 ppb) daje indeks okolo 47",
      44 <= scalar(aqi.us_aqi(z, z, np.full((1, 1), 100.0), z, z)) <= 50)
check("ozon 140 ug/m3 (71,3 ppb) przechodzi w trzecie pasmo",
      101 <= scalar(aqi.us_aqi(z, z, np.full((1, 1), 140.0), z, z)) <= 110)

print("srednia kroczaca")
stack = np.arange(48, dtype=np.float32).reshape(48, 1, 1)
check("okno 24 h konczace sie na 47 ma srednia 35.5",
      close(aqi.trailing_mean(stack, 47, 24), 35.5, 0.01))
check("okno przycina sie na poczatku serii",
      close(aqi.trailing_mean(stack, 3, 24), 1.5, 0.01))

print("kwantowanie")
for v in (0.0, 0.4, 12.3, 188.4, 3000.0):
    q = grid.quant_log(np.array([v]))
    back = float(grid.dequant_log(q)[0])
    rel = abs(back - v) / max(v, 1.0)
    check("log %.1f -> %d -> %.1f (blad %.1f%%)" % (v, q[0], back, rel * 100), rel < 0.05)
check("NaN koduje sie jako brak danych",
      int(grid.quant_log(np.array([np.nan]))[0]) == grid.MISSING)

print("format siatki")
lats, lons = grid.target_axes()
check("siatka ma zalozony ksztalt", (grid.NLAT, grid.NLON) == (169, 281),
      "%dx%d" % (grid.NLAT, grid.NLON))
check("os szerokosci konczy sie na 72", close(lats[-1], 72.0, 0.001))
check("os dlugosci konczy sie na 45", close(lons[-1], 45.0, 0.001))

layers = {name: np.full((grid.NLAT, grid.NLON), 0.0, dtype=np.float32) for name in grid.FIELDS}
layers["european_aqi"][:] = 42.0
layers["us_aqi"][:] = 88.0
layers["pm2_5"][:] = 16.2
layers["pm10"][:] = 22.0
layers["grass_pollen"][:] = 18.1
layers["mugwort_pollen"][:] = 188.4
blob = grid.encode(layers, 1785355200)
check("rozmiar pliku zgadza sie z naglowkiem i liczba komorek",
      len(blob) == grid.HEADER_SIZE + grid.NLAT * grid.NLON * grid.CELL_SIZE,
      str(len(blob)))

cell = grid.decode_cell(blob, 52.25, 21.0)
check("odczyt komorki dla Warszawy zwraca wartosci", cell is not None)
check("european_aqi bez zmian", close(cell["european_aqi"], 42.0))
check("us_aqi w rozdzielczosci 2", close(cell["us_aqi"], 88.0, 1.01))
check("pm2_5 w granicy bledu", close(cell["pm2_5"], 16.2, 0.9))
check("mugwort_pollen w granicy bledu", close(cell["mugwort_pollen"], 188.4, 9.0))
check("poza siatka zwraca None", grid.decode_cell(blob, 12.0, 100.0) is None)

# dane referencyjne dla testu dekodera w JavaScripcie
out_dir = os.path.join(ROOT, "tests", "fixtures")
if not os.path.isdir(out_dir):
    os.makedirs(out_dir)
with open(os.path.join(out_dir, "grid.bin"), "wb") as fh:
    fh.write(blob)
probes = [(52.25, 21.0), (30.0, -25.0), (72.0, 45.0), (48.5, 2.25)]
ref = [{"lat": la, "lon": lo, "cell": grid.decode_cell(blob, la, lo)} for la, lo in probes]
with open(os.path.join(out_dir, "expected.json"), "w") as fh:
    json.dump(ref, fh, indent=2)
print("  zapisano fixtures dla testu JS")

print()
if fails:
    print("NIEUDANE: %d" % len(fails))
    sys.exit(1)
print("wszystko przeszlo")
