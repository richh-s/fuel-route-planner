"""Import the OPIS fuel price CSV into FuelStation rows.

If the file has `Latitude` and `Longitude` columns they are used as the
station's exact position. The OPIS file has none (its addresses are highway
exits such as "I-44, EXIT 283", which address geocoders cannot resolve), so
each station falls back to the centroid of its city from the Census places
table. Either way this happens once at import time, so route requests never
need a geocoding API.

Data quirks handled here:
  * the same OPIS ID appears several times (one row per price/brand) — we keep the lowest price;
  * Canadian stations (AB, ON, ...) are skipped because routes are US-only;
  * city names are padded with spaces and spelled inconsistently.
"""

import csv
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.db import transaction

from apps.common.geo import Coordinates
from apps.geodata.normalization import place_key
from apps.geodata.services import place_coordinates_lookup
from apps.geodata.us_states import US_STATES
from apps.stations.models import FuelStation

logger = logging.getLogger(__name__)

PRICE_QUANTUM = Decimal("0.0001")
Precision = FuelStation.LocationPrecision


class ImportRejected(Exception):
    """The new file looks broken (e.g. truncated); the existing data was left untouched."""


@dataclass
class ImportReport:
    rows_read: int = 0
    stations: int = 0
    geocoded: int = 0
    skipped_non_us: int = 0
    skipped_invalid: int = 0
    exact_locations: int = 0
    ungeocoded: list[str] = field(default_factory=list)

    @property
    def ungeocoded_count(self) -> int:
        return len(self.ungeocoded)


def _exact_coordinates(row: dict) -> tuple[float | None, float | None]:
    """Coordinates supplied by the feed, if present and plausible."""
    try:
        latitude, longitude = float(row["Latitude"]), float(row["Longitude"])
    except (KeyError, TypeError, ValueError):
        return None, None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180) or (latitude == 0 and longitude == 0):
        return None, None
    return latitude, longitude


def _parse_row(row: dict) -> FuelStation | None:
    latitude, longitude = _exact_coordinates(row)
    try:
        return FuelStation(
            opis_id=int(row["OPIS Truckstop ID"]),
            name=row["Truckstop Name"].strip(),
            address=row["Address"].strip(),
            city=row["City"].strip(),
            state=row["State"].strip().upper(),
            rack_id=int(row["Rack ID"]) if row.get("Rack ID", "").strip() else None,
            price=Decimal(row["Retail Price"].strip()).quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP),
            latitude=latitude,
            longitude=longitude,
            location_precision=Precision.EXACT if latitude is not None else Precision.CITY_CENTROID,
        )
    except (KeyError, ValueError, InvalidOperation):
        return None


def build_stations(
    rows: Iterable[dict], places: dict[tuple[str, str], Coordinates]
) -> tuple[list[FuelStation], ImportReport]:
    """Deduplicate, filter and geocode CSV rows. Pure function: no database access."""
    report = ImportReport()
    cheapest: dict[int, FuelStation] = {}

    for row in rows:
        report.rows_read += 1
        station = _parse_row(row)
        if station is None:
            report.skipped_invalid += 1
            continue
        if station.state not in US_STATES:
            report.skipped_non_us += 1
            continue
        current = cheapest.get(station.opis_id)
        if current is None or station.price < current.price:
            cheapest[station.opis_id] = station

    for station in cheapest.values():
        if station.location_precision == Precision.EXACT:
            report.geocoded += 1
            report.exact_locations += 1
            continue
        coordinates = places.get((station.state, place_key(station.city)))
        if coordinates:
            station.latitude, station.longitude = coordinates.latitude, coordinates.longitude
            report.geocoded += 1
        else:
            report.ungeocoded.append(f"{station.city}, {station.state}")

    report.stations = len(cheapest)
    return list(cheapest.values()), report


@transaction.atomic
def import_fuel_prices(path: Path, force: bool = False) -> ImportReport:
    """Replace all stations with the contents of the price file.

    Refuses (unless `force`) a file that would leave far fewer usable stations
    than are loaded now, so a truncated or malformed feed cannot wipe good data.
    """
    with path.open(newline="", encoding="utf-8-sig") as handle:
        stations, report = build_stations(csv.DictReader(handle), place_coordinates_lookup())

    current = FuelStation.objects.filter(latitude__isnull=False).count()
    minimum = current * (1 - settings.FUEL_ROUTE["MAX_IMPORT_SHRINK_RATIO"])
    if not force and report.geocoded < minimum:
        raise ImportRejected(
            f"The file has {report.geocoded} usable stations but {current} are loaded now. "
            "Keeping the existing data; pass --force if this drop is expected."
        )

    FuelStation.objects.all().delete()
    FuelStation.objects.bulk_create(stations, batch_size=1000)
    logger.info(
        "Imported %d stations (%d geocoded, %d not geocoded) from %s",
        report.stations,
        report.geocoded,
        report.ungeocoded_count,
        path,
    )
    return report
