"""Import the OPIS fuel price CSV into FuelStation rows.

The file has no coordinates, so each station is geocoded offline to the
centroid of its city using the Census places table. This happens once at
import time, so route requests never need a geocoding API.

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

from django.db import transaction

from apps.common.geo import Coordinates
from apps.geodata.normalization import place_key
from apps.geodata.services import place_coordinates_lookup
from apps.geodata.us_states import US_STATES
from apps.stations.models import FuelStation

logger = logging.getLogger(__name__)

PRICE_QUANTUM = Decimal("0.0001")


@dataclass
class ImportReport:
    rows_read: int = 0
    stations: int = 0
    geocoded: int = 0
    skipped_non_us: int = 0
    skipped_invalid: int = 0
    ungeocoded: list[str] = field(default_factory=list)

    @property
    def ungeocoded_count(self) -> int:
        return len(self.ungeocoded)


def _parse_row(row: dict) -> FuelStation | None:
    try:
        return FuelStation(
            opis_id=int(row["OPIS Truckstop ID"]),
            name=row["Truckstop Name"].strip(),
            address=row["Address"].strip(),
            city=row["City"].strip(),
            state=row["State"].strip().upper(),
            rack_id=int(row["Rack ID"]) if row.get("Rack ID", "").strip() else None,
            price=Decimal(row["Retail Price"].strip()).quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP),
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
        coordinates = places.get((station.state, place_key(station.city)))
        if coordinates:
            station.latitude, station.longitude = coordinates.latitude, coordinates.longitude
            report.geocoded += 1
        else:
            report.ungeocoded.append(f"{station.city}, {station.state}")

    report.stations = len(cheapest)
    return list(cheapest.values()), report


@transaction.atomic
def import_fuel_prices(path: Path) -> ImportReport:
    """Replace all stations with the contents of the price file."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        stations, report = build_stations(csv.DictReader(handle), place_coordinates_lookup())

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
