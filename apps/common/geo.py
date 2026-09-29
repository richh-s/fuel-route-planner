"""Vectorized geodesy helpers (spherical earth, which is accurate enough for routing)."""

from dataclasses import dataclass

import numpy as np

EARTH_RADIUS_MILES = 3958.7613
METERS_PER_MILE = 1609.344


@dataclass(frozen=True)
class Coordinates:
    latitude: float
    longitude: float


def haversine_miles(lat1, lon1, lat2, lon2):
    """Great-circle distance in miles. Accepts scalars or numpy arrays."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def unit_vectors(lat, lon) -> np.ndarray:
    """Convert lat/lon arrays (degrees) into an (n, 3) array of unit vectors on the sphere."""
    lat = np.radians(np.asarray(lat, dtype=float))
    lon = np.radians(np.asarray(lon, dtype=float))
    cos_lat = np.cos(lat)
    return np.column_stack((cos_lat * np.cos(lon), cos_lat * np.sin(lon), np.sin(lat)))


def cumulative_miles(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Distance travelled along a polyline up to each vertex, starting at 0."""
    if len(lat) < 2:
        return np.zeros(len(lat))
    segments = haversine_miles(lat[:-1], lon[:-1], lat[1:], lon[1:])
    return np.concatenate(([0.0], np.cumsum(segments)))


def resample_polyline(lat: np.ndarray, lon: np.ndarray, step_miles: float):
    """Return points spaced every `step_miles` along the polyline (endpoints included).

    Linear interpolation in lat/lon is fine here because routing polylines have
    short segments. Returns (lat, lon, cumulative_miles) arrays.
    """
    distance = cumulative_miles(lat, lon)
    total = float(distance[-1]) if len(distance) else 0.0
    if total == 0.0:
        return lat[:1].copy(), lon[:1].copy(), np.zeros(min(len(lat), 1))

    samples = np.arange(0.0, total, step_miles)
    samples = np.append(samples, total)
    # np.interp needs strictly increasing x; drop zero-length segments.
    keep = np.concatenate(([True], np.diff(distance) > 0))
    return (
        np.interp(samples, distance[keep], lat[keep]),
        np.interp(samples, distance[keep], lon[keep]),
        samples,
    )
