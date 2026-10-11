"""Download daily observations from the modern USGS Water Data API."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path


# Change these defaults, or override them with command-line options.
SITE_ID = "02479500"
LATITUDE = None
LONGITUDE = None
PARAMETER = "discharge"
STATISTIC = "mean"
UNITS = "metric"
START_DATE = "2023-01-01"
END_DATE = "2023-12-31"
OUTPUT_DIR = Path(__file__).resolve().parent / "data"

API_URL = "https://api.waterdata.usgs.gov/ogcapi/v1/collections/daily/items"
METADATA_URL = "https://api.waterdata.usgs.gov/ogcapi/v1/collections/time-series-metadata/items"
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


def validate_coordinates(latitude: float, longitude: float) -> None:
    if not -90 <= latitude <= 90:
        raise ValueError("Latitude must be between -90 and 90.")
    if not -180 <= longitude <= 180:
        raise ValueError("Longitude must be between -180 and 180.")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance between two WGS84 coordinates."""
    earth_radius_km = 6371.0088
    lat1_rad, lat2_rad = math.radians(lat1), math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
    )
    return 2 * earth_radius_km * math.asin(math.sqrt(a))


def find_nearest_site(
    latitude: float,
    longitude: float,
    parameter_code: str,
    statistic_code: str,
    search_radius_km: float,
) -> tuple[str, str, float]:
    """Find the nearest gauge offering the requested daily time series."""
    validate_coordinates(latitude, longitude)
    if search_radius_km <= 0:
        raise ValueError("Search radius must be greater than zero.")

    latitude_delta = search_radius_km / 111.32
    longitude_scale = max(math.cos(math.radians(latitude)), 0.01)
    longitude_delta = search_radius_km / (111.32 * longitude_scale)
    bbox = (
        longitude - longitude_delta,
        latitude - latitude_delta,
        longitude + longitude_delta,
        latitude + latitude_delta,
    )
    params = {
        "f": "json",
        "limit": PAGE_SIZE,
        "bbox": ",".join(f"{value:.7f}" for value in bbox),
        "parameter_code": parameter_code,
        "statistic_id": statistic_code,
    }
    api_key = os.environ.get("USGS_API_KEY", "").strip()
    if api_key:
        params["api_key"] = api_key

    next_url: str | None = f"{METADATA_URL}?{urllib.parse.urlencode(params)}"
    sites: dict[str, float] = {}
    while next_url:
        payload = request_json(next_url)
        for feature in payload.get("features", []):
            properties = feature.get("properties", {})
            geometry = feature.get("geometry") or {}
            coordinates = geometry.get("coordinates") or []
            site_id = text_value(properties.get("monitoring_location_id"))
            if len(coordinates) < 2 or not site_id:
                continue
            site_lon, site_lat = float(coordinates[0]), float(coordinates[1])
            distance = haversine_km(latitude, longitude, site_lat, site_lon)
            if distance <= search_radius_km:
                sites[site_id] = min(distance, sites.get(site_id, float("inf")))

        next_url = None
        for link in payload.get("links", []):
            if link.get("rel") == "next" and link.get("href"):
                next_url = urllib.parse.urljoin(METADATA_URL, link["href"])
                break

    if not sites:
        raise RuntimeError(
            f"No gauge offering parameter {parameter_code} and statistic {statistic_code} "
            f"was found within {search_radius_km:g} km of the supplied coordinates."
        )

    api_site_id, distance = min(sites.items(), key=lambda item: item[1])
    plain_site_id = api_site_id.removeprefix("USGS-")
    return api_site_id, plain_site_id, distance


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


def format_decimal(value: Decimal, decimal_places: int = 6) -> str:
    quantizer = Decimal(1).scaleb(-decimal_places)
    text = format(value.quantize(quantizer), "f")
    return text.rstrip("0").rstrip(".")


def metric_value(value: str, unit: str) -> tuple[str, str]:
    """Convert common USGS customary units while leaving metric units unchanged."""
    if not value or not unit:
        return value, unit

    normalized_unit = unit.strip().lower().replace("³", "^3")
    try:
        numeric_value = Decimal(value)
    except InvalidOperation:
        return value, unit

    if normalized_unit in {"ft^3/s", "ft3/s", "cfs"}:
        return format_decimal(numeric_value * Decimal("0.028316846592")), "m^3/s"
    if normalized_unit in {"ft", "feet", "foot"}:
        return format_decimal(numeric_value * Decimal("0.3048")), "m"
    if normalized_unit in {"in", "inch", "inches"}:
        return format_decimal(numeric_value * Decimal("25.4")), "mm"
    if normalized_unit in {"degf", "°f"}:
        converted = (numeric_value - Decimal("32")) * Decimal(5) / Decimal(9)
        return format_decimal(converted), "degC"

    # Parameters already reported in metric or scientific units are retained.
    return value, unit


def apply_unit_choice(rows: list[dict[str, str]], unit_system: str) -> None:
    if unit_system == "usgs":
        return
    for row in rows:
        row["value"], row["unit"] = metric_value(row["value"], row["unit"])


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
    parser.add_argument("--site", help="USGS gauge/station number")
    parser.add_argument("--latitude", type=float, help="Latitude used to find the nearest gauge")
    parser.add_argument("--longitude", type=float, help="Longitude used to find the nearest gauge")
    parser.add_argument(
        "--search-radius-km",
        type=float,
        default=50.0,
        help="Maximum coordinate-search distance in kilometres (default: 50)",
    )
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
    parser.add_argument(
        "--units",
        choices=("metric", "usgs"),
        default=UNITS,
        help="Output unit system (default: metric)",
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

    parameter_code = resolve_code(args.parameter, PARAMETER_ALIASES, "parameter")
    statistic_code = resolve_code(args.statistic, STATISTIC_ALIASES, "statistic")
    start_date = validate_date(args.start_date)
    end_date = validate_date(args.end_date)
    if start_date > end_date:
        raise ValueError("The start date must be on or before the end date.")

    coordinate_option_used = args.latitude is not None or args.longitude is not None
    if args.site and coordinate_option_used:
        raise ValueError("Use either --site or --latitude/--longitude, not both.")

    if coordinate_option_used:
        if args.latitude is None or args.longitude is None:
            raise ValueError("Both --latitude and --longitude are required for a coordinate search.")
        api_site_id, plain_site_id, distance_km = find_nearest_site(
            args.latitude,
            args.longitude,
            parameter_code,
            statistic_code,
            args.search_radius_km,
        )
        print(f"Nearest matching gauge: {plain_site_id} ({distance_km:.2f} km away)")
    elif args.site:
        api_site_id, plain_site_id = normalize_site_id(args.site)
    elif LATITUDE is not None or LONGITUDE is not None:
        if LATITUDE is None or LONGITUDE is None:
            raise ValueError("Set both LATITUDE and LONGITUDE, or leave both as None.")
        api_site_id, plain_site_id, distance_km = find_nearest_site(
            float(LATITUDE),
            float(LONGITUDE),
            parameter_code,
            statistic_code,
            args.search_radius_km,
        )
        print(f"Nearest matching gauge: {plain_site_id} ({distance_km:.2f} km away)")
    else:
        api_site_id, plain_site_id = normalize_site_id(SITE_ID)

    output_path = args.output
    if output_path is None:
        filename = (
            f"USGS_{plain_site_id}_{parameter_code}_{statistic_code}_"
            f"{start_date}_{end_date}_{args.units}.csv"
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

    apply_unit_choice(rows, args.units)
    save_csv(rows, output_path)
    units = ", ".join(sorted({row["unit"] for row in rows if row["unit"]}))
    print(f"Saved {len(rows):,} observations to {output_path.resolve()}")
    if units:
        print(f"Units ({args.units} option): {units}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(f"Error: {exc}") from exc
