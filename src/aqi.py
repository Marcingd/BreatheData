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
# Tablice EPA podaje sie parami z przerwa (12,0 potem 12,1), bo zaklada
# obcinanie stezenia do jednego miejsca po przecinku. Trzymamy je tu jako
# ciagla lamana: gorna granica pasma jest zarazem dolna granica nastepnego.
# Dzieki temu zadna wartosc nie wpada w szczeline, tak samo jak w indeksie EU.
US_BREAKS = {
    "pm2_5": [0.0, 12.0, 35.4, 55.4, 150.4, 250.4, 500.4],
    "pm10":  [0.0, 54.0, 154.0, 254.0, 354.0, 424.0, 604.0],
    "o3":    [0.0, 54.0, 70.0, 85.0, 105.0, 200.0],
    "no2":   [0.0, 53.0, 100.0, 360.0, 649.0, 1249.0, 2049.0],
    "so2":   [0.0, 35.0, 75.0, 185.0, 304.0, 604.0],
}
US_INDEX6 = [0.0, 50.0, 100.0, 150.0, 200.0, 300.0, 500.0]
US_INDEX5 = [0.0, 50.0, 100.0, 150.0, 200.0, 300.0]

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


def _us_subindex(conc, breaks):
    """Podindeks EPA po ciaglej lamanej, z wysyceniem powyzej ostatniego progu."""
    idx = US_INDEX6 if len(breaks) == 7 else US_INDEX5
    return _interp_piecewise(conc, breaks, idx)


def us_aqi(pm25_24h, pm10_24h, o3_8h, no2, so2, parts=False):
    """US EPA AQI jako maksimum podindeksow. Wejscia w ug/m3."""
    sub = {
        "pm2_5": _us_subindex(pm25_24h, US_BREAKS["pm2_5"]),
        "pm10": _us_subindex(pm10_24h, US_BREAKS["pm10"]),
        "o3": _us_subindex(o3_8h * UG_TO_PPB["o3"], US_BREAKS["o3"]),
        "no2": _us_subindex(no2 * UG_TO_PPB["no2"], US_BREAKS["no2"]),
        "so2": _us_subindex(so2 * UG_TO_PPB["so2"], US_BREAKS["so2"]),
    }
    total = np.fmax.reduce(list(sub.values()))
    return (total, sub) if parts else total


def trailing_mean(stack, end_idx, hours):
    """Srednia kroczaca po osi czasu, konczaca sie na end_idx wlacznie."""
    start = max(0, end_idx - hours + 1)
    return np.nanmean(stack[start:end_idx + 1], axis=0)
