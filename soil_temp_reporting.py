#!/usr/bin/env python3
"""
greencast_soil_temp.py

Pull daily soil-temperature data the same way the GreenCast soil-temperature
tool does: a GET request to the ClearAg (Iteris) daily/soil endpoint.

Reverse-engineered from a HAR capture of
https://www.greencastonline.com/tools/soil-temperature

Field used for the chart line:  soil_temp_0to10cm  (labelled "2-5 cm layer")
Also available per day:          soil_temp_min_0to10cm, soil_temp_max_0to10cm

Each day is also cross-checked against Open-Meteo's free, key-less historical
weather API (https://open-meteo.com/), looked up by the same lat/lon:
soil temperature at its shallowest layer (0-7cm, closest available match to
ClearAg's 0-10cm) and daily precipitation. Open-Meteo is a separate service
from ClearAg, so it's unaffected by the credentials warning below — but it
may not have data for the last 1-2 days of a range, since recent
observations aren't finalized yet.

------------------------------------------------------------------------------
!!  IMPORTANT / READ ME  !!

The APP_ID / APP_KEY below are the credentials GreenCast (Syngenta) embeds in
its own web page. They are NOT yours. Reusing them:
  * may violate GreenCast's or ClearAg/Iteris' terms of service,
  * can stop working without warning if Syngenta rotates the key,
  * may be rate-limited.

Use this for personal, occasional lookups at most. For anything recurring or
production, get your own ClearAg/Iteris key, or use the key-free Open-Meteo
script (soil_temp_openmeteo.py) instead.
------------------------------------------------------------------------------

Usage:
  python greencast_soil_temp.py                       # launches the GUI (ZIP + date range)
  python greencast_soil_temp.py --zip 54650 --start 2026-01-01 --end 2026-07-16
  python greencast_soil_temp.py --lat 43.9083 --lon -91.2410 --start 2025-01-01 --end 2025-12-31
  python greencast_soil_temp.py --zip 54650 --start 2026-01-01 --end 2026-07-16 --out onalaska.csv

GUI requires the tkcalendar package (pip install tkcalendar) for the date
pickers; everything else (including the browser preview) is the Python
standard library.
"""
import argparse, csv, json, os, sys, tempfile, threading, urllib.parse, urllib.request, webbrowser
from datetime import datetime, date, timezone
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ClearAg endpoint + GreenCast's embedded credentials (see warning above)
CLEARAG_URL = "https://ag.us.clearapis.com/v1.1/daily/soil"
APP_ID  = "a2f0d7a4"
APP_KEY = "742a069efe55c7015c2245032fb16bbb"

HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Origin": "https://www.greencastonline.com",
    "Referer": "https://www.greencastonline.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


def geocode_zip(zipcode: str):
    """Free, key-less US ZIP -> (lat, lon) via zippopotam.us."""
    url = f"https://api.zippopotam.us/us/{zipcode}"
    with urllib.request.urlopen(url, timeout=30) as r:
        data = json.load(r)
    place = data["places"][0]
    return float(place["latitude"]), float(place["longitude"])


def to_unix(d: str) -> int:
    return int(datetime.strptime(d, "%Y-%m-%d")
              .replace(tzinfo=timezone.utc).timestamp())


def fetch(lat: float, lon: float, start: str, end: str):
    params = {
        "app_id": APP_ID,
        "app_key": APP_KEY,
        "location": f"{lat},{lon}",
        "start": to_unix(start),
        "end": to_unix(end),
    }
    url = CLEARAG_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def fetch_openmeteo(lat: float, lon: float, start: str, end: str):
    """Fetch daily soil temperature (shallowest layer, 0-7cm) and precipitation
    from Open-Meteo's free historical API in a single request. Returns
    ({date_str: soil_temp_F}, {date_str: precip_inches}).

    Never raises: this is a best-effort addition on top of the ClearAg data,
    so a failure here (service down, no coverage for the range, etc.) just
    yields empty maps rather than blocking the primary fetch."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start,
        "end_date": end,
        "daily": "soil_temperature_0_to_7cm_mean,precipitation_sum",
        "temperature_unit": "fahrenheit",
        "precipitation_unit": "inch",
        "timezone": "auto",
    }
    url = "https://archive-api.open-meteo.com/v1/archive?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": HEADERS["User-Agent"]})
        with urllib.request.urlopen(req, timeout=60) as r:
            payload = json.load(r)
        daily = payload.get("daily", {})
        dates = daily.get("time", [])
        temps = daily.get("soil_temperature_0_to_7cm_mean", [])
        precs = daily.get("precipitation_sum", [])
        soil_temp_f = {d: v for d, v in zip(dates, temps) if v is not None}
        precip_in = {d: v for d, v in zip(dates, precs) if v is not None}
        return soil_temp_f, precip_in
    except Exception:
        return {}, {}


def rows_from(payload: dict, om_temp_by_date=None, precip_by_date=None):
    loc = next(iter(payload))                      # single "lat,lon" key
    days = payload[loc]
    om_temp_by_date = om_temp_by_date or {}
    precip_by_date = precip_by_date or {}
    out = []
    for d in sorted(days):
        rec = days[d]
        def v(k):
            x = rec.get(k)
            return x["value"] if x else ""
        gc_avg = v("soil_temp_0to10cm")
        om_temp = om_temp_by_date.get(d, "")
        delta = round(float(gc_avg) - float(om_temp), 1) if gc_avg != "" and om_temp != "" else ""
        out.append([d, gc_avg,
                       v("soil_temp_min_0to10cm"),
                       v("soil_temp_max_0to10cm"),
                       om_temp, delta,
                       precip_by_date.get(d, "")])
    return out


_REPORT_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>__TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,440;9..144,560;9..144,650&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {
    color-scheme: dark;
    --page:        #0d0d0d;
    --surface:     #1a1a19;
    --surface-alt: rgba(255,255,255,0.035);
    --ink:         #ffffff;
    --ink-2:       #c3c2b7;
    --ink-3:       #8f8d87;
    --grid:        #2c2c2a;
    --baseline:    #383835;
    --border:      rgba(255,255,255,0.10);
    --greencast:   #d95926;
    --openmeteo:   #3987e5;
    --soil:        #c99a5b;
    --soil-wash:   rgba(201,154,91,0.14);
    --precip:      #199e70;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--page); color: var(--ink);
    font-family: 'IBM Plex Sans', system-ui, -apple-system, 'Segoe UI', sans-serif; -webkit-font-smoothing: antialiased; }
  .wrap { max-width: 880px; margin: 0 auto; padding: 48px 24px 80px; }
  .eyebrow { font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: 12px; font-weight: 500;
    letter-spacing: 0.11em; text-transform: uppercase; color: var(--soil); margin: 0 0 14px; }
  h1 { font-family: 'Fraunces', ui-serif, Georgia, serif; font-weight: 560; font-optical-sizing: auto;
    font-size: clamp(28px, 4.4vw, 40px); line-height: 1.1; letter-spacing: -0.01em; margin: 0 0 14px;
    text-wrap: balance; max-width: 22ch; }
  .dek { font-size: 15.5px; line-height: 1.6; color: var(--ink-2); max-width: 64ch; margin: 0 0 36px; }
  .dek b { color: var(--ink); font-weight: 600; }
  .stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1px; background: var(--border);
    border: 1px solid var(--border); border-radius: 12px; overflow: hidden; margin-bottom: 40px; }
  .stat { background: var(--surface); padding: 18px 20px 16px; }
  .stat-label { font-size: 12px; color: var(--ink-3); margin: 0 0 10px; }
  .stat-value { font-family: 'Fraunces', ui-serif, Georgia, serif; font-weight: 560; font-size: 30px;
    letter-spacing: -0.01em; color: var(--ink); margin: 0 0 6px; }
  .stat-value .unit { font-size: 17px; color: var(--ink-3); font-weight: 440; margin-left: 2px; }
  .stat-sub { font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: 12px; color: var(--ink-3); }
  section { margin-bottom: 40px; }
  h2 { font-family: 'Fraunces', ui-serif, Georgia, serif; font-weight: 560; font-size: 20px;
    letter-spacing: -0.005em; margin: 0 0 4px; }
  .section-note { font-size: 13.5px; color: var(--ink-3); margin: 0 0 16px; line-height: 1.55; max-width: 64ch; }
  .chart-card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px 20px 12px; }
  .legend { display: flex; gap: 18px; flex-wrap: wrap; margin-bottom: 6px; }
  .legend-item { display: flex; align-items: center; gap: 7px; font-size: 13px; color: var(--ink-2); font-weight: 500; }
  .swatch { width: 10px; height: 10px; border-radius: 50%; flex: none; }
  .swatch.sq { border-radius: 2px; }
  .chart-scroll { overflow-x: auto; }
  .chart-svg-wrap { min-width: 640px; position: relative; }
  svg.chart { display: block; width: 100%; height: auto; overflow: visible; }
  .tick-label { font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: 10.5px; fill: var(--ink-3); }
  .axis-caption { font-family: 'IBM Plex Sans', sans-serif; font-size: 11px; fill: var(--ink-3); font-weight: 500; }
  .end-label { font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: 11.5px; font-weight: 600; }
  .tooltip { position: absolute; pointer-events: none; background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 10px 12px; font-size: 12px; line-height: 1.65; box-shadow: 0 6px 20px rgba(0,0,0,0.14);
    opacity: 0; transform: translateY(2px); min-width: 150px; z-index: 5; }
  @media (prefers-reduced-motion: no-preference) { .tooltip { transition: opacity 110ms ease, transform 110ms ease; } }
  .tooltip.visible { opacity: 1; transform: translateY(0); }
  .tooltip .t-date { font-family: 'IBM Plex Mono', ui-monospace, monospace; color: var(--ink-3); font-size: 11px; margin-bottom: 6px; }
  .tooltip .t-row { display: flex; justify-content: space-between; gap: 14px; }
  .tooltip .t-row .k { color: var(--ink-2); display: flex; align-items: center; gap: 6px; }
  .tooltip .t-row .v { font-family: 'IBM Plex Mono', ui-monospace, monospace; font-variant-numeric: tabular-nums; font-weight: 600; }
  .tooltip .t-dot { width: 7px; height: 7px; border-radius: 50%; flex: none; }
  .tooltip hr { border: none; border-top: 1px solid var(--border); margin: 6px 0; }
  .caveat { display: flex; gap: 10px; background: var(--soil-wash); border: 1px solid var(--border);
    border-radius: 10px; padding: 14px 16px; font-size: 13px; line-height: 1.6; color: var(--ink-2); margin-top: 16px; }
  .caveat strong { color: var(--ink); font-weight: 600; }
  .caveat-mark { color: var(--soil); font-weight: 700; font-family: 'Fraunces', serif; font-size: 15px; line-height: 1.6; flex: none; }
  details.table-card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }
  details.table-card > summary { cursor: pointer; list-style: none; padding: 16px 20px; display: flex;
    align-items: center; justify-content: space-between; font-weight: 600; font-size: 14px; }
  details.table-card > summary::-webkit-details-marker { display: none; }
  details.table-card > summary .chev { font-family: 'IBM Plex Mono', monospace; color: var(--ink-3); font-weight: 400; font-size: 12px; }
  details.table-card[open] > summary { border-bottom: 1px solid var(--border); }
  .table-scroll { overflow-x: auto; max-height: 420px; overflow-y: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  thead th { position: sticky; top: 0; background: var(--surface); text-align: right; font-weight: 500;
    color: var(--ink-3); font-size: 11px; letter-spacing: 0.04em; text-transform: uppercase; padding: 10px 16px;
    border-bottom: 1px solid var(--border); }
  thead th:first-child, tbody td:first-child { text-align: left; }
  tbody td { padding: 7px 16px; text-align: right; font-family: 'IBM Plex Mono', ui-monospace, monospace;
    font-variant-numeric: tabular-nums; color: var(--ink-2); }
  tbody td:first-child { color: var(--ink); }
  tbody tr:nth-child(odd) { background: var(--surface-alt); }
  tbody td.delta { color: var(--soil); font-weight: 600; }
  tbody td.delta.neg { color: var(--openmeteo); }
  footer { margin-top: 44px; padding-top: 20px; border-top: 1px solid var(--border); font-size: 12px;
    color: var(--ink-3); line-height: 1.7; }
</style>
</head>
<body>
<div class="wrap">
  <p class="eyebrow">Field data report</p>
  <h1>__TITLE__</h1>
  <p class="dek">
    Daily soil temperature for <b>__LOCATION__</b>, <b>__START_LABEL__ &ndash; __END_LABEL__</b>.
    <b>GreenCast/ClearAg</b> is the primary source (0&ndash;10cm); <b>Open&#8209;Meteo</b>'s
    shallow layer (0&ndash;7cm) is shown alongside it as a free reference point, plus daily
    precipitation. Generated __GENERATED__.
  </p>

  <div class="stats">
    <div class="stat">
      <p class="stat-label">Mean difference vs Open&#8209;Meteo</p>
      <p class="stat-value">__MEAN_DELTA__<span class="unit">&deg;F</span></p>
      <p class="stat-sub">__MEAN_SUB__</p>
    </div>
    <div class="stat">
      <p class="stat-label">Smallest gap</p>
      <p class="stat-value">__MIN_DELTA__<span class="unit">&deg;F</span></p>
      <p class="stat-sub">__MIN_SUB__</p>
    </div>
    <div class="stat">
      <p class="stat-label">Largest gap</p>
      <p class="stat-value">__MAX_DELTA__<span class="unit">&deg;F</span></p>
      <p class="stat-sub">__MAX_SUB__</p>
    </div>
  </div>

  <section>
    <h2>Temperature, difference &amp; precipitation</h2>
    <p class="section-note">Hover the chart to compare any single day across all three panels.</p>
    <div class="chart-card">
      <div class="legend">
        <span class="legend-item"><span class="swatch" style="background:var(--greencast)"></span>GreenCast (0&ndash;10cm)</span>
        <span class="legend-item"><span class="swatch" style="background:var(--openmeteo)"></span>Open&#8209;Meteo (0&ndash;7cm)</span>
        <span class="legend-item"><span class="swatch sq" style="background:var(--soil)"></span>&Delta; difference</span>
        <span class="legend-item"><span class="swatch sq" style="background:var(--precip)"></span>Precipitation</span>
      </div>
      <div class="chart-scroll">
        <div class="chart-svg-wrap" id="chartWrap">
          <svg class="chart" id="chartSvg" viewBox="0 0 900 460" preserveAspectRatio="xMinYMin meet"></svg>
          <div class="tooltip" id="tooltip"></div>
        </div>
      </div>
    </div>
    <div class="caveat">
      <span class="caveat-mark">!</span>
      <span><strong>Not an apples-to-apples depth.</strong> GreenCast's field is labeled 0&ndash;10cm;
      Open&#8209;Meteo's closest daily aggregate is 0&ndash;7cm and is a <em>mean</em> only (no min/max),
      so treat the gap as directional, not exact.</span>
    </div>
  </section>

  <section>
    <details class="table-card">
      <summary>All __N_DAYS__ days<span class="chev">EXPAND &#9662;</span></summary>
      <div class="table-scroll">
        <table>
          <thead><tr><th>Date</th><th>GreenCast &deg;F</th><th>Open&#8209;Meteo &deg;F</th><th>&Delta; &deg;F</th><th>Precip in</th></tr></thead>
          <tbody id="tableBody"></tbody>
        </table>
      </div>
    </details>
  </section>

  <footer>
    Source: ClearAg (Iteris) daily/soil endpoint via GreenCast's embedded credentials,
    and Open&#8209;Meteo's free historical archive API
    (soil_temperature_0_to_7cm_mean, precipitation_sum).
  </footer>
</div>

<script>
(function () {
  var DATA = __DATA_JSON__;
  var n = DATA.length;
  var css = getComputedStyle(document.documentElement);
  function tok(name) { return css.getPropertyValue(name).trim(); }
  function fmtDate(d) { return new Date(d + "T00:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" }); }
  function fmtDateFull(d) { return new Date(d + "T00:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }); }

  // ---- table ----
  var tbody = document.getElementById("tableBody");
  DATA.forEach(function (r) {
    var tr = document.createElement("tr");
    var deltaTxt = r.delta === null ? "&ndash;" : (r.delta < 0 ? "&minus;" : "+") + Math.abs(r.delta).toFixed(1);
    tr.innerHTML = "<td>" + fmtDate(r.d) + "</td>" +
      "<td>" + (r.gc === null ? "&ndash;" : r.gc.toFixed(1)) + "</td>" +
      "<td>" + (r.om === null ? "&ndash;" : r.om.toFixed(1)) + "</td>" +
      "<td class=\\"delta" + (r.delta !== null && r.delta < 0 ? " neg" : "") + "\\">" + deltaTxt + "</td>" +
      "<td>" + r.precip.toFixed(2) + "</td>";
    tbody.appendChild(tr);
  });

  // ---- scales ----
  function niceBounds(lo, hi, forceZero) {
    if (forceZero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
    var span = Math.max(hi - lo, 0.5);
    var mag = Math.pow(10, Math.floor(Math.log10(span / 4)));
    var step = [1, 2, 5, 10].map(function (m) { return m * mag; })
      .find(function (s) { return span / s <= 5; }) || 10 * mag;
    var niceMin = Math.floor(lo / step) * step;
    var niceMax = Math.ceil(hi / step) * step;
    if (forceZero) { niceMin = Math.min(niceMin, 0); niceMax = Math.max(niceMax, 0); }
    var ticks = [];
    for (var v = niceMin; v <= niceMax + 1e-9; v += step) ticks.push(Math.round(v * 100) / 100);
    return { min: niceMin, max: niceMax, ticks: ticks };
  }

  var tempVals = [];
  DATA.forEach(function (r) { if (r.gc !== null) tempVals.push(r.gc); if (r.om !== null) tempVals.push(r.om); });
  var tempScale = tempVals.length ? niceBounds(Math.min.apply(null, tempVals), Math.max.apply(null, tempVals), false)
                                   : { min: 0, max: 100, ticks: [0, 25, 50, 75, 100] };

  var deltaVals = DATA.map(function (r) { return r.delta; }).filter(function (v) { return v !== null; });
  var deltaScale = deltaVals.length ? niceBounds(Math.min.apply(null, deltaVals), Math.max.apply(null, deltaVals), true)
                                     : { min: -1, max: 1, ticks: [-1, 0, 1] };

  var precipVals = DATA.map(function (r) { return r.precip; });
  var precipScale = niceBounds(0, Math.max.apply(null, precipVals.concat([0.1])), false);
  precipScale.min = 0;

  // ---- geometry ----
  var W = 900, marginL = 42, marginR = 14, marginT = 10;
  var tempH = 208, gap1 = 16, deltaH = 66, gap2 = 14, precipH = 66;
  var plotW = W - marginL - marginR;

  function xAt(i) { return marginL + (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW); }
  function yTemp(v) { return marginT + tempH - ((v - tempScale.min) / (tempScale.max - tempScale.min)) * tempH; }
  var deltaTop = marginT + tempH + gap1;
  function yDelta(v) { return deltaTop + deltaH - ((v - deltaScale.min) / (deltaScale.max - deltaScale.min)) * deltaH; }
  var precipTop = deltaTop + deltaH + gap2;
  function yPrecip(v) { return precipTop + precipH - ((v - precipScale.min) / (precipScale.max - precipScale.min)) * precipH; }
  var chartBottom = precipTop + precipH;

  var svgNS = "http://www.w3.org/2000/svg";
  var svg = document.getElementById("chartSvg");
  svg.setAttribute("viewBox", "0 0 " + W + " " + (chartBottom + 20));
  function el(tag, attrs) {
    var e = document.createElementNS(svgNS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }

  // temp panel gridlines
  tempScale.ticks.forEach(function (v) {
    svg.appendChild(el("line", { x1: marginL, x2: W - marginR, y1: yTemp(v), y2: yTemp(v), stroke: tok("--grid"), "stroke-width": 1 }));
    var t = el("text", { x: marginL - 8, y: yTemp(v) + 3.5, "text-anchor": "end", class: "tick-label" });
    t.textContent = v + "°"; svg.appendChild(t);
  });

  // delta panel gridlines + zero baseline
  deltaScale.ticks.forEach(function (v) {
    svg.appendChild(el("line", { x1: marginL, x2: W - marginR, y1: yDelta(v), y2: yDelta(v), stroke: tok("--grid"), "stroke-width": 1 }));
    var t = el("text", { x: marginL - 8, y: yDelta(v) + 3.5, "text-anchor": "end", class: "tick-label" });
    t.textContent = v; svg.appendChild(t);
  });
  svg.appendChild(el("line", { x1: marginL, x2: W - marginR, y1: yDelta(0), y2: yDelta(0), stroke: tok("--baseline"), "stroke-width": 1 }));
  var deltaCap = el("text", { x: marginL, y: deltaTop - 5, class: "axis-caption" });
  deltaCap.textContent = "Δ GreenCast − Open‑Meteo (°F)"; svg.appendChild(deltaCap);

  // precip panel gridlines
  precipScale.ticks.forEach(function (v) {
    svg.appendChild(el("line", { x1: marginL, x2: W - marginR, y1: yPrecip(v), y2: yPrecip(v), stroke: tok("--grid"), "stroke-width": 1 }));
    var t = el("text", { x: marginL - 8, y: yPrecip(v) + 3.5, "text-anchor": "end", class: "tick-label" });
    t.textContent = v; svg.appendChild(t);
  });
  var precipCap = el("text", { x: marginL, y: precipTop - 5, class: "axis-caption" });
  precipCap.textContent = "Precipitation (in)"; svg.appendChild(precipCap);

  // month boundary markers
  DATA.forEach(function (r, i) {
    if (i > 0 && r.d.slice(5, 7) !== DATA[i - 1].d.slice(5, 7)) {
      var mx = xAt(i);
      svg.appendChild(el("line", { x1: mx, x2: mx, y1: marginT, y2: chartBottom, stroke: tok("--grid"), "stroke-width": 1, "stroke-dasharray": "2,3" }));
    }
  });

  // delta bars
  var barW = Math.max(2.2, (plotW / n) * 0.62);
  var deltaBars = [];
  DATA.forEach(function (r, i) {
    var v = r.delta === null ? 0 : r.delta;
    var y0 = yDelta(0), y1 = yDelta(v);
    var b = el("rect", { x: xAt(i) - barW / 2, y: Math.min(y0, y1), width: barW, height: Math.abs(y1 - y0),
      rx: 1.2, fill: tok("--soil"), opacity: r.delta === null ? 0 : 0.55 });
    svg.appendChild(b); deltaBars.push(b);
  });

  // precip bars
  var precipBars = [];
  DATA.forEach(function (r, i) {
    var y1 = yPrecip(r.precip), y0 = yPrecip(precipScale.min);
    var b = el("rect", { x: xAt(i) - barW / 2, y: y1, width: barW, height: Math.max(y0 - y1, 0),
      rx: 1.2, fill: tok("--precip"), opacity: 0.6 });
    svg.appendChild(b); precipBars.push(b);
  });

  // temp lines (gaps at missing days)
  function pathFor(key) {
    var d = "", pen = false;
    DATA.forEach(function (r, i) {
      var v = r[key];
      if (v === null) { pen = false; return; }
      d += (pen ? "L" : "M") + xAt(i).toFixed(2) + "," + yTemp(v).toFixed(2) + " ";
      pen = true;
    });
    return d.trim();
  }
  svg.appendChild(el("path", { d: pathFor("om"), fill: "none", stroke: tok("--openmeteo"), "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
  svg.appendChild(el("path", { d: pathFor("gc"), fill: "none", stroke: tok("--greencast"), "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));

  // end markers
  function lastIdx(key) {
    for (var i = n - 1; i >= 0; i--) if (DATA[i][key] !== null) return i;
    return -1;
  }
  [["gc", "--greencast"], ["om", "--openmeteo"]].forEach(function (pair) {
    var idx = lastIdx(pair[0]);
    if (idx < 0) return;
    var cx = xAt(idx), cy = yTemp(DATA[idx][pair[0]]);
    svg.appendChild(el("circle", { cx: cx, cy: cy, r: 4.5, fill: tok(pair[1]), stroke: tok("--surface"), "stroke-width": 2 }));
    var lbl = el("text", { x: cx + 8, y: cy + 4, class: "end-label", fill: tok(pair[1]) });
    lbl.textContent = DATA[idx][pair[0]].toFixed(0) + "°"; svg.appendChild(lbl);
  });

  // hover layer
  var crosshair = el("line", { x1: 0, x2: 0, y1: marginT, y2: chartBottom, stroke: tok("--baseline"), "stroke-width": 1, opacity: 0 });
  svg.appendChild(crosshair);
  var hoverGc = el("circle", { r: 5, fill: tok("--greencast"), stroke: tok("--surface"), "stroke-width": 2, opacity: 0 });
  var hoverOm = el("circle", { r: 5, fill: tok("--openmeteo"), stroke: tok("--surface"), "stroke-width": 2, opacity: 0 });
  svg.appendChild(hoverGc); svg.appendChild(hoverOm);
  var overlay = el("rect", { x: marginL, y: marginT, width: plotW, height: chartBottom - marginT, fill: "transparent" });
  svg.appendChild(overlay);

  var tooltip = document.getElementById("tooltip");
  var wrap = document.getElementById("chartWrap");

  function showAt(i, clientX, clientY) {
    var r = DATA[i];
    if (r.gc !== null) { hoverGc.setAttribute("cx", xAt(i)); hoverGc.setAttribute("cy", yTemp(r.gc)); hoverGc.setAttribute("opacity", 1); }
    else hoverGc.setAttribute("opacity", 0);
    if (r.om !== null) { hoverOm.setAttribute("cx", xAt(i)); hoverOm.setAttribute("cy", yTemp(r.om)); hoverOm.setAttribute("opacity", 1); }
    else hoverOm.setAttribute("opacity", 0);
    crosshair.setAttribute("x1", xAt(i)); crosshair.setAttribute("x2", xAt(i)); crosshair.setAttribute("opacity", 1);
    deltaBars.forEach(function (b, bi) { b.setAttribute("opacity", bi === i ? 0.95 : (DATA[bi].delta === null ? 0 : 0.55)); });
    precipBars.forEach(function (b, bi) { b.setAttribute("opacity", bi === i ? 0.85 : 0.6); });

    var deltaColor = r.delta === null ? tok("--ink-3") : (r.delta < 0 ? tok("--openmeteo") : tok("--soil"));
    var deltaTxt = r.delta === null ? "n/a" : (r.delta < 0 ? "−" : "+") + Math.abs(r.delta).toFixed(1) + "°F";
    tooltip.innerHTML =
      '<div class="t-date">' + fmtDateFull(r.d) + '</div>' +
      '<div class="t-row"><span class="k"><span class="t-dot" style="background:' + tok("--greencast") + '"></span>GreenCast</span><span class="v">' + (r.gc === null ? "n/a" : r.gc.toFixed(1) + "°F") + '</span></div>' +
      '<div class="t-row"><span class="k"><span class="t-dot" style="background:' + tok("--openmeteo") + '"></span>Open‑Meteo</span><span class="v">' + (r.om === null ? "n/a" : r.om.toFixed(1) + "°F") + '</span></div>' +
      '<div class="t-row"><span class="k" style="color:' + deltaColor + '">Δ difference</span><span class="v" style="color:' + deltaColor + '">' + deltaTxt + '</span></div>' +
      '<hr>' +
      '<div class="t-row"><span class="k"><span class="t-dot" style="background:' + tok("--precip") + '"></span>Precipitation</span><span class="v">' + r.precip.toFixed(2) + ' in</span></div>';

    var wrapRect = wrap.getBoundingClientRect();
    var left = clientX - wrapRect.left + 14, top = clientY - wrapRect.top - 16;
    if (left + 175 > wrapRect.width) left = clientX - wrapRect.left - 175;
    tooltip.style.left = left + "px"; tooltip.style.top = Math.max(0, top) + "px";
    tooltip.classList.add("visible");
  }
  function hide() {
    hoverGc.setAttribute("opacity", 0); hoverOm.setAttribute("opacity", 0); crosshair.setAttribute("opacity", 0);
    deltaBars.forEach(function (b, bi) { b.setAttribute("opacity", DATA[bi].delta === null ? 0 : 0.55); });
    precipBars.forEach(function (b) { b.setAttribute("opacity", 0.6); });
    tooltip.classList.remove("visible");
  }
  function handleMove(clientX, clientY) {
    var svgRect = svg.getBoundingClientRect();
    var scale = svgRect.width / W;
    var localX = (clientX - svgRect.left) / scale;
    var i = Math.round(((localX - marginL) / plotW) * (n - 1));
    i = Math.max(0, Math.min(n - 1, i));
    showAt(i, clientX, clientY);
  }
  overlay.addEventListener("mousemove", function (e) { handleMove(e.clientX, e.clientY); });
  overlay.addEventListener("mouseleave", hide);
  overlay.addEventListener("touchstart", function (e) { var t = e.touches[0]; handleMove(t.clientX, t.clientY); }, { passive: true });
  overlay.addEventListener("touchmove", function (e) { var t = e.touches[0]; handleMove(t.clientX, t.clientY); e.preventDefault(); }, { passive: false });
  overlay.addEventListener("touchend", hide);
})();
</script>
</body>
</html>
"""


def build_html_report(rows, zipcode, start, end):
    """Build a self-contained HTML report (chart + table) from rows_from() output."""
    records = []
    for d, gc_avg, gc_min, gc_max, om_temp, delta, precip in rows:
        records.append({
            "d": d,
            "gc": float(gc_avg) if gc_avg != "" else None,
            "om": float(om_temp) if om_temp != "" else None,
            "delta": float(delta) if delta != "" else None,
            "precip": float(precip) if precip != "" else 0.0,
        })

    deltas = [r["delta"] for r in records if r["delta"] is not None]
    if deltas:
        mean_delta = sum(deltas) / len(deltas)
        min_rec = min((r for r in records if r["delta"] is not None), key=lambda r: abs(r["delta"]))
        max_rec = max((r for r in records if r["delta"] is not None), key=lambda r: r["delta"])
        cooler_days = sum(1 for v in deltas if v < 0)
        mean_txt = ("+" if mean_delta >= 0 else "−") + f"{abs(mean_delta):.1f}"
        mean_sub = f"GreenCast cooler on {cooler_days} of {len(records)} days" if cooler_days \
            else "GreenCast reads warmer, every day"
        min_txt = ("+" if min_rec["delta"] >= 0 else "−") + f"{abs(min_rec['delta']):.1f}"
        max_txt = ("+" if max_rec["delta"] >= 0 else "−") + f"{abs(max_rec['delta']):.1f}"
        min_sub = datetime.strptime(min_rec["d"], "%Y-%m-%d").strftime("%b %-d, %Y") if os.name != "nt" \
            else datetime.strptime(min_rec["d"], "%Y-%m-%d").strftime("%b %#d, %Y")
        max_sub = datetime.strptime(max_rec["d"], "%Y-%m-%d").strftime("%b %-d, %Y") if os.name != "nt" \
            else datetime.strptime(max_rec["d"], "%Y-%m-%d").strftime("%b %#d, %Y")
    else:
        mean_txt = min_txt = max_txt = "n/a"
        mean_sub = min_sub = max_sub = "No overlapping data"

    html = _REPORT_TEMPLATE
    html = html.replace("__TITLE__", f"Soil Temperature — {zipcode}")
    html = html.replace("__LOCATION__", f"ZIP {zipcode}")
    html = html.replace("__START_LABEL__", datetime.strptime(start, "%Y-%m-%d").strftime("%b %Y" if start[:7] != end[:7] else "%b %d"))
    html = html.replace("__END_LABEL__", datetime.strptime(end, "%Y-%m-%d").strftime("%b %d, %Y"))
    html = html.replace("__GENERATED__", datetime.now().strftime("%Y-%m-%d %H:%M"))
    html = html.replace("__N_DAYS__", str(len(records)))
    html = html.replace("__MEAN_DELTA__", mean_txt)
    html = html.replace("__MEAN_SUB__", mean_sub)
    html = html.replace("__MIN_DELTA__", min_txt)
    html = html.replace("__MIN_SUB__", min_sub)
    html = html.replace("__MAX_DELTA__", max_txt)
    html = html.replace("__MAX_SUB__", max_sub)
    html = html.replace("__DATA_JSON__", json.dumps(records))
    return html


def open_html_report(rows, zipcode, start, end):
    """Write the HTML report to a temp file and open it in the default browser."""
    html = build_html_report(rows, zipcode, start, end)
    path = os.path.join(tempfile.gettempdir(), f"soil_temp_report_{zipcode}_{start}_to_{end}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    webbrowser.open("file://" + os.path.abspath(path).replace(os.sep, "/"))


def run_gui():
    try:
        from tkcalendar import DateEntry
    except ImportError:
        sys.exit("Missing dependency: pip install tkcalendar")

    root = tk.Tk()
    root.title("GreenCast Soil Temperature Downloader")
    root.resizable(False, False)

    frame = ttk.Frame(root, padding=12)
    frame.grid(row=0, column=0, sticky="nsew")

    ttk.Label(frame, text="ZIP Code:").grid(row=0, column=0, sticky="w", pady=4)
    zip_var = tk.StringVar()
    ttk.Entry(frame, textvariable=zip_var, width=22).grid(row=0, column=1, pady=4)

    ttk.Label(frame, text="Start Date:").grid(row=1, column=0, sticky="w", pady=4)
    start_picker = DateEntry(frame, width=19, date_pattern="yyyy-mm-dd",
                              maxdate=date.today())
    start_picker.grid(row=1, column=1, pady=4)

    ttk.Label(frame, text="End Date:").grid(row=2, column=0, sticky="w", pady=4)
    end_picker = DateEntry(frame, width=19, date_pattern="yyyy-mm-dd",
                            maxdate=date.today())
    end_picker.set_date(date.today())
    end_picker.grid(row=2, column=1, pady=4)

    status_var = tk.StringVar(value="")
    ttk.Label(frame, textvariable=status_var, foreground="gray").grid(
        row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))

    button_frame = ttk.Frame(frame)
    button_frame.grid(row=3, column=0, columnspan=2, pady=(10, 0))
    fetch_button = ttk.Button(button_frame, text="Fetch && Save CSV")
    fetch_button.grid(row=0, column=0, padx=(0, 6))
    chart_button = ttk.Button(button_frame, text="Preview in Browser")
    chart_button.grid(row=0, column=1, padx=(6, 0))
    all_buttons = (fetch_button, chart_button)

    def set_status(text):
        status_var.set(text)

    def worker(zipcode, start, end, on_rows):
        try:
            root.after(0, set_status, f"Geocoding ZIP {zipcode}...")
            lat, lon = geocode_zip(zipcode)

            root.after(0, set_status, f"Fetching soil temperature data ({start} to {end})...")
            payload = fetch(lat, lon, start, end)

            root.after(0, set_status, f"Fetching Open-Meteo comparison data ({start} to {end})...")
            om_temp_map, precip_map = fetch_openmeteo(lat, lon, start, end)

            rows = rows_from(payload, om_temp_map, precip_map)

            if not rows:
                root.after(0, set_status, "")
                root.after(0, lambda: messagebox.showwarning(
                    "No data", "No soil temperature data was returned for that range."))
                root.after(0, lambda: [b.state(["!disabled"]) for b in all_buttons])
                return

            root.after(0, on_rows, rows)

        except Exception as e:
            root.after(0, set_status, "")
            root.after(0, lambda: messagebox.showerror("Error", str(e)))
            root.after(0, lambda: [b.state(["!disabled"]) for b in all_buttons])

    def get_inputs():
        zipcode = zip_var.get().strip()
        start = start_picker.get_date().strftime("%Y-%m-%d")
        end = end_picker.get_date().strftime("%Y-%m-%d")

        if not zipcode:
            messagebox.showerror("Missing input", "Please enter a ZIP code.")
            return None
        if start > end:
            messagebox.showerror("Invalid range", "Start date must be on or before end date.")
            return None
        return zipcode, start, end

    def on_fetch():
        inputs = get_inputs()
        if inputs is None:
            return
        zipcode, start, end = inputs

        def ask_and_save(rows):
            desktop = os.path.join(os.path.expanduser("~"), "Desktop")
            out_path = filedialog.asksaveasfilename(
                title="Save Soil Temperature CSV",
                initialdir=desktop if os.path.isdir(desktop) else os.path.expanduser("~"),
                initialfile=f"soil_temp_{zipcode}_{start}_to_{end}.csv",
                defaultextension=".csv",
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            )
            if not out_path:
                set_status("Save canceled.")
                for b in all_buttons:
                    b.state(["!disabled"])
                return

            with open(out_path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["date", "soil_temp_avg_F", "soil_temp_min_F", "soil_temp_max_F",
                            "openmeteo_soil_temp_F", "delta_F", "precip_in"])
                w.writerows(rows)

            set_status(f"Saved {len(rows)} rows to {out_path}")
            messagebox.showinfo("Done", f"Saved {len(rows)} rows to:\n{out_path}")
            root.destroy()  # close the app once the save is confirmed

        for b in all_buttons:
            b.state(["disabled"])
        set_status("Starting...")
        threading.Thread(target=worker, args=(zipcode, start, end, ask_and_save),
                          daemon=True).start()

    def on_preview():
        inputs = get_inputs()
        if inputs is None:
            return
        zipcode, start, end = inputs

        def show_preview(rows):
            set_status(f"Opening preview for {len(rows)} rows in your browser...")
            open_html_report(rows, zipcode, start, end)
            set_status(f"Previewed {len(rows)} rows.")
            for b in all_buttons:
                b.state(["!disabled"])

        for b in all_buttons:
            b.state(["disabled"])
        set_status("Starting...")
        threading.Thread(target=worker, args=(zipcode, start, end, show_preview),
                          daemon=True).start()

    fetch_button.configure(command=on_fetch)
    chart_button.configure(command=on_preview)
    root.mainloop()


def main():
    p = argparse.ArgumentParser(description="Pull GreenCast/ClearAg daily soil temperature.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--zip", help="US ZIP code (geocoded automatically)")
    p.add_argument("--lat", type=float, help="latitude (with --lon)")
    p.add_argument("--lon", type=float, help="longitude (with --lat)")
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end",   required=True, help="YYYY-MM-DD")
    p.add_argument("--out", default="soil_temp.csv", help="output CSV path")
    a = p.parse_args()

    if a.zip:
        lat, lon = geocode_zip(a.zip)
        print(f"ZIP {a.zip} -> {lat}, {lon}")
    else:
        if a.lat is None or a.lon is None:
            p.error("provide --zip OR both --lat and --lon")
        lat, lon = a.lat, a.lon

    payload = fetch(lat, lon, a.start, a.end)
    om_temp_map, precip_map = fetch_openmeteo(lat, lon, a.start, a.end)
    rows = rows_from(payload, om_temp_map, precip_map)

    with open(a.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "soil_temp_avg_F", "soil_temp_min_F", "soil_temp_max_F",
                    "openmeteo_soil_temp_F", "delta_F", "precip_in"])
        w.writerows(rows)

    print(f"Wrote {len(rows)} rows to {a.out}"
          + (f"  ({rows[0][0]} .. {rows[-1][0]})" if rows else ""))


if __name__ == "__main__":
    if len(sys.argv) == 1:
        run_gui()
    else:
        try:
            main()
        except urllib.error.HTTPError as e:
            sys.exit(f"HTTP {e.code} from API - the embedded key may have been "
                     f"rotated or rate-limited. Details: {e.read()[:200]!r}")
