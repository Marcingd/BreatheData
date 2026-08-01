# -*- coding: utf-8 -*-
"""Pobranie i odczyt prognoz CAMS dla Europy z Atmosphere Data Store.

Zbior: cams-europe-air-quality-forecasts (CC-BY, uzycie komercyjne dozwolone
pod warunkiem podania zrodla). Model ensemble, poziom przy gruncie.

Odczyt jest celowo odporny na szczegoly: nazwy zmiennych w plikach CAMS bywaja
skrocone (pm2p5_conc, gpg_conc), dlugosc geograficzna raz idzie 0..360, raz
-180..180, a szerokosc bywa malejaca. Zamiast zakladac jeden wariant,
rozpoznajemy go w locie i logujemy, co znaleziono.
"""
import io
import os
import zipfile

import numpy as np

DATASET = "cams-europe-air-quality-forecasts"

# nazwa w zadaniu ADS -> fragmenty nazwy zmiennej w pliku
VARIABLES = {
    "particulate_matter_2.5um": ("pm2_5", ["pm2p5", "pm2_5"]),
    "particulate_matter_10um":  ("pm10", ["pm10"]),
    "nitrogen_dioxide":         ("no2", ["no2"]),
    "ozone":                    ("o3", ["o3"]),
    "sulphur_dioxide":          ("so2", ["so2"]),
    "alder_pollen":             ("alder_pollen", ["apg", "alder"]),
    "birch_pollen":             ("birch_pollen", ["bpg", "birch"]),
    "grass_pollen":             ("grass_pollen", ["gpg", "grass"]),
    "mugwort_pollen":           ("mugwort_pollen", ["mpg", "mugwort"]),
    "olive_pollen":             ("olive_pollen", ["opg", "olive"]),
    "ragweed_pollen":           ("ragweed_pollen", ["rwpg", "ragweed"]),
}

AREA = [72, -25, 30, 45]        # N, W, S, E


def client():
    import cdsapi
    return cdsapi.Client()


def request_hours(target, kind, date, times, leadtimes, variables):
    """Jedno zadanie do ADS. kind: 'analysis' albo 'forecast'."""
    req = {
        "variable": variables,
        "model": ["ensemble"],
        "level": ["0"],
        "type": [kind],
        "date": [date],
        "time": times,
        "leadtime_hour": leadtimes,
        "data_format": "netcdf_zip",
        "area": AREA,
    }
    client().retrieve(DATASET, req, target)
    return target


def open_netcdf(path):
    """Zwraca uchwyt do Dataset; rozpakowuje archiwum, jesli ADS oddal zip."""
    from netCDF4 import Dataset
    with open(path, "rb") as fh:
        head = fh.read(4)
    if head[:2] == b"PK":
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.endswith(".nc")]
            if not names:
                raise RuntimeError("w archiwum nie ma pliku .nc: %s" % z.namelist())
            data = z.read(names[0])
        return Dataset("inmem.nc", mode="r", memory=data)
    return Dataset(path, mode="r")


def find_var(ds, fragments):
    """Zmienna, ktorej nazwa zawiera ktorykolwiek fragment (bez wielkosci liter)."""
    names = list(ds.variables.keys())
    for frag in fragments:
        for n in names:
            if frag.lower() in n.lower():
                return n
    raise KeyError("brak zmiennej dla %s; dostepne: %s" % (fragments, names))


def axes(ds):
    """Osie lat/lon w porzadku rosnacym plus indeksy sortujace."""
    lat_name = "latitude" if "latitude" in ds.variables else "lat"
    lon_name = "longitude" if "longitude" in ds.variables else "lon"
    lat = np.asarray(ds.variables[lat_name][:], dtype=np.float64)
    lon = np.asarray(ds.variables[lon_name][:], dtype=np.float64)
    lon = np.where(lon > 180.0, lon - 360.0, lon)
    lat_order = np.argsort(lat)
    lon_order = np.argsort(lon)
    return lat[lat_order], lon[lon_order], lat_order, lon_order


def read_layer(ds, var_name, lat_order, lon_order):
    """Tablica (time, lat, lon) posortowana rosnaco po obu osiach."""
    v = ds.variables[var_name]
    a = np.asarray(v[:], dtype=np.float32)
    a = np.squeeze(a)
    if a.ndim == 2:
        a = a[np.newaxis, :, :]
    if a.ndim != 3:
        raise RuntimeError("nieoczekiwany ksztalt %s dla %s" % (a.shape, var_name))
    a = a[:, lat_order, :][:, :, lon_order]
    fill = getattr(v, "_FillValue", None)
    if fill is not None:
        a = np.where(a == fill, np.nan, a)
    return np.where(a > 1e19, np.nan, a)


def nearest_index(src, dst):
    """Indeksy najblizszych punktow siatki zrodlowej dla kazdego punktu docelowego."""
    pos = np.searchsorted(src, dst)
    pos = np.clip(pos, 1, len(src) - 1)
    left = src[pos - 1]
    right = src[pos]
    return np.where(np.abs(dst - left) <= np.abs(right - dst), pos - 1, pos)


def resample(a, src_lat, src_lon, dst_lat, dst_lon):
    """Przerzedzenie do siatki docelowej metoda najblizszego sasiada."""
    ri = nearest_index(src_lat, dst_lat)
    ci = nearest_index(src_lon, dst_lon)
    return a[..., ri, :][..., :, ci]
