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

Nur Python-Standardbibliothek (>= 3.9).

  python3 wetterbericht.py                  # schreibt nach ./wetterbericht_out
  python3 wetterbericht.py --out site       # z. B. für GitHub Pages
  python3 wetterbericht.py --claude         # + synoptische Einordnung via Claude API
                                            #   (Umgebungsvariable ANTHROPIC_API_KEY)

Daten: Open-Meteo.com, CC BY 4.0 (nicht-kommerzielle Nutzung).
"""
from __future__ import annotations

import argparse
import calendar
import datetime as dt
import html
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

VERSION = "1.0"
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
                "snowfall_height", "sunshine_duration", "cape"]
ENS_VARS = ["temperature_2m", "precipitation", "snowfall", "cloud_cover_low"]
PAUSE_ENS = 8  # Ensemble-Aufrufe sind teuer (Member × Variablen); Minutenlimit schonen
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


def fetch_json(url, params=None, timeout=90, retries=4, quiet=False):
    full = url + ("?" + urllib.parse.urlencode(params, safe=",:") if params else "")
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(full, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
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


def fetch_model(api, label, candidates, params, keys, days):
    last = None
    for mid in candidates:
        p = dict(params)
        p["models"] = mid
        try:
            data = fetch_json(api, p)
            time.sleep(PAUSE)
            parsed = parse_hourly(data, keys, days)
            if not any(valid(arr) for rec in parsed.values() for arr in rec["v"].values()):
                raise ApiError("nur leere Werte")
            return mid, parsed
        except ApiError as e:
            last = e
            time.sleep(PAUSE)
    log(f"{label} ({last})")
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
        mid, parsed = fetch_model(API_ENS, label, cands, ebase, ENS_LOCS, days)
        if parsed:
            D["ens"].append((label, mid, parsed))
            D["runs"][label] = mid
        time.sleep(PAUSE_ENS)

    print("Lade Druckflächen Europa …", file=sys.stderr)
    ukeys = list(UPPER)
    udays = days + [days[-1] + dt.timedelta(1)]
    ubase = {**coords(ukeys, UPPER, elev=False), "hourly": ",".join(UPPER_VARS),
             "timezone": "GMT", "forecast_days": nd, "wind_speed_unit": "ms",
             "cell_selection": "nearest"}
    mid, up = fetch_model(API_FC, "ECMWF IFS 0,25° (Druckflächen)", ["ecmwf_ifs025"], ubase, ukeys, udays)
    D["up"] = up
    if mid:
        D["runs"]["IFS 0,25° Druckflächen"] = mid

    print("Lade CAMS-Aerosol …", file=sys.stderr)
    try:
        aqp = {**coords(["riederalp"], LOCAL, elev=False), "hourly": "dust,aerosol_optical_depth",
               "timezone": TZNAME, "forecast_days": min(5, nd)}
        D["aq"] = parse_hourly(fetch_json(API_AQ, aqp), ["riederalp"], days)
    except ApiError as e:
        log(f"CAMS-Aerosol ({e})")

    print("Klimatologie ERA5-Land …", file=sys.stderr)
    D["clim"] = clim_fetch(cache_dir, days)

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
    return {
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
    return {"n": len(tmax), "tmax": qstats(tmax), "tmin": qstats(per_member("temperature_2m", min)),
            "prec": qstats(prec), "p1": frac(prec, 1.0), "p10": frac(prec, 10.0),
            "p30": frac(prec, 30.0), "psnow5": frac(snow, 5.0), "snow": qstats(snow),
            "plow": frac(lowm, 80.0)}


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


def prec_sentence(s, ens=None, sfh=True, place=None):
    p = s["prec"]
    pe = ens.get("p1") if ens else None
    if p < 0.3:
        if pe is not None and pe >= 0.3:
            return f"Im Hauptlauf trocken, im Ensemble aber {pct(pe)} Wahrscheinlichkeit für mindestens 1 mm."
        return ""
    out = f"{cap(prec_kind(s))} {s['when']}, {place or 'im Hauptlauf'} {n(p, 1)} mm"
    if s["snow"] >= 1:
        out += f" (davon rund {n(s['snow'])} cm Neuschnee)"
    out += "."
    if ens and ens.get("prec"):
        out += (f" Ensemble: {pct(pe)} für ≥ 1 mm, Median {n(ens['prec']['p50'], 1)} mm, "
                f"90-%-Quantil {n(ens['prec']['p90'], 1)} mm.")
    if sfh and s.get("sfh_wet") is not None:
        out += f" Schneefallgrenze um {hm(s['sfh_wet'])}."
    return out


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
    A("<!--CHART:profil-->")
    A("")

    # ---------- Kernaussagen
    A("## Kernaussagen")
    A("")
    for txt in insights(X):
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
        ps = prec_sentence(s, es)
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
        cf = X["conf"].get(d)
        if cf:
            parts.append(f"Prognosesicherheit {cf['label']}.")
        if d == d0 and now.hour >= 15:
            parts.append(night_text(X))
        A(" ".join(p for p in parts if p))
        A("")
    if len(days) > 2:
        A("### Folgetage")
        A("")
        A("| Tag | Wetter | Temperatur | Niederschlag | Nullgradgrenze | Sicherheit |")
        A("|---|---|---|---|---|---|")
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
            A(f"| {dshort(d)} | {wx} | {tt} | {pr} | {hm(s['fzl_mean'])} | {cf} |")
        A("")
        A("Temperatur: Tiefst- / Höchstwert im Hauptlauf, in Klammern 10–90 % des Ensembles für den Höchstwert. "
          "Niederschlag: Tagessumme im Hauptlauf und Wahrscheinlichkeit für mindestens 1 mm.")
        A("")
        A(trend_text(X))
        A("")
    A("<!--CHART:fan-->")
    A("")

    # ---------- Wallis
    A("## Wallis")
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

    # ---------- Schweiz
    A("## Schweiz")
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

    # ---------- Europa
    A("## Europa")
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
                A(f"- **{dshort(d)}** {europe_short(e, prev)}")
                prev = e
        A("")
    else:
        A("Druckflächendaten nicht verfügbar.")
        A("")

    # ---------- Unsicherheiten
    A("## Unsicherheiten und Modellvergleich")
    A("")
    out.extend(uncertainty_section(X))

    # ---------- Klima
    A("## Klimatologische Einordnung")
    A("")
    out.extend(clim_section(X))

    # ---------- Methodik
    A("## Methodik und Quellen")
    A("")
    out.extend(method_section(X))
    return "\n".join(out)


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
            parts.append(prec_sentence(simp, es[1] if es else None, place="am Simplon"))
        if sion and simp["prec"] >= 5 and simp["prec"] >= 3 * (sion["prec"] + 0.3):
            parts.append(f"Südstaumuster: Simplon {n(simp['prec'], 1)} mm gegenüber {n(sion['prec'], 1)} mm "
                         "in Sion, die inneralpine Lage des Mittelwallis schirmt deutlich ab.")
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
        bits.append(f"Simplon {n(simp['prec'], 1)} mm")
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
        e = X["eu"].get(d) if X["eu"] else None
        if e and e.get("w700") and e["w700"][1] is not None:
            t += f" Höhenwind in 700 hPa (rund 3000 m) aus {dname(e['w700'][0])} mit {n(e['w700'][1] * 3.6)} km/h."
        lines.append(t)
    return lines


def swiss_short(X, d):
    LD = X["local"]
    zh, lu, ge = LD["zuerich"].get(d), LD["lugano"].get(d), LD["genf"].get(d)
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
        if r and r["snow"] >= 10:
            I.append((8, f"Neuschnee {lab} auf der Riederalp: rund {n(r['snow'])} cm."))
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


def md_to_html(md, charts):
    out, para, i = [], [], 0
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
            idattr = f' id="{slug(txt)}"' if lvl == 2 else ""
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
    return "\n".join(out)


CSS = """
:root{--paper:#eef1ec;--sheet:#f8f9f6;--ink:#1d2a2f;--muted:#566569;--rule:#c8cfc8;--contour:#9b6530;
--ice:#2c6a95;--ice2:rgba(44,106,149,.16);--ice3:rgba(44,106,149,.30);--rain:#2a8577;--warm:#b0482c;color-scheme:light;
box-sizing:border-box;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--paper:#11181b;--sheet:#172024;--ink:#dce4e5;--muted:#93a3a7;
--rule:#2c383c;--contour:#d49b5c;--ice:#7cb5dd;--ice2:rgba(124,181,221,.16);--ice3:rgba(124,181,221,.30);--rain:#6cc6b7;--warm:#e2876b;color-scheme:dark}}
:root[data-theme="dark"]{--paper:#11181b;--sheet:#172024;--ink:#dce4e5;--muted:#93a3a7;--rule:#2c383c;--contour:#d49b5c;
--ice:#7cb5dd;--ice2:rgba(124,181,221,.16);--ice3:rgba(124,181,221,.30);--rain:#6cc6b7;--warm:#e2876b;color-scheme:dark}
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
svg .fzl{stroke:var(--contour);stroke-width:1.5;stroke-dasharray:6 4}
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
.tw{overflow-x:auto;margin:.8rem 0 1.2rem;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;font-size:.95rem;min-width:100%}
th,td{text-align:left;padding:.35rem .6rem;border-bottom:1px solid var(--rule);vertical-align:top;white-space:nowrap}
td:nth-child(2){white-space:normal;min-width:9rem}
th{font-weight:600;color:var(--muted)}
.meta{color:var(--muted);font-size:1.05rem;margin:0}
.narr{border-left:3px solid var(--contour);padding:.2rem 0 .2rem 1rem;margin:1rem 0}
footer{margin-top:3rem;color:var(--muted);font-size:.9rem}
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
<footer>Daten: Open-Meteo.com (CC BY 4.0) mit Modellen von MeteoSchweiz, ECMWF, DWD, NOAA, Météo-France und ItaliaMeteo; ERA5-Land und CAMS (Copernicus). Kein offizieller Bericht von MeteoSchweiz.</footer>
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
    now = dt.datetime.now(TZ)
    days = [now.date() + dt.timedelta(i) for i in range(max(3, min(10, args.days)))]
    D = collect(days, args.cache)
    if not D["primary"]:
        print("Keine Primärdaten erhalten – Abbruch.", file=sys.stderr)
        sys.exit(1)
    X = diagnose(D, days, now)
    md = build_report(X)
    narr = claude_narrative(X, md) if args.claude else None
    if narr:
        block = ("## Synoptische Einordnung\n\n" + narr.strip() +
                 "\n\nFormuliert von Claude auf Grundlage der Diagnosen in diesem Bericht; Zahlen in den Tabellen sind massgeblich.\n")
        md = md.replace("<!--NARRATIV-->", block)
    charts = {"profil": svg_profile(X), "fan": svg_fan(X)}
    os.makedirs(args.out, exist_ok=True)
    md_clean = re.sub(r"<!--[^>]*-->\n?", "", md)
    with open(os.path.join(args.out, "bericht.md"), "w", encoding="utf-8") as f:
        f.write(md_clean)
    with open(os.path.join(args.out, "index.html"), "w", encoding="utf-8") as f:
        f.write(render_html(md, charts, X))
    diag = {k: v for k, v in X.items() if k not in ("ens_hourly", "det_hourly")}
    with open(os.path.join(args.out, "diagnostik.json"), "w", encoding="utf-8") as f:
        json.dump(to_jsonable(diag), f, ensure_ascii=False, indent=1)
    print(f"Fertig: {os.path.join(args.out, 'index.html')}", file=sys.stderr)


if __name__ == "__main__":
    main()
