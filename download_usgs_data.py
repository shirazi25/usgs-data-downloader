"""Download daily observations from the modern USGS Water Data API."""

from __future__ import annotations

import argparse
import csv
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path


# Change these defaults, or override them with command-line options.
SITE_ID = "02479500"
PARAMETER = "discharge"
STATISTIC = "mean"
START_DATE = "2023-01-01"
END_DATE = "2023-12-31"
OUTPUT_DIR = Path(__file__).resolve().parent / "data"

API_URL = "https://api.waterdata.usgs.gov/ogcapi/v1/collections/daily/items"
PAGE_SIZE = 10_000
MAX_RETRIES = 3

PARAMETER_ALIASES = {
    "discharge": "00060",
    "streamflow": "00060",
    "stage": "00065",
    "gage_height": "00065",
    "water_temperature": "00010",
}

STATISTIC_ALIASES = {
    "maximum": "00001",
    "max": "00001",
    "minimum": "00002",
    "min": "00002",
    "mean": "00003",
}

CSV_FIELDS = [
    "date",
    "value",
    "unit",
    "approval_status",
    "qualifier",
    "site_id",
    "parameter_code",
    "statistic_code",
    "time_series_id",
]


def resolve_code(value: str, aliases: dict[str, str], label: str) -> str:
    """Resolve a friendly name or validate a five-digit USGS code."""
    cleaned = value.strip().lower().replace("-", "_").replace(" ", "_")
    code = aliases.get(cleaned, cleaned)
    if len(code) != 5 or not code.isdigit():
        choices = ", ".join(sorted(set(aliases)))
        raise ValueError(
            f"Invalid {label} '{value}'. Use a five-digit USGS code or one of: {choices}."
        )
    return code


def normalize_site_id(site_id: str) -> tuple[str, str]:
    """Return the API form and the plain station number."""
    plain_id = site_id.strip()
    if plain_id.upper().startswith("USGS-"):
        plain_id = plain_id[5:]
    if not plain_id or not all(ch.isalnum() or ch in "-_" for ch in plain_id):
        raise ValueError("The site ID contains unsupported characters.")
    return f"USGS-{plain_id}", plain_id


def validate_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError(f"Invalid date '{value}'. Use YYYY-MM-DD.") from exc


def build_url(
    site_id: str,
    parameter_code: str,
    statistic_code: str,
    start_date: str,
    end_date: str,
) -> str:
    params = {
        "f": "json",
        "limit": PAGE_SIZE,
        "monitoring_location_id": site_id,
        "parameter_code": parameter_code,
        "statistic_id": statistic_code,
        "datetime": f"{start_date}/{end_date}",
    }
    api_key = os.environ.get("USGS_API_KEY", "").strip()
    if api_key:
        params["api_key"] = api_key
    return f"{API_URL}?{urllib.parse.urlencode(params)}"


def request_json(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/geo+json",
            "User-Agent": "usgs-data-downloader/1.0",
        },
    )

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.load(response)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                time.sleep(2 ** (attempt - 1))

    raise RuntimeError(f"USGS request failed after {MAX_RETRIES} attempts: {last_error}")


def text_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    return str(value)


def download_rows(first_url: str) -> list[dict[str, str]]:
    """Download all pages and convert GeoJSON features to CSV rows."""
    rows: list[dict[str, str]] = []
    next_url: str | None = first_url

    while next_url:
        payload = request_json(next_url)
        for feature in payload.get("features", []):
            properties = feature.get("properties", {})
            rows.append(
                {
                    "date": text_value(properties.get("time")),
                    "value": text_value(properties.get("value")),
                    "unit": text_value(properties.get("unit_of_measure")),
                    "approval_status": text_value(properties.get("approval_status")),
                    "qualifier": text_value(properties.get("qualifier")),
                    "site_id": text_value(properties.get("monitoring_location_id")),
                    "parameter_code": text_value(properties.get("parameter_code")),
                    "statistic_code": text_value(properties.get("statistic_id")),
                    "time_series_id": text_value(properties.get("time_series_id")),
                }
            )

        next_url = None
        for link in payload.get("links", []):
            if link.get("rel") == "next" and link.get("href"):
                next_url = urllib.parse.urljoin(API_URL, link["href"])
                break

    unique_rows = {
        (row["date"], row["time_series_id"]): row
        for row in rows
        if row["date"]
    }
    return sorted(unique_rows.values(), key=lambda row: (row["date"], row["time_series_id"]))


def save_csv(rows: list[dict[str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download daily discharge, stage, or another parameter from USGS."
    )
    parser.add_argument("--site", default=SITE_ID, help="USGS station number")
    parser.add_argument(
        "--parameter",
        default=PARAMETER,
        help="discharge, stage, water_temperature, or a five-digit USGS parameter code",
    )
    parser.add_argument(
        "--statistic",
        default=STATISTIC,
        help="mean, minimum, maximum, or a five-digit USGS statistic code",
    )
    parser.add_argument("--start-date", default=START_DATE, help="First date, YYYY-MM-DD")
    parser.add_argument("--end-date", default=END_DATE, help="Last date, YYYY-MM-DD")
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional CSV path; otherwise a descriptive filename is created in data/",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    api_site_id, plain_site_id = normalize_site_id(args.site)
    parameter_code = resolve_code(args.parameter, PARAMETER_ALIASES, "parameter")
    statistic_code = resolve_code(args.statistic, STATISTIC_ALIASES, "statistic")
    start_date = validate_date(args.start_date)
    end_date = validate_date(args.end_date)
    if start_date > end_date:
        raise ValueError("The start date must be on or before the end date.")

    output_path = args.output
    if output_path is None:
        filename = (
            f"USGS_{plain_site_id}_{parameter_code}_{statistic_code}_"
            f"{start_date}_{end_date}.csv"
        )
        output_path = OUTPUT_DIR / filename

    url = build_url(
        api_site_id,
        parameter_code,
        statistic_code,
        start_date,
        end_date,
    )
    print(
        f"Downloading site {plain_site_id}, parameter {parameter_code}, "
        f"statistic {statistic_code}, {start_date} to {end_date}..."
    )
    rows = download_rows(url)
    if not rows:
        raise RuntimeError(
            "No matching daily values were found. Check whether this station publishes "
            "the selected parameter and statistic for the requested dates."
        )

    save_csv(rows, output_path)
    units = ", ".join(sorted({row["unit"] for row in rows if row["unit"]}))
    print(f"Saved {len(rows):,} observations to {output_path.resolve()}")
    if units:
        print(f"Units: {units}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(f"Error: {exc}") from exc
