"""Offline geocoding against the Place table, and loading it from Census Gazetteer files."""

import csv
import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from django.db import transaction

from apps.common.geo import Coordinates
from apps.geodata.models import Place
from apps.geodata.normalization import census_name_aliases, census_suffix, clean_census_name, place_key
from apps.geodata.us_states import US_STATES

logger = logging.getLogger(__name__)

Source = Place.Source

# County subdivisions also include large statistical areas (CCDs, precincts,
# numbered districts) whose centroid can be far from any town. Keep only real
# municipalities: New England towns, Mid-Atlantic/Midwest townships, etc.
_MUNICIPAL_SUBDIVISIONS = {
    "township",
    "charter township",
    "town",
    "city",
    "borough",
    "village",
    "plantation",
    "gore",
    "grant",
    "location",
    "purchase",
}


def find_place(city: str, state: str) -> Place | None:
    return Place.objects.filter(state=state, key=place_key(city)).first()


def place_coordinates_lookup() -> dict[tuple[str, str], Coordinates]:
    """Load every place into memory for fast bulk geocoding (tens of thousands of rows, a few MB)."""
    return {
        (state, key): Coordinates(lat, lon)
        for state, key, lat, lon in Place.objects.values_list("state", "key", "latitude", "longitude")
    }


@dataclass(frozen=True)
class PlaceCandidate:
    state: str
    name: str
    latitude: float
    longitude: float
    land_area: float
    source: str

    @property
    def key(self) -> str:
        return place_key(self.name)

    def rank(self) -> tuple[int, float]:
        # Prefer the more authoritative source, then the larger place.
        return Place.SOURCE_PRIORITY[self.source], self.land_area


def _read_gazetteer(lines: Iterable[str]) -> Iterator[dict]:
    reader = csv.DictReader(lines, delimiter="\t")
    reader.fieldnames = [name.strip() for name in reader.fieldnames or []]
    for row in reader:
        if row["USPS"].strip() in US_STATES:
            yield row


def place_candidates(lines: Iterable[str]) -> Iterator[PlaceCandidate]:
    """Candidates from the Census places file (incorporated places and CDPs)."""
    for row in _read_gazetteer(lines):
        # LSAD "00" means the whole NAME is the name (e.g. "Carson City"); otherwise it ends with a descriptor.
        name = clean_census_name(row["NAME"], has_descriptor=row.get("LSAD", "").strip() != "00")
        common = {
            "state": row["USPS"].strip(),
            "latitude": float(row["INTPTLAT"]),
            "longitude": float(row["INTPTLONG"]),
            "land_area": float(row.get("ALAND_SQMI") or 0),
        }
        yield PlaceCandidate(name=name, source=Source.PLACE, **common)
        for alias in census_name_aliases(name):
            yield PlaceCandidate(name=alias, source=Source.PLACE_ALIAS, **common)


def county_subdivision_candidates(lines: Iterable[str]) -> Iterator[PlaceCandidate]:
    """Candidates from the Census county subdivisions file (townships and New England towns)."""
    for row in _read_gazetteer(lines):
        if census_suffix(row["NAME"]) not in _MUNICIPAL_SUBDIVISIONS:
            continue
        yield PlaceCandidate(
            state=row["USPS"].strip(),
            name=clean_census_name(row["NAME"]),
            latitude=float(row["INTPTLAT"]),
            longitude=float(row["INTPTLONG"]),
            land_area=float(row.get("ALAND_SQMI") or 0),
            source=Source.COUNTY_SUBDIVISION,
        )


def build_places(candidates: Iterable[PlaceCandidate]) -> list[Place]:
    """Keep the best candidate for each (state, name key)."""
    best: dict[tuple[str, str], PlaceCandidate] = {}
    for candidate in candidates:
        slot = (candidate.state, candidate.key)
        if slot[1] and (slot not in best or candidate.rank() > best[slot].rank()):
            best[slot] = candidate
    return [
        Place(
            state=state,
            key=key,
            name=candidate.name,
            latitude=candidate.latitude,
            longitude=candidate.longitude,
            land_area_sq_mi=candidate.land_area,
            source=candidate.source,
        )
        for (state, key), candidate in best.items()
    ]


def _chain_candidates(places_path: Path, subdivisions_path: Path | None) -> Iterator[PlaceCandidate]:
    with places_path.open(encoding="utf-8-sig") as handle:
        yield from place_candidates(handle)
    if subdivisions_path is not None:
        with subdivisions_path.open(encoding="utf-8-sig") as handle:
            yield from county_subdivision_candidates(handle)


@transaction.atomic
def load_places_from_gazetteers(places_path: Path, subdivisions_path: Path | None = None) -> int:
    places = build_places(_chain_candidates(places_path, subdivisions_path))
    Place.objects.all().delete()
    Place.objects.bulk_create(places, batch_size=2000)
    logger.info("Loaded %d places", len(places))
    return len(places)
