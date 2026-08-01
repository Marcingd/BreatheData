# -*- coding: utf-8 -*-
"""Przeliczanie stezen na indeksy jakosci powietrza.

Europejski indeks (EEA/CAMS): PM z sredniej 24-godzinnej, gazy z wartosci
godzinowej; indeks calkowity to najgorszy skladnik. Kazde pasmo ma szerokosc
20 punktow, wewnatrz pasma interpolujemy liniowo, dokladnie tak jak robi to
Open-Meteo, zeby liczby nie rozjechaly sie po zmianie zrodla.

US EPA AQI: te same stezenia przeliczone na jednostki EPA (ppb dla gazow)
i zlozone z tablic granicznych.
"""
import numpy as np

# --- Europejski indeks ------------------------------------------------------
# granice pasm w ug/m3; szesc pasm -> siedem punktow lamanej
EU_BREAKS = {
    "pm2_5": [0.0, 10.0, 20.0, 25.0, 50.0, 75.0, 800.0],
    "pm10":  [0.0, 20.0, 40.0, 50.0, 100.0, 150.0, 1200.0],
    "no2":   [0.0, 40.0, 90.0, 120.0, 230.0, 340.0, 1000.0],
    "o3":    [0.0, 50.0, 100.0, 130.0, 240.0, 380.0, 800.0],
    "so2":   [0.0, 100.0, 200.0, 350.0, 500.0, 750.0, 1250.0],
}
EU_INDEX = [0.0, 20.0, 40.0, 60.0, 80.0, 100.0, 120.0]

# --- US EPA -----------------------------------------------------------------
# (dolne stezenie, gorne stezenie, dolny indeks, gorny indeks)
US_BREAKS = {
    "pm2_5": [(0.0, 12.0, 0, 50), (12.1, 35.4, 51, 100), (35.5, 55.4, 101, 150),
              (55.5, 150.4, 151, 200), (150.5, 250.4, 201, 300), (250.5, 500.4, 301, 500)],
    "pm10":  [(0, 54, 0, 50), (55, 154, 51, 100), (155, 254, 101, 150),
              (255, 354, 151, 200), (355, 424, 201, 300), (425, 604, 301, 500)],
    "o3":    [(0, 54, 0, 50), (55, 70, 51, 100), (71, 85, 101, 150),
              (86, 105, 151, 200), (106, 200, 201, 300)],
    "no2":   [(0, 53, 0, 50), (54, 100, 51, 100), (101, 360, 101, 150),
              (361, 649, 151, 200), (650, 1249, 201, 300), (1250, 2049, 301, 500)],
    "so2":   [(0, 35, 0, 50), (36, 75, 51, 100), (76, 185, 101, 150),
              (186, 304, 151, 200), (305, 604, 201, 300)],
}

# ug/m3 -> ppb przy 25 C i 1013 hPa (24.45 / masa molowa)
UG_TO_PPB = {"o3": 24.45 / 48.00, "no2": 24.45 / 46.01, "so2": 24.45 / 64.07}


def _interp_piecewise(x, xs, ys):
    """Interpolacja po lamanej z wysyceniem na koncach; NaN przechodzi dalej."""
    out = np.interp(x, xs, ys, left=ys[0], right=ys[-1])
    return np.where(np.isnan(x), np.nan, out)


def european_aqi(pm25_24h, pm10_24h, no2, o3, so2):
    """Europejski indeks jako maksimum podindeksow. Wejscia w ug/m3."""
    parts = [
        _interp_piecewise(pm25_24h, EU_BREAKS["pm2_5"], EU_INDEX),
        _interp_piecewise(pm10_24h, EU_BREAKS["pm10"], EU_INDEX),
        _interp_piecewise(no2, EU_BREAKS["no2"], EU_INDEX),
        _interp_piecewise(o3, EU_BREAKS["o3"], EU_INDEX),
        _interp_piecewise(so2, EU_BREAKS["so2"], EU_INDEX),
    ]
    return np.fmax.reduce(parts)


def _us_subindex(conc, table, step):
    """Podindeks EPA. Stezenie jest najpierw obcinane do rozdzielczosci tablicy
    (PM2.5 do 0,1 ug/m3, reszta do jednostki), bo miedzy pasmami sa szczeliny:
    granice ida 12,0 / 12,1 i 54 / 55. Bez obcinania wartosc z takiej szczeliny
    nie trafia do zadnego pasma i po cichu wypada z maksimum."""
    trunc = np.floor(conc / step) * step
    out = np.full(conc.shape, np.nan, dtype=np.float32)
    for lo_c, hi_c, lo_i, hi_i in table:
        m = (trunc >= lo_c) & (trunc <= hi_c)
        if not np.any(m):
            continue
        span = (hi_c - lo_c) if hi_c > lo_c else 1.0
        out[m] = lo_i + (hi_i - lo_i) * (trunc[m] - lo_c) / span
    # powyzej ostatniego progu przypinamy gorny indeks
    top_c, top_i = table[-1][1], table[-1][3]
    out = np.where(trunc > top_c, float(top_i), out)
    return np.where(np.isnan(conc), np.nan, out)


def us_aqi(pm25_24h, pm10_24h, o3_8h, no2, so2, parts=False):
    """US EPA AQI jako maksimum podindeksow. Wejscia w ug/m3."""
    sub = {
        "pm2_5": _us_subindex(pm25_24h, US_BREAKS["pm2_5"], 0.1),
        "pm10": _us_subindex(pm10_24h, US_BREAKS["pm10"], 1.0),
        "o3": _us_subindex(o3_8h * UG_TO_PPB["o3"], US_BREAKS["o3"], 1.0),
        "no2": _us_subindex(no2 * UG_TO_PPB["no2"], US_BREAKS["no2"], 1.0),
        "so2": _us_subindex(so2 * UG_TO_PPB["so2"], US_BREAKS["so2"], 1.0),
    }
    total = np.fmax.reduce(list(sub.values()))
    return (total, sub) if parts else total


def trailing_mean(stack, end_idx, hours):
    """Srednia kroczaca po osi czasu, konczaca sie na end_idx wlacznie."""
    start = max(0, end_idx - hours + 1)
    return np.nanmean(stack[start:end_idx + 1], axis=0)
