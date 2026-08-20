#!/usr/bin/env python3
"""
greencast_soil_temp.py

Pull daily soil-temperature data the same way the GreenCast soil-temperature
tool does: a GET request to the ClearAg (Iteris) daily/soil endpoint.

Reverse-engineered from a HAR capture of
https://www.greencastonline.com/tools/soil-temperature

Field used for the chart line:  soil_temp_0to10cm  (labelled "2-5 cm layer")
Also available per day:          soil_temp_min_0to10cm, soil_temp_max_0to10cm

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
pickers; everything else is the Python standard library.
"""
import argparse, csv, json, os, sys, threading, urllib.parse, urllib.request
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


def rows_from(payload: dict):
    loc = next(iter(payload))                      # single "lat,lon" key
    days = payload[loc]
    out = []
    for d in sorted(days):
        rec = days[d]
        def v(k):
            x = rec.get(k)
            return x["value"] if x else ""
        out.append([d, v("soil_temp_0to10cm"),
                       v("soil_temp_min_0to10cm"),
                       v("soil_temp_max_0to10cm")])
    return out


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

    fetch_button = ttk.Button(frame, text="Fetch && Save CSV")
    fetch_button.grid(row=3, column=0, columnspan=2, pady=(10, 0))

    def set_status(text):
        status_var.set(text)

    def worker(zipcode, start, end):
        try:
            root.after(0, set_status, f"Geocoding ZIP {zipcode}...")
            lat, lon = geocode_zip(zipcode)

            root.after(0, set_status, f"Fetching soil temperature data ({start} to {end})...")
            payload = fetch(lat, lon, start, end)
            rows = rows_from(payload)

            if not rows:
                root.after(0, set_status, "")
                root.after(0, lambda: messagebox.showwarning(
                    "No data", "No soil temperature data was returned for that range."))
                root.after(0, lambda: fetch_button.state(["!disabled"]))
                return

            def ask_and_save():
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
                    fetch_button.state(["!disabled"])
                    return

                with open(out_path, "w", newline="") as f:
                    w = csv.writer(f)
                    w.writerow(["date", "soil_temp_avg_F", "soil_temp_min_F", "soil_temp_max_F"])
                    w.writerows(rows)

                set_status(f"Saved {len(rows)} rows to {out_path}")
                messagebox.showinfo("Done", f"Saved {len(rows)} rows to:\n{out_path}")
                root.destroy()  # close the app once the save is confirmed

            root.after(0, ask_and_save)

        except Exception as e:
            root.after(0, set_status, "")
            root.after(0, lambda: messagebox.showerror("Error", str(e)))
            root.after(0, lambda: fetch_button.state(["!disabled"]))

    def on_fetch():
        zipcode = zip_var.get().strip()
        start = start_picker.get_date().strftime("%Y-%m-%d")
        end = end_picker.get_date().strftime("%Y-%m-%d")

        if not zipcode:
            messagebox.showerror("Missing input", "Please enter a ZIP code.")
            return
        if start > end:
            messagebox.showerror("Invalid range", "Start date must be on or before end date.")
            return

        fetch_button.state(["disabled"])
        set_status("Starting...")
        threading.Thread(target=worker, args=(zipcode, start, end), daemon=True).start()

    fetch_button.configure(command=on_fetch)
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
    rows = rows_from(payload)

    with open(a.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "soil_temp_avg_F", "soil_temp_min_F", "soil_temp_max_F"])
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
