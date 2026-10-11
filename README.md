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

It downloads mean daily discharge for station `02479500` during 2023. The CSV is written to the `data` directory. Each run downloads one gauge only.

## Choose a station and variable

For mean daily discharge:

```bash
python download_usgs_data.py \
  --site 02479500 \
  --parameter discharge \
  --statistic mean \
  --units metric \
  --start-date 2023-01-01 \
  --end-date 2023-12-31
```

For mean daily stage at a station that publishes it:

```bash
python download_usgs_data.py \
  --site 07141780 \
  --parameter stage \
  --statistic mean \
  --units metric \
  --start-date 2023-01-01 \
  --end-date 2023-12-31
```

## Find the nearest gauge from coordinates

If the gauge number is unknown, provide a WGS84 latitude and longitude instead:

```bash
python download_usgs_data.py \
  --latitude 30.8624 \
  --longitude -88.4178 \
  --parameter discharge \
  --start-date 2023-01-01 \
  --end-date 2023-12-31
```

The script searches within 50 km, selects the nearest gauge that provides the requested parameter and statistic, prints its gauge number and distance, and downloads only that gauge. Change the maximum distance when necessary with `--search-radius-km`, for example `--search-radius-km 100`.

Latitude and longitude must be supplied together. Use either a gauge number or coordinates in one command, not both.

## Choose the units

Metric output is the default:

```bash
python download_usgs_data.py --site 02479500 --parameter discharge --units metric
```

For the common hydrological variables, the metric option converts:

| Unit supplied by USGS | Metric output |
| --- | --- |
| cubic feet per second (`ft^3/s`) | cubic metres per second (`m^3/s`) |
| feet (`ft`) | metres (`m`) |
| inches (`in`) | millimetres (`mm`) |
| degrees Fahrenheit (`degF`) | degrees Celsius (`degC`) |

Values already provided in metric or scientific units, such as `degC`, `mg/L`, or `uS/cm`, are left unchanged.

The conversions used by the script are:

```text
discharge in m^3/s = discharge in ft^3/s × 0.028316846592
stage in m          = stage in ft × 0.3048
depth in mm         = depth in in × 25.4
temperature in degC = (temperature in degF - 32) × 5/9
```

Converted values are written with up to six decimal places. The `unit` column changes with the value, so a converted discharge row contains `m^3/s`, while an unconverted USGS row contains `ft^3/s`.

To retain the value and unit exactly as supplied by USGS, use:

```bash
python download_usgs_data.py --site 02479500 --parameter discharge --units usgs
```

The CSV `unit` column always identifies the unit used in the `value` column.

## Example output

The repository includes one test output: [mean daily discharge for USGS gauge 02479500 during 2023](examples/USGS_02479500_discharge_2023_metric.csv). It was produced with:

```bash
python download_usgs_data.py \
  --site 02479500 \
  --parameter discharge \
  --statistic mean \
  --units metric \
  --start-date 2023-01-01 \
  --end-date 2023-12-31
```

The file contains 365 observations from 1 January through 31 December 2023. Its `value` column is discharge in `m^3/s`; the original USGS unit for this series is `ft^3/s`. The file is included to show the output structure and confirm that the downloader works. It should not be treated as a separate copy of the official USGS archive.

For another variable, use its five-digit parameter code. This example requests specific conductance (`00095`):

```bash
python download_usgs_data.py --site YOUR_STATION --parameter 00095
```

Not every station measures every variable, and not every measurement is available as a daily statistic. If the chosen combination is unavailable, the script reports that no matching values were found.

## Editing the defaults

The same settings appear near the top of `download_usgs_data.py`:

```python
SITE_ID = "02479500"
LATITUDE = None
LONGITUDE = None
PARAMETER = "discharge"
STATISTIC = "mean"
UNITS = "metric"
START_DATE = "2023-01-01"
END_DATE = "2023-12-31"
```

They can be edited for repeated use. To use coordinates as the default, set `LATITUDE` and `LONGITUDE` and leave them both as numbers; the coordinate settings then take precedence over `SITE_ID`. Command-line values take precedence over the stored settings, so the file does not need to be changed for an occasional download. Run `python download_usgs_data.py --help` to see all options.

## Output columns

Each CSV contains:

- observation date and value;
- the unit corresponding to the saved value, based on the selected `--units` option;
- approval status and any qualifier;
- station, parameter, statistic, and time-series identifiers.

## Understanding the USGS flags

USGS provides two fields that help interpret an observation: `approval_status` and `qualifier`. The downloader writes both fields exactly as they are returned by the modern USGS API. It does not replace them with a numeric flag.

| Output field | Meaning |
| --- | --- |
| `approval_status = Approved` | USGS processing and review have been completed and the value is approved for publication. |
| `approval_status = Provisional` | The value has not received final approval and remains subject to revision. |
| `qualifier` | An additional USGS description attached to the observation, for example when a value is estimated, affected by ice, or associated with another special condition. |
| blank `qualifier` | USGS returned no additional qualifier for that observation. |

In the older USGS Water Services output, `A` meant approved and `P` meant provisional. The modern API separates those codes into the clearer `approval_status` field; the remaining observation flags are returned in `qualifier`. These fields describe publication status and special conditions, not a numerical accuracy score. Users should keep them with the measurements and consult the station record before deciding whether to exclude a flagged value.

## Larger numbers of requests

The downloader follows API pagination automatically. USGS recommends using an API key when making more than a few requests per hour. If needed, place your key in the `USGS_API_KEY` environment variable before running the script; the program will add it to the request.

## Data source

Data are provided by the U.S. Geological Survey. Please check the station record, units, qualifiers, and USGS data-use guidance before using the downloaded values in an analysis or publication.

## License

This project is released under the MIT License.
