# soil_temp_reporting

Pull daily soil-temperature data the same way the GreenCast soil-temperature
tool does, via the ClearAg (Iteris) daily/soil endpoint. Reverse-engineered
from a HAR capture of https://www.greencastonline.com/tools/soil-temperature

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

The GUI requires [tkcalendar](https://pypi.org/project/tkcalendar/) (`pip install tkcalendar`)
for the date pickers; everything else is the Python standard library.

## License

Licensed under the [MIT License](LICENSE) — free to use, copy, modify, and distribute.
