"""Turn user input ("Dallas, TX" or "32.77,-96.79") into coordinates inside the USA."""

import re
from dataclasses import dataclass

from apps.common.exceptions import InvalidLocationError
from apps.common.geo import Coordinates
from apps.geodata.services import find_place
from apps.geodata.us_states import normalize_state

_COORDINATES = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")
_COUNTRY_SUFFIX = re.compile(r"[,\s]+(usa|us|u\.s\.a\.|u\.s\.|united states(?: of america)?)\s*$", re.IGNORECASE)

# Rough bounding boxes (south, west, north, east) for the contiguous US, Alaska and Hawaii.
_US_BOUNDS = (
    (24.3, -125.0, 49.5, -66.8),
    (51.0, -180.0, 71.6, -129.9),
    (18.8, -160.3, 22.3, -154.7),
)


@dataclass(frozen=True)
class ResolvedLocation:
    query: str
    label: str
    coordinates: Coordinates


def is_in_usa(coordinates: Coordinates) -> bool:
    return any(
        south <= coordinates.latitude <= north and west <= coordinates.longitude <= east
        for south, west, north, east in _US_BOUNDS
    )


def resolve_location(query: str) -> ResolvedLocation:
    """Resolve a location without any external API call.

    Accepted formats:
      * "latitude,longitude"      e.g. "40.7128,-74.0060"
      * "City, ST" / "City, State" e.g. "Austin, TX", "Saint Louis, Missouri"
    """
    text = query.strip()
    match = _COORDINATES.match(text)
    if match:
        coordinates = Coordinates(float(match.group(1)), float(match.group(2)))
        if not is_in_usa(coordinates):
            raise InvalidLocationError(f"'{query}' is outside the USA.", {"query": query})
        return ResolvedLocation(query, f"{coordinates.latitude:.5f}, {coordinates.longitude:.5f}", coordinates)

    text = _COUNTRY_SUFFIX.sub("", text)
    if "," in text:
        city, _, state_text = text.rpartition(",")
    else:
        city, _, state_text = text.rpartition(" ")
    state = normalize_state(state_text) if state_text else None
    if not city.strip() or state is None:
        raise InvalidLocationError(
            f"Could not understand '{query}'. Use 'City, ST' (e.g. 'Austin, TX') or 'lat,lng'.",
            {"query": query},
        )

    place = find_place(city, state)
    if place is None:
        raise InvalidLocationError(f"Unknown US city '{city.strip()}, {state}'.", {"query": query})
    return ResolvedLocation(query, str(place), Coordinates(place.latitude, place.longitude))
