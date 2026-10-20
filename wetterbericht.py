#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Wetterbericht Riederalp – Wallis – Schweiz – Europa
====================================================

Erzeugt aus Open-Meteo-Daten einen Wetterbericht im Stil von MeteoSchweiz,
zugeschnitten auf die Riederalp (1925 m), mit Diagnostik für Klima- und
Atmosphärenwissenschaftler und explizit ausgewiesenen Unsicherheiten.

Datenquellen (alle über Open-Meteo, kein API-Key nötig)
  * Deterministisch: MeteoSchweiz ICON-CH1/CH2, ECMWF IFS 9 km, ECMWF AIFS,
    DWD ICON-D2/EU, Météo-France AROME, ItaliaMeteo ICON-2I, NOAA GFS
  * Ensembles: ICON-CH1-EPS, ICON-CH2-EPS, ECMWF ENS, ECMWF AIFS-ENS, NOAA GEFS
  * Druckflächen Europa: ECMWF IFS 0,25°
  * Klimatologie: ERA5-Land 1991–2020 (wird lokal gecacht)
  * Aerosol: CAMS (Saharastaub)
  * Schnee: Schneefallgrenze, Neuschnee (fester Faktor und Dichte nach Anderson 1976),
    Modellschneehöhe, jeweils mit Ensemble-Wahrscheinlichkeiten

Nur Python-Standardbibliothek (>= 3.9).

  python3 wetterbericht.py                  # schreibt nach ./wetterbericht_out
  python3 wetterbericht.py --out site       # z. B. für GitHub Pages
  python3 wetterbericht.py --claude         # + synoptische Einordnung via Claude API
                                            #   (Umgebungsvariable ANTHROPIC_API_KEY)

Daten: Open-Meteo.com, CC BY 4.0 (nicht-kommerzielle Nutzung).
"""
from __future__ import annotations

import argparse
import base64
import calendar
import gzip
import datetime as dt
import html
import json
import math
import os
import re
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from zoneinfo import ZoneInfo

VERSION = "1.5"
TZNAME = "Europe/Zurich"
TZ = ZoneInfo(TZNAME)
UA = f"wetterbericht-riederalp/{VERSION} (Python urllib; nicht-kommerziell)"
PAUSE = 0.5  # Sekunden zwischen API-Aufrufen (Open-Meteo-Limits: 600/min)

API_FC = "https://api.open-meteo.com/v1/forecast"
API_ENS = "https://ensemble-api.open-meteo.com/v1/ensemble"
API_ARCH = "https://archive-api.open-meteo.com/v1/archive"
API_AQ = "https://air-quality-api.open-meteo.com/v1/air-quality"
API_META = ("https://api.open-meteo.com/data/{}/static/meta.json",
            "https://ensemble-api.open-meteo.com/data/{}/static/meta.json")


def _p(name, lat, lon, elev=None):
    return {"name": name, "lat": lat, "lon": lon, "elev": elev}


# ------------------------------------------------------------------ Orte
LOCAL = {
    # Riederalp und Höhenprofil Rhonetal – Aletsch
    "riederalp": _p("Riederalp", 46.3797, 8.0262, 1925),
    "moosfluh": _p("Moosfluh", 46.3925, 8.0440, 2333),
    "moerel": _p("Mörel", 46.3567, 8.0442, 759),
    "jungfraujoch": _p("Jungfraujoch", 46.5475, 7.9853, 3571),
    # Wallis
    "sion": _p("Sion", 46.2186, 7.3306, 482),
    "visp": _p("Visp", 46.2938, 7.8815, 651),
    "martigny": _p("Martigny", 46.1028, 7.0727, 471),
    "zermatt": _p("Zermatt", 46.0207, 7.7491, 1610),
    "simplon": _p("Simplon Dorf", 46.1963, 8.0567, 1472),
    "grimsel": _p("Grimsel Hospiz", 46.5717, 8.3333, 1980),
    # Schweiz
    "zuerich": _p("Zürich", 47.3779, 8.5658, 556),
    "bern": _p("Bern", 46.9481, 7.4474, 540),
    "napf": _p("Napf", 47.0046, 7.9403, 1404),
    "basel": _p("Basel", 47.5596, 7.5886, 260),
    "genf": _p("Genf", 46.2044, 6.1432, 375),
    "lugano": _p("Lugano", 46.0037, 8.9511, 273),
    "chur": _p("Chur", 46.8508, 9.5320, 593),
    "altdorf": _p("Altdorf", 46.8803, 8.6440, 449),
    "saentis": _p("Säntis", 47.2493, 9.3434, 2502),
    # zusätzliche Orte für die Schweizkarte
    "luzern": _p("Luzern", 47.0502, 8.3093, 436),
    "stgallen": _p("St. Gallen", 47.4245, 9.3767, 670),
    "neuchatel": _p("Neuchâtel", 46.9900, 6.9293, 430),
    "locarno": _p("Locarno", 46.1670, 8.7943, 200),
    "davos": _p("Davos", 46.8027, 9.8360, 1560),
    "samedan": _p("Samedan", 46.5340, 9.8721, 1709),
}
PROFILE = ["moerel", "riederalp", "moosfluh", "jungfraujoch"]
ENS_LOCS = ["riederalp", "bern", "simplon", "sion"]
CLIM_LOCS = ["riederalp", "sion", "bern", "lugano"]

EU_CITIES = {
    "reykjavik": ("Reykjavík", 64.15, -21.94), "dublin": ("Dublin", 53.35, -6.26),
    "london": ("London", 51.51, -0.13), "paris": ("Paris", 48.86, 2.35),
    "madrid": ("Madrid", 40.42, -3.70), "lissabon": ("Lissabon", 38.72, -9.14),
    "rom": ("Rom", 41.90, 12.50), "berlin": ("Berlin", 52.52, 13.40),
    "warschau": ("Warschau", 52.23, 21.01), "stockholm": ("Stockholm", 59.33, 18.07),
    "oslo": ("Oslo", 59.91, 10.75), "athen": ("Athen", 37.98, 23.73),
    "istanbul": ("Istanbul", 41.01, 28.98), "kiew": ("Kiew", 50.45, 30.52),
    "moskau": ("Moskau", 55.76, 37.62),
}
GRID_LATS = [40, 50, 60, 70, 80]
GRID_LONS = [-20, -10, 0, 10, 20, 30]
UPPER = {f"c_{k}": _p(*v) for k, v in EU_CITIES.items()}
for _la in GRID_LATS:
    for _lo in GRID_LONS:
        UPPER[f"g_{_la}_{_lo}"] = _p(f"{_la}° N {_lo}° E", _la, _lo)
UPPER["alpen"] = _p("Alpenhauptkamm", 46.38, 8.03)
UPPER["ossola"] = _p("Ossola", 46.12, 8.29)
UPPER["bern_ua"] = _p("Bern", 46.95, 7.45)

def region(la, lo):
    return REGION.get((la, lo), f"{la}° N {lo}° E")


MAPS = {
    "europa": {"bbox": (32.0, 71.0, -24.0, 39.0), "W": 420},
    "schweiz": {"bbox": (45.75, 47.85, 5.85, 10.55), "W": 420, "z": 8, "vex": 3.0},
    "wallis": {"bbox": (45.85, 46.65, 6.75, 8.45), "W": 420, "z": 9, "vex": 2.0},
    "riederalp": {"bbox": (46.34, 46.56, 7.90, 8.18), "W": 420, "z": 11, "vex": 1.3},
    "welt": {"bbox": (-90.0, 84.0, -180.0, 180.0), "W": 420},
}
EU_LATS = [32 + 3 * i for i in range(14)]
EU_LONS = [-24 + 3 * i for i in range(22)]
CH_LATS = [round(45.8 + 0.2 * i, 2) for i in range(11)]
CH_LONS = [round(5.9 + 0.2 * i, 2) for i in range(24)]
EU_GRID = {f"e{la}_{lo}": _p("", la, lo) for la in EU_LATS for lo in EU_LONS}
CH_GRID = {f"s{la}_{lo}": _p("", la, lo) for la in CH_LATS for lo in CH_LONS}


REGION = {
    (50, -10): "dem Seegebiet südwestlich von Irland", (50, 0): "dem Ärmelkanal",
    (50, 10): "Deutschland", (50, 20): "Polen",
    (40, -10): "Portugal", (40, 0): "Ostspanien und den Balearen",
    (40, 10): "Sardinien", (40, 20): "Süditalien",
}

# ---------------------------------------------------------------- Modelle
# Mehrere Kandidaten-IDs pro Modell: Open-Meteo benennt Modelle gelegentlich um.
PRIMARY = [("ICON-CH", ["meteoswiss_icon_seamless"]),
           ("IFS 9 km", ["ecmwf_ifs", "ecmwf_ifs025"])]
COMPARE = [
    ("ICON-CH1 (1 km)", ["meteoswiss_icon_ch1"]),
    ("ICON-CH2 (2 km)", ["meteoswiss_icon_ch2"]),
    ("AROME (1,3 km)", ["meteofrance_arome_france_hd", "meteofrance_arome_france"]),
    ("ICON-2I (2 km)", ["italia_meteo_arpae_icon_2i"]),
    ("ICON-D2 (2 km)", ["icon_d2", "dwd_icon_d2"]),
    ("ICON-EU (7 km)", ["icon_eu", "dwd_icon_eu"]),
    ("IFS (9 km)", ["ecmwf_ifs", "ecmwf_ifs025"]),
    ("AIFS (KI)", ["ecmwf_aifs025_single", "ecmwf_aifs025"]),
    ("GFS", ["gfs_seamless", "ncep_gfs_seamless"]),
]
ENSEMBLES = [
    ("ICON-CH1-EPS", ["meteoswiss_icon_ch1", "meteoswiss_icon_ch1_ensemble"]),
    ("ICON-CH2-EPS", ["meteoswiss_icon_ch2", "meteoswiss_icon_ch2_ensemble"]),
    ("ECMWF ENS", ["ecmwf_ifs025", "ecmwf_ifs025_ensemble"]),
    ("ECMWF AIFS-ENS", ["ecmwf_aifs025", "ecmwf_aifs025_ensemble"]),
    ("NOAA GEFS", ["gfs025", "ncep_gefs025"]),
]
HOURLY_LOCAL = ["temperature_2m", "relative_humidity_2m", "precipitation", "snowfall",
                "cloud_cover", "cloud_cover_low", "pressure_msl", "wind_speed_10m",
                "wind_direction_10m", "wind_gusts_10m", "freezing_level_height",
                "snowfall_height", "sunshine_duration", "cape", "snow_depth"]
ENS_VARS = ["temperature_2m", "precipitation", "snowfall", "cloud_cover_low", "snowfall_height", "snow_depth"]
SNOW_TABLE = ["moerel", "simplon", "zermatt", "riederalp", "grimsel", "moosfluh"]  # nach Höhe
UPPER_MODELS = ["ecmwf_ifs025", "gfs_global", "icon_global"]  # Druckflächen; Ersatzmodelle bei Ausfall
MODEL_NAMES = {"ecmwf_ifs025": "ECMWF IFS 0,25°", "gfs_global": "NOAA GFS 0,25°", "icon_global": "DWD ICON global",
               "meteoswiss_icon_seamless": "ICON-CH", "ecmwf_ifs": "ECMWF IFS 9 km",
               "best_match": "Open-Meteo Best Match"}
UPPER_VARS = ["pressure_msl", "geopotential_height_500hPa", "temperature_850hPa",
              "wind_speed_250hPa", "temperature_700hPa", "relative_humidity_700hPa",
              "wind_speed_700hPa", "wind_direction_700hPa"]

LOG: list[str] = []


def log(msg):
    LOG.append(msg)
    print(msg, file=sys.stderr)


# ================================================================ Abruf
class ApiError(Exception):
    pass


OM_KEY = os.environ.get("OPEN_METEO_API_KEY")  # optional: kommerzieller Zugang, höhere Limits


def fetch_json(url, params=None, timeout=90, retries=4, quiet=False):
    if OM_KEY and "open-meteo.com" in url and "/data/" not in url:
        url = url.replace("://", "://customer-", 1) if "://customer-" not in url else url
        params = {**(params or {}), "apikey": OM_KEY}
    full = url + ("?" + urllib.parse.urlencode(params, safe=",:") if params else "")
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(full, headers={"User-Agent": UA, "Accept": "application/json",
                                                        "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            try:
                reason = json.loads(body).get("reason", body)
            except Exception:
                reason = body
            if e.code == 429 or e.code >= 500:
                wait = 65 if e.code == 429 else 5 * (attempt + 1)
                last = ApiError(f"HTTP {e.code}: {str(reason)[:160]}")
                if not quiet:
                    print(f"  … {last}; warte {wait} s", file=sys.stderr)
                time.sleep(wait)
                continue
            raise ApiError(f"HTTP {e.code}: {str(reason)[:200]}")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = ApiError(f"Netzwerk: {e}")
            if attempt + 1 < retries:
                time.sleep(3 * (attempt + 1))
    raise last or ApiError("unbekannter Fehler")


def coords(keys, table, elev=True):
    p = {"latitude": ",".join(f"{table[k]['lat']:.4f}" for k in keys),
         "longitude": ",".join(f"{table[k]['lon']:.4f}" for k in keys)}
    if elev:
        p["elevation"] = ",".join(str(table[k]["elev"]) for k in keys)
    return p


def parse_hourly(data, keys, days):
    items = data if isinstance(data, list) else [data]
    if len(items) != len(keys):
        raise ApiError("Antwort enthält nicht alle Orte")
    out = {}
    for k, it in zip(keys, items):
        h = it.get("hourly") or {}
        rec = {"t": [dt.datetime.fromisoformat(s) for s in h.get("time", [])],
               "v": {kk: vv for kk, vv in h.items() if kk != "time"},
               "grid_elev": it.get("elevation")}
        index_days(rec, days)
        out[k] = rec
    return out


def index_days(rec, days):
    idx = {d: [] for d in days}
    for i, t in enumerate(rec["t"]):
        if t.date() in idx:
            idx[t.date()].append(i)
    rec["idx"] = idx
    rec["tidx"] = {t: i for i, t in enumerate(rec["t"])}


_CALLS = []


def rate_budget(weight, limit=500):
    """Hält die geschätzte Open-Meteo-Last unter dem Minutenlimit (600 gewichtete Aufrufe)."""
    for _ in range(4):
        now = time.time()
        _CALLS[:] = [(t, w) for t, w in _CALLS if now - t < 60]
        if not _CALLS or sum(w for _, w in _CALLS) + weight <= limit:
            break
        wait = max(1.0, 61 - (now - _CALLS[0][0]))
        print(f"  … warte {wait:.0f} s (Open-Meteo-Minutenlimit)", file=sys.stderr)
        time.sleep(wait)
    _CALLS.append((time.time(), weight))


def parse_daily(data, keys, days=None):
    items = data if isinstance(data, list) else [data]
    if len(items) != len(keys):
        raise ApiError("Antwort enthält nicht alle Orte")
    out = {}
    for k, it in zip(keys, items):
        dd = it.get("daily") or {}
        out[k] = {"d": [dt.date.fromisoformat(x) for x in dd.get("time", [])],
                  "v": {kk: vv for kk, vv in dd.items() if kk != "time"}}
    return out


def fetch_model(api, label, candidates, params, keys, days, factor=1.0, parser=None):
    last = None
    nvars = len((params.get("hourly") or params.get("daily") or "x").split(","))
    weight = len(keys) * max(1, math.ceil(nvars / 10)) * max(1, math.ceil(params.get("forecast_days", 7) / 14)) * factor
    known = MODEL_IDS.get(label)
    if known in candidates:
        candidates = [known] + [c for c in candidates if c != known]
    for mid in candidates:
        p = dict(params)
        if mid:
            p["models"] = mid
        try:
            rate_budget(weight)
            data = fetch_json(api, p)
            time.sleep(PAUSE)
            parsed = (parser or parse_hourly)(data, keys, days)
            if not any(valid(arr) for rec in parsed.values() for arr in rec["v"].values()):
                raise ApiError("nur leere Werte")
            MODEL_IDS[label] = mid
            status(label, True, mid or "Standard")
            return mid, parsed
        except ApiError as e:
            last = e
            time.sleep(PAUSE)
    log(f"{label} ({last})")
    status(label, False, last)
    return None, None


def model_run(mid):
    """Initialisierungszeit des letzten Laufs aus der Open-Meteo-Metadaten-API."""
    for host in API_META:
        for cand in (mid, mid + "_ensemble"):
            try:
                j = fetch_json(host.format(cand), timeout=15, retries=1, quiet=True)
                ts = j.get("last_run_initialisation_time")
                if ts:
                    return dt.datetime.fromtimestamp(ts, dt.timezone.utc)
            except ApiError:
                continue
    return None


def collect(days, cache_dir):
    D = {"primary": [], "compare": [], "ens": [], "up": None, "aq": None, "runs": {}}
    keys = list(LOCAL)
    nd = len(days)
    base = {**coords(keys, LOCAL), "hourly": ",".join(HOURLY_LOCAL), "timezone": TZNAME,
            "forecast_days": nd}
    for label, cands in PRIMARY:
        print(f"Lade {label} …", file=sys.stderr)
        mid, parsed = fetch_model(API_FC, label, cands, base, keys, days)
        if parsed:
            D["primary"].append((label, mid, parsed))
            D["runs"][label] = mid

    cbase = {**coords(["riederalp"], LOCAL), "hourly": "temperature_2m,precipitation,snowfall",
             "timezone": TZNAME, "forecast_days": nd}
    for label, cands in COMPARE:
        print(f"Lade Vergleich {label} …", file=sys.stderr)
        mid, parsed = fetch_model(API_FC, label, cands, cbase, ["riederalp"], days)
        if parsed:
            D["compare"].append((label, mid, parsed["riederalp"]))
            D["runs"][label] = mid

    ebase = {**coords(ENS_LOCS, LOCAL), "hourly": ",".join(ENS_VARS), "timezone": TZNAME,
             "forecast_days": nd}
    for label, cands in ENSEMBLES:
        print(f"Lade Ensemble {label} …", file=sys.stderr)
        mid, parsed = fetch_model(API_ENS, label, cands, ebase, ENS_LOCS, days, factor=5)
        if parsed:
            D["ens"].append((label, mid, parsed))
            D["runs"][label] = mid

    print("Lade Druckflächen Europa …", file=sys.stderr)
    ukeys = list(UPPER)
    udays = days + [days[-1] + dt.timedelta(1)]
    ubase = {**coords(ukeys, UPPER, elev=False), "hourly": ",".join(UPPER_VARS),
             "timezone": "GMT", "forecast_days": nd, "wind_speed_unit": "ms",
             "cell_selection": "nearest"}
    mid, up = fetch_model(API_FC, "Druckflächen Europa", UPPER_MODELS, ubase, ukeys, udays)
    D["up"] = up
    if mid:
        D["runs"]["Druckflächen Europa"] = mid

    print("Lade Kartenfeld Europa …", file=sys.stderr)
    ekeys = list(EU_GRID)
    egp = {**coords(ekeys, EU_GRID, elev=False), "hourly": "pressure_msl,temperature_850hPa", "timezone": "GMT",
           "forecast_days": nd, "cell_selection": "nearest"}
    D["eu_grid_model"], D["eu_grid"] = fetch_model(API_FC, "Europakarte", UPPER_MODELS, egp, ekeys, udays)
    print("Lade Kartenfeld Schweiz …", file=sys.stderr)
    skeys = list(CH_GRID)
    sgp = {**coords(skeys, CH_GRID, elev=False), "daily": "precipitation_sum,snowfall_sum,temperature_2m_max",
           "timezone": TZNAME, "forecast_days": min(5, nd)}
    D["ch_grid_model"], D["ch_grid"] = fetch_model(API_FC, "Schweizkarte", ["meteoswiss_icon_seamless", "ecmwf_ifs"],
                                                   sgp, skeys, days, parser=parse_daily)

    print("Lade CAMS-Aerosol …", file=sys.stderr)
    try:
        aqp = {**coords(["riederalp"], LOCAL, elev=False), "hourly": "dust,aerosol_optical_depth,european_aqi," + ",".join(POLLEN),
               "timezone": TZNAME, "forecast_days": min(5, nd)}
        D["aq"] = parse_hourly(fetch_json(API_AQ, aqp), ["riederalp"], days)
        status("CAMS Aerosol, Luftqualität, Pollen", True)
    except ApiError as e:
        log(f"CAMS-Aerosol ({e})")
        status("CAMS Aerosol, Luftqualität, Pollen", False, e)

    print("Lade UV und Sonnenzeiten …", file=sys.stderr)
    _m, D["sun"] = fetch_model(API_FC, "UV und Sonnenzeiten", ["best_match"],
                               {**coords(["riederalp"], LOCAL), "daily": "uv_index_max,sunrise,sunset,daylight_duration",
                                "timezone": TZNAME, "forecast_days": nd}, ["riederalp"], days, parser=parse_daily)
    print("Lade Prognosekonstanz Riederalp …", file=sys.stderr)
    hv = "temperature_2m,precipitation," + ",".join(f"{v}_previous_day{k}" for v in ("temperature_2m", "precipitation")
                                                    for k in (1, 2, 3))
    D["prev_ried_model"], pr = fetch_model(API_PREV, "Prognosekonstanz Riederalp",
                                           ["meteoswiss_icon_seamless", "ecmwf_ifs", "best_match"],
                                           {**coords(["riederalp"], LOCAL), "hourly": hv, "timezone": TZNAME,
                                            "forecast_days": 5}, ["riederalp"], days)
    D["prev_ried"] = pr["riederalp"] if pr else None
    print("Lade ECMWF ENS bis 15 Tage …", file=sys.stderr)
    d15 = [days[0] + dt.timedelta(k) for k in range(16)]
    _m, e15 = fetch_model(API_ENS, "ECMWF ENS 15 Tage", ["ecmwf_ifs025", "ecmwf_ifs025_ensemble"],
                          {**coords(["riederalp"], LOCAL), "hourly": "temperature_2m,precipitation",
                           "timezone": TZNAME, "forecast_days": 15}, ["riederalp"], d15, factor=5)
    D["ens15"] = e15["riederalp"] if e15 else None
    D["obs"] = collect_obs(cache_dir)
    D["slf"] = collect_slf()
    D["welt"] = collect_world()
    D["swiss_now"] = collect_swiss_now(cache_dir)
    D["mch"] = collect_mch(cache_dir)
    collect_rivers_aletsch(D, days, cache_dir)

    print("Klimatologie ERA5-Land …", file=sys.stderr)
    D["clim"] = clim_fetch(cache_dir, d15)
    try:
        months = sorted({(d + dt.timedelta(k)).month for d in days for k in (-7, 0, 7)})
        D["nao_clim"] = archive_months(cache_dir, "klima_nao_era5_v1.json", NAO_PTS, list(NAO_PTS),
                                       ["pressure_msl_mean"], months, ["era5", ""])
    except (ApiError, OSError) as e:
        log(f"NAO-Klimatologie ({e})")
        D["nao_clim"] = None

    print("Modell-Laufzeiten …", file=sys.stderr)
    D["runtimes"] = {}
    seen = {}
    for label, mid in D["runs"].items():
        if mid not in seen:
            seen[mid] = model_run(mid)
        if seen[mid]:
            D["runtimes"][label] = seen[mid]
    return D


def clim_fetch(cache_dir, days):
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, "klima_era5land_1991-2020.json")
    cache = {"version": 1, "model": None, "months": {}}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                cache = json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    need = sorted({(d + dt.timedelta(k)).month for d in days for k in (-7, 0, 7)})
    for mth in need:
        if str(mth) in cache["months"]:
            continue
        print(f"  Monat {mth}: 30 Jahre ERA5-Land (einmalig, wird gecacht) …", file=sys.stderr)
        block = {k: {"date": [], "tmax": [], "tmin": [], "prec": []} for k in CLIM_LOCS}
        complete = True
        for y in range(1991, 2021):
            last = calendar.monthrange(y, mth)[1]
            params = {**coords(CLIM_LOCS, LOCAL), "start_date": f"{y}-{mth:02d}-01",
                      "end_date": f"{y}-{mth:02d}-{last:02d}", "timezone": TZNAME,
                      "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum"}
            data, err = None, None
            cands = [cache["model"]] if cache.get("model") is not None else ["era5_land", "era5", ""]
            for c in cands:
                p = dict(params)
                if c:
                    p["models"] = c
                try:
                    data = fetch_json(API_ARCH, p)
                    cache["model"] = c
                    break
                except ApiError as e:
                    err = e
            if data is None:
                log(f"Klimatologie Monat {mth} ({err})")
                complete = False
                break
            items = data if isinstance(data, list) else [data]
            for k, it in zip(CLIM_LOCS, items):
                dd = it.get("daily") or {}
                block[k]["date"] += dd.get("time", [])
                block[k]["tmax"] += dd.get("temperature_2m_max", [])
                block[k]["tmin"] += dd.get("temperature_2m_min", [])
                block[k]["prec"] += dd.get("precipitation_sum", [])
            time.sleep(PAUSE)
        if complete:
            cache["months"][str(mth)] = block
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cache, f)
    return cache


# ================================================================ Hilfen
def valid(xs):
    return [x for x in (xs or []) if x is not None and not (isinstance(x, float) and math.isnan(x))]


def quant(xs, p):
    v = sorted(valid(xs))
    if not v:
        return None
    k = (len(v) - 1) * p
    f = math.floor(k)
    c = min(f + 1, len(v) - 1)
    return v[f] + (v[c] - v[f]) * (k - f)


def mean(xs):
    v = valid(xs)
    return sum(v) / len(v) if v else None


def qstats(xs):
    v = valid(xs)
    if not v:
        return None
    return {"p10": quant(v, .1), "p25": quant(v, .25), "p50": quant(v, .5),
            "p75": quant(v, .75), "p90": quant(v, .9), "mean": sum(v) / len(v), "n": len(v)}


def frac(xs, thr):
    v = valid(xs)
    return sum(1 for x in v if x >= thr) / len(v) if v else None


def n(x, dec=0):
    if x is None:
        return "–"
    s = f"{x:.{dec}f}"
    if float(s) == 0:
        s = s.lstrip("-")
    return s.replace(".", ",").replace("-", "−")


def sgn(x, dec=1):
    if x is None:
        return "–"
    s = n(x, dec)
    return s if s.startswith("−") or float(f"{x:.{dec}f}") == 0 else "+" + s


def deg(x, dec=0):
    return "–" if x is None else f"{n(x, dec)} °C"


def hm(z):
    return "–" if z is None else f"{int(round(z / 50.0) * 50)} m"


def pct(p):
    return "–" if p is None else f"{int(round(p * 100))} %"


def cap(s):
    return s[:1].upper() + s[1:] if s else s


def join_de(xs):
    xs = [x for x in xs if x]
    if not xs:
        return ""
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " und " + xs[-1]


WD = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
WDS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
          "September", "Oktober", "November", "Dezember"]


def dlong(d):
    return f"{WD[d.weekday()]}, {d.day}. {MONATE[d.month - 1]}"


def dshort(d):
    return f"{WDS[d.weekday()]} {d.day}.{d.month}."


DIRS = ["N", "NO", "O", "SO", "S", "SW", "W", "NW"]
DIRS_L = ["Nord", "Nordost", "Ost", "Südost", "Süd", "Südwest", "West", "Nordwest"]


def dname(d, long=False):
    if d is None:
        return ""
    return (DIRS_L if long else DIRS)[int(((d % 360) + 22.5) // 45) % 8]


def vec_mean(speeds, dirs):
    u = v = 0.0
    k = 0
    for s, d in zip(speeds or [], dirs or []):
        if s is None or d is None:
            continue
        u += -s * math.sin(math.radians(d))
        v += -s * math.cos(math.radians(d))
        k += 1
    if not k:
        return None, None
    u, v = u / k, v / k
    return math.degrees(math.atan2(-u, -v)) % 360, math.hypot(u, v)


def daylight_hours(lat, d):
    doy = d.timetuple().tm_yday
    decl = 23.44 * math.sin(math.radians(360 / 365 * (doy - 81)))
    x = -math.tan(math.radians(lat)) * math.tan(math.radians(decl))
    return 2 * math.degrees(math.acos(max(-1.0, min(1.0, x)))) / 15


BUCKETS = ((0, 5, "in der Nacht"), (6, 9, "am Morgen"), (10, 13, "über Mittag"),
           (14, 17, "am Nachmittag"), (18, 23, "am Abend"))


def when(hours):
    names = [nm for a, b, nm in BUCKETS if any(a <= h <= b for h in hours)]
    if len(names) >= 4:
        return "verteilt über den ganzen Tag"
    return join_de(names)


# ============================================================ Diagnostik
def new_snow_density(t):
    """Neuschneedichte in kg/m³ nach Anderson (1976), wie im Schneemodell SNOW-17."""
    t = min(t, 0.0)
    return 50.0 if t <= -15 else 50.0 + 1.7 * (t + 15) ** 1.5


def summarize_day(rec, d, lat):
    ii = rec["idx"].get(d, [])
    v, t = rec["v"], rec["t"]

    def g(var):
        arr = v.get(var)
        return [arr[i] for i in ii] if arr else [None] * len(ii)

    T = g("temperature_2m")
    if len(valid(T)) < 20:
        return None
    hrs = [t[i].hour for i in ii]
    P, S, C, Lc = g("precipitation"), g("snowfall"), g("cloud_cover"), g("cloud_cover_low")
    W, Wd, G = g("wind_speed_10m"), g("wind_direction_10m"), g("wind_gusts_10m")
    F, SF, SUN = g("freezing_level_height"), g("snowfall_height"), g("sunshine_duration")
    CAPE, PM, RH = g("cape"), g("pressure_msl"), g("relative_humidity_2m")
    SD = g("snow_depth")

    def at(arr, h):
        for x, hh in zip(arr, hrs):
            if hh == h:
                return x
        return None

    def sel(arr, ks):
        return [arr[k] for k in ks]

    day = [k for k, h in enumerate(hrs) if 8 <= h <= 17]
    morn = [k for k, h in enumerate(hrs) if 6 <= h <= 11]
    wet = [k for k, x in enumerate(P) if x is not None and x >= 0.2]
    wdir, wspd = vec_mean(sel(W, day), sel(Wd, day))
    sun = valid(SUN)
    sfw = valid(sel(SF, wet))
    snow_k = [k for k, x in enumerate(S) if x is not None and x > 0.05]
    hn_dens, wsum, tsum = 0.0, 0.0, 0.0
    for k in snow_k:
        tk = T[k] if T[k] is not None else 0.0
        hn_dens += (S[k] / 0.7) / new_snow_density(tk) * 100  # mm Wasserwert -> cm
        wsum += S[k]
        tsum += tk * S[k]
    sd_valid = [x for x in SD if x is not None]
    sd07 = at(SD, 7)
    return {
        "sfh_med": quant(sfw, .5) if sfw else None, "sfh_max": max(sfw) if sfw else None,
        "sfh_day": quant(valid(SF), .5) if valid(SF) else None,
        "hn_dens": hn_dens, "t_snow": tsum / wsum if wsum > 0 else None,
        "sd07": sd07 * 100 if sd07 is not None else None,
        "sd_end": sd_valid[-1] * 100 if sd_valid else None,
        "tmin": min(valid(T)), "tmax": max(valid(T)), "t07": at(T, 7), "t15": at(T, 15),
        "prec": sum(valid(P)), "snow": sum(valid(S)),
        "sun_h": sum(sun) / 3600 if len(sun) >= 20 else None,
        "daylen": daylight_hours(lat, d),
        "cloud": mean(sel(C, day)), "low": mean(sel(Lc, day)), "low_morn": mean(sel(Lc, morn)),
        "gust": max(valid(G)) if valid(G) else None, "wdir": wdir, "wspd": wspd,
        "fzl_min": min(valid(F)) if valid(F) else None,
        "fzl_max": max(valid(F)) if valid(F) else None,
        "fzl_mean": mean(F), "fzl15": at(F, 15),
        "sfh_wet": min(sfw) if sfw else None,
        "cape": max(valid(CAPE)) if valid(CAPE) else None,
        "wet_hours": len(wet), "when": when([hrs[k] for k in wet]), "rh": mean(RH),
        "_hrs": hrs, "_pmsl": PM,
    }


def members(rec, var):
    pat = re.compile(r"^" + re.escape(var) + r"(_member\d+)?$")
    return [arr for k, arr in rec["v"].items() if pat.match(k) and valid(arr)]


def ens_day_stats(rec, d, min_hours=20):
    ii = rec["idx"].get(d, [])
    hrs = [rec["t"][i].hour for i in ii]

    def per_member(var, fn, sub=None, need=min_hours):
        out = []
        for arr in members(rec, var):
            vv = valid([arr[i] for k, i in enumerate(ii) if sub is None or sub(hrs[k])])
            if len(vv) >= need:
                out.append(fn(vv))
        return out

    tmax = per_member("temperature_2m", max)
    if len(tmax) < 5:
        return None
    prec = per_member("precipitation", sum)
    snow = per_member("snowfall", sum)
    lowm = per_member("cloud_cover_low", lambda v: sum(v) / len(v), sub=lambda h: 7 <= h <= 11, need=3)
    sfh_vals, nmem = [], len(tmax)
    for parr, harr in zip(members(rec, "precipitation"), members(rec, "snowfall_height")):
        hv = [harr[i] for i in ii if i < len(parr) and i < len(harr) and parr[i] is not None
              and parr[i] >= 0.2 and harr[i] is not None]
        if hv:
            sfh_vals.append(quant(hv, .5))
    sd = per_member("snow_depth", lambda v: v[-1] * 100)
    return {"sfh": qstats(sfh_vals) if len(sfh_vals) >= 3 else None, "p_wet": len(sfh_vals) / nmem,
            "psnow1": frac(snow, 1.0), "psnow10": frac(snow, 10.0), "sd": qstats(sd), "psd5": frac(sd, 5.0),"n": len(tmax), "tmax": qstats(tmax), "tmin": qstats(per_member("temperature_2m", min)),
            "prec": qstats(prec), "p1": frac(prec, 1.0), "p10": frac(prec, 10.0),
            "p30": frac(prec, 30.0), "psnow5": frac(snow, 5.0), "snow": qstats(snow),
            "plow": frac(lowm, 80.0)}


def ens_period_snow(rec, days):
    """Neuschneesumme pro Member über den ganzen Berichtszeitraum."""
    ii = [i for d in days for i in rec["idx"].get(d, [])]
    sums = []
    for arr in members(rec, "snowfall"):
        vv = [arr[i] for i in ii if i < len(arr)]
        if len(valid(vv)) >= 0.9 * len(ii):
            sums.append(sum(valid(vv)))
    return qstats(sums) if len(sums) >= 5 else None


def merged_series(D, loc, variables):
    """Stundenreihe eines Orts: ICON-CH, wo vorhanden, sonst IFS."""
    recs = [(lab, parsed[loc]) for lab, _mid, parsed in D["primary"] if loc in parsed]
    if not recs:
        return None
    times = sorted({t for _, r in recs for t in r["t"]})
    out = {"t": times, "src": [], **{v: [] for v in variables}}
    for t in times:
        pick = None
        for lab, r in recs:
            i = r["tidx"].get(t)
            if i is not None and (r["v"].get("temperature_2m") or [None])[i] is not None:
                pick = (lab, r, i)
                break
        out["src"].append(pick[0] if pick else None)
        for v in variables:
            arr = pick[1]["v"].get(v) if pick else None
            out[v].append(arr[pick[2]] if arr and pick[2] < len(arr) else None)
    return out


def ens_hourly_quantiles(rec, var="temperature_2m"):
    M = members(rec, var)
    q = {p: [] for p in (10, 25, 50, 75, 90)}
    for i in range(len(rec["t"])):
        vals = valid([m[i] for m in M if i < len(m)])
        for p in q:
            q[p].append(quant(vals, p / 100) if len(vals) >= 5 else None)
    return rec["t"], q


def dp_stats(a, b):
    """Druckdifferenz a − b (MSL, hPa) über den Tag."""
    if not a or not b:
        return None
    da = dict(zip(a["_hrs"], a["_pmsl"]))
    db = dict(zip(b["_hrs"], b["_pmsl"]))
    diffs = [da[h] - db[h] for h in da if h in db and da[h] is not None and db[h] is not None]
    if len(diffs) < 12:
        return None
    return {"mean": sum(diffs) / len(diffs), "max": max(diffs), "min": min(diffs)}


def up_at(up, key, var, d, hour):
    rec = (up or {}).get(key)
    if not rec:
        return None
    i = rec["tidx"].get(dt.datetime(d.year, d.month, d.day, hour))
    arr = rec["v"].get(var)
    return arr[i] if (i is not None and arr and i < len(arr)) else None


def europe_day(up, d):
    out = {}

    def a(k, var, h=12):
        return up_at(up, k, var, d, h)

    cities = [(UPPER[k]["name"], a(k, "pressure_msl"), a(k, "temperature_850hPa"))
              for k in UPPER if k.startswith("c_")]
    cp = [c for c in cities if c[1] is not None]
    if cp:
        hi, lo = max(cp, key=lambda c: c[1]), min(cp, key=lambda c: c[1])
        out["pmax"], out["pmin"] = (hi[0], hi[1]), (lo[0], lo[1])
    ct = [c for c in cities if c[2] is not None]
    if ct:
        w, c = max(ct, key=lambda c: c[2]), min(ct, key=lambda c: c[2])
        out["t850max"], out["t850min"] = (w[0], w[2]), (c[0], c[2])
    Z = {(la, lo): a(f"g_{la}_{lo}", "geopotential_height_500hPa") for la in GRID_LATS for lo in GRID_LONS}
    try:  # geostrophischer Wind 500 hPa über den Alpen (grobe 10°-Differenzen)
        dzdy = (Z[(50, 10)] - Z[(40, 10)]) / (10 * 111.2e3)
        dzdx = ((Z[(40, 20)] + Z[(50, 20)]) / 2 - (Z[(40, 0)] + Z[(50, 0)]) / 2) / (
            20 * 111.2e3 * math.cos(math.radians(45)))
        f0 = 2 * 7.292e-5 * math.sin(math.radians(45))
        ug, vg = -9.81 / f0 * dzdy, 9.81 / f0 * dzdx
        out["geo"] = (math.degrees(math.atan2(-ug, -vg)) % 360, math.hypot(ug, vg))
    except TypeError:
        pass
    feats = []
    for la in (50, 40):
        for lo in (-10, 0, 10, 20):
            zz = (Z.get((la, lo - 10)), Z.get((la, lo + 10)), Z.get((la, lo)))
            if None not in zz:
                feats.append((zz[0] + zz[1] - 2 * zz[2], la, lo))
    if feats:
        tr, rd = max(feats), min(feats)
        if tr[0] >= 40:
            out["trough"] = (tr[1], tr[2], tr[0])
        if rd[0] <= -40:
            out["ridge"] = (rd[1], rd[2], rd[0])
    tm, blocked = {}, []
    for lo in GRID_LONS:
        z80, z60, z40 = Z.get((80, lo)), Z.get((60, lo)), Z.get((40, lo))
        if None in (z80, z60, z40):
            continue
        ghgs, ghgn = (z60 - z40) / 20, (z80 - z60) / 20
        tm[lo] = (ghgs, ghgn)
        if ghgs > 0 and ghgn < -10:
            blocked.append(lo)
    out["tm"], out["blocked"] = tm, blocked
    jets = []
    for lo in (-10, 0, 10, 20):
        ws = [(a(f"g_{la}_{lo}", "wind_speed_250hPa"), la) for la in (40, 50, 60, 70)]
        ws = [w for w in ws if w[0] is not None]
        if ws:
            jets.append(max(ws))
    if jets:
        out["jet_lat"] = sum(j[1] for j in jets) / len(jets)
        out["jet_max"] = max(j[0] for j in jets)
    out["t850_alps"] = a("alpen", "temperature_850hPa")
    out["z500_alps"] = a("alpen", "geopotential_height_500hPa")
    out["w700"] = (a("alpen", "wind_direction_700hPa"), a("alpen", "wind_speed_700hPa"))
    fl = []
    for h in (0, 6, 12, 18):
        T7, RH7 = a("ossola", "temperature_700hPa", h), a("ossola", "relative_humidity_700hPa", h)
        V7, D7 = a("ossola", "wind_speed_700hPa", h), a("ossola", "wind_direction_700hPa", h)
        if None in (T7, RH7, V7, D7):
            continue
        es = 6.112 * math.exp(17.67 * T7 / (T7 + 243.5))
        e = RH7 / 100 * es
        q = 622 * e / (700 - 0.378 * e)  # g/kg
        fl.append((q * V7 * math.cos(math.radians(D7 - 170)), q, V7, D7, h))
    if fl:
        out["flux"] = max(fl)
    out["t850_bern_06"] = a("bern_ua", "temperature_850hPa", 6)
    return out


def clim_window(cache, loc, d, var, half=7):
    target = dt.date(2001, d.month, 28 if (d.month == 2 and d.day == 29) else d.day).timetuple().tm_yday
    vals = []
    for block in cache.get("months", {}).values():
        b = block.get(loc)
        if not b:
            continue
        for ds, x in zip(b["date"], b[var]):
            if x is None:
                continue
            dd = dt.date.fromisoformat(ds)
            if dd.month == 2 and dd.day == 29:
                continue
            dist = abs(dt.date(2001, dd.month, dd.day).timetuple().tm_yday - target)
            if min(dist, 365 - dist) <= half:
                vals.append(x)
    return vals


def clim_compare(cache, loc, d, fc_tmax, fc_tmin):
    vx, vn = clim_window(cache, loc, d, "tmax"), clim_window(cache, loc, d, "tmin")
    if len(vx) < 100 or fc_tmax is None:
        return None
    mx, mn = sum(vx) / len(vx), (sum(vn) / len(vn) if vn else None)
    return {"mean_tmax": mx, "anom_tmax": fc_tmax - mx,
            "rank_tmax": sum(1 for x in vx if x < fc_tmax) / len(vx),
            "mean_tmin": mn, "anom_tmin": (fc_tmin - mn) if (mn is not None and fc_tmin is not None) else None,
            "p10": quant(vx, .1), "p90": quant(vx, .9), "n": len(vx)}


def diagnose(D, days, now):
    X = {"now": now, "days": days, "runs": D["runs"], "runtimes": D.get("runtimes", {})}
    # Primärdaten pro Ort und Tag (ICON-CH, ab Ende Horizont IFS)
    LD = {k: {} for k in LOCAL}
    for k, L in LOCAL.items():
        for d in days:
            for label, _mid, parsed in D["primary"]:
                rec = parsed.get(k)
                s = summarize_day(rec, d, L["lat"]) if rec else None
                if s:
                    s["src"] = label
                    LD[k][d] = s
                    break
    X["local"] = LD
    # Ensembles
    E = {}
    for label, mid, parsed in D["ens"]:
        E[label] = {loc: {d: ens_day_stats(rec, d) for d in days} for loc, rec in parsed.items()}
    X["ens"] = E
    X["ens_hourly"] = None
    for pref in ("ECMWF ENS", "NOAA GEFS", "ECMWF AIFS-ENS"):
        for label, mid, parsed in D["ens"]:
            if label == pref and "riederalp" in parsed:
                X["ens_hourly"] = (label, *ens_hourly_quantiles(parsed["riederalp"]))
                break
        if X["ens_hourly"]:
            break
    X["series"] = merged_series(D, "riederalp", ["snowfall_height", "freezing_level_height", "precipitation",
                                                  "snowfall", "snow_depth"])
    X["ens_sfh"], X["snow_period"] = None, {}
    for label, mid, parsed in D["ens"]:
        if "riederalp" in parsed:
            sp = ens_period_snow(parsed["riederalp"], days)
            if sp:
                X["snow_period"][label] = sp
            if X["ens_sfh"] is None and label in ("ECMWF ENS", "NOAA GEFS"):
                tq = ens_hourly_quantiles(parsed["riederalp"], "snowfall_height")
                if valid(tq[1][50]):
                    X["ens_sfh"] = (label, *tq)
    X["det_hourly"] = {label: (parsed["riederalp"]["t"], parsed["riederalp"]["v"].get("temperature_2m"))
                       for label, mid, parsed in D["primary"] if "riederalp" in parsed}
    # Modellvergleich Riederalp
    C = {}
    for label, mid, rec in D["compare"]:
        row = {}
        for d in days:
            ii = rec["idx"].get(d, [])
            T = valid([rec["v"].get("temperature_2m", [None] * len(rec["t"]))[i] for i in ii])
            if len(T) >= 20:
                P = valid([(rec["v"].get("precipitation") or [None] * len(rec["t"]))[i] for i in ii])
                row[d] = {"tmax": max(T), "tmin": min(T), "prec": sum(P) if P else None}
        if row:
            C[label] = row
    X["compare"] = C
    X["det_range"] = {}
    for d in days:
        vals = [C[m][d]["tmax"] for m in C if d in C[m]]
        X["det_range"][d] = (max(vals) - min(vals), len(vals)) if len(vals) >= 3 else None
    # Föhn, Bise, Inversion, Hochnebel
    X["foehn"] = {d: dp_stats(LD["lugano"].get(d), LD["zuerich"].get(d)) for d in days}
    X["bise"] = {d: dp_stats(LD["zuerich"].get(d), LD["genf"].get(d)) for d in days}
    X["inv"] = {}
    for d in days:
        r, m, j = LD["riederalp"].get(d), LD["moerel"].get(d), LD["jungfraujoch"].get(d)
        o = {}
        dz = (LOCAL["riederalp"]["elev"] - LOCAL["moerel"]["elev"]) / 1000
        if r and m:
            for h, key in ((7, "t07"), (15, "t15")):
                if r[key] is not None and m[key] is not None:
                    o[f"lapse{h}"] = (r[key] - m[key]) / dz
        if r and j and r["t15"] is not None and j["t15"] is not None:
            o["lapse_up15"] = (j["t15"] - r["t15"]) / ((LOCAL["jungfraujoch"]["elev"] - LOCAL["riederalp"]["elev"]) / 1000)
        X["inv"][d] = o
    X["eu"] = {d: europe_day(D["up"], d) for d in days} if D["up"] else {}
    X["hochnebel"] = {}
    for d in days:
        b, nf = LD["bern"].get(d), LD["napf"].get(d)
        if not b or not nf:
            continue
        inv = nf["t07"] - b["t07"] if (nf["t07"] is not None and b["t07"] is not None) else None
        eb = best_ens(X, "bern", d)
        t850 = X["eu"].get(d, {}).get("t850_bern_06") if X["eu"] else None
        X["hochnebel"][d] = {
            "low_bern": b["low_morn"], "low_napf": nf["low_morn"], "dT_napf": inv,
            "dT850": (t850 - b["t07"]) if (t850 is not None and b["t07"] is not None) else None,
            "likely": (b["low_morn"] or 0) >= 70 and (nf["low_morn"] if nf["low_morn"] is not None else 100) < 50
            and (inv if inv is not None else -1) > 0,
            "p_ens": eb[1].get("plow") if eb else None, "ens_label": eb[0] if eb else None}
    # Staub
    X["dust"] = {}
    if D.get("aq"):
        rec = D["aq"]["riederalp"]
        for d in days:
            ii = rec["idx"].get(d, [])
            du = valid([(rec["v"].get("dust") or [None] * len(rec["t"]))[i] for i in ii])
            ao = valid([(rec["v"].get("aerosol_optical_depth") or [None] * len(rec["t"]))[i] for i in ii])
            if du or ao:
                X["dust"][d] = {"dust": max(du) if du else None, "aod": max(ao) if ao else None}
    # Klimatologie
    X["clim"] = {}
    for loc in CLIM_LOCS:
        X["clim"][loc] = {}
        for d in days:
            s = LD[loc].get(d)
            if s:
                X["clim"][loc][d] = clim_compare(D["clim"], loc, d, s["tmax"], s["tmin"])
    X["clim_model"] = D["clim"].get("model")
    # Prognosesicherheit, Vorhersagbarkeitshorizont, KI vs. Physik
    X["conf"], X["horizon"] = {}, None
    ref = E.get("ECMWF ENS") or E.get("NOAA GEFS") or {}
    for d in days:
        st = (ref.get("riederalp") or {}).get(d)
        dr = X["det_range"].get(d)
        if st and st["tmax"]:
            sT = st["tmax"]["p90"] - st["tmax"]["p10"]
            p1 = st["p1"] if st["p1"] is not None else 0.0
            u = 0.6 * min(1, sT / 6) + 0.4 * (1 - abs(2 * p1 - 1))
            if dr:
                u = 0.75 * u + 0.25 * min(1, dr[0] / 5)
            X["conf"][d] = {"u": u, "label": "hoch" if u < 0.35 else ("mittel" if u < 0.6 else "gering"),
                            "spreadT": sT, "p1": p1}
        meds = [E[m]["riederalp"][d]["tmax"]["p50"] for m in ("ECMWF ENS", "NOAA GEFS", "ECMWF AIFS-ENS")
                if m in E and (E[m].get("riederalp") or {}).get(d)]
        crit = (X["conf"].get(d, {}).get("spreadT") or 0) >= 5 or (len(meds) >= 2 and max(meds) - min(meds) >= 3)
        if crit and X["horizon"] is None:
            X["horizon"] = d
    X["ai"] = {}
    if "ECMWF ENS" in E and "ECMWF AIFS-ENS" in E:
        for d in days:
            a = (E["ECMWF AIFS-ENS"].get("riederalp") or {}).get(d)
            b = (E["ECMWF ENS"].get("riederalp") or {}).get(d)
            if a and b:
                sa, sb = a["tmax"]["p90"] - a["tmax"]["p10"], b["tmax"]["p90"] - b["tmax"]["p10"]
                X["ai"][d] = {"ratio": sa / sb if sb > 0.05 else None,
                              "dmed": a["tmax"]["p50"] - b["tmax"]["p50"], "sa": sa, "sb": sb}
    X["log"] = LOG
    return X


def diagnose_extra(D, X):
    now_utc = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    O = D.get("obs") or {}
    X["obs"] = []
    for st in O.get("stations", []):
        sm = obs_summary(O.get("now", {}).get(st["abbr"]), now_utc)
        if sm:
            X["obs"].append({**st, **sm})
    X["obs_lapse"] = None
    pts = [(o["elev"], o["T"], o["tT"], o["name"]) for o in X["obs"] if o["T"] is not None]
    if len(pts) >= 2:
        tl = max(q[2] for q in pts)
        cur = sorted((q for q in pts if q[2] == tl), key=lambda q: q[0])
        if len(cur) >= 2 and cur[-1][0] - cur[0][0] >= 800:
            X["obs_lapse"] = {"lo": cur[0], "hi": cur[-1], "t": tl,
                              "rate": (cur[-1][1] - cur[0][1]) / (cur[-1][0] - cur[0][0]) * 1000}
    X["verif"], X["verif_model"] = (verify(O) if O else None), O.get("prev_model")
    X["consist"] = consistency(D["prev_ried"], X["days"]) if D.get("prev_ried") else {}
    X["consist_model"] = D.get("prev_ried_model")
    X["slf"], X["slf_level"] = D.get("slf"), slf_level(D.get("slf"))
    X["nao"] = nao_proxy(D, X, D.get("nao_clim"))
    X["weeks"] = week_trend(D["ens15"], D["clim"], X) if D.get("ens15") else []
    Wd = D.get("welt") or {}
    X["world_raw"], X["peaks"], X["gdacs"], X["enso"] = Wd.get("world"), Wd.get("peaks"), Wd.get("gdacs"), Wd.get("enso")
    X["swiss_now"] = D.get("swiss_now") or []
    X["mch"] = D.get("mch")
    X["rivers"] = river_diag(D, X)
    X["aletsch"] = aletsch_diag(D, X)
    X["records"] = {d: day_records(D.get("clim"), "riederalp", d) for d in X["days"][:2]}
    X["moon"] = moon(X["days"][0])
    X["air"] = {}
    aq = (D.get("aq") or {}).get("riederalp")
    if aq:
        for d in X["days"]:
            ii = aq["idx"].get(d, [])
            col = lambda k: valid([(aq["v"].get(k) or [None] * len(aq["t"]))[i] for i in ii])
            aqi = col("european_aqi")
            X["air"][d] = {"aqi": max(aqi) if aqi else None,
                           "pollen": {k: (max(col(k)) if col(k) else None) for k in POLLEN}}
    X["sun"] = {}
    sr = (D.get("sun") or {}).get("riederalp")
    if sr:
        for i, d in enumerate(sr["d"]):
            g = lambda k: (sr["v"].get(k) or [None] * len(sr["d"]))[i]
            hh = lambda x: str(x)[11:16] if x and "T" in str(x) else None
            X["sun"][d] = {"uv": g("uv_index_max"), "rise": hh(g("sunrise")), "set": hh(g("sunset"))}


def best_ens(X, loc, d, prefer=("ICON-CH1-EPS", "ICON-CH2-EPS", "ECMWF ENS", "NOAA GEFS")):
    for lab in prefer:
        st = (X["ens"].get(lab, {}).get(loc) or {}).get(d)
        if st:
            return lab, st
    return None


# ================================================================ Texte
def sky_word(s, lowland=False):
    f = s["sun_h"] / s["daylen"] if (s.get("sun_h") is not None and s.get("daylen")) else None
    if f is None:
        if s.get("cloud") is None:
            return "bewölkt"
        f = 1 - s["cloud"] / 100
    wet = s.get("wet_hours") or 0
    if wet >= 12:
        f = min(f, 0.2)
    elif wet >= 6:
        f = min(f, 0.45)
    if lowland and (s.get("low_morn") or 0) >= 70 and f < 0.5:
        return "hochnebelartig bewölkt" if f >= 0.15 else "trüb mit Hochnebel"
    if f >= 0.75:
        return "sonnig"
    if f >= 0.5:
        return "ziemlich sonnig"
    if f >= 0.25:
        return "wechselnd bewölkt"
    if f >= 0.08:
        return "stark bewölkt"
    return "bedeckt"


def prec_kind(s):
    p, snow = s["prec"], s["snow"]
    liquid = max(0.0, p - snow / 0.7)
    base = "Schneefall" if (snow >= 1 and liquid < 0.5) else ("Regen und Schnee" if snow >= 1 else "Regen")
    if p < 2:
        return "etwas " + {"Schneefall": "Schnee"}.get(base, base)
    if p >= 25:
        return "ergiebiger " + base if base != "Regen und Schnee" else "ergiebiger Regen und Schnee"
    if p >= 10:
        return "zeitweise kräftiger " + base if base != "Regen und Schnee" else "kräftiger Regen und Schnee"
    return base


def prec_sentence(s, ens=None, sfh=True, place=None, snow=True):
    p = s["prec"]
    pe = ens.get("p1") if ens else None
    if p < 0.3:
        if pe is not None and pe >= 0.3:
            return f"Im Hauptlauf trocken, im Ensemble aber {pct(pe)} Wahrscheinlichkeit für mindestens 1 mm."
        return ""
    out = f"{cap(prec_kind(s))} {s['when']}, {place or 'im Hauptlauf'} {n(p, 1)} mm"
    if snow and s["snow"] >= 1:
        out += f" (davon rund {n(s['snow'])} cm Neuschnee)"
    out += "."
    if ens and ens.get("prec"):
        out += (f" Ensemble: {pct(pe)} für ≥ 1 mm, Median {n(ens['prec']['p50'], 1)} mm, "
                f"90-%-Quantil {n(ens['prec']['p90'], 1)} mm.")
    if sfh and s.get("sfh_med") is not None:
        out += f" Schneefallgrenze um {hm(s['sfh_med'])}."
    return out


def melt_cooling(s):
    """Schneefallgrenze weit unter der Nullgradgrenze bei kräftigem Niederschlag."""
    if s.get("sfh_med") is None or s.get("fzl_mean") is None or s["prec"] < 8:
        return None
    gap = s["fzl_mean"] - s["sfh_med"]
    return gap if gap >= 500 else None


def snow_text(s, e, elev, name):
    es = e[1] if e else None
    out = []
    if s["prec"] >= 0.3 and s.get("sfh_med") is not None:
        t = f"Schneefallgrenze während des Niederschlags um {hm(s['sfh_med'])}"
        if s.get("sfh_wet") is not None and s.get("sfh_max") is not None and s["sfh_max"] - s["sfh_wet"] >= 300:
            t += f" (zwischen {hm(s['sfh_wet'])} und {hm(s['sfh_max'])})"
        rel = s["sfh_med"] - elev
        if rel <= -150:
            t += f", auf der {name} fällt Schnee"
        elif rel < 150:
            t += f", also etwa auf Höhe der {name}: nasser Schnee oder Schneeregen"
        else:
            t += f", auf der {name} Regen"
        t += "."
        if es and es.get("sfh"):
            t += (f" {e[0]}: 10–90 % der nassen Member zwischen {hm(es['sfh']['p10'])} und "
                  f"{hm(es['sfh']['p90'])}.")
        out.append(t)
        gap = melt_cooling(s)
        if gap:
            out.append(f"Die Schneefallgrenze liegt {hm(gap)} unter der Nullgradgrenze, deutlich mehr als die "
                       "üblichen rund 300 m: Schmelzen und Verdunsten im kräftigen Niederschlag kühlen die Luft, "
                       "die Grenze kann dann rascher sinken als die Nullgradgrenze.")
    elif s.get("sfh_day") is not None:
        out.append(f"Kein nennenswerter Niederschlag; die Schneefallgrenze läge im Modell um {hm(s['sfh_day'])}.")
    elif s.get("fzl_mean") is not None:
        out.append(f"Kein nennenswerter Niederschlag; die Schneefallgrenze läge nach Faustregel "
                   f"(Nullgradgrenze minus 300 m) um {hm(s['fzl_mean'] - 300)}.")
    ps1 = es.get("psnow1") if es else None
    if s["snow"] >= 0.5 or (ps1 or 0) >= 0.2:
        t = f"Neuschnee im Hauptlauf {n(s['snow'])} cm (fester Faktor 0,7 cm pro mm Wasserwert)"
        if s["snow"] >= 0.5 and s.get("t_snow") is not None:
            t += (f"; mit temperaturabhängiger Neuschneedichte nach Anderson (1976) bei {deg(s['t_snow'])} "
                  f"eher {n(s['hn_dens'])} cm")
        t += "."
        if es and es.get("snow"):
            t += (f" {e[0]}: {pct(es['psnow1'])} für ≥ 1 cm, {pct(es['psnow5'])} für ≥ 5 cm, "
                  f"{pct(es['psnow10'])} für ≥ 10 cm, 90-%-Quantil {n(es['snow']['p90'])} cm.")
        out.append(t)
    else:
        out.append("Kein Neuschnee.")
    sd07, sde = s.get("sd07"), s.get("sd_end")
    if (sd07 or 0) < 0.5 and (sde or 0) < 0.5:
        out.append("Keine Schneedecke im Modell.")
    else:
        t = f"Modellschneehöhe am Morgen {n(sd07)} cm, am Abend {n(sde)} cm"
        if es and es.get("sd"):
            t += f" ({e[0]} am Abend 10–90 %: {n(es['sd']['p10'])} bis {n(es['sd']['p90'])} cm)"
        out.append(t + ".")
    return "**Schnee:** " + " ".join(out)


def wind_phrase(g, d):
    if g is None:
        return ""
    dl = dname(d, True)
    w = f"{dl}wind" if dl else "Wind"
    if g < 30:
        return "schwacher Wind"
    if g < 50:
        return f"mässiger {w}, Böen um {n(g)} km/h"
    if g < 75:
        return f"lebhafter {w} mit Böen bis {n(g)} km/h"
    if g < 100:
        return f"stürmischer {w}, Böen bis {n(g)} km/h"
    return f"Sturm aus {dl}, Böen bis {n(g)} km/h"


def fzl_sentence(s):
    lo, hi, mn = s.get("fzl_min"), s.get("fzl_max"), s.get("fzl_mean")
    if mn is None:
        return ""
    if lo is not None and hi is not None and hi - lo > 400:
        return f"Nullgradgrenze zwischen {hm(lo)} und {hm(hi)}."
    return f"Nullgradgrenze um {hm(mn)}."


def foehn_text(fo, flux=None):
    if not fo:
        return ""
    mx, mi, me = fo["max"], fo["min"], fo["mean"]
    if mx >= 8:
        t = f"Kräftige Südföhnlage: Δp Lugano − Zürich bis {sgn(mx)} hPa."
    elif mx >= 4:
        t = f"Südföhntendenz: Δp Lugano − Zürich bis {sgn(mx)} hPa."
    elif mi <= -8:
        t = f"Kräftige Nordföhnlage: Δp Lugano − Zürich bis {sgn(mi)} hPa."
    elif mi <= -4:
        t = f"Nordföhntendenz: Δp Lugano − Zürich bis {sgn(mi)} hPa."
    else:
        t = f"Kein Föhn: Δp Lugano − Zürich im Mittel {sgn(me)} hPa."
    if flux and flux[0] >= 40:
        strength = "kräftiger" if flux[0] >= 80 else "deutlicher"
        t += (f" {cap(strength)} Feuchtezufluss gegen den Alpenhauptkamm: q·V⊥ in 700 hPa über dem "
              f"Ossolatal {n(flux[0])} g kg⁻¹ m s⁻¹ (q {n(flux[1], 1)} g/kg, Wind {dname(flux[3])} "
              f"{n(flux[2])} m/s, {flux[4]:02d} UTC).")
    return t


def bise_text(bi):
    if not bi:
        return ""
    me = bi["mean"]
    if me >= 5:
        return f"Kräftige Bise: Δp Zürich − Genf im Mittel {sgn(me)} hPa, am Genfersee Böen über 60 km/h möglich."
    if me >= 3:
        return f"Mässige Bise: Δp Zürich − Genf im Mittel {sgn(me)} hPa."
    if me <= -3:
        return f"Südwestlicher Gradient über dem Mittelland: Δp Zürich − Genf {sgn(me)} hPa."
    return ""


def inv_text(o, s_r, s_m):
    out = []
    l7, l15 = o.get("lapse7"), o.get("lapse15")
    if l7 is not None and s_r and s_m:
        if l7 > 0.5:
            out.append(f"Am Morgen Inversion: Riederalp {deg(s_r['t07'])}, Mörel im Tal {deg(s_m['t07'])} "
                       f"({sgn(l7)} K/km). Die Alp liegt in der warmen Hangzone über dem Kaltluftsee im Rhonetal.")
        elif l7 > -3:
            out.append(f"Am Morgen stabile, nahezu isotherme Schichtung zwischen Mörel und Riederalp ({sgn(l7)} K/km).")
    if l15 is not None and l15 < -8.5:
        out.append(f"Am Nachmittag fast trockenadiabatischer Gradient Tal–Alp ({sgn(l15)} K/km): "
                   "gut durchmischte Grenzschicht, kräftiger Hangaufwind.")
    return " ".join(out)


def ens_range(st, key="tmax"):
    if not st or not st.get(key):
        return ""
    return f"{n(st[key]['p10'])} bis {n(st[key]['p90'])} °C"


def runtime_str(t):
    return f"{t.day}.{t.month}. {t.hour:02d} UTC"


def sent(s):
    """Satzende ohne doppelten Punkt (Daten enden bereits auf '.')."""
    s = s.rstrip()
    return s if s.endswith(".") else s + "."


# ================================================================ Bericht
def build_report(X):
    days, now, LD = X["days"], X["now"], X["local"]
    d0 = days[0]
    out = []
    A = out.append
    rt = X["runtimes"]
    A("# Wetterbericht Riederalp")
    A("")
    A(f"Ausgegeben am {dlong(d0)} {now.year}, {now:%H:%M} Uhr. "
      f"Gültig für heute, morgen und die Folgetage bis {dlong(days[-1])}.")
    A("")
    runs = [f"{lab} {runtime_str(t)}" for lab, t in rt.items()
            if lab in ("ICON-CH", "IFS 9 km", "ECMWF ENS", "ICON-CH1-EPS", "ICON-CH2-EPS")]
    if runs:
        A(f"Modellläufe: {join_de(runs)}.")
        A("")
    out.extend(safe_lines(glance, X))
    A("<!--CHART:profil-->")
    A("")

    # ---------- Kernaussagen
    A("## Kernaussagen")
    A("")
    for txt in safe_lines(insights, X):
        A(f"- {txt}")
    A("")
    A("<!--NARRATIV-->")

    # ---------- Riederalp
    A("## Riederalp, 1925 m")
    A("")
    for d in days[:2]:
        A(f"### {'Heute' if d == d0 else 'Morgen'}, {dlong(d)}")
        A("")
        s = LD["riederalp"].get(d)
        if not s:
            A("Keine Modelldaten.")
            A("")
            continue
        e = best_ens(X, "riederalp", d)
        es = e[1] if e else None
        sky = sky_word(s)
        ps = prec_sentence(s, es, sfh=False, snow=False)
        parts = [f"{cap(sky)}{' und trocken' if not ps else ''}.", ps]
        tsent = f"Tiefstwert {deg(s['tmin'])}, Höchstwert {deg(s['tmax'])}"
        if es:
            tsent += f" ({e[0]} mit {es['n']} Membern, 10–90 %: {ens_range(es)})"
        parts.append(tsent + ".")
        wp = wind_phrase(s["gust"], s["wdir"])
        if wp:
            parts.append(cap(wp) + ".")
        parts.append(fzl_sentence(s))
        A(" ".join(p for p in parts if p))
        A("")
        A(snow_text(s, e, LOCAL["riederalp"]["elev"], "Riederalp"))
        A("")
        parts = [inv_text(X["inv"].get(d, {}), s, LD["moerel"].get(d))]
        j = LD["jungfraujoch"].get(d)
        if j:
            parts.append(f"Jungfraujoch {deg(j['tmin'])} bis {deg(j['tmax'])}"
                         + (f", Böen bis {n(j['gust'])} km/h." if j["gust"] else "."))
        if s.get("cape") is not None and s["cape"] >= 300:
            parts.append(f"Labil geschichtet, CAPE bis {n(s['cape'])} J/kg: einzelne Gewitter möglich.")
        du = X["dust"].get(d)
        if du and ((du["dust"] or 0) >= 30 or (du["aod"] or 0) >= 0.3):
            parts.append(f"Saharastaub laut CAMS: bodennah bis {n(du['dust'])} µg/m³, AOD {n(du['aod'], 2)}. "
                         "Staub auf Schnee und Firn senkt die Albedo und verstärkt die Schmelze.")
        cl = X["clim"]["riederalp"].get(d)
        if cl:
            word = "über" if cl["anom_tmax"] >= 0 else "unter"
            parts.append(f"Der Höchstwert liegt {n(abs(cl['anom_tmax']), 1)} K {word} dem Mittel 1991–2020 "
                         f"({int(round(cl['rank_tmax'] * 100))}. Perzentil).")
        sn = X.get("sun", {}).get(d)
        if sn and sn.get("rise"):
            t = f"Sonne astronomisch von {sn['rise']} bis {sn['set']} Uhr (ohne Horizontabschattung)"
            if sn.get("uv") is not None:
                t += f", UV-Index bis {n(sn['uv'])}"
                if sn["uv"] >= 3 and (s.get("sd_end") or 0) >= 5:
                    t += "; Schnee reflektiert zusätzlich bis zu 80 % der UV-Strahlung"
            parts.append(t + ".")
        parts.append(air_text(X, d))
        cf = X["conf"].get(d)
        if cf:
            parts.append(f"Prognosesicherheit {cf['label']}.")
        if d == d0 and now.hour >= 15:
            parts.append(night_text(X))
        A(" ".join(p for p in parts if p))
        A("")
    A("<!--CHART:karte_ried-->")
    A("")
    out.extend(safe_lines(obs_section, X))
    if len(days) > 2:
        A("### Folgetage")
        A("")
        A("| Tag | Wetter | Temperatur | Niederschlag | Neuschnee | Schneefall- / Nullgradgrenze | Sicherheit |")
        A("|---|---|---|---|---|---|---|")
        for d in days[2:]:
            s = LD["riederalp"].get(d)
            if not s:
                continue
            e = best_ens(X, "riederalp", d, prefer=("ICON-CH2-EPS", "ECMWF ENS", "NOAA GEFS"))
            es = e[1] if e else None
            wx = sky_word(s) + (f", {prec_kind(s)}" if s["prec"] >= 0.3 else "")
            tt = f"{n(s['tmin'])} / {n(s['tmax'])} °C"
            if es and es.get("tmax"):
                tt += f" ({n(es['tmax']['p10'])} bis {n(es['tmax']['p90'])})"
            pr = f"{n(s['prec'], 1)} mm"
            if es and es.get("p1") is not None:
                pr += f", {pct(es['p1'])}"
            cf = X["conf"].get(d, {}).get("label", "–")
            hn = f"{n(s['snow'])} cm" + (f", {pct(es['psnow5'])}" if es and es.get("psnow5") is not None else "")
            sfg = hm(s["sfh_med"]) if (s["prec"] >= 0.3 and s.get("sfh_med") is not None) else "–"
            A(f"| {dshort(d)} | {wx} | {tt} | {pr} | {hn} | {sfg} / {hm(s['fzl_mean'])} | {cf} |")
        A("")
        A("Temperatur: Tiefst- / Höchstwert im Hauptlauf, in Klammern 10–90 % des Ensembles für den Höchstwert. "
          "Niederschlag: Tagessumme im Hauptlauf und Wahrscheinlichkeit für mindestens 1 mm. "
          "Neuschnee: Hauptlauf und Wahrscheinlichkeit für mindestens 5 cm. "
          "Grenzen: Schneefallgrenze bei Niederschlag und mittlere Nullgradgrenze.")
        A("")
        A(trend_text(X))
        A("")
        A(snow_trend_text(X))
        A("")
    A("<!--CHART:schnee-->")
    A("")
    out.extend(safe_lines(aletsch_section, X))
    A("")
    A("<!--CHART:fan-->")
    A("")

    # ---------- Wallis
    A("## Wallis")
    A("")
    A("<!--CHART:karte_wallis-->")
    A("")
    for d in days[:2]:
        A(f"### {'Heute' if d == d0 else 'Morgen'}, {dlong(d)}")
        A("")
        A(wallis_text(X, d))
        A("")
    if len(days) > 2:
        A("### Folgetage")
        A("")
        for d in days[2:]:
            A(f"- **{dshort(d)}** {wallis_short(X, d)}")
        A("")
    out.extend(safe_lines(snow_table, X))

    # ---------- Schweiz
    A("## Schweiz")
    A("")
    A("<!--CHART:karte_schweiz-->")
    A("")
    for d in days[:2]:
        A(f"### {'Heute' if d == d0 else 'Morgen'}, {dlong(d)}")
        A("")
        for line in swiss_text(X, d):
            A(line)
            A("")
    if len(days) > 2:
        A("### Folgetage")
        A("")
        for d in days[2:]:
            A(f"- **{dshort(d)}** {swiss_short(X, d)}")
        A("")
    out.extend(safe_lines(river_section, X))

    # ---------- Europa
    A("## Europa")
    A("")
    A("<!--CHART:karte_europa-->")
    A("")
    if X["eu"]:
        A("### Allgemeine Lage")
        A("")
        A(europe_text(X, d0))
        A("")
        A(f"### Entwicklung bis {dlong(days[-1])}")
        A("")
        prev = X["eu"].get(d0)
        for d in days[1:]:
            e = X["eu"].get(d)
            if e:
                nv = X.get("nao", {}).get(d)
                A(f"- **{dshort(d)}** {europe_short(e, prev)}" + (f" NAO-Proxy {sgn(nv)}." if nv is not None else ""))
                prev = e
        A("")
        nao = list(X.get("nao", {}).values())
        if len(nao) >= 3:
            m = sum(nao) / len(nao)
            if m >= 1:
                ph = ("positive Phase: kräftige Westdrift, Tiefs ziehen nördlich vorbei, die Alpensüdseite bleibt "
                      "eher trocken")
            elif m <= -1:
                ph = ("negative Phase: blockierte oder südwärts verlagerte Zugbahnen, Mittelmeertiefs und Südstau am "
                      "Alpenhauptkamm werden wahrscheinlicher")
            else:
                ph = "keine ausgeprägte Phase"
            A(f"NAO-Proxy (Bodendruck Lissabon minus Reykjavík, je standardisiert gegen ERA5 1991–2020) im Mittel des "
              f"Zeitraums {sgn(m)}: {ph}. Einzelne Tageswerte dieses Stationsindex streuen stark.")
            A("")
    else:
        A("Druckflächendaten nicht verfügbar.")
        A("")

    # ---------- Welt und Fun Facts
    out.extend(safe_lines(world_section, X, title="Welt"))
    out.extend(safe_lines(fun_section, X, title="Fun Facts"))

    # ---------- Unsicherheiten
    A("## Unsicherheiten und Modellvergleich")
    A("")
    out.extend(safe_lines(mch_section, X))
    out.extend(safe_lines(uncertainty_section, X))

    # ---------- Klima
    A("## Klimatologische Einordnung")
    A("")
    out.extend(safe_lines(clim_section, X))

    # ---------- Methodik
    A("## Methodik und Quellen")
    A("")
    out.extend(safe_lines(method_section, X))
    return "\n".join(out)


def glance(X):
    days, LD = X["days"], X["local"]["riederalp"]
    cols = [d for d in days[:3] if LD.get(d)]
    if not cols:
        return []
    lab = lambda d: "Heute" if d == days[0] else ("Morgen" if d == days[1] else dshort(d))
    rows = ["| Riederalp | " + " | ".join(lab(d) for d in cols) + " |", "|---|" + "---|" * len(cols)]

    def row(name, fn):
        rows.append(f"| {name} | " + " | ".join(fn(LD[d], d) for d in cols) + " |")

    def ens(d):
        e = best_ens(X, "riederalp", d)
        return e[1] if e else {}

    row("Wetter", lambda s, d: sky_word(s) + (f", {prec_kind(s)}" if s["prec"] >= 0.3 else ""))
    row("Temperatur", lambda s, d: f"{n(s['tmin'])} / {n(s['tmax'])} °C")
    row("Niederschlag", lambda s, d: f"{n(s['prec'], 1)} mm" + (f" ({pct(ens(d)['p1'])})" if ens(d).get("p1") is not None else ""))
    row("Neuschnee", lambda s, d: f"{n(s['snow'])} cm")
    row("Schneefall- / 0°-Grenze", lambda s, d: (hm(s["sfh_med"]) if s["prec"] >= 0.3 and s.get("sfh_med") else "–")
        + f" / {hm(s['fzl_mean'])}")
    row("Böen", lambda s, d: f"{n(s['gust'])} km/h" if s.get("gust") is not None else "–")
    row("Sicherheit", lambda s, d: X["conf"].get(d, {}).get("label", "–"))
    M = ((X.get("mch") or {}).get("data") or {}).get("riederalp") or {}
    if M.get("tre200px"):
        row("MeteoSchweiz", lambda s, d: (f"{picto(M.get('jp2000d0', {}).get(d))}, {n(M.get('tre200pn', {}).get(d))} / "
                                          f"{n(M['tre200px'].get(d))} °C") if d in M["tre200px"] else "–")
    return rows + ["", "Niederschlag mit Ensemble-Wahrscheinlichkeit für mindestens 1 mm. Letzte Zeile: offizielle "
                   "Lokalprognose von MeteoSchweiz zum Vergleich.", ""]


def obs_section(X):
    out = []
    A = out.append
    if X.get("obs"):
        A("### Gemessen in der Region")
        A("")
        A("| Station | Jetzt | Min / Max 24 h | Niederschlag 24 h | Schnee | Böen | Föhn |")
        A("|---|---|---|---|---|---|---|")
        tts = [o["tT"] for o in X["obs"] if o.get("tT")]
        tlast = max(tts).replace(tzinfo=dt.timezone.utc).astimezone(TZ) if tts else None
        for o in X["obs"]:
            A(f"| {o['name']}, {int(o['elev'])} m | {deg(o['T'], 1)} | "
              f"{n(o['tmin'], 1)} / {n(o['tmax'], 1)} °C | {n(o['prec'], 1)} mm | "
              f"{(n(o['sd']) + ' cm') if o.get('sd') is not None else '–'} | "
              f"{(n(o['gust']) + ' km/h') if o.get('gust') is not None else '–'} | "
              f"{(n(o['foehn_h'], 1) + ' h') if o.get('foehn_h') else '–'} |")
        A("")
        bits = []
        L = X.get("obs_lapse")
        if L:
            r = L["rate"]
            kind = ("stabil geschichtet bis invers" if r > -3 else
                    ("nahezu trockenadiabatisch" if r < -8.5 else "im Bereich der Standardatmosphäre"))
            tl = L["t"].replace(tzinfo=dt.timezone.utc).astimezone(TZ)
            bits.append(f"Gemessener Temperaturgradient zwischen {L['lo'][3]} ({int(L['lo'][0])} m) und {L['hi'][3]} "
                        f"({int(L['hi'][0])} m) um {tl:%H:%M} Uhr: {sgn(r)} K/km, {kind}.")
        fo = [o for o in X["obs"] if (o.get("foehn_h") or 0) >= 1]
        if fo:
            bits.append("Föhn gemessen (Föhnindex MeteoSchweiz, letzte 24 h): "
                        + join_de([f"{o['name']} {n(o['foehn_h'], 1)} h" for o in fo]) + ".")
        wet = sorted((o for o in X["obs"] if (o.get("prec") or 0) >= 1), key=lambda o: -o["prec"])
        if wet:
            bits.append("Am meisten Niederschlag: " + join_de([f"{o['name']} {n(o['prec'], 1)} mm" for o in wet[:3]]) + ".")
        nw = (X.get("verif") or {}).get("now")
        if nw:
            bits.append(f"Der aktuelle Lauf ({MODEL_NAMES.get(X.get('verif_model'), X.get('verif_model'))}) liegt heute seit Mitternacht UTC an diesen Stationen im "
                        f"Mittel {sgn(nw['bias'])} K neben den Messungen (mittlerer Absolutfehler {n(nw['mae'], 1)} K, "
                        f"{nw['n']} Stundenwerte).")
        if bits:
            A(" ".join(bits))
            A("")
        A("Messwerte: MeteoSchweiz, Stationen im Umkreis von 45 km um die Riederalp"
          + (f"; «Jetzt» ist der letzte Stundenwert ({tlast:%H:%M} Uhr)." if tlast else "."))
        A("")
    A("### Lawinen")
    A("")
    A(slf_text(X.get("slf")))
    A("")
    return out


def verif_section(X):
    out = []
    A = out.append
    V = X.get("verif")
    if V and any(V.get(L, {}).get("tx") for L in (1, 2, 3)):
        A("### Verifikation der letzten 14 Tage")
        A("")
        A(f"Frühere Läufe von {MODEL_NAMES.get(X.get('verif_model'), X.get('verif_model'))} an den MeteoSchweiz-Stationen der Region gegen die gemessenen "
          "Tageswerte; Fehler als Prognose minus Messung.")
        A("")
        A("| Vorlauf | Tmax Bias / MAE | Tmin Bias / MAE | ≥ 1 mm: Treffer / Fehlalarme | n |")
        A("|---|---|---|---|---|")
        for L in (1, 2, 3):
            v = V.get(L) or {}
            tx, tn = v.get("tx"), v.get("tn")
            if not tx:
                continue
            A(f"| {L} Tag{'e' if L > 1 else ''} | {sgn(tx['bias'])} / {n(tx['mae'], 1)} K | "
              f"{(sgn(tn['bias']) + ' / ' + n(tn['mae'], 1) + ' K') if tn else '–'} | "
              f"{pct(v.get('pod'))} / {pct(v.get('far'))} | {tx['n']} |")
        A("")
        v1 = V.get(1) or {}
        lo, hi = v1.get("tn_low"), v1.get("tn_high")
        bits = []
        if lo and hi and abs(lo["bias"] - hi["bias"]) >= 1:
            dd = lo["bias"] - hi["bias"]
            what = (f"gegenüber den Hochlagen sind die Tallagen nachts um {n(abs(dd), 1)} K zu warm vorhergesagt, ein "
                    "Hinweis auf unterschätzte Kaltluftseen" if dd > 0 else
                    f"gegenüber den Hochlagen sind die Tallagen nachts um {n(abs(dd), 1)} K zu kalt vorhergesagt, die "
                    "nächtliche Auskühlung der Täler wird überschätzt")
            bits.append(f"Tiefstwerte am Folgetag: in Lagen unter 1200 m {sgn(lo['bias'])} K, über 2000 m "
                        f"{sgn(hi['bias'])} K; {what}.")
        bits.append("Grenzen: Messextreme stammen aus 10-Minuten-Werten, Modellextreme aus Stundenwerten, was "
                    "Höchstwerte im Modell leicht drückt und Tiefstwerte leicht anhebt; Tage in UTC; kleine Stichprobe.")
        A(" ".join(bits))
        A("")
    C = X.get("consist")
    if C:
        A("### Prognosekonstanz Riederalp")
        A("")
        A(f"Höchstwert und Niederschlag für die Riederalp, wie sie {MODEL_NAMES.get(X.get('consist_model'), X.get('consist_model'))} in den Läufen von heute und "
          "den Vortagen vorhergesagt hat.")
        A("")
        A("| Tag | aktuell | vor 1 Tag | vor 2 Tagen | vor 3 Tagen |")
        A("|---|---|---|---|---|")
        jumps, big = [], None
        for d, row in C.items():
            cells = [f"{n(row[L][0])} °C; {n(row[L][1], 1)} mm" if L in row else "" for L in (0, 1, 2, 3)]
            A(f"| {dshort(d)} | " + " | ".join(cells) + " |")
            if 0 in row and 1 in row:
                jumps.append(abs(row[0][0] - row[1][0]))
            far = max((L for L in row if L > 0), default=None)
            if far and 0 in row:
                dd = row[0][0] - row[far][0]
                if big is None or abs(dd) > abs(big[1]):
                    big = (d, dd, far)
        A("")
        bits = []
        if jumps:
            bits.append(f"Mittlere Änderung des Höchstwerts gegenüber dem Vortageslauf: {n(sum(jumps) / len(jumps), 1)} K.")
        if big and abs(big[1]) >= 1:
            bits.append(f"Grösste Verschiebung: {dshort(big[0])} um {sgn(big[1])} K gegenüber dem Lauf von vor "
                        f"{big[2]} Tag{'en' if big[2] > 1 else ''}.")
        if bits:
            A(" ".join(bits))
            A("")
    return out


def week_section(X):
    W = X.get("weeks")
    if not W:
        return []
    out = [f"### Tendenz bis {dlong(W[-1]['to'])}", "",
           "ECMWF-Ensemble für die Riederalp, Wochenmittel des Höchstwerts und Wochensumme des Niederschlags gegenüber "
           "1991–2020.", "",
           "| Zeitraum | Höchstwert gegenüber Mittel | wärmer | ≥ +2 K | ≤ −2 K | Niederschlag (Median / Mittel) | nasser |",
           "|---|---|---|---|---|---|---|"]
    for w in W:
        a = w["an"]
        pr = (f"{n(w['pr']['p50'])} / {n(w['clim_pr'])} mm" if w.get("pr") and w.get("clim_pr") else "–")
        out.append(f"| {dshort(w['from'])} bis {dshort(w['to'])} | {sgn(a['p50'])} K ({sgn(a['p10'])} … {sgn(a['p90'])}) | "
                   f"{pct(w['p_warm'])} | {pct(w['p_w2'])} | {pct(w['p_c2'])} | {pr} | {pct(w.get('p_wet'))} |")
    out.append("")
    w2 = W[-1]
    if w2["p_warm"] is not None:
        if w2["p_warm"] >= 0.8:
            t = f"Für {w2['label']} deutet das Ensemble klar auf überdurchschnittliche Temperaturen hin."
        elif w2["p_warm"] <= 0.2:
            t = f"Für {w2['label']} deutet das Ensemble klar auf unterdurchschnittliche Temperaturen hin."
        else:
            t = f"Für {w2['label']} zeigt das Ensemble kein eindeutiges Temperatursignal."
        out += [t + " Bei diesem Vorlauf ist die Ensemble-Wahrscheinlichkeit aussagekräftiger als der Median.", ""]
    return out


def night_text(X):
    for label, (t, T) in X["det_hourly"].items():
        now = X["now"].replace(tzinfo=None)
        end = dt.datetime.combine(now.date() + dt.timedelta(1), dt.time(7))
        vals = valid([x for tt, x in zip(t, T or []) if now <= tt <= end])
        if vals:
            return f"Kommende Nacht bis 07 Uhr: Tiefstwert um {deg(min(vals))} ({label})."
    return ""


def trend_text(X):
    days, LD = X["days"], X["local"]
    rows = [(d, LD["riederalp"].get(d)) for d in days if LD["riederalp"].get(d)]
    if len(rows) < 3:
        return ""
    tx = [(d, s["tmax"]) for d, s in rows]
    hi, lo = max(tx, key=lambda x: x[1]), min(tx, key=lambda x: x[1])
    wet = [d for d, s in rows if s["prec"] >= 1]
    parts = [f"Wärmster Tag {dshort(hi[0])} mit {deg(hi[1])}, kühlster {dshort(lo[0])} mit {deg(lo[1])}."]
    if wet:
        if len(wet) == len(rows):
            parts.append("Im Hauptlauf an jedem Tag mindestens 1 mm Niederschlag.")
        else:
            parts.append(sent(f"Nennenswerter Niederschlag (≥ 1 mm im Hauptlauf) am {join_de([dshort(d) for d in wet])}"))
    else:
        parts.append("Im Hauptlauf kein Tag mit nennenswertem Niederschlag.")
    srcs = sorted({s["src"] for _, s in rows})
    if len(srcs) > 1:
        switch = next((d for d, s in rows if s["src"] != rows[0][1]["src"]), None)
        if switch:
            parts.append(f"Ab {dshort(switch)} stammen die Werte aus {LD['riederalp'][switch]['src']} statt "
                         f"{rows[0][1]['src']}: an dieser Naht können Sprünge entstehen, die nicht meteorologisch sind.")
    if X["horizon"]:
        parts.append(f"Ab {dshort(X['horizon'])} wird die Prognose deutlich unsicherer "
                     "(Ensemble-Spread Höchstwert ≥ 5 K oder Ensemble-Mediane weichen ≥ 3 K voneinander ab).")
    return " ".join(parts)


def snow_trend_text(X):
    days, LD = X["days"], X["local"]
    rows = [(d, LD["riederalp"].get(d)) for d in days if LD["riederalp"].get(d)]
    if not rows:
        return ""
    tot = sum(r["snow"] for _, r in rows)
    parts = []
    if tot >= 0.5:
        t = f"Neuschnee auf der Riederalp bis {dshort(days[-1])} im Hauptlauf insgesamt {n(tot)} cm"
        for lab in ("ECMWF ENS", "NOAA GEFS"):
            sp = X["snow_period"].get(lab)
            if sp:
                t += f", im {lab} Median {n(sp['p50'])} cm (10–90 %: {n(sp['p10'])} bis {n(sp['p90'])} cm)"
                break
        parts.append(t + ".")
    else:
        parts.append(f"Bis {dshort(days[-1])} im Hauptlauf kein Neuschnee auf der Riederalp.")
    a, b = rows[0][1].get("sd07"), rows[-1][1].get("sd_end")
    if (a or 0) >= 0.5 or (b or 0) >= 0.5:
        parts.append(f"Modellschneehöhe von {n(a)} cm heute früh auf {n(b)} cm am {dshort(rows[-1][0])} abends.")
    wet = [(d, r["sfh_med"]) for d, r in rows if r["prec"] >= 0.3 and r.get("sfh_med") is not None]
    if len(wet) >= 2:
        lo, hi = min(wet, key=lambda x: x[1]), max(wet, key=lambda x: x[1])
        if hi[1] - lo[1] >= 300:
            parts.append(f"Die Schneefallgrenze bei Niederschlag bewegt sich zwischen {hm(lo[1])} "
                         f"({dshort(lo[0])}) und {hm(hi[1])} ({dshort(hi[0])}).")
    return " ".join(parts)


def snow_table(X):
    days, LD = X["days"], X["local"]
    out = ["### Schnee im Wallis", "",
           f"| Ort | Höhe | Schneehöhe heute 07 Uhr | Neuschnee heute und morgen | Neuschnee bis {dshort(days[-1])} "
           f"| Schneehöhe {dshort(days[-1])} abends |",
           "|---|---|---|---|---|---|"]
    for k in SNOW_TABLE:
        rows = [LD[k].get(d) for d in days]
        if not rows[0]:
            continue
        two = sum(r["snow"] for r in rows[:2] if r)
        tot = sum(r["snow"] for r in rows if r)
        last = next((r for r in reversed(rows) if r), None)
        out.append(f"| {LOCAL[k]['name']} | {LOCAL[k]['elev']} m | {n(rows[0].get('sd07'))} cm | {n(two)} cm | "
                   f"{n(tot)} cm | {n(last.get('sd_end') if last else None)} cm |")
    out += ["", "Neuschnee aus dem Hauptlauf mit festem Faktor 0,7 cm pro mm. Schneehöhen sind Gitterpunktwerte "
            "des Modells; Messungen des SLF können besonders zu Saisonbeginn und in steilem Gelände stark abweichen.", ""]
    return out


def wallis_text(X, d):
    LD = X["local"]
    g = lambda k: LD[k].get(d)
    sion, visp, mart, zer, simp, grim = g("sion"), g("visp"), g("martigny"), g("zermatt"), g("simplon"), g("grimsel")
    parts = []
    if sion:
        ps = prec_sentence(sion, sfh=False, place="in Sion")
        parts.append(f"Im Rhonetal {sky_word(sion)}{'' if ps else ' und trocken'}. "
                     f"Höchstwerte {deg(sion['tmax'])} in Sion" + (f" und {deg(visp['tmax'])} in Visp." if visp else "."))
        if ps:
            parts.append(ps)
    gusts = [(s["gust"], s["wdir"], nm) for s, nm in ((mart, "Martigny"), (sion, "Sion")) if s and s["gust"]]
    if gusts:
        gmax = max(gusts)
        if gmax[0] >= 45:
            reg = "Im Unterwallis" if gmax[2] == "Martigny" else "Im Mittelwallis"
            parts.append(f"{reg} {wind_phrase(gmax[0], gmax[1])} ({gmax[2]}).")
    if simp:
        if simp["prec"] >= 1:
            es = best_ens(X, "simplon", d)
            parts.append(prec_sentence(simp, es[1] if es else None, place="am Simplon", sfh=False))
        if sion and simp["prec"] >= 5 and simp["prec"] >= 3 * (sion["prec"] + 0.3):
            parts.append(f"Südstaumuster: Simplon {n(simp['prec'], 1)} mm gegenüber {n(sion['prec'], 1)} mm "
                         "in Sion, die inneralpine Lage des Mittelwallis schirmt deutlich ab.")
    sg = [(nm, st["sfh_med"]) for st, nm in ((simp, "am Simplon"), (zer, "in Zermatt"), (grim, "am Grimsel"))
          if st and st["prec"] >= 0.3 and st.get("sfh_med") is not None]
    if sg:
        parts.append("Schneefallgrenze " + join_de([f"{nm} um {hm(h)}" for nm, h in sg]) + ".")
    if simp:
        gap = melt_cooling(simp)
        if gap:
            parts.append(f"Am Simplon liegt die Schneefallgrenze {hm(gap)} unter der Nullgradgrenze: "
                         "Hinweis auf Schmelzkühlung im Stauniederschlag, die Schneefallgrenze kann tiefer sinken als "
                         "die Faustregel erwarten lässt.")
    fo = X["foehn"].get(d)
    parts.append("\n\n")
    if grim and grim["prec"] >= 5 and fo and fo["min"] <= -3:
        parts.append(f"Nordstau am Grimsel mit {n(grim['prec'], 1)} mm.")
    flux = X["eu"].get(d, {}).get("flux") if X["eu"] else None
    ft = foehn_text(fo, flux)
    if ft:
        parts.append(ft)
    if zer and zer["tmin"] is not None:
        parts.append(f"In Zermatt {deg(zer['tmin'])} bis {deg(zer['tmax'])}"
                     + (", verbreitet Frost am Morgen." if zer["tmin"] <= -1 else "."))
    txt = " ".join(parts).replace(" \n\n ", "\n\n").strip()
    return txt if txt else "Keine Modelldaten."


def wallis_short(X, d):
    LD = X["local"]
    sion, simp = LD["sion"].get(d), LD["simplon"].get(d)
    bits = []
    if sion:
        bits.append(f"Sion {sky_word(sion)}, {deg(sion['tmax'])}")
    if simp:
        b = f"Simplon {n(simp['prec'], 1)} mm"
        if simp["snow"] >= 1:
            b += f", {n(simp['snow'])} cm Neuschnee"
        if simp["prec"] >= 0.3 and simp.get("sfh_med") is not None:
            b += f", Schneefallgrenze {hm(simp['sfh_med'])}"
        bits.append(b)
    fo = X["foehn"].get(d)
    if fo:
        if fo["max"] >= 4:
            bits.append(f"Südföhn/Südstau (Δp {sgn(fo['max'])} hPa)")
        elif fo["min"] <= -4:
            bits.append(f"Nordföhn/Nordstau (Δp {sgn(fo['min'])} hPa)")
    flux = X["eu"].get(d, {}).get("flux") if X["eu"] else None
    if flux and flux[0] >= 40:
        bits.append(f"Feuchtefluss 700 hPa {n(flux[0])} g kg⁻¹ m s⁻¹")
    return "; ".join(bits) + "." if bits else "Keine Daten."


def swiss_text(X, d):
    LD = X["local"]
    g = lambda k: LD[k].get(d)
    lines = []
    north = [(k, g(k)) for k in ("zuerich", "bern", "basel") if g(k)]
    hn = X["hochnebel"].get(d)
    if north:
        txs = [s["tmax"] for _, s in north]
        zs = north[0][1]
        ps = prec_sentence(zs, sfh=False, place="in " + LOCAL[north[0][0]]["name"])
        t = f"**Alpennordseite:** {cap(sky_word(zs, lowland=True))}{'' if ps else ' und trocken'}."
        if hn and hn["likely"]:
            t += (f" Hochnebel im Mittelland, Obergrenze vermutlich zwischen etwa 800 und 1300 m: Bern am Morgen "
                  f"{n(hn['low_bern'])} % tiefe Wolken, Napf {n(hn['low_napf'])} % und dort {n(hn['dT_napf'], 1)} K "
                  "wärmer als in Bern")
            if hn.get("dT850") is not None:
                t += f", T850 − T2m(Bern) {sgn(hn['dT850'])} K"
            t += "."
            if hn.get("p_ens") is not None:
                t += (f" Im {hn['ens_label']} zeigen {pct(hn['p_ens'])} der Member am Vormittag mindestens 80 % "
                      "tiefe Bewölkung in Bern.")
        if ps:
            t += " " + ps
        t += f" Höchstwerte {n(min(txs))} bis {n(max(txs))} °C."
        lines.append(t)
    gf = g("genf")
    if gf:
        t = f"**Westschweiz:** {cap(sky_word(gf, lowland=True))}, in Genf bis {deg(gf['tmax'])}."
        bt = bise_text(X["bise"].get(d))
        if bt:
            t += " " + bt
        lines.append(t)
    lu = g("lugano")
    if lu:
        ps = prec_sentence(lu, sfh=True, place="in Lugano")
        lines.append(f"**Alpensüdseite:** {cap(sky_word(lu))}{'' if ps else ' und trocken'}, "
                     f"in Lugano {deg(lu['tmin'])} bis {deg(lu['tmax'])}." + (f" {ps}" if ps else ""))
    ch, al, zh = g("chur"), g("altdorf"), g("zuerich")
    if ch and al:
        t = f"**Zentral- und Ostalpen:** Chur bis {deg(ch['tmax'])}, Altdorf bis {deg(al['tmax'])}"
        fo = X["foehn"].get(d)
        if fo and fo["max"] >= 4 and zh and (al["tmax"] - zh["tmax"] >= 1.5 or (al["gust"] or 0) >= 50):
            t += (f". Föhnsignatur: Altdorf {sgn(al['tmax'] - zh['tmax'])} K gegenüber Zürich, "
                  f"Böen bis {n(al['gust'])} km/h")
        lines.append(t + ".")
    sa, jj = g("saentis"), g("jungfraujoch")
    if sa or jj:
        bits = []
        if sa:
            bits.append(f"Säntis {deg(sa['tmin'])} bis {deg(sa['tmax'])}, Böen bis {n(sa['gust'])} km/h")
        if jj:
            bits.append(f"Jungfraujoch {deg(jj['tmin'])} bis {deg(jj['tmax'])}")
        r = LD["riederalp"].get(d)
        t = "**Berge:** " + "; ".join(bits) + "."
        if r and r.get("fzl_mean") is not None:
            t += f" Nullgradgrenze über dem Wallis um {hm(r['fzl_mean'])}."
        sn = sfh_ns(X, d)
        if sn:
            t += " " + sn
        e = X["eu"].get(d) if X["eu"] else None
        if e and e.get("w700") and e["w700"][1] is not None:
            t += f" Höhenwind in 700 hPa (rund 3000 m) aus {dname(e['w700'][0])} mit {n(e['w700'][1] * 3.6)} km/h."
        lines.append(t)
    return lines


def sfh_ns(X, d, short=False):
    LD = X["local"]

    def med(keys):
        v = [LD[k][d]["sfh_med"] for k in keys if LD[k].get(d) and LD[k][d]["prec"] >= 0.3
             and LD[k][d].get("sfh_med") is not None]
        return quant(v, .5) if v else None

    nn, ss = med(("zuerich", "bern", "altdorf", "chur", "saentis")), med(("lugano",))
    if nn is None and ss is None:
        return ""
    if short:
        return "Schneefallgrenze " + ", ".join(x for x in (f"Nord {hm(nn)}" if nn else "",
                                                           f"Süd {hm(ss)}" if ss else "") if x)
    bits = [x for x in (f"im Norden um {hm(nn)}" if nn else "", f"im Süden um {hm(ss)}" if ss else "") if x]
    return f"Schneefallgrenze bei Niederschlag {join_de(bits)}."


def swiss_short(X, d):
    LD = X["local"]
    zh, lu = LD["zuerich"].get(d), LD["lugano"].get(d)
    bits = []
    if zh:
        bits.append(f"Norden {sky_word(zh, lowland=True)}, Zürich {deg(zh['tmax'])}"
                    + (f", {n(zh['prec'], 1)} mm" if zh["prec"] >= 1 else ""))
    if lu:
        bits.append(f"Süden {sky_word(lu)}, Lugano {deg(lu['tmax'])}"
                    + (f", {n(lu['prec'], 1)} mm" if lu["prec"] >= 1 else ""))
    bt = X["bise"].get(d)
    if bt and bt["mean"] >= 3:
        bits.append(f"Bise (Δp {sgn(bt['mean'])} hPa)")
    hn = X["hochnebel"].get(d)
    if hn and hn["likely"]:
        bits.append("Hochnebel im Mittelland")
    sn = sfh_ns(X, d, short=True)
    if sn:
        bits.append(sn)
    return "; ".join(bits) + "." if bits else "Keine Daten."


def europe_text(X, d):
    e = X["eu"].get(d) or {}
    parts = []
    if e.get("pmax"):
        parts.append(f"Bodendruck um 12 UTC: höchster Wert unter den Referenzorten in {e['pmax'][0]} "
                     f"({n(e['pmax'][1])} hPa), tiefster in {e['pmin'][0]} ({n(e['pmin'][1])} hPa).")
    zs = []
    if e.get("trough"):
        la, lo, c = e["trough"]
        zs.append(f"ein Höhentrog mit Achse etwa über {region(la, lo)} "
                  f"(Krümmung {n(c)} m pro 10°)")
    if e.get("ridge"):
        la, lo, c = e["ridge"]
        zs.append(f"ein Höhenrücken über {region(la, lo)}")
    if zs:
        parts.append(f"In 500 hPa liegt {join_de(zs)}.")
    if e.get("geo"):
        dd, vv = e["geo"]
        parts.append(f"Die geostrophische Höhenströmung über den Alpen kommt aus {dname(dd, True)} "
                     f"mit rund {n(vv)} m/s ({n(vv * 1.944)} kn).")
    if e.get("jet_lat") is not None:
        parts.append(f"Der Strahlstrom in 250 hPa liegt zwischen 10° W und 20° E im Mittel bei "
                     f"{n(e['jet_lat'])}° N, maximal {n(e['jet_max'])} m/s.")
    if e.get("blocked"):
        parts.append(f"Tibaldi-Molteni-Kriterium erfüllt bei {join_de([f'{lo}° E' for lo in e['blocked']])}: "
                     "blockierte Strömung, das erhöht die Persistenz der Lage.")
    elif e.get("tm"):
        parts.append("Kein blockierter Sektor zwischen 20° W und 30° E (Tibaldi-Molteni).")
    nv = X.get("nao", {}).get(d)
    if nv is not None:
        parts.append(f"NAO-Proxy heute {sgn(nv)}.")
    if e.get("t850_alps") is not None:
        t = f"Temperatur in 850 hPa über den Alpen {deg(e['t850_alps'])}"
        if e.get("t850max"):
            t += (f"; die wärmste Luft unter den Referenzorten liegt über {e['t850max'][0]} "
                  f"({deg(e['t850max'][1])}), die kälteste über {e['t850min'][0]} ({deg(e['t850min'][1])})")
        parts.append(t + ".")
    return " ".join(parts) if parts else "Keine Daten."


def europe_short(e, prev):
    bits = []
    if e.get("geo"):
        bits.append(f"Höhenströmung Alpen {dname(e['geo'][0])} {n(e['geo'][1])} m/s")
    for key, word in (("trough", "Trog"), ("ridge", "Rücken")):
        if e.get(key):
            same = prev and prev.get(key) and prev[key][:2] == e[key][:2]
            bits.append(f"{word} {'weiterhin ' if same else ''}über {region(*e[key][:2])}")
    if e.get("jet_lat") is not None:
        bits.append(f"Jet bei {n(e['jet_lat'])}° N")
    if e.get("t850_alps") is not None:
        bits.append(f"T850 Alpen {deg(e['t850_alps'])}")
    if e.get("blocked"):
        bits.append(f"Blockierung bei {join_de([f'{lo}° E' for lo in e['blocked']])}")
    txt = "; ".join(bits) + "."
    if prev and e.get("t850_alps") is not None and prev.get("t850_alps") is not None:
        dtt = e["t850_alps"] - prev["t850_alps"]
        if abs(dtt) >= 5:
            txt += f" Luftmassenwechsel: T850 {sgn(dtt)} K gegenüber dem Vortag."
    if prev and e.get("geo") and prev.get("geo"):
        dd = abs((e["geo"][0] - prev["geo"][0] + 180) % 360 - 180)
        if dd >= 90 and e["geo"][1] > 8:
            txt += " Deutliche Drehung der Höhenströmung."
    return txt


def insights(X):
    days, LD = X["days"], X["local"]
    d0 = days[0]
    I = []  # (Priorität, Text)
    for d in days[:3]:
        lab = "heute" if d == d0 else ("morgen" if d == days[1] else dshort(d))
        r, g, s = LD["riederalp"].get(d), LD["grimsel"].get(d), LD["simplon"].get(d)
        for st, nm in ((r, "Riederalp"), (g, "Grimsel"), (LD["saentis"].get(d), "Säntis")):
            if st and st["gust"] and st["gust"] >= 75:
                I.append((9, f"Sturmböen {lab}: {nm} bis {n(st['gust'])} km/h."))
                break
        if s and s["prec"] >= 30:
            I.append((9, f"Starkniederschlag {lab} am Simplon: {n(s['prec'])} mm in 24 h (Südstau)."))
    def both(flags):
        """flags: {d: wert oder None} für heute/morgen -> Tageslabel."""
        hit = [d for d in days[:2] if flags.get(d) is not None]
        if not hit:
            return None
        return "Heute und morgen" if len(hit) == 2 else ("Heute" if hit[0] == d0 else "Morgen")

    fo_f = {d: X["foehn"].get(d) for d in days[:2]
            if X["foehn"].get(d) and (X["foehn"][d]["max"] >= 8 or X["foehn"][d]["min"] <= -8)}
    lab = both(fo_f)
    if lab:
        ext = max((v["max"] if v["max"] >= 8 else v["min"] for v in fo_f.values()), key=abs)
        kind = "Südföhn" if ext > 0 else "Nordföhn"
        I.append((8, f"{lab} kräftiger {kind}: Δp Lugano − Zürich bis {sgn(ext)} hPa."))
    inv_f = {}
    for d in days[:2]:
        l7 = X["inv"].get(d, {}).get("lapse7")
        r, m = LD["riederalp"].get(d), LD["moerel"].get(d)
        if l7 is not None and l7 > 1 and r and m and r["t07"] is not None and m["t07"] is not None:
            inv_f[d] = (r["t07"] - m["t07"], l7)
    lab = both(inv_f)
    if lab:
        dT, l7 = max(inv_f.values())
        I.append((6, f"{lab} früh warme Hangzone: Riederalp bis {sgn(dT)} K wärmer als Mörel im Tal "
                     f"(Gradient bis {sgn(l7)} K/km)."))
    lab = both({d: 1 for d in days[:2] if (X["hochnebel"].get(d) or {}).get("likely")})
    if lab:
        I.append((5, f"{lab} Hochnebel im Mittelland, Obergrenze grob 800–1300 m; über dem Nebel sonnig."))
    fz = {d: LD["riederalp"][d]["fzl_max"] for d in days[:2]
          if LD["riederalp"].get(d) and (LD["riederalp"][d].get("fzl_max") or 0) >= 3300}
    lab = both(fz)
    if lab:
        I.append((5, f"{lab} Nullgradgrenze bis {hm(max(fz.values()))}: positive Lufttemperaturen bis über den "
                     "Konkordiaplatz (2700 m), Schmelze auf dem Aletschgletscher auch Ende Saison."))
    du = {d: X["dust"][d]["dust"] for d in days[:2] if (X["dust"].get(d) or {}).get("dust") and X["dust"][d]["dust"] >= 50}
    lab = both(du)
    if lab:
        I.append((6, f"{lab} Saharastaub: CAMS bis {n(max(du.values()))} µg/m³ bodennah; Staub auf Schnee und "
                     "Firn senkt die Albedo."))
    for d in days[:3]:
        cl = X["clim"]["riederalp"].get(d)
        if cl and (abs(cl["anom_tmax"]) >= 4 or cl["rank_tmax"] >= 0.95 or cl["rank_tmax"] <= 0.05):
            word = "warm" if cl["anom_tmax"] > 0 else "kalt"
            dl = "Heute" if d == d0 else ("Morgen" if d == days[1] else f"Am {dshort(d)}")
            I.append((7, f"{dl} aussergewöhnlich {word} für die Jahreszeit: Höchstwert Riederalp "
                         f"{sgn(cl['anom_tmax'])} K gegenüber 1991–2020 ({int(round(cl['rank_tmax'] * 100))}. Perzentil)."))
            break
    rows = [(d, LD["riederalp"].get(d)) for d in days if LD["riederalp"].get(d)]
    sdays = [d for d, r in rows if r["snow"] >= 1]
    if sdays:
        tot = sum(r["snow"] for _, r in rows)
        t = f"Neuschnee auf der Riederalp ab {dshort(sdays[0])}, bis {dshort(days[-1])} insgesamt {n(tot)} cm im Hauptlauf"
        sp = X["snow_period"].get("ECMWF ENS") or X["snow_period"].get("NOAA GEFS")
        if sp:
            t += f" (Ensemble 10–90 %: {n(sp['p10'])} bis {n(sp['p90'])} cm)"
        I.append((8 if tot >= 10 or max(r["snow"] for _, r in rows) >= 10 else 6, t + "."))
    if rows and (rows[0][1].get("sd07") or 0) < 1:
        onset = next(((d, r["sd_end"]) for d, r in rows if (r.get("sd_end") or 0) >= 5), None)
        if onset:
            I.append((7, f"Eine Schneedecke bildet sich: Modellschneehöhe Riederalp am {dshort(onset[0])} abends "
                         f"{n(onset[1])} cm."))
    wet = [(d, r["sfh_med"]) for d, r in rows if r["prec"] >= 0.3 and r.get("sfh_med") is not None]
    if len(wet) >= 2:
        lo = min(wet[1:], key=lambda x: x[1])
        if wet[0][1] - lo[1] >= 800:
            I.append((6, f"Schneefallgrenze sinkt von {hm(wet[0][1])} ({dshort(wet[0][0])}) auf {hm(lo[1])} "
                         f"({dshort(lo[0])})."))
    for d in days[:3]:
        for k, nm in (("simplon", "Simplon"), ("riederalp", "Riederalp")):
            st = LD[k].get(d)
            gap = melt_cooling(st) if st else None
            if gap:
                I.append((5, f"{dshort(d)} {nm}: Schneefallgrenze {hm(gap)} unter der Nullgradgrenze, "
                             "Hinweis auf Schmelzkühlung im Niederschlag."))
                break
        else:
            continue
        break
    lv = X.get("slf_level")
    if lv and lv >= 2:
        word = {2: "mässig", 3: "erheblich", 4: "gross", 5: "sehr gross"}[lv]
        I.append((9 if lv >= 3 else 5, f"Lawinengefahr in der Region Riederalp: Stufe {lv}, {word} (SLF)."))
    fo = sorted((o for o in X.get("obs", []) if (o.get("foehn_h") or 0) >= 3), key=lambda o: -o["foehn_h"])
    if fo:
        I.append((6, f"Föhn gemessen: {fo[0]['name']} {n(fo[0]['foehn_h'], 1)} h in den letzten 24 h."))
    v1 = ((X.get("verif") or {}).get(1) or {}).get("tx")
    if v1 and v1["n"] >= 20 and abs(v1["bias"]) >= 1.5:
        I.append((4, f"Verifikation: Höchstwerte am Folgetag lagen in den letzten 14 Tagen im Mittel {sgn(v1['bias'])} K "
                     "neben den Messungen der Region."))
    jmp = [(d, r[0][0] - r[1][0]) for d, r in (X.get("consist") or {}).items()
           if 0 in r and 1 in r and 1 <= (d - days[0]).days <= 3]
    if jmp:
        d_, dd = max(jmp, key=lambda x: abs(x[1]))
        if abs(dd) >= 4:
            I.append((5, f"Prognosesprung: Höchstwert Riederalp für {dshort(d_)} {sgn(dd)} K gegenüber dem Vortageslauf."))
    for w in X.get("weeks") or []:
        if w["label"] == "Woche 2" and w["p_warm"] is not None and (w["p_warm"] >= 0.8 or w["p_warm"] <= 0.2):
            I.append((5, f"Tendenz {dshort(w['from'])} bis {dshort(w['to'])}: mit {pct(max(w['p_warm'], 1 - w['p_warm']))} "
                         f"{'wärmer' if w['p_warm'] >= 0.5 else 'kälter'} als 1991–2020 (Median {sgn(w['an']['p50'])} K)."))
    M = ((X.get("mch") or {}).get("data") or {}).get("riederalp") or {}
    for d in days[:2]:
        s = LD["riederalp"].get(d)
        mx = (M.get("tre200px") or {}).get(d)
        if s and mx is not None and abs(s["tmax"] - mx) >= 3:
            I.append((4, f"{'Heute' if d == days[0] else 'Morgen'} weicht der Höchstwert für die Riederalp um "
                         f"{sgn(s['tmax'] - mx)} K von der MeteoSchweiz-Lokalprognose ({deg(mx)}) ab."))
            break
    for r in X.get("rivers") or []:
        if r.get("clim_max") and r.get("pk_max") and r["pk_max"][2] and r["pk_max"][2] > r["clim_max"]:
            I.append((6, f"Abfluss {r['name']}: einzelne Ensemble-Member übersteigen bis {dshort(r['pk_max'][0])} den "
                         f"höchsten Wert dieser Jahreszeit 1991–2020 (GloFAS)."))
            break
    big = [e for e in (X.get("gdacs") or []) if e["alert"] in ("Red", "Orange")]
    if big:
        e = big[0]
        I.append((6 if e["alert"] == "Red" else 4,
                  f"Weltweit: {GDACS_TYPES[e['type']]} {e['name'] or e['desc']}".rstrip()
                  + (f" ({e['country']})" if e["country"] else "")
                  + f", GDACS-Warnstufe {'Rot' if e['alert'] == 'Red' else 'Orange'}."))
    blk = [d for d in days if X["eu"].get(d, {}).get("blocked")]
    if blk:
        I.append((6, f"Blockierung (Tibaldi-Molteni) ab {dshort(blk[0])} bei "
                     f"{join_de([f'{lo}° E' for lo in X['eu'][blk[0]]['blocked']])}."))
    if X["horizon"]:
        I.append((4, f"Vorhersagbarkeit: ab {dshort(X['horizon'])} grosse Unsicherheit."))
    dr = [(d, X["det_range"][d][0]) for d in days[:3] if X["det_range"].get(d)]
    if dr:
        dmax = max(dr, key=lambda x: x[1])
        if dmax[1] >= 4:
            I.append((5, f"Modelle uneinig am {dshort(dmax[0])}: Höchstwert Riederalp streut um {n(dmax[1])} K "
                         "zwischen den deterministischen Läufen."))
    if X["ai"]:
        rs = [v["ratio"] for d, v in X["ai"].items() if v["ratio"] and (d - days[0]).days >= 3]
        if rs:
            r = sum(rs) / len(rs)
            if r < 0.7 or r > 1.4:
                I.append((4, f"KI und Physik: Das AIFS-Ensemble streut ab Tag 3 {'schmaler' if r < 1 else 'breiter'} "
                             f"als das IFS-Ensemble (Spread-Verhältnis {n(r, 2)})."))
    if not I:
        I.append((1, "Ruhige Lage ohne markante Signale in den Diagnosen."))
    I.sort(key=lambda x: -x[0])
    return [t for _, t in I[:6]]


def uncertainty_section(X):
    days, out = X["days"], []
    A = out.append
    C = X["compare"]
    if C:
        A("### Deterministische Modelle, Riederalp")
        A("")
        A("Höchstwert in °C und Tagesniederschlag in mm. Leere Felder: Tag liegt ausserhalb des Modellhorizonts.")
        A("")
        A("| Modell | " + " | ".join(dshort(d) for d in days) + " |")
        A("|---|" + "---|" * len(days))
        for m, row in C.items():
            cells = []
            for d in days:
                c = row.get(d)
                cells.append(f"{n(c['tmax'])}; {n(c['prec'], 1)}" if c else "")
            A(f"| {m} | " + " | ".join(cells) + " |")
        rng = [f"{n(X['det_range'][d][0])} K" if X["det_range"].get(d) else "" for d in days]
        A("| Spannweite Tmax | " + " | ".join(rng) + " |")
        A("")
        for d in days[:3]:
            vals = [(m, C[m][d]["tmax"]) for m in C if d in C[m]]
            if len(vals) >= 4:
                med = quant([v for _, v in vals], .5)
                out_m = max(vals, key=lambda x: abs(x[1] - med))
                if abs(out_m[1] - med) >= 2.5:
                    A(f"Ausreisser am {dshort(d)}: {out_m[0]} liegt {sgn(out_m[1] - med)} K neben dem Modellmedian.")
                    A("")
    if X["ens"]:
        A("### Ensembles, Riederalp")
        A("")
        A("Höchstwert als Median [10–90 %] und Wahrscheinlichkeit für mindestens 1 mm Niederschlag.")
        A("")
        A("| Ensemble | " + " | ".join(dshort(d) for d in days) + " |")
        A("|---|" + "---|" * len(days))
        for lab, locs in X["ens"].items():
            row = locs.get("riederalp") or {}
            cells = []
            for d in days:
                st = row.get(d)
                cells.append(f"{n(st['tmax']['p50'])} [{n(st['tmax']['p10'])} … {n(st['tmax']['p90'])}]; {pct(st['p1'])}"
                             if st and st.get("tmax") else "")
            A(f"| {lab} | " + " | ".join(cells) + " |")
        A("")
    if X["conf"]:
        A("### Prognosesicherheit")
        A("")
        lines = [f"{dshort(d)} {c['label']} (Spread {n(c['spreadT'], 1)} K, P(≥ 1 mm) {pct(c['p1'])})"
                 for d, c in X["conf"].items()]
        A("Index aus Ensemble-Spread des Höchstwerts, Niederschlagswahrscheinlichkeit nahe 50 % und Streuung der "
          "deterministischen Modelle: " + "; ".join(lines) + ".")
        A("")
    if X["ai"]:
        rs = [(d, v) for d, v in X["ai"].items() if v["ratio"]]
        if rs:
            A("### KI-Ensemble gegen physikalisches Ensemble")
            A("")
            A("Spread (10–90 %) des Höchstwerts Riederalp, AIFS-ENS gegenüber IFS-ENS: " + "; ".join(
                f"{dshort(d)} {n(v['sa'], 1)} gegen {n(v['sb'], 1)} K, Mediandifferenz {sgn(v['dmed'])} K"
                for d, v in rs) + ". Beide Systeme laufen auf 0,25°; Unterschiede im Spread sagen etwas über die "
                "Kalibrierung, nicht automatisch über die Treffsicherheit.")
            A("")
    out.extend(verif_section(X))
    if X["horizon"]:
        A(f"Vorhersagbarkeitsgrenze für die Riederalp in diesem Lauf: ab {dlong(X['horizon'])}.")
    else:
        A("Innerhalb des Berichtszeitraums bleibt der Ensemble-Spread des Höchstwerts unter 5 K.")
    A("")
    return out


def clim_section(X):
    days, out = X["days"], []
    A = out.append
    model = {"era5_land": "ERA5-Land", "era5": "ERA5", "": "Open-Meteo Best Match"}.get(X.get("clim_model"), "ERA5")
    rows = [(d, X["clim"]["riederalp"].get(d)) for d in days]
    rows = [(d, c) for d, c in rows if c]
    if not rows:
        A("Klimatologie nicht verfügbar.")
        A("")
        return out
    A(f"Vergleich der prognostizierten Höchstwerte mit {model} 1991–2020, Fenster ±7 Tage um das Datum.")
    A("")
    A("| Tag | Prognose | Mittel | Anomalie | Perzentil |")
    A("|---|---|---|---|---|")
    for d, c in rows:
        A(f"| {dshort(d)} | {deg(X['local']['riederalp'][d]['tmax'])} | {deg(c['mean_tmax'], 1)} | "
          f"{sgn(c['anom_tmax'])} K | {int(round(c['rank_tmax'] * 100))} |")
    A("")
    other = []
    for loc in ("sion", "bern", "lugano"):
        for d in days[:2]:
            c = X["clim"][loc].get(d)
            if c:
                other.append(f"{LOCAL[loc]['name']} {dshort(d)} {sgn(c['anom_tmax'])} K")
    if other:
        A("Zum Vergleich: " + "; ".join(other) + ".")
        A("")
    an = [c["anom_tmax"] for _, c in rows]
    A(f"Mittlere Anomalie über den Berichtszeitraum {sgn(sum(an) / len(an))} K. Einordnung: Die Schweiz hat sich "
      "seit 1991–2020 weiter erwärmt; eine Anomalie von +1 K gegenüber dieser Periode entspricht heute "
      "ungefähr einem normalen Tag. Prognose und Reanalyse sind nicht gegeneinander biaskorrigiert, "
      "in steilem Gelände sind ±1–2 K Unsicherheit in der Anomalie realistisch.")
    A("")
    rc = (X.get("records") or {}).get(days[0])
    if rc:
        t = (f"Dieser Kalendertag auf der Riederalp 1991–2020 (ERA5-Land): höchster Höchstwert {deg(rc['hot'][0], 1)} "
             f"({rc['hot'][1]}), tiefster Tiefstwert {deg(rc['cold'][0], 1)} ({rc['cold'][1]}), nassester Tag "
             f"{n(rc['wet'][0], 1)} mm ({rc['wet'][1]}).")
        s0 = X["local"]["riederalp"].get(days[0])
        if s0 and s0["tmax"] > rc["hot"][0]:
            t += f" Der heutige Höchstwert von {deg(s0['tmax'])} läge über dem Wert dieser 30 Jahre."
        A(t)
        A("")
    out.extend(safe_lines(week_section, X))
    return out


def method_section(X):
    out = []
    A = out.append
    rt = X["runtimes"]
    used = [f"{lab} ({mid}{', Lauf ' + runtime_str(rt[lab]) if lab in rt else ''})" for lab, mid in X["runs"].items()]
    A("**Verwendete Modelle:** " + "; ".join(used) + ".")
    A("")
    A("**Primärmodell:** MeteoSchweiz ICON-CH (CH1 bis +33 h, CH2 bis +120 h), danach ECMWF IFS 9 km. "
      "Tageswerte aus Stundenwerten in Lokalzeit; Open-Meteo skaliert die Temperatur mit einem 90-m-Höhenmodell "
      "auf die angegebene Stationshöhe.")
    A("")
    A("**Diagnosen:** Föhnindex Δp(MSL) Lugano − Zürich, Faustregel ±4 und ±8 hPa. Bise Δp Zürich − Genf, "
      "Faustregel +3 und +5 hPa. Feuchtefluss q·V⊥ in 700 hPa über dem Ossolatal, Normalrichtung 170°. "
      "Inversion aus Mörel (759 m) und Riederalp (1925 m) um 07 und 15 Uhr. Hochnebel aus tiefer Bewölkung "
      "Bern und Napf, Temperaturdifferenz und T850 − T2m. Geostrophischer Wind und Trog-/Rückenachsen aus "
      "Z500 auf einem 10°-Raster (grob). Blockierung nach Tibaldi & Molteni (1990), 80/60/40° N ohne "
      "Breitenvariation. Strahlstrom: Breite des Windmaximums in 250 hPa, 40–70° N.")
    A("")
    A("**Schnee:** Neuschnee ist der Schneefall von Open-Meteo: Bei ICON-CH zählt Niederschlag als Schnee, wo "
      "die modelleigene Schneefallgrenze unter der Stationshöhe liegt, umgerechnet mit festem Faktor 0,7 cm pro mm. "
      "Als Alternative rechnet der Bericht mit der temperaturabhängigen Neuschneedichte nach Anderson (1976), "
      "ρ = 50 + 1,7·(T + 15)^1,5 kg/m³ für T > −15 °C, mit der 2-m-Temperatur zur Schneefallstunde; "
      "Setzung, Wind und Schmelze sind nicht berücksichtigt. Schneefallgrenze: Median der Modellwerte während "
      "Stunden mit mindestens 0,2 mm Niederschlag. Schneehöhe: Modellzustand am Gitterpunkt, keine Messung.")
    A("")
    A("**Karten:** Bodendruckkarte und 850-hPa-Temperatur aus ECMWF IFS 0,25° (Ersatz: GFS, ICON global) auf "
      "einem 3°-Raster (12 UTC), "
      "Isobaren per Marching Squares und Glättung; Hoch und Tief als lokale Extrema des Rasters. Niederschlag Schweiz "
      "aus ICON-CH auf einem 0,2°-Raster, bilinear interpoliert. Wallis und Riederalp: Relief aus Terrain Tiles "
      "(Mapzen, AWS Open Data; Ersatz: Open-Meteo-Höhenmodell), Schneefallgrenze und Nullgradgrenze als Höhenlinien "
      "des Reliefs. Gewässer und Grenzen: Natural Earth.")
    A("")
    A("**Messungen und Verifikation:** Stationswerte von MeteoSchweiz (Open Data, Quelle: MeteoSchweiz) im Umkreis "
      "von 45 km um die Riederalp; Föhnindex wcc006 von MeteoSchweiz. Verifikation mit der Previous-Runs-API von "
      "Open-Meteo: Prognosen, die 1, 2 und 3 Tage vor dem jeweiligen Tag erstellt wurden, gegen gemessene Tageswerte "
      "(Tmax, Tmin, Niederschlag 0–0 UTC). Prognosekonstanz: dieselbe API für die Riederalp.")
    A("")
    A("**Lawinen:** CAAML-Bulletin des SLF für die Warnregion 4214 Riederalp; massgebend ist immer das Bulletin auf "
      "slf.ch. **NAO-Proxy:** tägliches Mittel des Bodendrucks in Lissabon und Reykjavík, je standardisiert gegen "
      "ERA5 1991–2020 (±7 Tage), Differenz nach Hurrell. **Tendenz:** ECMWF ENS bis 15 Tage gegen ERA5-Land.")
    A("")
    A("**Welt und Fun Facts:** Tagesvorhersage (Open-Meteo, beste verfügbare Modelle) für 46 Orte in Ortszeit; "
      "Gipfelwerte statistisch auf die Gipfelhöhe skaliert. Naturgefahren: GDACS-Ereignisliste der letzten 14 Tage, "
      "nur aktive Ereignisse. ENSO: wöchentliche relative Niño-3.4-Anomalie von NOAA CPC. Schweizweite Messwerte: "
      "aktuelle 10-Minuten-Werte aller SwissMetNet-Stationen (MeteoSchweiz).")
    A("")
    A("**MeteoSchweiz-Vergleich:** offizielle Lokalprognose (Open Data, Quelle: MeteoSchweiz) für den Postleitzahlpunkt "
      "Riederalp 3987 und Messstationen, Tageswerte in Lokalzeit; Wettertexte nach dem Piktogrammverzeichnis von "
      "MeteoSchweiz. **Abfluss:** GloFAS (Copernicus Emergency Management Service) über Open-Meteo, Klimatologie aus der "
      "GloFAS-Reanalyse 1991–2020. **Aletsch:** Temperatur auf drei Höhenstufen (statistisch skaliert), Gradtagmodell. "
      "**Luft und Pollen:** CAMS Europa. **Mond:** Rechnung mit mittlerem synodischem Monat, Genauigkeit etwa ±1 Tag.")
    A("")
    out.extend(status_table())
    A("**Prognosesicherheit:** u = 0,6 · min(1, Spread/6 K) + 0,4 · (1 − |2p − 1|), mit 25 % Anteil der "
      "Modellstreuung; hoch < 0,35, mittel < 0,6, sonst gering. Heuristik, nicht verifiziert.")
    A("")
    if X["log"]:
        A("**Nicht verfügbar in diesem Lauf:** " + "; ".join(X["log"]) + ".")
        A("")
    A("**Daten:** Wetterdaten von Open-Meteo.com (CC BY 4.0), Modelle von MeteoSchweiz, ECMWF, DWD, NOAA, "
      "Météo-France und ItaliaMeteo; Reanalyse Copernicus ERA5-Land; Aerosol Copernicus CAMS. "
      "Dies ist kein offizieller Bericht von MeteoSchweiz; Warnungen gibt es auf meteoschweiz.admin.ch.")
    A("")
    A(f"Neu erzeugen: `python3 wetterbericht.py`. Version {VERSION}.")
    return out


# ================================================================ Grafiken
def svg_profile(X):
    d = X["days"][0]
    LD = X["local"]
    rows = []
    for k in PROFILE:
        st = LD[k].get(d)
        rows.append((LOCAL[k]["name"], LOCAL[k]["elev"], st["t07"] if st else None, st["t15"] if st else None))
    temps = [r[2] for r in rows if r[2] is not None] + [r[3] for r in rows if r[3] is not None]
    if len(temps) < 4:
        return None
    r = LD["riederalp"].get(d) or {}
    fzl = r.get("fzl15") or r.get("fzl_mean")
    W, H, L_, R_, T_, B_ = 420, 400, 40, 132, 18, 44
    tlo, thi = math.floor(min(temps + [0]) - 2), math.ceil(max(temps + [0]) + 2)
    zlo, zhi = 500, 3900
    X_ = lambda t: L_ + (t - tlo) / (thi - tlo) * (W - L_ - R_)
    Y_ = lambda z: T_ + (zhi - z) / (zhi - zlo) * (H - T_ - B_)
    p = [f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="Temperaturprofil Mörel bis Jungfraujoch heute">']
    p.append(f'<text x="{L_ - 6}" y="{T_ - 4}" class="ax" text-anchor="end">m</text>')
    for z in range(1000, 4000, 500):
        p.append(f'<line x1="{L_}" x2="{W - R_}" y1="{Y_(z):.1f}" y2="{Y_(z):.1f}" class="grid"/>'
                 f'<text x="{L_ - 6}" y="{Y_(z) + 4:.1f}" class="ax" text-anchor="end">{z}</text>')
    tstep = 5 if thi - tlo > 16 else 2
    for t in range(int(math.ceil(tlo / tstep) * tstep), thi + 1, tstep):
        p.append(f'<text x="{X_(t):.1f}" y="{H - B_ + 18}" class="ax" text-anchor="middle">{n(t)}</text>')
    p.append(f'<line x1="{L_}" x2="{W - R_}" y1="{H - B_}" y2="{H - B_}" class="axl"/>')
    p.append(f'<text x="{(L_ + W - R_) / 2:.0f}" y="{H - 8}" class="ax" text-anchor="middle">°C</text>')
    p.append(f'<line x1="{X_(0):.1f}" x2="{X_(0):.1f}" y1="{T_}" y2="{H - B_}" class="zero"/>')
    base = next(((rr[1], rr[3]) for rr in rows if rr[3] is not None), None)
    if base:
        for rate in (9.8, 6.5):
            z2 = zhi
            t2 = base[1] - rate * (z2 - base[0]) / 1000
            if t2 < tlo:
                z2, t2 = base[0] + (base[1] - tlo) / rate * 1000, tlo
            p.append(f'<line x1="{X_(base[1]):.1f}" y1="{Y_(base[0]):.1f}" x2="{X_(t2):.1f}" y2="{Y_(z2):.1f}" class="ref"/>')
    if fzl and zlo < fzl < zhi:
        p.append(f'<line x1="{L_}" x2="{W - R_}" y1="{Y_(fzl):.1f}" y2="{Y_(fzl):.1f}" class="fzl"/>'
                 f'<text x="{W - R_ - 4}" y="{Y_(fzl) - 5:.1f}" class="fzlt" text-anchor="end">0 °C {hm(fzl)}</text>')
    for col, cls in ((2, "m07"), (3, "m15")):
        pts = [(X_(rr[col]), Y_(rr[1])) for rr in rows if rr[col] is not None]
        if len(pts) >= 2:
            p.append(f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}" class="{cls}"/>')
            p.extend(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" class="{cls}d"/>' for x, y in pts)
    ys = []
    for nm, z, t7, t15 in rows:  # Beschriftungen ohne Überlappung
        y = Y_(z) + 4
        if ys and y > ys[-1] - 30:
            y = ys[-1] - 30
        ys.append(y)
    for (nm, z, t7, t15), y in zip(rows, ys):
        p.append(f'<text x="{W - R_ + 8}" y="{y:.1f}" class="st">{html.escape(nm)}</text>'
                 f'<text x="{W - R_ + 8}" y="{y + 14:.1f}" class="stv">{z} m, {n(t7)} / {n(t15)} °C</text>')
    p.append("</svg>")
    cap = ("Temperaturprofil heute vom Rhonetal bis zum Jungfraujoch: gestrichelt 07 Uhr, durchgezogen 15 Uhr. "
           "Punktiert zum Vergleich der trockenadiabatische Gradient (−9,8 K/km) und die Standardatmosphäre "
           "(−6,5 K/km) ab Mörel. Waagrecht die Nullgradgrenze des Primärmodells.")
    return "\n".join(p), cap


def svg_snow(X):
    S = X.get("series")
    if not S or not valid(S["snowfall_height"] + S["freezing_level_height"]):
        return None
    days = X["days"]
    W, H, L_, R_, T_ = 420, 352, 40, 6, 24
    panel_b, bar_top, bar_b = 236, 262, 318
    zlo, zhi = 500, 4000
    x0 = dt.datetime.combine(days[0], dt.time(0))
    x1 = dt.datetime.combine(days[-1] + dt.timedelta(1), dt.time(0))
    span = (x1 - x0).total_seconds()
    X_ = lambda tt: L_ + (tt - x0).total_seconds() / span * (W - L_ - R_)
    Y_ = lambda z: T_ + (zhi - max(zlo, min(zhi, z))) / (zhi - zlo) * (panel_b - T_)
    bw = (W - L_ - R_) / len(days)
    p = [f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="Schneefallgrenze und Neuschnee Riederalp">']
    for z in range(1000, 4000, 500):
        p.append(f'<line x1="{L_}" x2="{W - R_}" y1="{Y_(z):.1f}" y2="{Y_(z):.1f}" class="grid"/>'
                 f'<text x="{L_ - 4}" y="{Y_(z) + 4:.1f}" class="ax" text-anchor="end">{z}</text>')
    for d in days:
        xa = X_(dt.datetime.combine(d, dt.time(0)))
        p.append(f'<line x1="{xa:.1f}" x2="{xa:.1f}" y1="{T_ - 6}" y2="{bar_b}" class="grid"/>'
                 f'<text x="{xa + bw / 2:.1f}" y="{T_ - 10}" class="ax" text-anchor="middle">{WDS[d.weekday()]} {d.day}.</text>')
    if X.get("ens_sfh"):
        _lab, et, eq = X["ens_sfh"]
        good = [i for i in range(len(et)) if eq[10][i] is not None and eq[90][i] is not None and x0 <= et[i] <= x1]
        if len(good) > 2:
            top = " ".join(f"{X_(et[i]):.1f},{Y_(eq[90][i]):.1f}" for i in good)
            bot = " ".join(f"{X_(et[i]):.1f},{Y_(eq[10][i]):.1f}" for i in reversed(good))
            p.append(f'<polygon points="{top} {bot}" class="b1"/>')
    for k in ("moerel", "riederalp", "moosfluh"):
        z = LOCAL[k]["elev"]
        cls = "elev1" if k == "riederalp" else "elev"
        p.append(f'<line x1="{L_}" x2="{W - R_}" y1="{Y_(z):.1f}" y2="{Y_(z):.1f}" class="{cls}"/>'
                 f'<text x="{W - R_ - 3}" y="{Y_(z) - 4:.1f}" class="ax" text-anchor="end">{LOCAL[k]["name"]}</text>')
    t = S["t"]
    xs = [X_(tt) for tt in t]
    inside = [x0 <= tt <= x1 for tt in t]
    fz = [v if ok else None for v, ok in zip(S["freezing_level_height"], inside)]
    for seg in _segments(xs, fz):
        p.append(f'<polyline points="{" ".join(f"{x:.1f},{Y_(y):.1f}" for x, y in seg)}" class="fzl"/>')
    sf = [v if ok else None for v, ok in zip(S["snowfall_height"], inside)]
    for seg in _segments(xs, sf):
        p.append(f'<polyline points="{" ".join(f"{x:.1f},{Y_(y):.1f}" for x, y in seg)}" class="sfh"/>')
    wet = [v if (ok and pr is not None and pr >= 0.2) else None
           for v, ok, pr in zip(S["snowfall_height"], inside, S["precipitation"])]
    for seg in _segments(xs, wet):
        if len(seg) == 1:
            p.append(f'<circle cx="{seg[0][0]:.1f}" cy="{Y_(seg[0][1]):.1f}" r="2.5" class="sfhwd"/>')
        else:
            p.append(f'<polyline points="{" ".join(f"{x:.1f},{Y_(y):.1f}" for x, y in seg)}" class="sfhw"/>')
    p.append(f'<line x1="{L_}" x2="{W - R_}" y1="{bar_b}" y2="{bar_b}" class="axl"/>')
    LD = X["local"]["riederalp"]
    hmax = max([LD[d]["snow"] for d in days if LD.get(d)] + [5])
    for d in days:
        st = LD.get(d)
        if not st:
            continue
        xa = X_(dt.datetime.combine(d, dt.time(0)))
        h = st["snow"] / hmax * (bar_b - bar_top - 14)
        if h > 0.5:
            p.append(f'<rect x="{xa + bw * 0.22:.1f}" y="{bar_b - h:.1f}" width="{bw * 0.56:.1f}" height="{h:.1f}" class="snowbar"/>')
        p.append(f'<text x="{xa + bw / 2:.1f}" y="{bar_b - h - 4:.1f}" class="ax" text-anchor="middle">{n(st["snow"])}</text>')
        if st.get("sd_end") is not None:
            p.append(f'<text x="{xa + bw / 2:.1f}" y="{bar_b + 16:.1f}" class="stv" text-anchor="middle">{n(st["sd_end"])}</text>')
    p.append("</svg>")
    ens = f", hellblaues Band {X['ens_sfh'][0]} 10–90 %" if X.get("ens_sfh") else ""
    cap = ("Schneefallgrenze über der Riederalp (blau, kräftig während Niederschlag" + ens +
           ") und Nullgradgrenze (braun gestrichelt), Höhen in m. Säulen: Neuschnee pro Tag im Hauptlauf in cm; "
           "Zahlen darunter: Modellschneehöhe am Abend in cm.")
    return "\n".join(p), cap


def _segments(xs, ys):
    seg, cur = [], []
    for x, y in zip(xs, ys):
        if y is None:
            if cur:
                seg.append(cur)
            cur = []
        else:
            cur.append((x, y))
    if cur:
        seg.append(cur)
    return seg


def svg_fan(X):
    if not X["ens_hourly"]:
        return None
    label, t, q = X["ens_hourly"]
    days = X["days"]
    W, H, L_, R_, T_ = 420, 330, 30, 6, 24
    panel_b, bar_top, bar_b = 236, 268, 322
    x0 = dt.datetime.combine(days[0], dt.time(0))
    x1 = dt.datetime.combine(days[-1] + dt.timedelta(1), dt.time(0))
    span = (x1 - x0).total_seconds()
    X_ = lambda tt: L_ + (tt - x0).total_seconds() / span * (W - L_ - R_)
    allv = valid(q[10]) + valid(q[90])
    for _, (tt, TT) in X["det_hourly"].items():
        allv += valid(TT)
    if not allv:
        return None
    ylo, yhi = math.floor(min(allv)) - 1, math.ceil(max(allv)) + 1
    Y_ = lambda v: T_ + (yhi - v) / (yhi - ylo) * (panel_b - T_)
    xs = [X_(tt) for tt in t]
    bw = (W - L_ - R_) / len(days)
    p = [f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="Ensemble-Temperatur Riederalp">']
    step = 2 if yhi - ylo <= 12 else 5
    for v in range(int(math.ceil(ylo / step) * step), yhi + 1, step):
        p.append(f'<line x1="{L_}" x2="{W - R_}" y1="{Y_(v):.1f}" y2="{Y_(v):.1f}" class="{"zero" if v == 0 else "grid"}"/>'
                 f'<text x="{L_ - 4}" y="{Y_(v) + 4:.1f}" class="ax" text-anchor="end">{n(v)}</text>')
    for d in days:
        xa = X_(dt.datetime.combine(d, dt.time(0)))
        p.append(f'<line x1="{xa:.1f}" x2="{xa:.1f}" y1="{T_ - 6}" y2="{bar_b}" class="grid"/>'
                 f'<text x="{xa + bw / 2:.1f}" y="{T_ - 10}" class="ax" text-anchor="middle">{WDS[d.weekday()]} {d.day}.</text>')
    for lo, hi, cls in ((10, 90, "b1"), (25, 75, "b2")):
        good = [i for i in range(len(xs)) if q[lo][i] is not None and q[hi][i] is not None and x0 <= t[i] <= x1]
        if len(good) > 2:
            top = " ".join(f"{xs[i]:.1f},{Y_(q[hi][i]):.1f}" for i in good)
            bot = " ".join(f"{xs[i]:.1f},{Y_(q[lo][i]):.1f}" for i in reversed(good))
            p.append(f'<polygon points="{top} {bot}" class="{cls}"/>')
    for seg in _segments(xs, q[50]):
        p.append(f'<polyline points="{" ".join(f"{x:.1f},{Y_(y):.1f}" for x, y in seg)}" class="med"/>')
    dets = []
    for k, (lab, (tt, TT)) in enumerate(X["det_hourly"].items()):
        if not TT:
            continue
        dets.append(lab)
        pts = [(X_(a), b) for a, b in zip(tt, TT) if x0 <= a <= x1]
        for seg in _segments([a for a, _ in pts], [b for _, b in pts]):
            p.append(f'<polyline points="{" ".join(f"{x:.1f},{Y_(y):.1f}" for x, y in seg)}" class="det{k}"/>')
    ref = X["ens"].get(label, {}).get("riederalp", {})
    p.append(f'<line x1="{L_}" x2="{W - R_}" y1="{bar_b}" y2="{bar_b}" class="axl"/>')
    for d in days:
        st = ref.get(d)
        if not st or st.get("p1") is None:
            continue
        xa = X_(dt.datetime.combine(d, dt.time(0))) + bw * 0.22
        h = max(1.0, st["p1"] * (bar_b - bar_top - 16))
        p.append(f'<rect x="{xa:.1f}" y="{bar_b - h:.1f}" width="{bw * 0.56:.1f}" height="{h:.1f}" class="bar"/>'
                 f'<text x="{xa + bw * 0.28:.1f}" y="{bar_b - h - 4:.1f}" class="ax" text-anchor="middle">{pct(st["p1"])}</text>')
    p.append("</svg>")
    names = {0: "rot", 1: "braun gestrichelt"}
    cap = (f"Temperatur Riederalp, stündlich: Bänder {label} (10–90 % hell, 25–75 % dunkel), blaue Linie Median"
           + "".join(f", {lab} {names.get(k, '')}" for k, lab in enumerate(dets))
           + f". Säulen unten: Wahrscheinlichkeit für mindestens 1 mm Niederschlag pro Tag ({label}).")
    return "\n".join(p), cap


# ================================================================ Messungen, Verifikation, Lawinen, NAO
SMN_BASE = "https://data.geo.admin.ch/ch.meteoschweiz.ogd-smn"
SMN_STAC = "https://data.geo.admin.ch/api/stac/v1/collections/ch.meteoschweiz.ogd-smn/items/{}"
API_PREV = "https://previous-runs-api.open-meteo.com/v1/forecast"
SLF_URLS = ("https://aws.slf.ch/api/bulletin/caaml/de/json",
            "https://aws.slf.ch/api/bulletin/caaml/json?lang=de")
SLF_REGION = "4214"  # SLF-Warnregion Riederalp
OBS_RADIUS_KM, OBS_MAX, VERIF_DAYS = 45, 10, 14
NAO_PTS = {"lis": _p("Lissabon", 38.72, -9.14), "rey": _p("Reykjavík", 64.15, -21.94)}
DANGER = {"low": (1, "gering"), "moderate": (2, "mässig"), "considerable": (3, "erheblich"),
          "high": (4, "gross"), "very_high": (5, "sehr gross")}
PROBLEMS = {"new_snow": "Neuschnee", "wind_slab": "Triebschnee", "persistent_weak_layers": "Altschnee",
            "wet_snow": "Nassschnee", "gliding_snow": "Gleitschnee", "cornices": "Wechten",
            "favourable_situation": "günstige Situation", "no_distinct_avalanche_problem": "kein ausgeprägtes Problem"}


def km(la1, lo1, la2, lo2):
    p1, p2 = math.radians(la1), math.radians(la2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2
    return 12742 * math.asin(math.sqrt(min(1.0, a)))


def parse_smn(text):
    """MeteoSchweiz-OGD-CSV (Semikolon, Zeitstempel dd.mm.yyyy HH:MM in UTC)."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    head = [h.strip() for h in lines[0].split(";")]
    it = head.index("reference_timestamp")
    cols = [h for h in head if h not in ("station_abbr", "reference_timestamp")]
    out = {"t": [], **{h: [] for h in cols}}
    for ln in lines[1:]:
        c = [x.strip() for x in ln.split(";")]
        c += [""] * (len(head) - len(c))
        try:
            t = dt.datetime.strptime(c[it], "%d.%m.%Y %H:%M")
        except ValueError:
            continue
        out["t"].append(t)
        for h, v in zip(head, c):
            if h in out and h != "t":
                try:
                    out[h].append(float(v) if v else None)
                except ValueError:
                    out[h].append(None)
    return out


def smn_get(abbr, gran, upd):
    a = abbr.lower()
    name = f"ogd-smn_{a}_{gran}_{upd}.csv"
    try:
        return parse_smn(fetch_bytes(f"{SMN_BASE}/{a}/{name}").decode("cp1252", "replace"))
    except ApiError:
        item = fetch_json(SMN_STAC.format(a), retries=1, quiet=True)
        assets = item.get("assets", {})
        href = (assets.get(name) or {}).get("href") or next(
            (v.get("href") for k, v in assets.items() if k.endswith(f"_{gran}_{upd}.csv")), None)
        if not href:
            raise ApiError(f"{name} nicht gefunden")
        return parse_smn(fetch_bytes(href).decode("cp1252", "replace"))


def smn_stations(cache_dir):
    path = os.path.join(cache_dir, "smn_stationen_v1.json")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 30 * 86400:
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    text = fetch_bytes(f"{SMN_BASE}/ogd-smn_meta_stations.csv").decode("cp1252", "replace")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    head = lines[0].split(";")
    need = ("station_abbr", "station_name", "station_height_masl",
            "station_coordinates_wgs84_lat", "station_coordinates_wgs84_lon")
    if not all(k in head for k in need):
        raise ApiError("Stationsliste in unbekanntem Format")
    ix = {k: head.index(k) for k in need}
    out = []
    for ln in lines[1:]:
        c = ln.split(";")
        try:
            out.append({"abbr": c[ix["station_abbr"]], "name": c[ix["station_name"]],
                        "elev": float(c[ix["station_height_masl"]]),
                        "lat": float(c[ix["station_coordinates_wgs84_lat"]]),
                        "lon": float(c[ix["station_coordinates_wgs84_lon"]])})
        except (ValueError, IndexError):
            continue
    os.makedirs(cache_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f)
    return out


def collect_obs(cache_dir):
    O = {"stations": [], "now": {}, "recent": {}, "prev": None, "prev_model": None}
    print("Lade Messwerte MeteoSchweiz …", file=sys.stderr)
    try:
        st = smn_stations(cache_dir)
    except (ApiError, OSError) as e:
        log(f"MeteoSchweiz-Stationsliste ({e})")
        status("MeteoSchweiz-Messwerte", False, e)
        return O
    R = LOCAL["riederalp"]
    near = sorted(({**s, "km": km(R["lat"], R["lon"], s["lat"], s["lon"])} for s in st), key=lambda s: s["km"])
    O["stations"] = [s for s in near if s["km"] <= OBS_RADIUS_KM][:OBS_MAX]
    tasks = [(s["abbr"], gran, upd) for s in O["stations"] for gran, upd in (("h", "now"), ("d", "recent"))]

    def get(t):
        try:
            return t, smn_get(*t), None
        except (ApiError, ValueError) as e:
            return t, None, e

    nok = 0
    for (a, gran, upd), res, err in pmap(get, tasks, workers=6):
        if res is not None:
            O["now" if upd == "now" else "recent"][a] = res
            nok += 1
        else:
            log(f"MeteoSchweiz {a} {gran}_{upd} ({err})")
    status("MeteoSchweiz-Messwerte", nok > 0, f"{nok} von {len(tasks)} Dateien")
    if O["stations"]:
        print("Lade frühere Modellläufe für die Verifikation …", file=sys.stderr)
        tab = {s["abbr"]: _p(s["name"], s["lat"], s["lon"], int(round(s["elev"]))) for s in O["stations"]}
        keys = list(tab)
        today = dt.datetime.now(dt.timezone.utc).date()
        vdays = [today - dt.timedelta(k) for k in range(VERIF_DAYS + 1, -2, -1)]
        hv = ",".join(f"{v}_previous_day{k}" for v in ("temperature_2m", "precipitation") for k in (1, 2, 3))
        params = {**coords(keys, tab), "hourly": hv + ",temperature_2m", "timezone": "GMT",
                  "past_days": VERIF_DAYS + 1, "forecast_days": 1}
        O["prev_model"], O["prev"] = fetch_model(API_PREV, "Verifikation (frühere Läufe)",
                                                 ["meteoswiss_icon_seamless", "ecmwf_ifs", "best_match"],
                                                 params, keys, vdays)
    return O


def _col(rec, name, i):
    arr = rec.get(name) or []
    return arr[i] if i < len(arr) else None


def obs_summary(o, now_utc):
    if not o or not o.get("t"):
        return None
    t0 = now_utc - dt.timedelta(hours=24)
    idx = [i for i, t in enumerate(o["t"]) if t > t0]

    def last(name):
        arr = o.get(name) or []
        for i in range(min(len(arr), len(o["t"])) - 1, -1, -1):
            if arr[i] is not None:
                return arr[i], o["t"][i]
        return None, None

    def over(name, fn):
        v = valid([_col(o, name, i) for i in idx])
        return fn(v) if v else None

    T, tT = last("tre200h0")
    fo = over("wcc006h0", sum)
    return {"T": T, "tT": tT, "tmin": over("tre200hn", min) if o.get("tre200hn") else over("tre200h0", min),
            "tmax": over("tre200hx", max) if o.get("tre200hx") else over("tre200h0", max),
            "prec": over("rre150h0", sum), "sd": last("htoauths")[0], "gust": over("fu3010h1", max),
            "foehn_h": fo / 60 if fo is not None else None, "n": len(idx)}


def verify(O):
    """Vorlauf 1–3 Tage: Tageswerte der früheren Läufe gegen Messungen (UTC-Tage)."""
    if not O.get("prev"):
        return None
    today = dt.datetime.now(dt.timezone.utc).date()
    R = {L: {"tx": [], "tn": [], "pr": []} for L in (1, 2, 3)}
    for s in O["stations"]:
        a = s["abbr"]
        rec, obs = O["prev"].get(a), O["recent"].get(a)
        if not rec or not obs:
            continue
        od = {t.date(): i for i, t in enumerate(obs["t"])}
        for d, ii in rec["idx"].items():
            if d >= today or d not in od:
                continue
            j = od[d]
            otx, otn, opr = _col(obs, "tre200dx", j), _col(obs, "tre200dn", j), _col(obs, "rka150d0", j)
            for L in (1, 2, 3):
                arr = rec["v"].get(f"temperature_2m_previous_day{L}") or []
                T = valid([arr[i] for i in ii if i < len(arr)])
                if len(T) >= 20:
                    if otx is not None:
                        R[L]["tx"].append((max(T) - otx, s["elev"]))
                    if otn is not None:
                        R[L]["tn"].append((min(T) - otn, s["elev"]))
                parr = rec["v"].get(f"precipitation_previous_day{L}") or []
                hrs = [dt.datetime(d.year, d.month, d.day) + dt.timedelta(hours=h) for h in range(1, 25)]
                pv = [parr[rec["tidx"][t]] if t in rec["tidx"] and rec["tidx"][t] < len(parr) else None for t in hrs]
                if opr is not None and len(valid(pv)) >= 20:
                    R[L]["pr"].append((sum(valid(pv)), opr))
    out = {}
    for L, r in R.items():
        def es(lst):
            e = [x[0] for x in lst]
            return {"bias": sum(e) / len(e), "mae": sum(abs(x) for x in e) / len(e), "n": len(e)} if e else None
        hits = sum(1 for f, o in r["pr"] if f >= 1 and o >= 1)
        miss = sum(1 for f, o in r["pr"] if f < 1 and o >= 1)
        fa = sum(1 for f, o in r["pr"] if f >= 1 and o < 1)
        out[L] = {"tx": es(r["tx"]), "tn": es(r["tn"]), "n_pr": len(r["pr"]),
                  "pod": hits / (hits + miss) if hits + miss else None,
                  "far": fa / (hits + fa) if hits + fa else None,
                  "tn_low": es([x for x in r["tn"] if x[1] < 1200]),
                  "tn_high": es([x for x in r["tn"] if x[1] >= 2000])}
    # Nowcast: aktueller Lauf heute gegen stündliche Messungen
    errs = []
    for s in O["stations"]:
        rec, obs = O["prev"].get(s["abbr"]), O["now"].get(s["abbr"])
        if not rec or not obs:
            continue
        arr = rec["v"].get("temperature_2m") or []
        for t, v in zip(obs["t"], obs.get("tre200h0") or []):
            i = rec["tidx"].get(t)
            if v is not None and t.date() == today and i is not None and i < len(arr) and arr[i] is not None:
                errs.append(arr[i] - v)
    out["now"] = {"bias": sum(errs) / len(errs), "mae": sum(abs(e) for e in errs) / len(errs),
                  "n": len(errs)} if errs else None
    return out


def consistency(rec, days):
    out = {}
    for d in days[:5]:
        ii = rec["idx"].get(d, [])
        row = {}
        for L in (0, 1, 2, 3):
            vt = "temperature_2m" if L == 0 else f"temperature_2m_previous_day{L}"
            vp = "precipitation" if L == 0 else f"precipitation_previous_day{L}"
            T = valid([(rec["v"].get(vt) or [None] * len(rec["t"]))[i] for i in ii])
            P = valid([(rec["v"].get(vp) or [None] * len(rec["t"]))[i] for i in ii])
            if len(T) >= 20:
                row[L] = (max(T), sum(P) if len(P) >= 20 else None)
        if row:
            out[d] = row
    return out


def find_bulletin(obj, region=SLF_REGION, depth=0):
    if depth > 8:
        return None
    if isinstance(obj, dict):
        regs = obj.get("regions")
        if isinstance(regs, list):
            for r in regs:
                rid = str((r or {}).get("regionID") or (r or {}).get("id") or "") if isinstance(r, dict) else str(r)
                if rid.endswith(region) and ("CH" in rid or rid == region):
                    return obj
        for v in obj.values():
            b = find_bulletin(v, region, depth + 1)
            if b:
                return b
    elif isinstance(obj, list):
        for v in obj:
            b = find_bulletin(v, region, depth + 1)
            if b:
                return b
    return None


def collect_slf():
    print("Lade Lawinenbulletin SLF …", file=sys.stderr)
    reached = False
    for url in SLF_URLS:
        try:
            data = json.loads(fetch_bytes(url, timeout=30).decode("utf-8"))
        except (ApiError, ValueError, UnicodeDecodeError):
            continue
        reached = True
        b = find_bulletin(data)
        if b:
            status("SLF-Lawinenbulletin", True, "Region 4214")
            return {"status": "ok", "bulletin": b}
    status("SLF-Lawinenbulletin", reached, "kein Bulletin für 4214" if reached else "nicht erreichbar")
    return {"status": "keins" if reached else "fehler"}


def elev_text(e):
    if not isinstance(e, dict):
        return ""
    f = lambda x: "der Waldgrenze" if str(x) == "treeline" else f"{x} m"
    lo, hi = e.get("lowerBound"), e.get("upperBound")
    if lo and hi:
        return f"zwischen {f(lo)} und {f(hi)}"
    return f"oberhalb {f(lo)}" if lo else (f"unterhalb {f(hi)}" if hi else "")


def slf_text(S):
    if not S or S["status"] == "fehler":
        return "Das Lawinenbulletin des SLF war bei der Erstellung nicht abrufbar; aktuell auf slf.ch."
    if S["status"] == "keins":
        return ("Für die SLF-Warnregion Riederalp (4214) liegt derzeit kein Lawinenbulletin vor. Das SLF publiziert "
                "das tägliche Bulletin in der Regel ab dem ersten bedeutenden Schneefall im Herbst bis in den Frühling.")
    b = S["bulletin"]
    rat = []
    for r in b.get("dangerRatings") or []:
        lv = DANGER.get(r.get("mainValue"))
        if lv:
            rat.append((lv, elev_text(r.get("elevation")), r.get("validTimePeriod")))
    if not rat:
        return "Lawinenbulletin für die Region Riederalp (4214) vorhanden, aber ohne Gefahrenstufe (z. B. kein Schnee)."
    top = max(rat, key=lambda x: x[0][0])
    t = f"Lawinengefahr in der SLF-Warnregion Riederalp (4214): Stufe {top[0][0]}, {top[0][1]}"
    t += f" {top[1]}." if top[1] else "."
    others = [f"Stufe {lv[0]} {el}".strip() for lv, el, _ in rat if (lv, el) != (top[0], top[1])]
    if others:
        t += " Daneben " + join_de(others) + "."
    pr = []
    for p in b.get("avalancheProblems") or []:
        nm = PROBLEMS.get(p.get("problemType"), p.get("problemType"))
        asp = p.get("aspects") or []
        bits = [x for x in (elev_text(p.get("elevation")), "Expositionen " + ", ".join(asp) if asp else "") if x]
        pr.append(nm + (f" ({'; '.join(bits)})" if bits else ""))
    if pr:
        t += " Lawinenprobleme: " + "; ".join(pr) + "."
    vt = (b.get("validTime") or {}).get("endTime")
    if vt:
        try:
            e = dt.datetime.fromisoformat(str(vt).replace("Z", "+00:00")).astimezone(TZ)
            t += f" Gültig bis {dlong(e.date())}, {e:%H:%M} Uhr."
        except ValueError:
            pass
    return t + " Quelle: SLF (slf.ch)."


def slf_level(S):
    if not S or S.get("status") != "ok":
        return None
    lv = [DANGER[r.get("mainValue")][0] for r in (S["bulletin"].get("dangerRatings") or []) if r.get("mainValue") in DANGER]
    return max(lv) if lv else None


def archive_months(cache_dir, fname, table, keys, daily_vars, months, cands, tz="GMT", api=None):
    path = os.path.join(cache_dir, fname)
    cache = {"model": None, "months": {}}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                cache = json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    for mth in months:
        if str(mth) in cache["months"]:
            continue
        print(f"  {fname}: Monat {mth}, 30 Jahre (einmalig) …", file=sys.stderr)
        block = {k: {"date": [], **{v: [] for v in daily_vars}} for k in keys}
        ok = True
        for y in range(1991, 2021):
            last = calendar.monthrange(y, mth)[1]
            params = {**coords(keys, table, elev=False), "start_date": f"{y}-{mth:02d}-01",
                      "end_date": f"{y}-{mth:02d}-{last:02d}", "timezone": tz, "daily": ",".join(daily_vars)}
            data, err = None, None
            for c in ([cache["model"]] if cache.get("model") is not None else cands):
                p = dict(params)
                if c:
                    p["models"] = c
                try:
                    rate_budget(len(keys) * 3)
                    data = fetch_json(api or API_ARCH, p)
                    cache["model"] = c
                    break
                except ApiError as e:
                    err = e
            if data is None:
                log(f"{fname} Monat {mth} ({err})")
                ok = False
                break
            for k, it in zip(keys, data if isinstance(data, list) else [data]):
                dd = it.get("daily") or {}
                block[k]["date"] += dd.get("time", [])
                for v in daily_vars:
                    block[k][v] += dd.get(v, [None] * len(dd.get("time", [])))
            time.sleep(PAUSE)
        if ok:
            cache["months"][str(mth)] = block
            os.makedirs(cache_dir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cache, f)
    return cache


def window_vals(cache, loc, d, var, half=7):
    target = dt.date(2001, d.month, 28 if (d.month == 2 and d.day == 29) else d.day).timetuple().tm_yday
    out = []
    for block in cache.get("months", {}).values():
        b = block.get(loc)
        if not b:
            continue
        for ds, x in zip(b["date"], b.get(var, [])):
            if x is None:
                continue
            dd = dt.date.fromisoformat(ds)
            if dd.month == 2 and dd.day == 29:
                continue
            dist = abs(dt.date(2001, dd.month, dd.day).timetuple().tm_yday - target)
            if min(dist, 365 - dist) <= half:
                out.append(x)
    return out


def nao_proxy(D, X, cache):
    """Stationsbasierter NAO-Index (Hurrell-Art): standardisierte Bodendruckanomalie Lissabon minus Reykjavík."""
    up = D.get("up") or {}
    L, R = up.get("c_lissabon"), up.get("c_reykjavik")
    if not L or not R or not cache:
        return {}
    out = {}
    for d in X["days"]:
        vals = []
        for rec, k in ((L, "lis"), (R, "rey")):
            ii = rec["idx"].get(d, [])
            p = valid([(rec["v"].get("pressure_msl") or [None] * len(rec["t"]))[i] for i in ii])
            cl = window_vals(cache, k, d, "pressure_msl_mean")
            if len(p) < 20 or len(cl) < 100:
                break
            mu = sum(cl) / len(cl)
            sd = math.sqrt(sum((x - mu) ** 2 for x in cl) / (len(cl) - 1))
            vals.append((sum(p) / len(p) - mu) / sd)
        if len(vals) == 2:
            out[d] = vals[0] - vals[1]
    return out


def week_trend(rec, clim, X):
    """ECMWF ENS bis 15 Tage: Wochenmittel des Höchstwerts und Wochensumme Niederschlag gegen 1991–2020."""
    today = X["days"][0]
    weeks = [("Woche 1", [today + dt.timedelta(k) for k in range(1, 8)]),
             ("Woche 2", [today + dt.timedelta(k) for k in range(8, 15)])]
    index_days(rec, [today + dt.timedelta(k) for k in range(16)])
    T, P = members(rec, "temperature_2m"), members(rec, "precipitation")
    out = []
    for lab, wd in weeks:
        cx = [window_vals(clim, "riederalp", d, "tmax") for d in wd]
        cp = [window_vals(clim, "riederalp", d, "prec") for d in wd]
        if any(len(c) < 100 for c in cx):
            continue
        clim_tx = sum(sum(c) / len(c) for c in cx) / len(wd)
        clim_pr = sum(sum(c) / len(c) for c in cp) if all(cp) else None
        an, pr = [], []
        for m in range(len(T)):
            txs = []
            for d in wd:
                v = valid([T[m][i] for i in rec["idx"].get(d, []) if i < len(T[m])])
                if len(v) < 20:
                    break
                txs.append(max(v))
            if len(txs) == len(wd):
                an.append(sum(txs) / len(txs) - clim_tx)
                if m < len(P):
                    pv = valid([P[m][i] for d in wd for i in rec["idx"].get(d, []) if i < len(P[m])])
                    pr.append(sum(pv))
        if len(an) < 10:
            continue
        out.append({"label": lab, "from": wd[0], "to": wd[-1], "an": qstats(an), "p_warm": frac(an, 0.0),
                    "p_w2": frac(an, 2.0), "p_c2": sum(1 for a in an if a <= -2) / len(an),
                    "clim_tx": clim_tx, "clim_pr": clim_pr, "pr": qstats(pr),
                    "p_wet": (sum(1 for x in pr if x > clim_pr) / len(pr)) if (pr and clim_pr) else None,
                    "n": len(an)})
    return out


# ================================================================ Welt und Fun Facts
WORLD = {  # Schlüssel: (Name, Breite, Länge, Region)
    "kairo": ("Kairo", 30.04, 31.24, "Afrika"), "lagos": ("Lagos", 6.52, 3.38, "Afrika"),
    "nairobi": ("Nairobi", -1.29, 36.82, "Afrika"), "kapstadt": ("Kapstadt", -33.92, 18.42, "Afrika"),
    "timbuktu": ("Timbuktu", 16.77, -3.01, "Afrika"), "kinshasa": ("Kinshasa", -4.32, 15.31, "Afrika"),
    "addis": ("Addis Abeba", 9.03, 38.74, "Afrika"),
    "delhi": ("Delhi", 28.61, 77.21, "Asien"), "mumbai": ("Mumbai", 19.08, 72.88, "Asien"),
    "peking": ("Peking", 39.90, 116.40, "Asien"), "tokio": ("Tokio", 35.68, 139.69, "Asien"),
    "singapur": ("Singapur", 1.35, 103.82, "Asien"), "riad": ("Riad", 24.71, 46.68, "Asien"),
    "dubai": ("Dubai", 25.20, 55.27, "Asien"), "jakutsk": ("Jakutsk", 62.03, 129.73, "Asien"),
    "oimjakon": ("Oimjakon", 63.46, 142.79, "Asien"), "lhasa": ("Lhasa", 29.65, 91.17, "Asien"),
    "bangkok": ("Bangkok", 13.76, 100.50, "Asien"), "cherrapunji": ("Cherrapunji", 25.27, 91.73, "Asien"),
    "ahvaz": ("Ahvaz", 31.32, 48.67, "Asien"),
    "sydney": ("Sydney", -33.87, 151.21, "Ozeanien"), "perth": ("Perth", -31.95, 115.86, "Ozeanien"),
    "alice": ("Alice Springs", -23.70, 133.88, "Ozeanien"), "auckland": ("Auckland", -36.85, 174.76, "Ozeanien"),
    "honolulu": ("Honolulu", 21.31, -157.86, "Ozeanien"),
    "newyork": ("New York", 40.71, -74.01, "Nordamerika"), "chicago": ("Chicago", 41.88, -87.63, "Nordamerika"),
    "losangeles": ("Los Angeles", 34.05, -118.24, "Nordamerika"),
    "deathvalley": ("Death Valley", 36.46, -116.87, "Nordamerika"),
    "mexiko": ("Mexiko-Stadt", 19.43, -99.13, "Nordamerika"), "anchorage": ("Anchorage", 61.22, -149.90, "Nordamerika"),
    "utqiagvik": ("Utqiaġvik", 71.29, -156.79, "Nordamerika"), "miami": ("Miami", 25.76, -80.19, "Nordamerika"),
    "toronto": ("Toronto", 43.65, -79.38, "Nordamerika"),
    "rio": ("Rio de Janeiro", -22.91, -43.17, "Südamerika"), "buenosaires": ("Buenos Aires", -34.60, -58.38, "Südamerika"),
    "lima": ("Lima", -12.05, -77.04, "Südamerika"), "manaus": ("Manaus", -3.12, -60.02, "Südamerika"),
    "ushuaia": ("Ushuaia", -54.80, -68.30, "Südamerika"), "lapaz": ("La Paz", -16.50, -68.15, "Südamerika"),
    "atacama": ("San Pedro de Atacama", -22.91, -68.20, "Südamerika"),
    "wostok": ("Wostok", -78.46, 106.84, "Polargebiete"), "suedpol": ("Südpol-Station", -89.99, 0.0, "Polargebiete"),
    "mcmurdo": ("McMurdo", -77.85, 166.67, "Polargebiete"), "summit": ("Summit (Grönland)", 72.58, -38.46, "Polargebiete"),
    "longyearbyen": ("Longyearbyen", 78.22, 15.65, "Polargebiete"),
}
PEAKS = {"everest": _p("Mount Everest", 27.9881, 86.9250, 8848), "montblanc": _p("Mont Blanc", 45.8326, 6.8652, 4806)}
WORLD_DAILY = ("weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,snowfall_sum,"
               "wind_gusts_10m_max,daylight_duration")
API_GDACS = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
CPC_NINO = "https://www.cpc.ncep.noaa.gov/data/indices/rel_wksst9120.txt"
VQHA80 = "https://data.geo.admin.ch/ch.meteoschweiz.messwerte-aktuell/VQHA80.csv"
GDACS_TYPES = {"TC": "Tropischer Wirbelsturm", "FL": "Überschwemmung", "DR": "Dürre", "WF": "Waldbrand"}
MON = {m: i + 1 for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"))}


ORT_IN = {"Death Valley": "im Death Valley", "Südpol-Station": "an der Südpol-Station",
          "Summit (Grönland)": "auf Summit (Grönland)", "Wostok": "in Wostok (Antarktis)"}


def ort_in(name):
    return ORT_IN.get(name, f"in {name}")


def wmo_text(c):
    if c is None:
        return "–"
    c = int(c)
    for lo, hi, t in ((0, 0, "klar"), (1, 1, "überwiegend klar"), (2, 2, "teils bewölkt"), (3, 3, "bedeckt"),
                      (45, 48, "Nebel"), (51, 57, "Niesel"), (61, 67, "Regen"), (71, 77, "Schnee"),
                      (80, 82, "Schauer"), (85, 86, "Schneeschauer"), (95, 99, "Gewitter")):
        if lo <= c <= hi:
            return t
    return "–"


def pmap(fn, items, workers=6):
    """Netzwerkzugriffe parallel (I/O-gebunden); Reihenfolge bleibt erhalten."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(fn, items))


def collect_world():
    W = {"world": None, "peaks": None, "gdacs": None, "enso": None}
    print("Lade Weltwetter …", file=sys.stderr)
    tab = {k: _p(v[0], v[1], v[2]) for k, v in WORLD.items()}
    keys = list(tab)
    _m, W["world"] = fetch_model(API_FC, "Weltwetter", ["best_match"],
                                 {**coords(keys, tab, elev=False), "daily": WORLD_DAILY, "timezone": "auto",
                                  "forecast_days": 2}, keys, None, parser=parse_daily)
    _m, W["peaks"] = fetch_model(API_FC, "Gipfelwetter", ["best_match"],
                                 {**coords(list(PEAKS), PEAKS), "daily": "temperature_2m_max,temperature_2m_min,"
                                  "wind_gusts_10m_max", "timezone": "auto", "forecast_days": 1},
                                 list(PEAKS), None, parser=parse_daily)
    print("Lade Naturgefahren (GDACS) …", file=sys.stderr)
    try:
        t = dt.date.today()
        url = (f"{API_GDACS}?eventlist=TC;FL;DR;WF&fromdate={t - dt.timedelta(14)}&todate={t}"
               "&alertlevel=green;orange;red")
        W["gdacs"] = parse_gdacs(json.loads(fetch_bytes(url, timeout=60).decode("utf-8")))
        status("GDACS-Naturgefahren", True, f"{len(W['gdacs'])} aktive Ereignisse")
    except (ApiError, ValueError, UnicodeDecodeError) as e:
        log(f"GDACS ({e})")
        status("GDACS-Naturgefahren", False, e)
    print("Lade ENSO-Index (NOAA CPC) …", file=sys.stderr)
    try:
        W["enso"] = parse_nino(fetch_bytes(CPC_NINO, timeout=30).decode("latin-1"))
        status("NOAA ENSO-Index", True, f"Woche {W['enso'][-1][0]:%d.%m.}")
    except (ApiError, ValueError) as e:
        log(f"ENSO-Index ({e})")
        status("NOAA ENSO-Index", False, e)
    return W


def parse_gdacs(data):
    def first_xy(c):
        while isinstance(c, list) and c and isinstance(c[0], list):
            c = c[0]
        return (c[1], c[0]) if isinstance(c, list) and len(c) >= 2 else (None, None)

    out = []
    for f in (data or {}).get("features") or []:
        p = {str(k).lower(): v for k, v in (f.get("properties") or {}).items()}
        if str(p.get("iscurrent", "true")).lower() != "true":
            continue
        typ = str(p.get("eventtype") or "").upper()
        if typ not in GDACS_TYPES:
            continue
        sev = p.get("severitydata") if isinstance(p.get("severitydata"), dict) else {}
        url = p.get("url") if isinstance(p.get("url"), dict) else {}
        lat, lon = first_xy((f.get("geometry") or {}).get("coordinates"))
        out.append({"type": typ, "name": p.get("eventname") or p.get("name") or "",
                    "desc": p.get("description") or "", "country": p.get("country") or "",
                    "alert": str(p.get("alertlevel") or "").capitalize(), "lat": lat, "lon": lon,
                    "sev": sev.get("severitytext") or "", "report": url.get("report") or ""})
    rank = {"Red": 0, "Orange": 1, "Green": 2}
    out.sort(key=lambda e: (rank.get(e["alert"], 3), e["type"]))
    return out


def parse_nino(text):
    rows = []
    for ln in text.splitlines():
        m = re.match(r"\s*(\d{2})([A-Z]{3})(\d{4})\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)", ln)
        if m and m.group(2) in MON:
            d = dt.date(int(m.group(3)), MON[m.group(2)], int(m.group(1)))
            rows.append((d, float(m.group(4)), float(m.group(5)), float(m.group(6)), float(m.group(7))))
    if not rows:
        raise ValueError("unbekanntes Format")
    return rows[-12:]


def collect_swiss_now(cache_dir):
    """Aktuelle 10-Minuten-Werte aller SwissMetNet-Stationen (eine Datei)."""
    print("Lade aktuelle Messwerte Schweiz …", file=sys.stderr)
    try:
        text = fetch_bytes(VQHA80, timeout=30).decode("latin-1")
        meta = {s["abbr"]: s for s in smn_stations(cache_dir)}
    except (ApiError, OSError) as e:
        log(f"Messwerte Schweiz ({e})")
        status("MeteoSchweiz aktuelle Werte (alle Stationen)", False, e)
        return []
    lines = [ln for ln in text.splitlines() if ln.strip()]
    hi = next((i for i, ln in enumerate(lines) if "tre200s0" in ln), None)
    if hi is None:
        log("Messwerte Schweiz (unbekanntes Format)")
        return []
    head = [h.strip() for h in lines[hi].split(";")]
    out = []
    for ln in lines[hi + 1:]:
        c = [x.strip() for x in ln.split(";")]
        if len(c) < len(head) or c[0] not in meta:
            continue
        rec = dict(zip(head, c))

        def f(k):
            try:
                return float(rec.get(k, "")) if rec.get(k, "-") not in ("", "-") else None
            except ValueError:
                return None
        m = meta[c[0]]
        out.append({"abbr": c[0], "name": m["name"], "elev": m["elev"], "T": f("tre200s0"),
                    "rain": f("rre150z0"), "rh": f("ure200s0"), "gust": f("fu3010z1"), "qff": f("pp0qffs0"),
                    "date": c[1] if len(c) > 1 else ""})
    status("MeteoSchweiz aktuelle Werte (alle Stationen)", bool(out), f"{len(out)} Stationen")
    return out


def world_rows(X):
    W = X.get("world_raw") or {}
    rows = []
    for k, (nm, la, lo, reg) in WORLD.items():
        rec = W.get(k)
        if not rec or not rec.get("d"):
            continue
        g = lambda v, i=0: (rec["v"].get(v) or [None])[i] if len(rec["v"].get(v) or []) > i else None
        rows.append({"key": k, "name": nm, "lat": la, "lon": lo, "region": reg, "code": g("weather_code"),
                     "tmax": g("temperature_2m_max"), "tmin": g("temperature_2m_min"),
                     "prec": g("precipitation_sum"), "snow": g("snowfall_sum"),
                     "gust": g("wind_gusts_10m_max"), "day": g("daylight_duration"),
                     "tmax1": g("temperature_2m_max", 1)})
    return rows


def enso_text(E):
    if not E:
        return "Der ENSO-Index von NOAA war bei der Erstellung nicht abrufbar."
    d, _n12, _n3, n34, _n4 = E[-1]
    prev = E[-5][3] if len(E) >= 5 else None
    phase = ("El-Niño-artig" if n34 >= 0.5 else ("La-Niña-artig" if n34 <= -0.5 else "neutral"))
    t = (f"Niño 3.4, Woche vom {d.day}. {MONATE[d.month - 1]}: {sgn(n34)} K relative Anomalie (gegenüber dem Mittel "
         f"der gesamten Tropen, NOAA CPC, Basis 1991–2020), also {phase}")
    if prev is not None:
        dd = n34 - prev
        t += f"; gegenüber vor vier Wochen {sgn(dd)} K" + (", Tendenz steigend" if dd >= 0.3 else
                                                         (", Tendenz fallend" if dd <= -0.3 else ""))
    t += (". Ein Wochenwert ist noch kein ENSO-Ereignis; dafür braucht es mehrere Monate über der Schwelle. "
          "Auf das Wetter in den Alpen wirkt ENSO nur schwach, stark dagegen auf die Tropen, Australien und Amerika.")
    return t


def world_section(X):
    out = ["## Welt", "", "<!--CHART:karte_welt-->", ""]
    A = out.append
    rows = world_rows(X)
    if rows:
        hot = max((r for r in rows if r["tmax"] is not None), key=lambda r: r["tmax"])
        cold = min((r for r in rows if r["tmin"] is not None), key=lambda r: r["tmin"])
        wet = max((r for r in rows if r["prec"] is not None), key=lambda r: r["prec"])
        t = (f"Unter {len(rows)} Orten auf allen Kontinenten ist es heute {ort_in(hot['name'])} mit {deg(hot['tmax'])} am "
             f"heissesten und {ort_in(cold['name'])} mit {deg(cold['tmin'])} am kältesten")
        t += (f"; den meisten Niederschlag erwartet {wet['name']} mit {n(wet['prec'])} mm." if (wet["prec"] or 0) >= 1
              else "; nirgends ist mehr als 1 mm Niederschlag vorhergesagt.")
        A(t + " Werte in Ortszeit, Modellvorhersage für heute.")
        A("")
        for reg in ("Afrika", "Asien", "Ozeanien", "Nordamerika", "Südamerika", "Polargebiete"):
            items = [f"{r['name']} {n(r['tmax'])}° {wmo_text(r['code'])}" for r in rows if r["region"] == reg
                     and r["tmax"] is not None]
            if items:
                A(f"- **{reg}:** " + "; ".join(items) + ".")
        A("")
    else:
        A("Die weltweite Vorhersage war bei der Erstellung nicht abrufbar.")
        A("")
    G = X.get("gdacs")
    A("### Naturgefahren aktuell")
    A("")
    if G is None:
        A("Die Ereignisliste von GDACS war bei der Erstellung nicht abrufbar.")
    elif not G:
        A("GDACS meldet derzeit keine aktiven tropischen Wirbelstürme, Überschwemmungen, Dürren oder Waldbrände.")
    else:
        strong = [e for e in G if e["alert"] in ("Red", "Orange")]
        cnt = {t: sum(1 for e in G if e["type"] == t) for t in GDACS_TYPES}
        A("Aktive Ereignisse laut GDACS: " + join_de([f"{c} × {GDACS_TYPES[t]}" for t, c in cnt.items() if c]) + ".")
        A("")
        alert_de = {"Red": "Rot", "Orange": "Orange", "Green": "Grün"}
        for e in (strong or G)[:8]:
            nm = e["name"] or e["desc"]
            A(f"- **{GDACS_TYPES[e['type']]}{' ' + nm if nm else ''}**"
              + (f" ({e['country']})" if e["country"] else "") + f", Warnstufe {alert_de.get(e['alert'], e['alert'])}"
              + (f", {e['sev']}" if e["sev"] else "") + ".")
        if not strong:
            A("")
            A("Alle aktiven Ereignisse haben die Warnstufe Grün, also geringe erwartete humanitäre Auswirkungen.")
    A("")
    A("### El Niño und La Niña")
    A("")
    A(enso_text(X.get("enso")))
    A("")
    A("Quellen: Open-Meteo (Modelle), GDACS (Europäische Kommission und Vereinte Nationen), NOAA Climate Prediction Center.")
    A("")
    return out


def fun_facts(X):
    ch, eu, wo = [], [], []
    S = [s for s in (X.get("swiss_now") or []) if s.get("T") is not None]
    if len(S) >= 10:
        hot, cold = max(S, key=lambda s: s["T"]), min(S, key=lambda s: s["T"])
        ch.append(f"Gerade jetzt ist {hot['name']} ({int(hot['elev'])} m) mit {deg(hot['T'], 1)} der wärmste und "
                  f"{cold['name']} ({int(cold['elev'])} m) mit {deg(cold['T'], 1)} der kälteste Messort der Schweiz: "
                  f"{n(hot['T'] - cold['T'], 1)} K Unterschied.")
        high = [s for s in S if s["elev"] >= 1500]
        low = [s for s in S if s["elev"] < 800]
        if high and low:
            h, l = max(high, key=lambda s: s["T"]), min(low, key=lambda s: s["T"])
            if h["T"] > l["T"]:
                ch.append(f"Verkehrte Welt: Auf {h['name']} ({int(h['elev'])} m) ist es mit {deg(h['T'], 1)} wärmer als "
                          f"in {l['name']} ({int(l['elev'])} m) mit {deg(l['T'], 1)}, ein klassischer Kaltluftsee.")
        gs = [s for s in S if s.get("gust") is not None]
        if gs:
            g = max(gs, key=lambda s: s["gust"])
            if g["gust"] >= 40:
                ch.append(f"Stärkste Böe der letzten zehn Minuten: {g['name']} mit {n(g['gust'])} km/h.")
        wet = [s for s in S if (s.get("rain") or 0) > 0]
        ch.append(f"In diesem Moment fällt an {len(wet)} von {len(S)} Stationen Niederschlag."
                  if wet else f"In diesem Moment ist es an allen {len(S)} Stationen trocken.")
        rh = [s for s in S if s.get("rh") is not None]
        if rh:
            d = min(rh, key=lambda s: s["rh"])
            if d["rh"] <= 35:
                ch.append(f"Am trockensten ist die Luft {ort_in(d['name'])} mit {n(d['rh'])} % relativer Feuchte, "
                          "in den Alpentälern oft ein Föhnzeichen.")
    r = X["local"]["riederalp"].get(X["days"][0])
    if r and r.get("fzl_max"):
        z = r["fzl_max"]
        if z >= 4478:
            ch.append(f"Die Nullgradgrenze steigt heute bis {hm(z)} und damit über den Gipfel des Matterhorns (4478 m).")
        elif z <= 1500:
            ch.append(f"Die Nullgradgrenze liegt heute höchstens bei {hm(z)}, unter dem Dorf Riederalp.")
    pk = X.get("peaks") or {}
    j = X["local"]["jungfraujoch"].get(X["days"][0])
    ev = pk.get("everest")
    if ev and j:
        tx = (ev["v"].get("temperature_2m_max") or [None])[0]
        gx = (ev["v"].get("wind_gusts_10m_max") or [None])[0]
        if tx is not None:
            ch.append(f"Auf dem Gipfel des Mount Everest rechnet das Modell heute mit höchstens {deg(tx)}"
                      + (f" und Böen bis {n(gx)} km/h" if gx is not None else "")
                      + f", {n(abs(j['tmax'] - tx))} K {'kälter' if tx <= j['tmax'] else 'wärmer'} als auf dem "
                      "Jungfraujoch.")
    e = X["eu"].get(X["days"][0]) if X.get("eu") else None
    if e:
        if e.get("pmax") and e.get("pmin"):
            dp = e["pmax"][1] - e["pmin"][1]
            eu.append(f"Zwischen {e['pmax'][0]} ({n(e['pmax'][1])} hPa) und {e['pmin'][0]} ({n(e['pmin'][1])} hPa) "
                      f"liegen {n(dp)} hPa, etwa so viel wie zwischen Meeresniveau und {int(round(dp * 8.3, -1))} m Höhe.")
        if e.get("jet_max"):
            v = e["jet_max"] * 3.6
            eu.append(f"Der Strahlstrom über Europa erreicht in rund 10 km Höhe {n(v)} km/h"
                      + (", schneller als ein TGV im Regelbetrieb (320 km/h)." if v > 320 else "."))
        if e.get("t850max") and e.get("t850min"):
            eu.append(f"In etwa 1500 m Höhe ist die Luft über {e['t850max'][0]} um "
                      f"{n(e['t850max'][1] - e['t850min'][1])} K wärmer als über {e['t850min'][0]}.")
    mb = pk.get("montblanc")
    if mb:
        tx = (mb["v"].get("temperature_2m_max") or [None])[0]
        if tx is not None:
            eu.append(f"Gipfel des Mont Blanc (4806 m), höchster Punkt der Alpen: heute höchstens {deg(tx)}.")
    mo = X.get("moon")
    if mo:
        eu.append(f"Mond heute Abend: {mo['name']}, zu {pct(mo['illum'])} beleuchtet; nächster Vollmond um den "
                  f"{mo['full'].day}. {MONATE[mo['full'].month - 1]}.")
    rc = (X.get("records") or {}).get(X["days"][0])
    if rc:
        y = X["days"][0].year - int(rc["cold"][1])
        ch.append(f"Heute vor {y} Jahren ({rc['cold'][1]}) war dieser Kalendertag auf der Riederalp mit "
                  f"{deg(rc['cold'][0], 1)} der kälteste der Jahre 1991–2020.")
    rows = world_rows(X)
    if rows:
        tx = [r_ for r_ in rows if r_["tmax"] is not None]
        tn = [r_ for r_ in rows if r_["tmin"] is not None]
        if tx and tn:
            h, c = max(tx, key=lambda q: q["tmax"]), min(tn, key=lambda q: q["tmin"])
            wo.append(f"Zwischen {h['name']} ({deg(h['tmax'])}) und {c['name']} ({deg(c['tmin'])}) liegen heute "
                      f"{n(h['tmax'] - c['tmin'])} K.")
        dl = [r_ for r_ in rows if r_["day"] is not None]
        if dl:
            lo_, hi_ = min(dl, key=lambda q: q["day"]), max(dl, key=lambda q: q["day"])
            fmt = lambda s: ("keine Sonne (Polarnacht)" if s < 60 else
                             ("Sonne rund um die Uhr" if s > 86000 else f"{n(s / 3600, 1)} h Tageslicht"))
            wo.append(f"Längster Tag: {hi_['name']} mit {fmt(hi_['day'])}; kürzester: {lo_['name']} mit {fmt(lo_['day'])}.")
        gs = [r_ for r_ in rows if r_["gust"] is not None]
        if gs:
            g = max(gs, key=lambda q: q["gust"])
            wo.append(f"Windigster der {len(rows)} Orte: {g['name']} mit Böen bis {n(g['gust'])} km/h.")
        sn = [r_ for r_ in rows if (r_["snow"] or 0) >= 1]
        if sn:
            wo.append("Schnee fällt heute unter anderem in " + join_de([s_["name"] for s_ in sn[:4]]) + ".")
    G = X.get("gdacs") or []
    tcs = [g for g in G if g["type"] == "TC"]
    if tcs:
        wo.append(f"Aktive tropische Wirbelstürme laut GDACS: {len(tcs)}"
                  + (f", darunter {tcs[0]['name'] or tcs[0]['desc']}" if (tcs[0]['name'] or tcs[0]['desc']) else "") + ".")
    E = X.get("enso")
    if E:
        v = E[-1][3]
        wo.append(f"Der zentrale tropische Pazifik (Niño 3.4) ist gerade {n(abs(v), 1)} K "
                  f"{'wärmer' if v >= 0 else 'kälter'} als die Tropen im Mittel.")
    return ch, eu, wo


def fun_section(X):
    ch, eu, wo = fun_facts(X)
    if not (ch or eu or wo):
        return []
    out = ["## Fun Facts", ""]
    for title, items in (("Schweiz", ch), ("Europa", eu), ("Welt", wo)):
        if items:
            out += [f"### {title}", ""] + [f"- {t}" for t in items] + [""]
    out += ["Messwerte Schweiz: MeteoSchweiz; übrige Werte: Modellvorhersagen über Open-Meteo, an Gipfeln statistisch "
            "auf die Gipfelhöhe skaliert und entsprechend unsicher.", ""]
    return out


T2M_STOPS = [(-50, (70, 30, 110)), (-30, (44, 95, 168)), (-10, (110, 175, 220)), (0, (225, 236, 240)),
             (10, (245, 225, 140)), (20, (243, 165, 80)), (30, (215, 80, 50)), (42, (120, 20, 35))]


def map_world(X, geo):
    rows = world_rows(X)
    if not rows:
        return None
    P = Proj(MAPS["welt"]["bbox"], MAPS["welt"]["W"])
    g = geo.get("welt", {}) if geo else {}
    h = [svg_open(P, "eu")]
    h.append(f'<rect x="0" y="0" width="{P.W}" height="{P.H}" class="ocean"/>')
    for lat in (-60, -30, 0, 30, 60):
        y = P.xy(lat, 0)[1]
        h.append(f'<line x1="0" x2="{P.W}" y1="{y:.1f}" y2="{y:.1f}" class="grat"/>')
    if g.get("coast"):
        h.append(f'<path d="{path_d(P, g["coast"], tol=0.7)}" class="coastw"/>')
    for r in rows:
        if r["tmax"] is None:
            continue
        x, y = P.xy(r["lat"], r["lon"])
        c = band_color(r["tmax"], T2M_STOPS, 2, 255)[:3]
        h.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.6" fill="rgb{c}" class="wdot"><title>{html.escape(r["name"])} '
                 f'{n(r["tmax"])} °C</title></circle>')
    col = {"Red": "#c0392b", "Orange": "#e67e22", "Green": "#2e8b57"}
    for e in X.get("gdacs") or []:
        if e["lat"] is None:
            continue
        x, y = P.xy(e["lat"], e["lon"])
        h.append(f'<rect x="{x - 3.5:.1f}" y="{y - 3.5:.1f}" width="7" height="7" transform="rotate(45 {x:.1f} {y:.1f})" '
                 f'fill="none" stroke="{col.get(e["alert"], "#555")}" stroke-width="1.8"/>')
    tx = [r for r in rows if r["tmax"] is not None]
    for r in (max(tx, key=lambda q: q["tmax"]), min(tx, key=lambda q: q["tmax"])):
        x, y = P.xy(r["lat"], r["lon"])
        anchor = "end" if x > P.W * 0.7 else "start"
        dx = -6 if anchor == "end" else 6
        y = min(max(y, 10), P.H - 6)
        h.append(f'<text x="{x + dx:.1f}" y="{y + 4:.1f}" class="ilbl" text-anchor="{anchor}">{html.escape(r["name"])} '
                 f'{n(r["tmax"])}°</text>')
    x, y = P.xy(LOCAL["riederalp"]["lat"], LOCAL["riederalp"]["lon"])
    h.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.6" class="rmark"/></svg>')
    leg = legend_html([(f"background:rgb{band_color(t, T2M_STOPS, 2, 255)[:3]};border-radius:50%", f"{n(t)}")
                       for t in (-40, -20, 0, 10, 20, 30, 40)] + [("", "°C Höchstwert")]
                      + [(f"border:2px solid {c};transform:rotate(45deg);width:8px;height:8px;background:none", lab)
                         for c, lab in (("#c0392b", "GDACS Rot"), ("#e67e22", "Orange"), ("#2e8b57", "Grün"))])
    cap = ("Höchstwerte heute an den Orten des Kapitels (Ortszeit, Modellvorhersage) und aktive Naturgefahren laut GDACS "
           "(Rauten: Wirbelstürme, Überschwemmungen, Dürren, Waldbrände). Punkt mit rotem Rand: Riederalp. "
           "Küstenlinien: Natural Earth.")
    return map_widget("wo", P, [("Heute", "".join(h))], legend=leg), cap


def short_summary(X, insights_list):
    d0 = X["days"][0]
    LD = X["local"]["riederalp"]
    parts = []
    for d, lab in ((d0, "Heute"), (X["days"][1], "Morgen")):
        s = LD.get(d)
        if s:
            parts.append(f"{lab}: {sky_word(s)}, {n(s['tmin'])} bis {n(s['tmax'])} °C"
                         + (f", {n(s['prec'], 1)} mm" if s["prec"] >= 0.3 else "")
                         + (f", {n(s['snow'])} cm Neuschnee" if s["snow"] >= 1 else ""))
    return "Riederalp " + "; ".join(parts) + ".\n" + "\n".join(f"• {t}" for t in insights_list[:3])


def push_ntfy(text):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return
    if dt.datetime.now(TZ).hour >= 12 and not os.environ.get("NTFY_ALWAYS"):
        return
    headers = {"Title": "Wetter Riederalp", "Tags": "mountain"}
    if os.environ.get("REPORT_URL"):
        headers["Click"] = os.environ["REPORT_URL"]
    try:
        req = urllib.request.Request(f"https://ntfy.sh/{urllib.parse.quote(topic)}", data=text.encode("utf-8"),
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=20):
            pass
    except (urllib.error.URLError, TimeoutError) as e:
        log(f"Push-Nachricht ({e})")


# ================================================================ Ausbau: MeteoSchweiz, Abfluss, Aletsch, Mond
MCH_LF = "ch.meteoschweiz.ogd-local-forecasting"
MCH_ITEM = "https://data.geo.admin.ch/api/stac/v1/collections/" + MCH_LF + "/items/{}-ch"
MCH_POINTS = "https://data.geo.admin.ch/" + MCH_LF + "/ogd-local-forecasting_meta_point.csv"
MCH_DAILY = ("tre200px", "tre200pn", "rka150p0", "rreq10p0", "rreq90p0", "jp2000d0")
MCH_SEL = {"riederalp": None, "sion": "SIO", "visp": "VIS", "zermatt": "ZER", "simplon": "SIM", "grimsel": "GRH",
           "jungfraujoch": "JUN", "zuerich": "SMA", "bern": "BER", "basel": "BAS", "genf": "GVE", "lugano": "LUG",
           "chur": "CHU"}
PICTO = {1: "sonnig", 2: "ziemlich sonnig", 3: "teilweise sonnig", 4: "wechselnd bewölkt", 5: "bedeckt",
         6: "Aufhellungen, einzelne Regenschauer", 7: "Aufhellungen, einzelne Regen- oder Schneeschauer",
         8: "Aufhellungen, einzelne Schneeschauer", 9: "bewölkt, einige Regenschauer",
         10: "bewölkt, einige Regen- oder Schneeschauer", 11: "bewölkt, einige Schneeschauer",
         12: "Aufhellungen, leicht gewitterhaft", 13: "Aufhellungen und gewitterhaft",
         14: "stark bewölkt, schwacher Regen", 15: "stark bewölkt, schwacher Schnee oder Regen",
         16: "stark bewölkt, schwacher Schnee", 17: "stark bewölkt, zeitweise Regen",
         18: "stark bewölkt, zeitweise Schnee oder Regen", 19: "stark bewölkt, zeitweise Schnee",
         20: "stark bewölkt, anhaltender Regen", 21: "stark bewölkt, anhaltender Regen oder Schnee",
         22: "stark bewölkt, anhaltender Schnee", 23: "stark bewölkt, leicht gewitterhaft",
         24: "stark bewölkt, gewitterhaft", 25: "stark bewölkt, stark gewitterhaft", 26: "hohe Bewölkung",
         27: "Hochnebel", 28: "Nebel", 29: "leicht bewölkt, einzelne Regenschauer",
         30: "leicht bewölkt, leichter Schneefall", 31: "teilweise sonnig, einige Schnee- oder Regenschauer",
         32: "teilweise sonnig, einige Regenschauer", 33: "bewölkt, häufige Regenschauer",
         34: "bewölkt, häufige Schneeschauer", 35: "bedeckt und trocken", 36: "teilweise sonnig, gewitterhaft",
         37: "teilweise sonnig, Gewitter und Schneeschauer", 38: "bewölkt, Gewitter und häufige Regenschauer",
         39: "bewölkt, Gewitter und häufige Schneeschauer", 40: "stark bewölkt, leicht gewitterhaft",
         41: "bewölkt, leicht gewitterhaft", 42: "stark bewölkt, Gewitter und häufige Schneeschauer"}
API_FLOOD = "https://flood-api.open-meteo.com/v1/flood"
RIVERS = {"rhone_sion": _p("Rhône bei Sion", 46.225, 7.355), "rhone_scex": _p("Rhône bei Porte du Scex", 46.349, 6.889),
          "ticino": _p("Ticino bei Bellinzona", 46.190, 9.010)}
ALETSCH = {f"a{z}": _p(f"Aletsch {z} m", 46.465, 8.010, z) for z in (2200, 2700, 3300)}
STATUS = []          # (Quelle, ok, Hinweis)
MODEL_IDS = {}       # funktionierende Open-Meteo-IDs aus früheren Läufen


def status(name, ok, info=""):
    STATUS.append((name, bool(ok), str(info)[:120]))


def picto(code):
    if code is None:
        return "–"
    c = int(code)
    return PICTO.get(c - 100 if c > 100 else c, f"Symbol {c}")


def mch_points(cache_dir):
    path = os.path.join(cache_dir, "mch_punkte_v1.json")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 30 * 86400:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    text = fetch_bytes(MCH_POINTS, timeout=60).decode("latin-1")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    head = [h.strip() for h in lines[0].split(";")]
    ix = {k: head.index(k) for k in ("point_id", "point_type_id", "station_abbr", "postal_code", "point_name",
                                      "point_height_masl") if k in head}
    if len(ix) < 6:
        raise ApiError("Punktliste in unbekanntem Format")
    sel = {}
    for ln in lines[1:]:
        c = [x.strip() for x in ln.split(";")]
        if len(c) < len(head):
            continue
        for key, abbr in MCH_SEL.items():
            if key in sel:
                continue
            if abbr and c[ix["point_type_id"]] == "1" and c[ix["station_abbr"]] == abbr:
                sel[key] = [c[ix["point_id"]], "1", c[ix["point_name"]], c[ix["point_height_masl"]]]
            elif abbr is None and c[ix["point_type_id"]] == "2" and c[ix["postal_code"]] == "3987" \
                    and "Riederalp" in c[ix["point_name"]]:
                sel[key] = [c[ix["point_id"]], "2", c[ix["point_name"]], c[ix["point_height_masl"]]]
    os.makedirs(cache_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sel, f)
    return sel


def collect_mch(cache_dir):
    print("Lade MeteoSchweiz-Lokalprognose …", file=sys.stderr)
    try:
        sel = mch_points(cache_dir)
        item = None
        for back in (0, 1):
            d = dt.datetime.now(TZ).date() - dt.timedelta(back)
            try:
                item = fetch_json(MCH_ITEM.format(d.strftime("%Y%m%d")), retries=2, quiet=True)
                break
            except ApiError:
                continue
        if not item:
            raise ApiError("kein aktuelles STAC-Item")
        assets = item.get("assets", {})
        runs = sorted({k.split(".")[2] for k in assets if k.count(".") >= 4})
        run = runs[-1]
        hrefs = {p: next((a["href"] for k, a in assets.items() if k.split(".")[2] == run and k.split(".")[3] == p), None)
                 for p in MCH_DAILY}
        want = {(v[1], v[0]): k for k, v in sel.items()}
        data = {k: {} for k in sel}

        def load(p):
            if not hrefs.get(p):
                return p, None
            return p, fetch_bytes(hrefs[p], timeout=60).decode("latin-1")

        for p, text in pmap(load, MCH_DAILY, workers=6):
            if not text:
                continue
            lines = text.splitlines()
            head = [h.strip().strip('"') for h in lines[0].split(";")]
            ip, it = head.index("point_id"), head.index("point_type_id")
            itime = next(i for i, h in enumerate(head) if h.lower() in ("date", "time"))
            iv = head.index(p) if p in head else len(head) - 1
            for ln in lines[1:]:
                c = [x.strip().strip('"') for x in ln.split(";")]
                if len(c) < len(head):
                    continue
                key = want.get((c[it], c[ip]))
                if not key or c[iv] in ("", "-"):
                    continue
                t = dt.datetime.strptime(c[itime][:12], "%Y%m%d%H%M").replace(tzinfo=dt.timezone.utc)
                data[key].setdefault(p, {})[t.astimezone(TZ).date()] = float(c[iv])
        rt = dt.datetime.strptime(run, "%Y%m%d%H%M").replace(tzinfo=dt.timezone.utc).astimezone(TZ)
        status("MeteoSchweiz-Lokalprognose", True, f"Lauf {rt:%d.%m. %H:%M} Uhr")
        return {"run": dt.datetime.strptime(run, "%Y%m%d%H%M").replace(tzinfo=dt.timezone.utc),
                "points": sel, "data": data}
    except (ApiError, ValueError, StopIteration, IndexError, KeyError, OSError) as e:
        log(f"MeteoSchweiz-Lokalprognose ({e})")
        status("MeteoSchweiz-Lokalprognose", False, e)
        return None


def collect_rivers_aletsch(D, days, cache_dir):
    print("Lade Abfluss (GloFAS) und Aletsch-Höhenstufen …", file=sys.stderr)
    keys = list(RIVERS)
    _m, D["rivers"] = fetch_model(API_FLOOD, "Abflussprognose (GloFAS)", [""],
                                  {**coords(keys, RIVERS, elev=False), "daily": "river_discharge,river_discharge_median,"
                                   "river_discharge_p25,river_discharge_p75,river_discharge_max,river_discharge_min",
                                   "past_days": 3, "forecast_days": 16}, keys, None, parser=parse_daily)
    try:
        months = sorted({(days[0] + dt.timedelta(k)).month for k in range(-7, 23)})
        D["river_clim"] = archive_months(cache_dir, "klima_abfluss_glofas_v1.json", RIVERS, keys,
                                         ["river_discharge"], months, [""], api=API_FLOOD)
    except (ApiError, OSError) as e:
        log(f"Abfluss-Klimatologie ({e})")
        D["river_clim"] = None
    ak = list(ALETSCH)
    _m, D["aletsch"] = fetch_model(API_FC, "Aletsch-Höhenstufen", ["best_match"],
                                   {**coords(ak, ALETSCH), "hourly": "temperature_2m", "timezone": TZNAME,
                                    "forecast_days": len(days)}, ak, days)


def river_diag(D, X):
    R, C = D.get("rivers") or {}, D.get("river_clim")
    out = []
    today = X["days"][0]
    for k, meta in RIVERS.items():
        rec = R.get(k)
        if not rec or not rec.get("d"):
            continue
        v = rec["v"]
        g = lambda name, i: (v.get(name) or [None] * len(rec["d"]))[i]
        idx_today = next((i for i, d in enumerate(rec["d"]) if d == today), None)
        if idx_today is None:
            continue
        now = g("river_discharge", idx_today)
        fut = [(rec["d"][i], g("river_discharge_median", i), g("river_discharge_max", i), g("river_discharge_p75", i))
               for i in range(idx_today, len(rec["d"]))]
        fut = [f for f in fut if f[1] is not None]
        if now is None or not fut:
            continue
        pk_med = max(fut, key=lambda f: f[1])
        pk_max = max((f for f in fut if f[2] is not None), key=lambda f: f[2], default=None)
        cl = window_vals(C, k, today, "river_discharge", half=7) if C else []
        cl_all = []
        if C:
            for d in {f[0] for f in fut}:
                cl_all += window_vals(C, k, d, "river_discharge", half=3)
        res = {"name": meta["name"], "now": now, "pk_med": pk_med, "pk_max": pk_max}
        if len(cl) >= 100:
            res.update(clim_med=quant(cl, .5), clim_p90=quant(cl, .9), rank=sum(1 for x in cl if x < now) / len(cl))
        if len(cl_all) >= 100:
            res["clim_max"] = max(cl_all)
        out.append(res)
    return out


def aletsch_diag(D, X):
    A = D.get("aletsch") or {}
    out = {}
    for k, meta in ALETSCH.items():
        rec = A.get(k)
        if not rec:
            continue
        row = {}
        for d in X["days"]:
            T = valid([rec["v"].get("temperature_2m", [None] * len(rec["t"]))[i] for i in rec["idx"].get(d, [])])
            if len(T) >= 20:
                row[d] = {"pdd": sum(max(t, 0.0) for t in T) / len(T), "hrs": sum(1 for t in T if t > 0),
                          "tmax": max(T)}
        out[meta["elev"]] = row
    return out


def day_records(cache, loc, d):
    b = None
    for block in (cache or {}).get("months", {}).values():
        if loc in block:
            b = block[loc] if b is None else {kk: b[kk] + block[loc][kk] for kk in b}
    if not b:
        return None
    md = f"-{d.month:02d}-{d.day:02d}"
    rows = [(ds[:4], tx, tn, pr) for ds, tx, tn, pr in zip(b["date"], b["tmax"], b["tmin"], b["prec"]) if ds.endswith(md)]
    rows = [r for r in rows if r[1] is not None and r[2] is not None]
    if len(rows) < 20:
        return None
    hot, cold = max(rows, key=lambda r: r[1]), min(rows, key=lambda r: r[2])
    wet = max(rows, key=lambda r: r[3] or 0)
    return {"hot": (hot[1], hot[0]), "cold": (cold[2], cold[0]), "wet": (wet[3], wet[0]), "n": len(rows)}


MOON_NAMES = ((1.85, "Neumond"), (5.54, "zunehmende Sichel"), (9.23, "erstes Viertel"), (12.92, "zunehmender Mond"),
              (16.61, "Vollmond"), (20.30, "abnehmender Mond"), (23.99, "letztes Viertel"),
              (27.68, "abnehmende Sichel"), (29.54, "Neumond"))


def moon(d):
    syn = 29.530588853
    ref = dt.datetime(2000, 1, 6, 18, 14, tzinfo=dt.timezone.utc)
    t = dt.datetime(d.year, d.month, d.day, 21, tzinfo=TZ)
    age = ((t - ref).total_seconds() / 86400) % syn
    name = next(nm for lim, nm in MOON_NAMES if age < lim)
    illum = (1 - math.cos(2 * math.pi * age / syn)) / 2
    to_full = (14.765 - age) % syn
    return {"age": age, "name": name, "illum": illum, "full": d + dt.timedelta(days=round(to_full))}


AQI_WORDS = ((20, "gut"), (40, "recht gut"), (60, "mässig"), (80, "schlecht"), (100, "sehr schlecht"))
POLLEN = {"birch_pollen": "Birke", "grass_pollen": "Gräser", "alder_pollen": "Erle", "mugwort_pollen": "Beifuss",
          "ragweed_pollen": "Ambrosia"}


def air_text(X, d):
    a = (X.get("air") or {}).get(d)
    if not a:
        return ""
    bits = []
    if a.get("aqi") is not None and a["aqi"] >= 40:
        word = next((w for lim, w in AQI_WORDS if a["aqi"] < lim), "extrem schlecht")
        bits.append(f"Luftqualität (Europäischer Index, CAMS) bis {n(a['aqi'])}, {word}")
    pol = [(POLLEN[k], v) for k, v in a.get("pollen", {}).items() if v is not None and v >= 10]
    if pol:
        bits.append("Pollen: " + join_de([f"{nm} {'stark' if v >= 50 else 'mässig'} ({n(v)} Körner/m³)" for nm, v in pol]))
    return (". ".join(bits) + ".") if bits else ""


def mch_section(X):
    M = X.get("mch")
    if not M:
        return []
    out = ["### Vergleich mit der offiziellen Prognose von MeteoSchweiz", ""]
    A = out.append
    run = M["run"].astimezone(TZ)
    pr = M["data"].get("riederalp") or {}
    LD = X["local"]["riederalp"]
    rows = [d for d in X["days"] if d in pr.get("tre200px", {})]
    if rows:
        nm = (M["points"].get("riederalp") or ["", "", "Riederalp"])[2]
        A(f"Lokalprognose für {nm} (Lauf {run:%d.%m. %H:%M} Uhr), daneben die Werte dieses Berichts.")
        A("")
        A("| Tag | MeteoSchweiz | Min / Max MCH | Min / Max Bericht | Niederschlag MCH (10–90 %) | Bericht |")
        A("|---|---|---|---|---|---|")
        diffs = []
        for d in rows:
            s = LD.get(d)
            tx, tn = pr["tre200px"].get(d), pr.get("tre200pn", {}).get(d)
            q10, q90 = pr.get("rreq10p0", {}).get(d), pr.get("rreq90p0", {}).get(d)
            ptxt = (f"{n(pr.get('rka150p0', {}).get(d), 1)} mm" + (f" ({n(q10, 1)}–{n(q90, 1)})" if q10 is not None and q90 is not None else ""))
            A(f"| {dshort(d)} | {picto(pr.get('jp2000d0', {}).get(d))} | {n(tn)} / {n(tx)} °C | "
              + (f"{n(s['tmin'])} / {n(s['tmax'])} °C | {ptxt} | {n(s['prec'], 1)} mm |" if s else f"– | {ptxt} | – |"))
            if s and tx is not None:
                diffs.append((d, s["tmax"] - tx))
        A("")
        if diffs:
            mx = max(diffs, key=lambda x: abs(x[1]))
            A(f"Mittlere Abweichung des Höchstwerts (Bericht minus MeteoSchweiz): "
              f"{sgn(sum(x[1] for x in diffs) / len(diffs))} K; grösste am {dshort(mx[0])} mit {sgn(mx[1])} K. "
              "MeteoSchweiz kombiniert INCA, ICON-CH1/2-EPS und ECMWF mit statistischer Nachbearbeitung an Messstationen; "
              "dieser Bericht verwendet die Rohmodelle. Grosse Unterschiede zeigen, wo die Nachbearbeitung eingreift.")
            A("")
    st = [k for k in MCH_SEL if k != "riederalp" and (M["data"].get(k) or {}).get("tre200px")]
    d0, d1 = X["days"][0], X["days"][1]
    if st:
        A("| Station | heute MCH / Bericht | morgen MCH / Bericht |")
        A("|---|---|---|")
        dd = []
        for k in st:
            mx = M["data"][k]["tre200px"]
            cells = []
            for d in (d0, d1):
                s = X["local"][k].get(d)
                cells.append(f"{n(mx.get(d))} / {n(s['tmax']) if s else '–'} °C")
                if s and mx.get(d) is not None:
                    dd.append(abs(s["tmax"] - mx[d]))
            A(f"| {LOCAL[k]['name']} | {cells[0]} | {cells[1]} |")
        A("")
        if dd:
            A(f"Höchstwerte heute und morgen an {len(st)} Stationen: mittlere absolute Differenz {n(sum(dd) / len(dd), 1)} K. "
              "Quelle der Lokalprognose: MeteoSchweiz.")
            A("")
    return out


def river_section(X):
    R = X.get("rivers") or []
    if not R:
        return []
    out = ["### Abfluss", "", "GloFAS-Simulation (Copernicus) über Open-Meteo: heutiger Abfluss, Einordnung gegenüber "
           "1991–2020 (±7 Tage) und Spitze der nächsten 16 Tage.", ""]
    for r in R:
        t = f"- **{r['name']}:** heute {n(r['now'])} m³/s"
        if r.get("clim_med"):
            t += f" (Median der Jahreszeit {n(r['clim_med'])} m³/s, {int(round(r['rank'] * 100))}. Perzentil)"
        t += f"; Spitze im Ensemble-Median {n(r['pk_med'][1])} m³/s am {dshort(r['pk_med'][0])}"
        if r.get("pk_max") and r["pk_max"][2] is not None:
            t += f", im ungünstigsten Member {n(r['pk_max'][2])} m³/s"
            if r.get("clim_max") and r["pk_max"][2] > r["clim_max"]:
                t += f", mehr als der höchste Wert dieser Jahreszeit 1991–2020 ({n(r['clim_max'])} m³/s)"
        out.append(t + ".")
    out += ["", "Grenzen: Der Abfluss von Rhône und Ticino ist durch Speicherkraftwerke stark reguliert, was GloFAS nur "
            "vereinfacht abbildet; das 0,05°-Raster trifft kleine Flüsse nicht. Massgebend sind die Hochwasserwarnungen "
            "des Bundes (naturgefahren.ch).", ""]
    return out


def aletsch_section(X):
    A_ = X.get("aletsch") or {}
    if not A_:
        return []
    days = [d for d in X["days"] if any(d in A_[z] for z in A_)]
    out = ["### Aletschgletscher: Schmelzpotenzial", "",
           "Positive Gradtage (Tagesmittel der Stundentemperaturen über 0 °C) auf drei Höhenstufen des Gletschers, daraus "
           "die Schmelze mit Gradtagfaktoren von 4 mm pro K und Tag für Schnee und 8 mm für Eis.", "",
           "| Tag | " + " | ".join(f"{z} m" for z in sorted(A_)) + " |", "|---|" + "---|" * len(A_)]
    tot = {z: 0.0 for z in A_}
    for d in days:
        cells = []
        for z in sorted(A_):
            r = A_[z].get(d)
            if r:
                tot[z] += r["pdd"]
                cells.append("0" if r["pdd"] < 0.05 else f"{n(r['pdd'], 1)} K·d ({n(4 * r['pdd'])}–{n(8 * r['pdd'])} mm)")
            else:
                cells.append("–")
        out.append(f"| {dshort(d)} | " + " | ".join(cells) + " |")
    out.append("")
    z27 = tot.get(2700)
    if z27 is not None:
        if z27 < 0.05:
            out.append(f"Auf 2700 m (etwa Konkordiaplatz) bleibt es bis {dshort(days[-1])} ohne positive Gradtage, also ohne "
                       "nennenswertes Schmelzpotenzial" + (f"; Schmelze nur unterhalb davon ({n(tot.get(2200, 0), 1)} Gradtage auf "
                                                           "2200 m)." if (tot.get(2200) or 0) >= 0.05 else "."))
        else:
            out.append(f"Summe bis {dshort(days[-1])} auf 2700 m (etwa Konkordiaplatz): {n(z27, 1)} Gradtage, also "
                       f"{n(4 * z27)} bis {n(8 * z27)} mm Wasseräquivalent. Heller Neuschnee reflektiert mehr Strahlung, "
                       "als ein Gradtagmodell annimmt; Staub und blankes Eis verstärken die Schmelze.")
        out.append("")
    return out


def status_table():
    if not STATUS:
        return []
    seen = {}
    for name, ok, info in STATUS:
        seen[name] = (ok, info) if name not in seen or ok else seen[name]
    out = ["**Datenquellen in diesem Lauf:**", "", "| Quelle | Status | Hinweis |", "|---|---|---|"]
    for name, (ok, info) in seen.items():
        out.append(f"| {name} | {'✓' if ok else '✗'} | {info.replace('|', '/')} |")
    return out + [""]


def safe_lines(fn, *args, title=None):
    """Ein Fehler in einem Abschnitt darf nie den ganzen Bericht verhindern."""
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001 - bewusst breit: Bericht muss erscheinen
        log(f"Abschnitt {title or fn.__name__} ({type(e).__name__}: {e})")
        return ([f"## {title}", ""] if title else []) + [f"Dieser Abschnitt konnte nicht erstellt werden "
                                                          f"({type(e).__name__}).", ""]


def load_model_ids(cache_dir):
    try:
        with open(os.path.join(cache_dir, "modell_ids_v1.json"), encoding="utf-8") as f:
            MODEL_IDS.update(json.load(f))
    except (OSError, json.JSONDecodeError):
        pass


def save_model_ids(cache_dir):
    try:
        with open(os.path.join(cache_dir, "modell_ids_v1.json"), "w", encoding="utf-8") as f:
            json.dump(MODEL_IDS, f)
    except OSError:
        pass


# ================================================================ Karten
NE_BASE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/"
NE_LAYERS = [("coast", "ne_50m_coastline", ("europa",)),
             ("coast", "ne_110m_coastline", ("welt",)),
             ("borders", "ne_50m_admin_0_boundary_lines_land", ("europa",)),
             ("borders", "ne_10m_admin_0_boundary_lines_land", ("schweiz", "wallis", "riederalp")),
             ("lakes", "ne_10m_lakes", ("schweiz", "wallis", "riederalp")),
             ("lakes", "ne_10m_lakes_europe", ("schweiz", "wallis", "riederalp")),
             ("rivers", "ne_10m_rivers_lake_centerlines", ("schweiz", "wallis", "riederalp"))]
TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
API_ELEV = "https://api.open-meteo.com/v1/elevation"


class Proj:
    """Flächentreue genügt hier nicht; einfache Plattkarte mit cos(φ)-Streckung."""

    def __init__(self, bbox, W):
        self.la0, self.la1, self.lo0, self.lo1 = bbox
        self.k = math.cos(math.radians((self.la0 + self.la1) / 2))
        self.s = W / ((self.lo1 - self.lo0) * self.k)
        self.W, self.H = W, int(round((self.la1 - self.la0) * self.s))

    def xy(self, lat, lon):
        return (lon - self.lo0) * self.k * self.s, (self.la1 - lat) * self.s

    def ll(self, x, y):
        return self.la1 - y / self.s, self.lo0 + x / (self.k * self.s)


def fetch_bytes(url, timeout=60):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return gzip.decompress(raw) if (r.headers.get("Content-Encoding") or "").lower() == "gzip" else raw
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ApiError(f"{url.split('/')[2]}: {e}")


# ---------------------------------------------------------------- PNG
def png_encode(w, h, rows, ct, palette=None):
    """rows: Bytezeilen ohne Filterbyte; ct 2 = RGB, 3 = Palette, 4 = Grau+Alpha, 6 = RGBA. Filter 'Up'."""
    out, prev = bytearray(), bytes(len(rows[0]))
    for r in rows:
        out.append(2)
        out.extend(bytes((a - b) & 255 for a, b in zip(r, prev)))
        prev = r

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    plte = chunk(b"PLTE", b"".join(bytes(c) for c in palette)) if palette else b""
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, ct, 0, 0, 0)) + plte
            + chunk(b"IDAT", zlib.compress(bytes(out), 9)) + chunk(b"IEND", b""))


def png_uri(w, h, rows, ct, palette=None):
    return "data:image/png;base64," + base64.b64encode(png_encode(w, h, rows, ct, palette)).decode()


def png_decode(data):
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("kein PNG")
    pos, idat, hdr = 8, [], None
    while pos < len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        typ, chunk = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + ln]
        pos += 12 + ln
        if typ == b"IHDR":
            hdr = struct.unpack(">IIBBBBB", chunk)
        elif typ == b"IDAT":
            idat.append(chunk)
        elif typ == b"IEND":
            break
    w, h, bd, ct, _c, _f, il = hdr
    if bd != 8 or ct not in (2, 6) or il != 0:
        raise ValueError(f"PNG-Format nicht unterstützt (Tiefe {bd}, Typ {ct})")
    bpp = 3 if ct == 2 else 4
    raw, stride = zlib.decompress(b"".join(idat)), w * bpp
    out, prev, i = bytearray(h * stride), bytearray(stride), 0
    for y in range(h):
        ft = raw[i]
        line = bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        if ft == 1:
            for x in range(bpp, stride):
                line[x] = (line[x] + line[x - bpp]) & 255
        elif ft == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 255
        elif ft == 3:
            for x in range(stride):
                left = line[x - bpp] if x >= bpp else 0
                line[x] = (line[x] + ((left + prev[x]) >> 1)) & 255
        elif ft == 4:
            for x in range(stride):
                a = line[x - bpp] if x >= bpp else 0
                b = prev[x]
                c = prev[x - bpp] if x >= bpp else 0
                pp = a + b - c
                pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - c)
                line[x] = (line[x] + (a if (pa <= pb and pa <= pc) else (b if pb <= pc else c))) & 255
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return w, h, bpp, out


# ---------------------------------------------------------------- Geodaten
def geom_parts(g):
    t, c = (g or {}).get("type"), (g or {}).get("coordinates") or []
    if t == "LineString":
        return [c]
    if t in ("MultiLineString", "Polygon"):
        return list(c)
    if t == "MultiPolygon":
        return [ring for poly in c for ring in poly]
    return []


def load_geo(cache_dir):
    path = os.path.join(cache_dir, "geo_naturalearth_v2.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    print("Lade Geodaten (Natural Earth, einmalig) …", file=sys.stderr)
    geo = {m: {"coast": [], "borders": [], "lakes": [], "rivers": []} for m in MAPS}
    complete = True
    for kind, fname, maps in NE_LAYERS:
        try:
            data = json.loads(fetch_bytes(NE_BASE + fname + ".geojson", timeout=120))
        except (ApiError, json.JSONDecodeError) as e:
            log(f"Natural Earth {fname} ({e})")
            complete = False
            continue
        for m in maps:
            la0, la1, lo0, lo1 = MAPS[m]["bbox"]
            pad = {"europa": 2.0, "welt": 0.0}.get(m, 0.15)
            for f in data.get("features", []):
                for part in geom_parts(f.get("geometry")):
                    if part and any(lo0 - pad <= q[0] <= lo1 + pad and la0 - pad <= q[1] <= la1 + pad for q in part):
                        geo[m][kind].append([[round(q[0], 4), round(q[1], 4)] for q in part])
    if complete:
        os.makedirs(cache_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(geo, f)
    return geo


def path_d(P, parts, closed=False, tol=0.8, margin=25):
    """Linien in SVG-Pfade; Linien werden am Rand aufgetrennt, Flächen auf den Rand geklemmt."""
    out = []
    for part in parts:
        run, last = [], None
        for q in part:
            x, y = P.xy(q[1], q[0])
            inside = -margin <= x <= P.W + margin and -margin <= y <= P.H + margin
            if closed:
                x, y = min(max(x, -margin), P.W + margin), min(max(y, -margin), P.H + margin)
            elif not inside:
                if len(run) > 1:
                    out.append(run)
                run, last = [], None
                continue
            if last and abs(x - last[0]) < tol and abs(y - last[1]) < tol:
                continue
            run.append((x, y))
            last = (x, y)
        if len(run) > 1 and (not closed or any(0 <= x <= P.W and 0 <= y <= P.H for x, y in run)):
            out.append(run)
    return "".join("M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in r) + ("Z" if closed else "") for r in out)


# ---------------------------------------------------------------- Relief
def tile_frac(lat, lon, z):
    n2 = 2 ** z
    return (lon + 180) / 360 * n2, (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n2


def dem_from_tiles(P, z):
    fx0, fy0 = tile_frac(P.la1, P.lo0, z)
    fx1, fy1 = tile_frac(P.la0, P.lo1, z)
    keys = [(tx, ty) for tx in range(int(fx0), int(fx1) + 1) for ty in range(int(fy0), int(fy1) + 1)]

    def load(k):
        w, h, bpp, px = png_decode(fetch_bytes(TILE_URL.format(z=z, x=k[0], y=k[1])))
        return w, h, [px[i] * 256 + px[i + 1] + px[i + 2] / 256 - 32768 for i in range(0, len(px), bpp)]

    tiles = dict(zip(keys, pmap(load, keys, workers=6)))
    fys = [tile_frac(P.ll(0, j + 0.5)[0], P.lo0, z)[1] for j in range(P.H)]
    fxs = [tile_frac(P.la0, P.ll(i + 0.5, 0)[1], z)[0] for i in range(P.W)]
    dem = []
    for fy in fys:
        ty = int(fy)
        row = []
        for fx in fxs:
            tx = int(fx)
            w, h, e = tiles[(tx, ty)]
            u, v = (fx - tx) * w - 0.5, (fy - ty) * h - 0.5
            x0, y0 = min(max(int(math.floor(u)), 0), w - 1), min(max(int(math.floor(v)), 0), h - 1)
            x1, y1 = min(x0 + 1, w - 1), min(y0 + 1, h - 1)
            ax, ay = min(max(u - x0, 0.0), 1.0), min(max(v - y0, 0.0), 1.0)
            top = e[y0 * w + x0] * (1 - ax) + e[y0 * w + x1] * ax
            bot = e[y1 * w + x0] * (1 - ax) + e[y1 * w + x1] * ax
            row.append(round(top * (1 - ay) + bot * ay))
        dem.append(row)
    return dem


def dem_from_openmeteo(P, nx=56):
    ny = max(8, round(nx * P.H / P.W))
    pts = [P.ll((i + 0.5) * P.W / nx, (j + 0.5) * P.H / ny) for j in range(ny) for i in range(nx)]
    vals = []
    for c in range(0, len(pts), 100):
        chunk = pts[c:c + 100]
        rate_budget(10)
        j = fetch_json(API_ELEV, {"latitude": ",".join(f"{a:.4f}" for a, _ in chunk),
                                  "longitude": ",".join(f"{b:.4f}" for _, b in chunk)})
        vals += j.get("elevation", [])
        time.sleep(PAUSE)
    if len(vals) != len(pts):
        raise ApiError("Höhendaten unvollständig")
    G = [vals[j * nx:(j + 1) * nx] for j in range(ny)]
    dem = []
    for y in range(P.H):
        fy = min(max((y + 0.5) / P.H * ny - 0.5, 0), ny - 1.001)
        j0, ay = int(fy), fy - int(fy)
        row = []
        for x in range(P.W):
            fx = min(max((x + 0.5) / P.W * nx - 0.5, 0), nx - 1.001)
            i0, ax = int(fx), fx - int(fx)
            t = G[j0][i0] * (1 - ax) + G[j0][i0 + 1] * ax
            b = G[j0 + 1][i0] * (1 - ax) + G[j0 + 1][i0 + 1] * ax
            row.append(round(t * (1 - ay) + b * ay))
        dem.append(row)
    return dem


def load_dem(name, cache_dir):
    cfg = MAPS[name]
    P = Proj(cfg["bbox"], cfg["W"])
    path = os.path.join(cache_dir, f"dem_{name}_v1.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            if d.get("W") == P.W and d.get("H") == P.H:
                return d["dem"], d.get("src")
        except (OSError, json.JSONDecodeError):
            pass
    print(f"Lade Relief {name} (einmalig) …", file=sys.stderr)
    dem, src = None, None
    try:
        dem, src = dem_from_tiles(P, cfg["z"]), "Terrain Tiles"
    except (ApiError, ValueError, zlib.error, KeyError, struct.error) as e:
        log(f"Relief {name}: Terrain Tiles ({e})")
    if dem is None:
        try:
            dem, src = dem_from_openmeteo(P), "Open-Meteo-Höhenmodell"
        except ApiError as e:
            log(f"Relief {name}: Open-Meteo ({e})")
            return None, None
    os.makedirs(cache_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"W": P.W, "H": P.H, "src": src, "dem": dem}, f)
    return dem, src


def relief_uri(dem, P, vex):
    W, H = P.W, P.H
    dx = (P.lo1 - P.lo0) / W * 111320 * P.k
    dy = (P.la1 - P.la0) / H * 110574
    az, alt = math.radians(315), math.radians(45)
    Lx, Ly, Lz = math.sin(az) * math.cos(alt), math.cos(az) * math.cos(alt), math.sin(alt)
    BAYER = [[(v + 0.5) / 16 for v in row] for row in ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))]
    # Palettenbild: 32 Höhenstufen à 150 m × 8 Schattierungsstufen = 256 Farben, 1 Byte pro Pixel
    NB, NS = 32, 8
    shades = [0.5 + 0.72 * k / (NS - 1) for k in range(NS)]
    pal = []
    for band in range(NB):
        zz = band * 150 + 75
        c = TINT_STOPS[0][1] if zz <= TINT_STOPS[0][0] else TINT_STOPS[-1][1]
        for (a_, ca), (b_, cb) in zip(TINT_STOPS, TINT_STOPS[1:]):
            if a_ <= zz <= b_:
                t = (zz - a_) / (b_ - a_)
                c = tuple(x + (y - x) * t for x, y in zip(ca, cb))
                break
        pal += [tuple(min(255, int(x * f)) for x in c) for f in shades]
    rows = []
    for j in range(H):
        jm, jp = max(j - 1, 0), min(j + 1, H - 1)
        r = bytearray(W)
        for i in range(W):
            im, ip = max(i - 1, 0), min(i + 1, W - 1)
            gx = (dem[j][ip] - dem[j][im]) / ((ip - im) * dx) * vex
            gy = (dem[jm][i] - dem[jp][i]) / ((jp - jm) * dy) * vex
            sh = (-gx * Lx - gy * Ly + Lz) / math.sqrt(gx * gx + gy * gy + 1)
            bfac = min(1.22, max(0.5, 1.0 + 1.15 * (sh - Lz)))
            band = min(NB - 1, max(0, int(dem[j][i] // 150)))
            lev = (bfac - 0.5) / 0.72 * (NS - 1) + BAYER[j & 3][i & 3]
            r[i] = band * NS + min(NS - 1, max(0, int(lev)))
        rows.append(bytes(r))
    return png_uri(W, H, rows, 3, pal)


TINT_STOPS = [(200, (178, 198, 160)), (900, (192, 204, 165)), (1600, (214, 204, 168)), (2300, (206, 186, 158)),
              (3000, (178, 168, 160)), (4200, (160, 160, 168))]


CACHE_DIR = None


def relief_cached(name, dem, P, vex):
    """Das Relief hängt nur vom Höhenmodell ab: einmal rechnen, danach aus dem Cache."""
    path = os.path.join(CACHE_DIR, f"relief_{name}_v3.txt") if CACHE_DIR else None
    if path and os.path.exists(path):
        with open(path, encoding="ascii") as f:
            return f.read()
    uri = relief_uri(dem, P, vex)
    if path:
        with open(path, "w", encoding="ascii") as f:
            f.write(uri)
    return uri


def snow_uri(dem, P, level):
    rows = []
    for j in range(P.H):
        r = bytearray(2 * P.W)
        for i, z in enumerate(dem[j]):
            a = (z - level) / 80.0 + 0.5
            if a > 0:
                r[2 * i], r[2 * i + 1] = 255, int(min(1.0, a) * 215)
        rows.append(bytes(r))
    return png_uri(P.W, P.H, rows, 4)


# ---------------------------------------------------------------- Isolinien
def march(Z, level):
    """Marching Squares auf einem Raster (Zeilen × Spalten); liefert Segmente in Indexkoordinaten (x=Spalte)."""
    segs = []
    for j in range(len(Z) - 1):
        r0, r1 = Z[j], Z[j + 1]
        for i in range(len(r0) - 1):
            a, b, c, d = r0[i], r0[i + 1], r1[i + 1], r1[i]
            if a is None or b is None or c is None or d is None:
                continue
            idx = (a > level) | ((b > level) << 1) | ((c > level) << 2) | ((d > level) << 3)
            if idx == 0 or idx == 15:
                continue
            f = lambda p, q: (level - p) / (q - p) if q != p else 0.5
            T, R = (i + f(a, b), j), (i + 1, j + f(b, c))
            B, L = (i + f(d, c), j + 1), (i, j + f(a, d))
            ctr = (a + b + c + d) / 4 > level
            cases = {1: [(L, T)], 2: [(T, R)], 3: [(L, R)], 4: [(R, B)], 6: [(T, B)], 7: [(L, B)],
                     8: [(B, L)], 9: [(T, B)], 11: [(R, B)], 12: [(L, R)], 13: [(T, R)], 14: [(L, T)],
                     5: [(T, R), (B, L)] if ctr else [(L, T), (R, B)],
                     10: [(L, T), (R, B)] if ctr else [(T, R), (B, L)]}
            segs.extend(cases[idx])
    return segs


def join_segments(segs):
    key = lambda q: (round(q[0], 4), round(q[1], 4))
    ends = {}
    for k, (a, b) in enumerate(segs):
        ends.setdefault(key(a), []).append(k)
        ends.setdefault(key(b), []).append(k)
    used, lines = [False] * len(segs), []
    for k in range(len(segs)):
        if used[k]:
            continue
        used[k] = True
        line = list(segs[k])
        for _ in range(2):
            while True:
                kk = key(line[-1])
                nxt = next((m for m in ends.get(kk, []) if not used[m]), None)
                if nxt is None:
                    break
                used[nxt] = True
                a, b = segs[nxt]
                line.append(b if key(a) == kk else a)
            line.reverse()
        lines.append(line)
    return lines


def chaikin(pts, it=2):
    closed = len(pts) > 3 and abs(pts[0][0] - pts[-1][0]) < 1e-6 and abs(pts[0][1] - pts[-1][1]) < 1e-6
    for _ in range(it):
        if len(pts) < 3:
            break
        new = [] if closed else [pts[0]]
        for a, b in zip(pts, pts[1:]):
            new += [(0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1]),
                    (0.25 * a[0] + 0.75 * b[0], 0.25 * a[1] + 0.75 * b[1])]
        if closed:
            new.append(new[0])
        else:
            new.append(pts[-1])
        pts = new
    return pts


def thin(pts, tol=1.2):
    """Punkte näher als tol Pixel am zuletzt behaltenen Punkt weglassen (Endpunkt bleibt)."""
    if len(pts) < 3:
        return pts
    out = [pts[0]]
    for q in pts[1:-1]:
        if abs(q[0] - out[-1][0]) >= tol or abs(q[1] - out[-1][1]) >= tol:
            out.append(q)
    out.append(pts[-1])
    return out


def poly_len(pts):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))


def dem_contour(dem, level, step=2):
    Z = [row[::step] for row in dem[::step]]
    lines = [thin([(x * step + 0.5, y * step + 0.5) for x, y in ln], 1.5) for ln in join_segments(march(Z, level))]
    return "".join("M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in ln) for ln in lines if len(ln) > 3)


def grid_interp(G, lats, lons, lat, lon):
    fi = min(max((lat - lats[0]) / (lats[1] - lats[0]), 0.0), len(lats) - 1.0001)
    fj = min(max((lon - lons[0]) / (lons[1] - lons[0]), 0.0), len(lons) - 1.0001)
    i0, j0 = int(fi), int(fj)
    a, b = fi - i0, fj - j0
    v = (G[i0][j0], G[i0][j0 + 1], G[i0 + 1][j0], G[i0 + 1][j0 + 1])
    if None in v:
        vv = [x for x in v if x is not None]
        return sum(vv) / len(vv) if vv else None
    return (v[0] * (1 - b) + v[1] * b) * (1 - a) + (v[2] * (1 - b) + v[3] * b) * a


def field_uri(P, G, lats, lons, color, scale=2):
    w, h = P.W // scale, P.H // scale
    rows = []
    for j in range(h):
        r = bytearray(4 * w)
        for i in range(w):
            lat, lon = P.ll((i + 0.5) * scale, (j + 0.5) * scale)
            c = color(grid_interp(G, lats, lons, lat, lon))
            if c:
                r[4 * i:4 * i + 4] = bytes(c)
        rows.append(bytes(r))
    return f'<image href="{png_uri(w, h, rows, 6)}" x="0" y="0" width="{P.W}" height="{P.H}" preserveAspectRatio="none"/>'


def grid_isolines(P, G, lats, lons, levels, up=3, label=True, cls="isob", avoid=()):
    nla, nlo = (len(lats) - 1) * up + 1, (len(lons) - 1) * up + 1
    dla, dlo = (lats[1] - lats[0]) / up, (lons[1] - lons[0]) / up
    F = [[grid_interp(G, lats, lons, lats[0] + j * dla, lons[0] + i * dlo) for i in range(nlo)] for j in range(nla)]
    paths, labels = [], []
    for lev in levels:
        for ln in join_segments(march(F, lev)):
            pts = thin(chaikin([P.xy(lats[0] + y * dla, lons[0] + x * dlo) for x, y in ln], 2), 1.5)
            if len(pts) < 3:
                continue
            paths.append("M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in pts))
            L = poly_len(pts)
            if label and L > 90:
                for frac_ in ((0.5,) if L < 320 else (0.3, 0.7)):
                    x, y = pts[int(len(pts) * frac_)]
                    if 12 < x < P.W - 12 and 10 < y < P.H - 6 and all(math.hypot(x - ax, y - ay) > 24 for ax, ay in avoid):
                        labels.append(f'<text x="{x:.0f}" y="{y + 3.5:.0f}" class="ilbl" text-anchor="middle">{lev}</text>')
    return f'<path d="{"".join(paths)}" class="{cls}"/>' + "".join(labels)


def grid_extrema(P, G, lats, lons, pos=None):
    out = []
    for i in range(1, len(lats) - 1):
        for j in range(1, len(lons) - 1):
            v = G[i][j]
            nb = [G[i + a][j + b] for a in (-1, 0, 1) for b in (-1, 0, 1) if (a or b)]
            if v is None or None in nb:
                continue
            m = sum(nb) / len(nb)
            if v < min(nb) and m - v >= 0.8:
                kind = "T"
            elif v > max(nb) and v - m >= 0.8:
                kind = "H"
            else:
                continue
            x, y = P.xy(lats[i], lons[j])
            if pos is not None:
                pos.append((x, y))
            out.append(f'<text x="{x:.0f}" y="{y:.0f}" class="ext ext{kind}" text-anchor="middle">{kind}</text>'
                       f'<text x="{x:.0f}" y="{y + 12:.0f}" class="extv" text-anchor="middle">{n(v)}</text>')
    return "".join(out)


def band_color(v, stops, step, alpha):
    if v is None:
        return None
    v = math.floor(v / step) * step + step / 2
    if v <= stops[0][0]:
        c = stops[0][1]
    elif v >= stops[-1][0]:
        c = stops[-1][1]
    else:
        for (a, ca), (b, cb) in zip(stops, stops[1:]):
            if a <= v <= b:
                t = (v - a) / (b - a)
                c = tuple(round(x + (y - x) * t) for x, y in zip(ca, cb))
                break
    return (*c, alpha)


T850_STOPS = [(-24, (59, 42, 122)), (-16, (44, 95, 168)), (-8, (79, 155, 209)), (-2, (169, 211, 232)),
              (2, (240, 232, 190)), (8, (242, 181, 107)), (14, (226, 112, 63)), (20, (184, 50, 42)),
              (26, (122, 21, 40))]
PREC_BANDS = [(0.5, (205, 228, 242)), (2, (150, 196, 232)), (5, (86, 150, 210)), (10, (44, 104, 184)),
              (20, (104, 72, 178)), (40, (165, 62, 160)), (80, (212, 60, 112))]


def prec_color(v):
    if v is None or v < PREC_BANDS[0][0]:
        return None
    c = [col for thr, col in PREC_BANDS if v >= thr][-1]
    return (*c, 205)


# ---------------------------------------------------------------- Bausteine
def svg_open(P, cls=""):
    return f'<svg viewBox="0 0 {P.W} {P.H}" class="maplayer {cls}" aria-hidden="true">'


def scale_bar(P):
    px_km = P.s / 111.32
    km = next(k for k in (1, 2, 5, 10, 20, 50, 100, 200) if k * px_km >= 60)
    L, x, y = km * px_km, 12, P.H - 14
    return (f'<rect x="{x - 4}" y="{y - 14}" width="{L + 8}" height="22" rx="3" class="box"/>'
            f'<line x1="{x}" x2="{x + L:.1f}" y1="{y}" y2="{y}" class="sbar"/>'
            f'<text x="{x + L / 2:.1f}" y="{y - 4}" class="ax" text-anchor="middle">{km} km</text>')


def base_layer(P, geo, name, dem, vex):
    h = [svg_open(P, "base")]
    if dem:
        h.append(f'<image href="{relief_cached(name, dem, P, vex)}" x="0" y="0" width="{P.W}" height="{P.H}" '
                 'preserveAspectRatio="none" class="relief"/>')
    g = geo.get(name, {}) if geo else {}
    if g.get("lakes"):
        h.append(f'<path d="{path_d(P, g["lakes"], closed=True, tol=0.6)}" class="lake"/>')
    if g.get("rivers"):
        h.append(f'<path d="{path_d(P, g["rivers"], tol=0.8)}" class="river"/>')
    if g.get("borders"):
        h.append(f'<path d="{path_d(P, g["borders"], tol=0.6)}" class="border"/>')
    h.append(scale_bar(P))
    h.append("</svg>")
    return "".join(h)


def station(P, k, top, sub=None, anchor="c", strong=False):
    L = LOCAL[k]
    x, y = P.xy(L["lat"], L["lon"])
    dot = (f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{4 if strong else 2.6}" class="{"sdot2" if strong else "sdot"}"/>')
    if anchor == "c":
        t = (f'<text x="{x:.1f}" y="{y - 6:.1f}" class="lbl lt" text-anchor="middle">{top}</text>'
             + (f'<text x="{x:.1f}" y="{y + 13:.1f}" class="lbl ls" text-anchor="middle">{sub}</text>' if sub else ""))
    else:
        ta, dx = ("start", 6) if anchor == "e" else ("end", -6)
        t = (f'<text x="{x + dx:.1f}" y="{y + 1:.1f}" class="lbl lt" text-anchor="{ta}">{top}</text>'
             + (f'<text x="{x + dx:.1f}" y="{y + 12:.1f}" class="lbl ls" text-anchor="{ta}">{sub}</text>' if sub else ""))
    return dot + t


def info_box(lines, P=None, corner="tl"):
    h = 8 + 15 * len(lines)
    w = 12 + 6.4 * max(len(t) for t in lines)
    x = 8 if corner == "tl" or P is None else P.W - w - 8
    return (f'<rect x="{x:.0f}" y="8" width="{w:.0f}" height="{h}" rx="3" class="box"/>'
            + "".join(f'<text x="{x + 6:.0f}" y="{24 + 15 * k}" class="ax">{html.escape(t)}</text>'
                      for k, t in enumerate(lines)))


def map_widget(group, P, panes, base=None, top=None, legend=""):
    h = [f'<div class="mapw{" single" if len(panes) == 1 else ""}">']
    for i, (lab, _) in enumerate(panes):
        h.append(f'<input type="radio" name="{group}" id="{group}{i}"{" checked" if i == 0 else ""}>'
                 f'<label for="{group}{i}">{html.escape(lab)}</label>')
    h.append(f'<div class="stack" style="aspect-ratio:{P.W}/{P.H}">')
    if base:
        h.append(base)
    h.append('<div class="panes">' + "".join(f"<div>{svg}</div>" for _, svg in panes) + "</div>")
    if top:
        h.append(top)
    h.append("</div>" + legend + "</div>")
    return "".join(h)


def legend_html(items):
    return ('<div class="mlegend">' + "".join(
        f'<span>{f"<i style={chr(34)}{style}{chr(34)}></i>" if style else ""}{html.escape(txt)}</span>'
        for style, txt in items) + "</div>")


def tab_label(d, days):
    return "Heute" if d == days[0] else ("Morgen" if len(days) > 1 and d == days[1] else f"{WDS[d.weekday()]} {d.day}.")


# ---------------------------------------------------------------- Karten
def map_europe(D, X, geo):
    eg = D.get("eu_grid")
    if not eg:
        return None
    P = Proj(MAPS["europa"]["bbox"], MAPS["europa"]["W"])
    days = X["days"]
    panes = []
    for d in days:
        pm = [[up_at(eg, f"e{la}_{lo}", "pressure_msl", d, 12) for lo in EU_LONS] for la in EU_LATS]
        t8 = [[up_at(eg, f"e{la}_{lo}", "temperature_850hPa", d, 12) for lo in EU_LONS] for la in EU_LATS]
        if sum(v is not None for r in pm for v in r) < 0.8 * len(EU_LATS) * len(EU_LONS):
            continue
        pos = []
        ext = grid_extrema(P, pm, EU_LATS, EU_LONS, pos)
        body = [svg_open(P, "eu"),
                field_uri(P, t8, EU_LATS, EU_LONS, lambda v: band_color(v, T850_STOPS, 2, 225)),
                grid_isolines(P, pm, EU_LATS, EU_LONS, range(940, 1064, 4), avoid=pos), ext, "</svg>"]
        panes.append((f"{tab_label(d, days)}", "".join(body)))
    if not panes:
        return None
    g = geo.get("europa", {}) if geo else {}
    top = [svg_open(P, "eu top")]
    for lat in range(40, 71, 10):
        y = P.xy(lat, 0)[1]
        top.append(f'<line x1="0" x2="{P.W}" y1="{y:.1f}" y2="{y:.1f}" class="grat"/>')
    for lon in range(-20, 40, 10):
        x = P.xy(50, lon)[0]
        top.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="0" y2="{P.H}" class="grat"/>')
    if g.get("coast"):
        top.append(f'<path d="{path_d(P, g["coast"], tol=1.0)}" class="coastd"/>')
    if g.get("borders"):
        top.append(f'<path d="{path_d(P, g["borders"], tol=1.0)}" class="borderd"/>')
    x, y = P.xy(LOCAL["riederalp"]["lat"], LOCAL["riederalp"]["lon"])
    top.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.2" class="rmark"/>')
    top.append(f'<text x="{P.W - 6}" y="{P.H - 8}" class="ilbl" text-anchor="end">12 UTC</text></svg>')
    leg = legend_html([(f"background:rgb{band_color(t, T850_STOPS, 2, 255)[:3]}", f"{n(t)}")
                       for t in range(-20, 25, 4)] + [("", "°C in 850 hPa")])
    cap = ("Bodendruck (Isobaren alle 4 hPa, H Hoch, T Tief) und Temperatur in 850 hPa (Farbe) jeweils um 12 UTC, "
           f"{MODEL_NAMES.get(D.get('eu_grid_model'), 'Globalmodell')} auf einem 3°-Raster. Die Isobaren sind dadurch geglättet; Randtiefs unter rund 500 km "
           "Durchmesser fehlen. Punkt: Riederalp.")
    return map_widget("eu", P, panes, top="".join(top), legend=leg), cap


CH_LABELS = {"genf": "c", "neuchatel": "w", "basel": "c", "bern": "w", "luzern": "w", "zuerich": "c",
             "stgallen": "e", "saentis": "w", "chur": "e", "davos": "e", "samedan": "e", "altdorf": "e",
             "sion": "c", "lugano": "e", "locarno": "w", "jungfraujoch": "c", "riederalp": "e"}


def map_swiss(D, X, geo, cache_dir):
    P = Proj(MAPS["schweiz"]["bbox"], MAPS["schweiz"]["W"])
    dem, dsrc = load_dem("schweiz", cache_dir)
    base = base_layer(P, geo, "schweiz", dem, MAPS["schweiz"]["vex"])
    sg, LD, days = D.get("ch_grid"), X["local"], X["days"][:3]
    panes = []
    for d in days:
        body = [svg_open(P, "ov")]
        if sg:
            G = []
            for la in CH_LATS:
                row = []
                for lo in CH_LONS:
                    rec = sg.get(f"s{la}_{lo}")
                    try:
                        row.append(rec["v"]["precipitation_sum"][rec["d"].index(d)])
                    except (TypeError, KeyError, ValueError, IndexError):
                        row.append(None)
                G.append(row)
            if sum(v is not None for r in G for v in r) > 0.8 * len(CH_LATS) * len(CH_LONS):
                body.append(field_uri(P, G, CH_LATS, CH_LONS, prec_color))
        for k, anc in CH_LABELS.items():
            st = LD[k].get(d)
            if st:
                sub = LOCAL[k]["name"] + (f", {n(st['snow'])} cm" if st["snow"] >= 1 else "")
                body.append(station(P, k, f"{n(st['tmax'])}°", sub, anc, strong=(k == "riederalp")))
        body.append("</svg>")
        panes.append((tab_label(d, X["days"]), "".join(body)))
    leg = legend_html([(f"background:rgb{c}", f"{n(t, 1 if t < 1 else 0)}") for t, c in PREC_BANDS]
                      + [("", "mm pro Tag")])
    model = MODEL_NAMES.get(D.get("ch_grid_model"), "Modell")
    cap = (f"Niederschlag pro Tag (Farbe, {model} auf einem 0,2°-Raster, interpoliert) und Höchstwert an den Orten "
           "des Berichts, bei Neuschnee mit Menge. Relief: " + (dsrc or "nicht verfügbar") +
           "; Gewässer und Grenzen: Natural Earth.")
    return map_widget("ch", P, panes, base=base, legend=leg), cap


def region_levels(X, d, keys):
    ss = [X["local"][k][d] for k in keys if X["local"][k].get(d)]
    wet = [s["sfh_med"] for s in ss if s["prec"] >= 0.3 and s.get("sfh_med") is not None]
    fz = [s["fzl_mean"] for s in ss if s.get("fzl_mean") is not None]
    return (quant(wet, .5) if wet else None), (quant(fz, .5) if fz else None)


def snow_panes(P, dem, X, keys, label_fn, group_levels, corner="tl"):
    panes = []
    for d in X["days"][:3]:
        sfh, fzl = group_levels(d)
        body = [svg_open(P, "ov")]
        if dem and sfh is not None:
            body.append(f'<image href="{snow_uri(dem, P, sfh)}" x="0" y="0" width="{P.W}" height="{P.H}" '
                        'preserveAspectRatio="none"/>')
            body.append(f'<path d="{dem_contour(dem, sfh)}" class="csfh"/>')
        if dem and fzl is not None:
            body.append(f'<path d="{dem_contour(dem, fzl)}" class="cfzl"/>')
        body.extend(label_fn(d))
        lines = [f"Schneefallgrenze {hm(sfh)}" if sfh is not None else "kein nennenswerter Niederschlag",
                 f"Nullgradgrenze {hm(fzl)}"]
        body.append(info_box(lines, P, corner))
        body.append("</svg>")
        panes.append((tab_label(d, X["days"]), "".join(body)))
    return panes


SNOW_LEGEND = [("background:#fff;border:1px solid var(--rule)", "Niederschlag als Schnee"),
               ("height:0;border-top:2px solid var(--ice)", "Schneefallgrenze"),
               ("height:0;border-top:2px dashed var(--contour)", "Nullgradgrenze")]


def map_wallis(D, X, geo, cache_dir):
    P = Proj(MAPS["wallis"]["bbox"], MAPS["wallis"]["W"])
    dem, dsrc = load_dem("wallis", cache_dir)
    keys = ["sion", "visp", "martigny", "zermatt", "simplon", "grimsel", "riederalp", "moosfluh"]
    anchors = {"martigny": "c", "sion": "c", "visp": "w", "zermatt": "c", "simplon": "e", "grimsel": "w",
               "riederalp": "e"}

    def labels(d):
        out = []
        for k, anc in anchors.items():
            st = X["local"][k].get(d)
            if st:
                sub = LOCAL[k]["name"] + (f", +{n(st['snow'])} cm" if st["snow"] >= 1 else "")
                out.append(station(P, k, f"{n(st['tmax'])}°", sub, anc, strong=(k == "riederalp")))
        return out

    panes = snow_panes(P, dem, X, keys, labels, lambda d: region_levels(X, d, keys))
    cap = ("Gelände oberhalb der mittleren Schneefallgrenze der Walliser Orte (weiss, nur an Tagen mit Niederschlag), "
           "Schneefallgrenze und Nullgradgrenze als Höhenlinien; Zahlen: Höchstwert und Neuschnee. Die Grenzen sind "
           "regionale Mittelwerte, im Stau und im Lee liegen sie oft mehrere hundert Meter auseinander. Relief: "
           + (dsrc or "nicht verfügbar") + "; Gewässer und Grenzen: Natural Earth.")
    return map_widget("vs", P, panes, base=base_layer(P, geo, "wallis", dem, MAPS["wallis"]["vex"]),
                      legend=legend_html(SNOW_LEGEND)), cap


RIED_NAMES = [("Bettmeralp", 46.3917, 8.0639), ("Eggishorn", 46.4278, 8.0922)]


def map_ried(D, X, geo, cache_dir):
    P = Proj(MAPS["riederalp"]["bbox"], MAPS["riederalp"]["W"])
    dem, dsrc = load_dem("riederalp", cache_dir)
    anchors = {"moerel": "e", "riederalp": "w", "moosfluh": "w", "jungfraujoch": "e"}

    def labels(d):
        out = []
        for nm, la, lo in RIED_NAMES:
            x, y = P.xy(la, lo)
            out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2" class="sdot"/>'
                       f'<text x="{x + 5:.1f}" y="{y + 4:.1f}" class="lbl ls">{nm}</text>')
        for k, anc in anchors.items():
            st = X["local"][k].get(d)
            if st:
                out.append(station(P, k, f"{n(st['tmin'])} / {n(st['tmax'])}°",
                                   f"{LOCAL[k]['name']} {LOCAL[k]['elev']} m", anc, strong=(k == "riederalp")))
        return out

    def levels(d):
        s = X["local"]["riederalp"].get(d)
        if not s:
            return None, None
        return (s["sfh_med"] if s["prec"] >= 0.3 else None), s.get("fzl_mean")

    panes = snow_panes(P, dem, X, ["riederalp"], labels, levels, corner="tr")
    cap = ("Riederalp und Aletschgebiet: Gelände oberhalb der Schneefallgrenze der Riederalp (weiss, an Tagen mit "
           "Niederschlag), Schneefallgrenze und Nullgradgrenze als Höhenlinien, Zahlen: Tiefst- und Höchstwert. Relief: "
           + (dsrc or "nicht verfügbar") + ".")
    return map_widget("ri", P, panes, base=base_layer(P, geo, "riederalp", dem, MAPS["riederalp"]["vex"]),
                      legend=legend_html(SNOW_LEGEND)), cap


def build_maps(D, X, cache_dir):
    out = {}
    try:
        geo = load_geo(cache_dir)
    except Exception as e:  # Karten sind Beiwerk: nie den Bericht gefährden
        log(f"Geodaten ({type(e).__name__}: {e})")
        geo = None
    for key, fn, args in (("karte_europa", map_europe, (D, X, geo)),
                          ("karte_schweiz", map_swiss, (D, X, geo, cache_dir)),
                          ("karte_wallis", map_wallis, (D, X, geo, cache_dir)),
                          ("karte_ried", map_ried, (D, X, geo, cache_dir)),
                          ("karte_welt", map_world, (X, geo))):
        print(f"Zeichne {key} …", file=sys.stderr)
        try:
            res = fn(*args)
            if res:
                out[key] = res
        except Exception as e:
            log(f"Karte {key} ({type(e).__name__}: {e})")
    return out


# ================================================================ HTML
def inline(s):
    s = html.escape(s, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
    return s


def slug(s):
    s = s.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


COLLAPSE = {"Methodik und Quellen"}


def md_to_html(md, charts):
    out, para, i = [], [], 0
    open_details = [False]
    lines = md.split("\n")

    def flush():
        if para:
            out.append(f"<p>{inline(' '.join(para))}</p>")
            para.clear()

    while i < len(lines):
        ln = lines[i]
        m = re.match(r"<!--CHART:(\w+)-->", ln)
        if m:
            flush()
            ch = charts.get(m.group(1))
            if ch:
                out.append(f'<figure class="fig-{m.group(1)}">{ch[0]}<figcaption>{html.escape(ch[1])}</figcaption></figure>')
            i += 1
            continue
        if ln.startswith("<!--"):
            flush()
            i += 1
            continue
        h = re.match(r"(#{1,3}) (.*)", ln)
        if h:
            flush()
            lvl, txt = len(h.group(1)), h.group(2)
            if lvl == 2 and open_details[0]:
                out.append("</details>")
                open_details[0] = False
            idattr = f' id="{slug(txt)}"' if lvl == 2 else ""
            if lvl == 2 and txt in COLLAPSE:
                out.append(f'<details class="sec"><summary><h2{idattr}>{inline(txt)}</h2></summary>')
                open_details[0] = True
            else:
                out.append(f"<h{lvl}{idattr}>{inline(txt)}</h{lvl}>")
            i += 1
            continue
        if ln.startswith("- "):
            flush()
            items = []
            while i < len(lines) and lines[i].startswith("- "):
                items.append(f"<li>{inline(lines[i][2:])}</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue
        if ln.startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            head, body = rows[0], [r for r in rows[1:] if not all(set(c) <= set("-:") for c in r)]
            t = ["<div class='tw'><table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead><tbody>"]
            for r in body:
                t.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>")
            t.append("</tbody></table></div>")
            out.append("".join(t))
            continue
        if not ln.strip():
            flush()
            i += 1
            continue
        para.append(ln)
        i += 1
    flush()
    if open_details[0]:
        out.append("</details>")
    return "\n".join(out)


CSS = """
:root{--paper:#eef1ec;--sheet:#f8f9f6;--ink:#1d2a2f;--muted:#566569;--rule:#c8cfc8;--contour:#9b6530;
--ice:#2c6a95;--ice2:rgba(44,106,149,.16);--ice3:rgba(44,106,149,.30);--rain:#2a8577;--warm:#b0482c;color-scheme:light;
--water:#b9d5e7;--water2:#6d9cbd;
box-sizing:border-box;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--paper:#11181b;--sheet:#172024;--ink:#dce4e5;--muted:#93a3a7;
--rule:#2c383c;--contour:#d49b5c;--ice:#7cb5dd;--ice2:rgba(124,181,221,.16);--ice3:rgba(124,181,221,.30);--rain:#6cc6b7;--warm:#e2876b;color-scheme:dark;
--water:#1f3b4a;--water2:#4b7f9c}}
:root[data-theme="dark"]{--paper:#11181b;--sheet:#172024;--ink:#dce4e5;--muted:#93a3a7;--rule:#2c383c;--contour:#d49b5c;
--ice:#7cb5dd;--ice2:rgba(124,181,221,.16);--ice3:rgba(124,181,221,.30);--rain:#6cc6b7;--warm:#e2876b;color-scheme:dark;
--water:#1f3b4a;--water2:#4b7f9c}
*{box-sizing:border-box}
html{scroll-padding-top:env(safe-area-inset-top,0px)}
body{margin:0;background:var(--paper);color:var(--ink);font:17px/1.62 "Literata",Georgia,"Times New Roman",serif;
font-variant-numeric:tabular-nums}
main{max-width:44rem;margin:0 auto;padding:2.2rem 1.25rem 4rem}
h1,h2,h3,nav,figure text,table,.meta{font-family:"Barlow Semi Condensed","Arial Narrow",Arial,sans-serif}
h1{font-size:2.6rem;line-height:1.05;font-weight:600;margin:0 0 .6rem;letter-spacing:-.01em}
h2{font-size:1.65rem;font-weight:600;margin:3rem 0 .6rem;padding-top:.7rem;border-top:2px solid var(--contour)}
h3{font-size:1.15rem;font-weight:600;margin:1.6rem 0 .3rem;color:var(--muted)}
p{margin:.5rem 0 1rem;hyphens:auto}
ul{padding-left:1.1rem}li{margin:.35rem 0}
strong{font-weight:600}
code{font-size:.9em}
nav{display:flex;flex-wrap:wrap;gap:.3rem 1rem;font-size:.98rem;margin:1rem 0 1.4rem}
nav a{color:var(--ice);text-decoration:none;border-bottom:1px solid var(--ice2)}
nav a:hover,nav a:focus-visible{border-bottom-color:var(--ice)}
a:focus-visible{outline:2px solid var(--ice);outline-offset:2px}
figure{margin:1.2rem 0 1.6rem;background:var(--sheet);border:1px solid var(--rule);border-radius:6px;padding:.7rem .5rem .3rem;
max-width:34rem}
.fig-profil{margin-top:1.4rem}
svg.chart{width:100%;height:auto;display:block}
svg .grid{stroke:var(--rule);stroke-width:1}
svg .axl{stroke:var(--muted);stroke-width:1}
svg .zero{stroke:var(--ice);stroke-width:1.2;stroke-dasharray:2 3}
svg .ax{fill:var(--muted);font-size:13px}
svg .ref{stroke:var(--muted);stroke-width:1;stroke-dasharray:1 4;opacity:.8}
figcaption{font-family:"Barlow Semi Condensed","Arial Narrow",Arial,sans-serif;font-size:.92rem;line-height:1.4;
color:var(--muted);padding:.5rem .5rem .2rem}
svg .fzl{fill:none;stroke:var(--contour);stroke-width:1.5;stroke-dasharray:6 4}
svg .fzlt{fill:var(--contour);font-size:13px}
svg .m07{fill:none;stroke:var(--ice);stroke-width:2;stroke-dasharray:5 3}
svg .m15{fill:none;stroke:var(--warm);stroke-width:2.4}
svg .m07d{fill:var(--ice)}svg .m15d{fill:var(--warm)}
svg .st{fill:var(--ink);font-size:14px;font-weight:600}
svg .stv{fill:var(--muted);font-size:12.5px}
svg .b1{fill:var(--ice2)}svg .b2{fill:var(--ice3)}
svg .med{fill:none;stroke:var(--ice);stroke-width:1.6}
svg .det0{fill:none;stroke:var(--warm);stroke-width:1.4}
svg .det1{fill:none;stroke:var(--contour);stroke-width:1.2;stroke-dasharray:4 3}
svg .bar{fill:var(--rain);opacity:.75}
svg .snowbar{fill:var(--ice);opacity:.55}
svg .sfh{fill:none;stroke:var(--ice);stroke-width:1;opacity:.55}
svg .sfhw{fill:none;stroke:var(--ice);stroke-width:2.8}
svg .sfhwd{fill:var(--ice)}
svg .elev{stroke:var(--muted);stroke-width:1;stroke-dasharray:1 3}
svg .elev1{stroke:var(--ink);stroke-width:1.2;stroke-dasharray:3 3}
.tw{overflow-x:auto;margin:.8rem 0 1.2rem;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;font-size:.95rem;min-width:100%}
th,td{text-align:left;padding:.35rem .6rem;border-bottom:1px solid var(--rule);vertical-align:top;white-space:nowrap}
td:first-child,td:nth-child(2){white-space:normal;min-width:5.5rem}
th{font-weight:600;color:var(--muted)}
.meta{color:var(--muted);font-size:1.05rem;margin:0}
.narr{border-left:3px solid var(--contour);padding:.2rem 0 .2rem 1rem;margin:1rem 0}
footer{margin-top:3rem;color:var(--muted);font-size:.9rem}
.mapw>input{position:absolute;opacity:0;width:1px;height:1px;pointer-events:none}
.mapw>label{display:inline-block;font-family:"Barlow Semi Condensed","Arial Narrow",Arial,sans-serif;font-size:.92rem;
padding:.15rem .65rem;margin:0 .3rem .5rem 0;border:1px solid var(--rule);border-radius:999px;cursor:pointer;color:var(--muted)}
.mapw>input:checked+label{background:var(--ice);border-color:var(--ice);color:var(--sheet)}
.mapw.single>label{display:none}
.mapw>input:focus-visible+label{outline:2px solid var(--ice);outline-offset:2px}
.stack{position:relative;width:100%;border-radius:4px;overflow:hidden;background:var(--paper)}
.stack>svg,.stack>.panes{position:absolute;inset:0;width:100%;height:100%}
.panes>div{display:none;width:100%;height:100%}
svg.maplayer{width:100%;height:100%;display:block}
.mapw>input:nth-of-type(1):checked~.stack .panes>div:nth-child(1){display:block}
.mapw>input:nth-of-type(2):checked~.stack .panes>div:nth-child(2){display:block}
.mapw>input:nth-of-type(3):checked~.stack .panes>div:nth-child(3){display:block}
.mapw>input:nth-of-type(4):checked~.stack .panes>div:nth-child(4){display:block}
.mapw>input:nth-of-type(5):checked~.stack .panes>div:nth-child(5){display:block}
.mapw>input:nth-of-type(6):checked~.stack .panes>div:nth-child(6){display:block}
.mapw>input:nth-of-type(7):checked~.stack .panes>div:nth-child(7){display:block}
.mapw>input:nth-of-type(8):checked~.stack .panes>div:nth-child(8){display:block}
.mapw>input:nth-of-type(9):checked~.stack .panes>div:nth-child(9){display:block}
.mapw>input:nth-of-type(10):checked~.stack .panes>div:nth-child(10){display:block}
svg .lake{fill:var(--water)}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]) svg .relief{filter:brightness(.6) saturate(.8)}}
:root[data-theme="dark"] svg .relief{filter:brightness(.6) saturate(.8)}
svg .river{fill:none;stroke:var(--water2);stroke-width:1.1}
svg .border{fill:none;stroke:var(--ink);stroke-width:.8;stroke-dasharray:4 2;opacity:.5}
svg .sbar{stroke:var(--ink);stroke-width:2}
svg .box{fill:var(--sheet);opacity:.86}
svg .lbl{paint-order:stroke;stroke:var(--sheet);stroke-width:3px;stroke-linejoin:round}
svg .lt{fill:var(--ink);font-size:12.5px;font-weight:600}
svg .ls{fill:var(--muted);font-size:10.5px}
svg .sdot{fill:var(--ink)}
svg .sdot2{fill:var(--warm);stroke:var(--sheet);stroke-width:1.5}
svg .csfh{fill:none;stroke:var(--ice);stroke-width:1.6}
svg .cfzl{fill:none;stroke:var(--contour);stroke-width:1.6;stroke-dasharray:5 3}
svg .isob{fill:none;stroke:#1c2328;stroke-width:.9}
svg .ilbl{font-size:10px;fill:#1c2328;paint-order:stroke;stroke:rgba(255,255,255,.85);stroke-width:2.5px}
svg .ext{font-size:16px;font-weight:700;paint-order:stroke;stroke:rgba(255,255,255,.9);stroke-width:3px}
svg .extH{fill:#1f5f99}svg .extT{fill:#b0302a}
svg .extv{font-size:10px;fill:#1c2328;paint-order:stroke;stroke:rgba(255,255,255,.85);stroke-width:2.5px}
svg .coastd{fill:none;stroke:#26323a;stroke-width:.8}
svg .borderd{fill:none;stroke:#26323a;stroke-width:.5;stroke-dasharray:2 2;opacity:.8}
svg .grat{stroke:#26323a;stroke-width:.4;opacity:.25}
svg .rmark{fill:#fff;stroke:#b0302a;stroke-width:2}
svg .ocean{fill:var(--water);opacity:.55}
svg .coastw{fill:none;stroke:var(--ink);stroke-width:.7;opacity:.6}
svg .wdot{stroke:#1c2328;stroke-width:.6}
details.sec>summary{list-style:none;cursor:pointer}
details.sec>summary::-webkit-details-marker{display:none}
details.sec>summary h2::after{content:" ▸";color:var(--muted);font-weight:400}
details.sec[open]>summary h2::after{content:" ▾"}
details.sec>summary:focus-visible{outline:2px solid var(--ice);outline-offset:4px}
.mlegend{display:flex;flex-wrap:wrap;gap:.25rem .7rem;font-family:"Barlow Semi Condensed","Arial Narrow",Arial,sans-serif;
font-size:.86rem;color:var(--muted);padding:.55rem .2rem 0}
.mlegend span{display:inline-flex;align-items:center;gap:.3rem}
.mlegend i{display:inline-block;width:14px;height:10px;border-radius:2px}

@media (max-width:520px){body{font-size:16px}h1{font-size:2.1rem}}
"""


def render_html(md, charts, X):
    body = md_to_html(md, charts)
    sections = re.findall(r'<h2 id="([^"]+)">([^<]+)</h2>', body)
    nav = "<nav aria-label='Inhalt'>" + "".join(f'<a href="#{i}">{html.escape(html.unescape(t))}</a>' for i, t in sections) + "</nav>"
    body = body.replace("</p>", "</p>" + nav, 1) if "</p>" in body else nav + body
    now = X["now"]
    return f"""<!doctype html>
<html lang="de-CH">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Wetterbericht Riederalp, {now:%d.%m.%Y %H:%M}</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>⛰️</text></svg>">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Semi+Condensed:wght@400;600&family=Literata:opsz,wght@7..72,400;7..72,600&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head>
<body>
<main>
{body}
<footer>Daten: Open-Meteo.com (CC BY 4.0) mit Modellen von MeteoSchweiz, ECMWF, DWD, NOAA, Météo-France und ItaliaMeteo; ERA5-Land und CAMS (Copernicus). Messwerte und Lokalprognose: Quelle MeteoSchweiz. Lawinenbulletin: SLF (slf.ch). Abfluss: GloFAS (Copernicus). Naturgefahren: GDACS. ENSO: NOAA CPC. Kartengrundlagen: Natural Earth; Relief: Terrain Tiles (Mapzen, AWS Open Data). Kein offizieller Bericht von MeteoSchweiz.</footer>
</main>
</body>
</html>"""


# ================================================================ Claude
def claude_narrative(X, md):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        log("Claude-Einordnung (ANTHROPIC_API_KEY fehlt)")
        return None
    model = os.environ.get("WB_CLAUDE_MODEL", "claude-sonnet-5-5")
    system = (
        "Du bist Prognostiker bei einem Schweizer Wetterdienst und schreibst für eine Leserschaft aus der "
        "Klima- und Atmosphärenforschung. Schreibe auf Deutsch in Schweizer Rechtschreibung (kein ß). "
        "Aufgabe: eine synoptische Einordnung von höchstens 220 Wörtern in drei Absätzen ohne Überschriften: "
        "(1) Grosswetterlage und ihre Entwicklung, (2) der physikalisch interessanteste Mechanismus dieser Lage "
        "für Riederalp und Wallis (z. B. Föhn, Stau, Inversion, Kaltluftsee, Blockierung, Staub) mit Begründung "
        "aus den Diagnosen, (3) wo die Unsicherheit steckt und woran man sie in den nächsten Läufen erkennt. "
        "Verwende ausschliesslich Zahlen, die im Bericht stehen, und kennzeichne Vermutungen als solche. "
        "Keine Aufzählungen, kein Markdown ausser **fett** für höchstens zwei Begriffe.")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps({"model": model, "max_tokens": 1200, "system": system,
                         "messages": [{"role": "user", "content": "Automatisch erzeugter Bericht:\n\n" + md[:60000]}]}).encode(),
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read().decode("utf-8"))
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
        return text or None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        log(f"Claude-Einordnung ({e})")
        return None


# ================================================================ Ausgabe
def to_jsonable(o):
    if isinstance(o, dict):
        return {(k.isoformat() if isinstance(k, (dt.date, dt.datetime)) else str(k)): to_jsonable(v)
                for k, v in o.items() if not (isinstance(k, str) and k.startswith("_"))}
    if isinstance(o, (list, tuple)):
        return [to_jsonable(v) for v in o]
    if isinstance(o, (dt.date, dt.datetime)):
        return o.isoformat()
    if isinstance(o, float):
        return round(o, 3)
    return o


def main():
    ap = argparse.ArgumentParser(description="Wetterbericht Riederalp – Wallis – Schweiz – Europa (Open-Meteo)")
    ap.add_argument("--out", default="wetterbericht_out", help="Ausgabeordner")
    ap.add_argument("--cache", default="cache", help="Cache-Ordner für die Klimatologie")
    ap.add_argument("--days", type=int, default=7, help="Anzahl Tage inkl. heute (3–10)")
    ap.add_argument("--claude", action="store_true", help="synoptische Einordnung über die Claude API")
    args = ap.parse_args()
    global CACHE_DIR
    CACHE_DIR = args.cache
    os.makedirs(args.cache, exist_ok=True)
    load_model_ids(args.cache)
    now = dt.datetime.now(TZ)
    days = [now.date() + dt.timedelta(i) for i in range(max(3, min(10, args.days)))]
    D = collect(days, args.cache)
    if not D["primary"]:
        print("Keine Primärdaten erhalten – Abbruch.", file=sys.stderr)
        sys.exit(1)
    X = diagnose(D, days, now)
    diagnose_extra(D, X)
    md = build_report(X)
    narr = claude_narrative(X, md) if args.claude else None
    if narr:
        block = ("## Synoptische Einordnung\n\n" + narr.strip() +
                 "\n\nFormuliert von Claude auf Grundlage der Diagnosen in diesem Bericht; Zahlen in den Tabellen sind massgeblich.\n")
        md = md.replace("<!--NARRATIV-->", block)
    charts = {"profil": svg_profile(X), "fan": svg_fan(X), "schnee": svg_snow(X)}
    charts.update(build_maps(D, X, args.cache))
    os.makedirs(args.out, exist_ok=True)
    md_clean = re.sub(r"<!--[^>]*-->\n?", "", md)
    with open(os.path.join(args.out, "bericht.md"), "w", encoding="utf-8") as f:
        f.write(md_clean)
    with open(os.path.join(args.out, "index.html"), "w", encoding="utf-8") as f:
        f.write(render_html(md, charts, X))
    diag = {k: v for k, v in X.items() if k not in ("ens_hourly", "det_hourly")}
    with open(os.path.join(args.out, "diagnostik.json"), "w", encoding="utf-8") as f:
        json.dump(to_jsonable(diag), f, ensure_ascii=False, indent=1)
    save_model_ids(args.cache)
    kurz = short_summary(X, safe_lines(insights, X))
    with open(os.path.join(args.out, "kurz.txt"), "w", encoding="utf-8") as f:
        f.write(kurz + "\n")
    push_ntfy(kurz)
    print(f"Fertig: {os.path.join(args.out, 'index.html')}", file=sys.stderr)


if __name__ == "__main__":
    main()
