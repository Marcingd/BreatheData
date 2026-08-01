# -*- coding: utf-8 -*-
"""Format siatki "BRT1" - jeden bajt na wartosc, jeden plik na godzine.

Wiersz = szerokosc rosnaco od lat0, kolumna = dlugosc rosnaco od lon0.
Naglowek (little endian, 28 B):
    magic 4s | lat0 f32 | lon0 f32 | dlat f32 | dlon f32 | nlat u16 | nlon u16 | epoch u32
Dalej nlat*nlon rekordow po 10 bajtow:
    0 eaqi | 1 usaqi | 2 pm2_5 | 3 pm10 | 4 alder | 5 birch | 6 grass | 7 mugwort
    8 olive | 9 ragweed
Stezenia sa kwantowane logarytmicznie (0,05 ug/m3 rozdzielczosci przy dole
skali, gorna granica ~39 000), bo pylki potrafia isc w tysiace ziaren na m3.
Wartosc 255 oznacza brak danych. Dekoder w workerze musi trzymac te same stale.
"""
import struct
import numpy as np

MAGIC = b"BRT1"
HEADER = "<4sffffHHI"
HEADER_SIZE = struct.calcsize(HEADER)
CELL_SIZE = 10
MISSING = 255
LOG_SCALE = 24.0

# kolejnosc pol w rekordzie
FIELDS = ["european_aqi", "us_aqi", "pm2_5", "pm10",
          "alder_pollen", "birch_pollen", "grass_pollen",
          "mugwort_pollen", "olive_pollen", "ragweed_pollen"]

# domena CAMS dla Europy, przerzedzona do 0,25 stopnia (~25 km)
LAT0, LAT1 = 30.0, 72.0
LON0, LON1 = -25.0, 45.0
STEP = 0.25
NLAT = int(round((LAT1 - LAT0) / STEP)) + 1
NLON = int(round((LON1 - LON0) / STEP)) + 1


def quant_log(a):
    """Stezenie -> bajt (logarytmicznie), NaN -> 255."""
    a = np.asarray(a, dtype=np.float64)
    q = np.rint(np.log1p(np.clip(a, 0.0, None)) * LOG_SCALE)
    q = np.clip(q, 0, MISSING - 1)
    return np.where(np.isfinite(a), q, MISSING).astype(np.uint8)


def dequant_log(q):
    q = np.asarray(q, dtype=np.float64)
    return np.where(q >= MISSING, np.nan, np.expm1(q / LOG_SCALE))


def quant_linear(a, divisor=1.0):
    a = np.asarray(a, dtype=np.float64)
    q = np.rint(a / divisor)
    q = np.clip(q, 0, MISSING - 1)
    return np.where(np.isfinite(a), q, MISSING).astype(np.uint8)


def encode(layers, epoch):
    """layers: slownik nazwa pola -> tablica (NLAT, NLON) w jednostkach fizycznych."""
    cells = np.empty((NLAT, NLON, CELL_SIZE), dtype=np.uint8)
    cells[:, :, 0] = quant_linear(layers["european_aqi"])
    cells[:, :, 1] = quant_linear(layers["us_aqi"], 2.0)
    for i, name in enumerate(FIELDS[2:], start=2):
        cells[:, :, i] = quant_log(layers[name])
    head = struct.pack(HEADER, MAGIC, LAT0, LON0, STEP, STEP, NLAT, NLON, int(epoch))
    return head + cells.tobytes()


def decode_cell(blob, lat, lon):
    """Referencyjny dekoder - sluzy testom i musi zgadzac sie z workerem."""
    magic, lat0, lon0, dlat, dlon, nlat, nlon, epoch = struct.unpack_from(HEADER, blob, 0)
    if magic != MAGIC:
        raise ValueError("zly naglowek")
    r = int(round((lat - lat0) / dlat))
    c = int(round((lon - lon0) / dlon))
    if r < 0 or r >= nlat or c < 0 or c >= nlon:
        return None
    off = HEADER_SIZE + (r * nlon + c) * CELL_SIZE
    raw = blob[off:off + CELL_SIZE]
    out = {"time": epoch}
    for i, name in enumerate(FIELDS):
        v = raw[i]
        if v == MISSING:
            out[name] = None
        elif i == 0:
            out[name] = float(v)
        elif i == 1:
            out[name] = float(v) * 2.0
        else:
            out[name] = round(float(dequant_log(v)), 1)
    return out


def target_axes():
    """Osie siatki docelowej."""
    lats = LAT0 + np.arange(NLAT) * STEP
    lons = LON0 + np.arange(NLON) * STEP
    return lats, lons
