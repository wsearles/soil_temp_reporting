# soil_temp_reporting

Pull daily soil-temperature data the same way the GreenCast soil-temperature
tool does, via the ClearAg (Iteris) daily/soil endpoint. Reverse-engineered
from a HAR capture of https://www.greencastonline.com/tools/soil-temperature

Each day is also cross-checked against [Open-Meteo's](https://open-meteo.com/)
free, key-less historical weather API by the same latitude/longitude: soil
temperature at its shallowest layer (0-7cm, the closest available match to
ClearAg's 0-10cm) plus daily precipitation (inches). Open-Meteo is a separate
service from ClearAg, so it's unaffected by the credentials note below —
though it may not have data yet for the last 1-2 days of a range.

> **Note:** `soil_temp_reporting.py` embeds the `APP_ID`/`APP_KEY` credentials
> that GreenCast (Syngenta) uses on its own web page — they are not the
> project's own credentials. Reusing them may violate GreenCast's or
> ClearAg/Iteris' terms of service, and they can stop working or be
> rate-limited without warning. Use for personal, occasional lookups only.

## Usage

```
python soil_temp_reporting.py                       # launches the GUI (ZIP + date range)
python soil_temp_reporting.py --zip 54650 --start 2026-01-01 --end 2026-07-16
python soil_temp_reporting.py --lat 43.9083 --lon -91.2410 --start 2025-01-01 --end 2025-12-31
python soil_temp_reporting.py --zip 54650 --start 2026-01-01 --end 2026-07-16 --out onalaska.csv
```

The GUI has two actions: **Fetch && Save CSV** downloads the data and saves it to
a CSV file, and **Preview in Browser** fetches the same data and opens a
self-contained HTML report in your default browser — soil temperature
(GreenCast vs. Open-Meteo), the daily difference between them, and
precipitation, all on a shared, hoverable timeline — without saving anything.

Saved CSVs include GreenCast's avg/min/max soil temperature, the Open-Meteo
comparison temperature and its delta from GreenCast, and precipitation.

The GUI requires [tkcalendar](https://pypi.org/project/tkcalendar/)
(`pip install -r requirements.txt`) for the date pickers; the browser preview
uses only the Python standard library (no extra dependency).

## License

Licensed under the [MIT License](LICENSE) — free to use, copy, modify, and distribute.
