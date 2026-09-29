"""Find the fuel stations that lie along a route.

The route is resampled to evenly spaced points (default: every mile). For each
station near the route's bounding box we find the closest route point with one
matrix product of unit vectors (cosine similarity == great-circle proximity),
which gives both how far off-route the station is and its mile marker along
the route. Everything is vectorized, so thousands of stations against a
coast-to-coast route takes tens of milliseconds.
"""

from dataclasses import dataclass

import numpy as np

from apps.common.geo import EARTH_RADIUS_MILES, unit_vectors
from apps.stations.index import StationIndex, StationRecord

_CHUNK_SIZE = 512
_MILES_PER_DEGREE_LAT = 69.0


@dataclass(frozen=True)
class StationOnRoute:
    record: StationRecord
    mile_marker: float
    distance_from_route_miles: float

    @property
    def price(self) -> float:
        return self.record.price


def _bounding_box_mask(index: StationIndex, lat: np.ndarray, lon: np.ndarray, margin_miles: float) -> np.ndarray:
    lat_margin = margin_miles / _MILES_PER_DEGREE_LAT
    widest = np.cos(np.radians(min(abs(lat).max() + lat_margin, 89.0)))
    lon_margin = margin_miles / (_MILES_PER_DEGREE_LAT * widest)
    return (
        (index.latitude >= lat.min() - lat_margin)
        & (index.latitude <= lat.max() + lat_margin)
        & (index.longitude >= lon.min() - lon_margin)
        & (index.longitude <= lon.max() + lon_margin)
    )


def find_stations_along_route(
    index: StationIndex,
    route_lat: np.ndarray,
    route_lon: np.ndarray,
    route_miles: np.ndarray,
    corridor_miles: float,
) -> list[StationOnRoute]:
    """Return stations within `corridor_miles` of the route, ordered by mile marker."""
    if len(index) == 0 or len(route_lat) == 0:
        return []

    candidates = np.flatnonzero(_bounding_box_mask(index, route_lat, route_lon, corridor_miles))
    if candidates.size == 0:
        return []

    route_vectors = unit_vectors(route_lat, route_lon)
    nearest_point = np.empty(candidates.size, dtype=np.int64)
    best_cosine = np.empty(candidates.size)

    for start in range(0, candidates.size, _CHUNK_SIZE):
        chunk = candidates[start : start + _CHUNK_SIZE]
        cosines = index.vectors[chunk] @ route_vectors.T  # (chunk, route_points)
        nearest = cosines.argmax(axis=1)
        nearest_point[start : start + chunk.size] = nearest
        best_cosine[start : start + chunk.size] = cosines[np.arange(chunk.size), nearest]

    offsets = EARTH_RADIUS_MILES * np.arccos(np.clip(best_cosine, -1.0, 1.0))
    within = offsets <= corridor_miles

    stations = [
        StationOnRoute(
            record=index.records[station],
            mile_marker=float(route_miles[point]),
            distance_from_route_miles=float(offset),
        )
        for station, point, offset in zip(candidates[within], nearest_point[within], offsets[within], strict=True)
    ]
    stations.sort(key=lambda s: (s.mile_marker, s.price))
    return stations


def cheapest_per_stretch(stations: list[StationOnRoute], stretch_miles: float) -> list[StationOnRoute]:
    """Keep only the cheapest station in each `stretch_miles` stretch of road.

    Stations a few miles apart are interchangeable for planning purposes, and
    thinning them keeps the optimizer fast and its plans free of pointless
    back-to-back stops. Input and output are ordered by mile marker.
    """
    if stretch_miles <= 0:
        return list(stations)

    kept: list[StationOnRoute] = []
    stretch_start: float | None = None
    for station in stations:
        if stretch_start is None or station.mile_marker - stretch_start > stretch_miles:
            stretch_start = station.mile_marker
            kept.append(station)
        elif (station.price, station.distance_from_route_miles) < (kept[-1].price, kept[-1].distance_from_route_miles):
            kept[-1] = station
    return kept
