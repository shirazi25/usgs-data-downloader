# USGS Data Downloader

This is a small Python script for downloading daily observations from a USGS monitoring station. I wrote it to make a common task straightforward: choose a station, a period, and a variable, then save the result as a clean CSV file.

The script uses the modern [USGS Water Data API](https://api.waterdata.usgs.gov/) directly. It does not require a specialist hydrology package; Python 3.10 or newer is enough.

## What can be downloaded?

The two most common choices have simple names:

| Choice | USGS parameter code | Description |
| --- | --- | --- |
| `discharge` | `00060` | Discharge in cubic feet per second |
| `stage` | `00065` | Gage height in feet |
| `water_temperature` | `00010` | Water temperature in degrees Celsius |

You are not limited to these names. Pass any valid five-digit USGS parameter code with `--parameter`. The selected station must publish that parameter as a daily value. The official [USGS parameter-code catalogue](https://api.waterdata.usgs.gov/ogcapi/v1/collections/parameter-codes?f=html) lists the available codes.

Daily data also have a statistic code. The script understands `mean`, `minimum`, and `maximum`, corresponding to USGS codes `00003`, `00002`, and `00001`. A five-digit statistic code can also be supplied directly.

## Quick start

Clone the repository and move into it:

```bash
git clone https://github.com/shirazi25/usgs-data-downloader.git
cd usgs-data-downloader
```

Run the built-in example:

```bash
python download_usgs_data.py
```

It downloads mean daily discharge for station `02479500` during 2023. The CSV is written to the `data` directory.

## Choose a station and variable

For mean daily discharge:

```bash
python download_usgs_data.py \
  --site 02479500 \
  --parameter discharge \
  --statistic mean \
  --start-date 2023-01-01 \
  --end-date 2023-12-31
```

For mean daily stage at a station that publishes it:

```bash
python download_usgs_data.py \
  --site 07141780 \
  --parameter stage \
  --statistic mean \
  --start-date 2023-01-01 \
  --end-date 2023-12-31
```

For another variable, use its five-digit parameter code. This example requests specific conductance (`00095`):

```bash
python download_usgs_data.py --site YOUR_STATION --parameter 00095
```

Not every station measures every variable, and not every measurement is available as a daily statistic. If the chosen combination is unavailable, the script reports that no matching values were found.

## Editing the defaults

The same settings appear near the top of `download_usgs_data.py`:

```python
SITE_ID = "02479500"
PARAMETER = "discharge"
STATISTIC = "mean"
START_DATE = "2023-01-01"
END_DATE = "2023-12-31"
```

They can be edited for repeated use. Command-line values take precedence, so the file does not need to be changed for an occasional download. Run `python download_usgs_data.py --help` to see all options.

## Output columns

Each CSV contains:

- observation date and value;
- the unit reported by USGS;
- approval status and any qualifier;
- station, parameter, statistic, and time-series identifiers.

The approval status is kept in its original USGS form, such as `Approved` or `Provisional`, rather than being replaced with a project-specific flag.

## Larger numbers of requests

The downloader follows API pagination automatically. USGS recommends using an API key when making more than a few requests per hour. If needed, place your key in the `USGS_API_KEY` environment variable before running the script; the program will add it to the request.

## Data source

Data are provided by the U.S. Geological Survey. Please check the station record, units, qualifiers, and USGS data-use guidance before using the downloaded values in an analysis or publication.

## License

This project is released under the MIT License.
