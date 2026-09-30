"""In-memory, vectorized index of geocoded stations.

Loading ~7k stations from the database takes a few milliseconds, but doing it
on every request is wasted work, so the index is built once per process and
reused. Every `STATION_INDEX_REFRESH_SECONDS` each process compares a cheap
fingerprint of the table (row count, newest `updated_at`) with the one it
built from, and rebuilds when prices have been re-imported. No restart needed.
"""

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from django.conf import settings
from django.db.models import Count, Max

from apps.common.geo import unit_vectors
from apps.stations.models import FuelStation

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StationRecord:
    opis_id: int
    name: str
    address: str
    city: str
    state: str
    price: float
    latitude: float
    longitude: float
    location_precision: str = "city_centroid"


@dataclass(frozen=True)
class StationIndex:
    records: tuple[StationRecord, ...]
    latitude: np.ndarray
    longitude: np.ndarray
    price: np.ndarray
    vectors: np.ndarray  # (n, 3) unit vectors, for fast nearest-point queries
    prices_updated_at: datetime | None = None

    @classmethod
    def from_records(cls, records: list[StationRecord], prices_updated_at: datetime | None = None) -> "StationIndex":
        latitude = np.array([r.latitude for r in records], dtype=float)
        longitude = np.array([r.longitude for r in records], dtype=float)
        return cls(
            records=tuple(records),
            latitude=latitude,
            longitude=longitude,
            price=np.array([r.price for r in records], dtype=float),
            vectors=unit_vectors(latitude, longitude) if records else np.empty((0, 3)),
            prices_updated_at=prices_updated_at,
        )

    def __len__(self) -> int:
        return len(self.records)


_GEOCODED = FuelStation.objects.filter(latitude__isnull=False, longitude__isnull=False)

Fingerprint = tuple[int, datetime | None]


def _fingerprint() -> Fingerprint:
    summary = _GEOCODED.aggregate(count=Count("id"), newest=Max("updated_at"))
    return summary["count"], summary["newest"]


def build_station_index(prices_updated_at: datetime | None = None) -> StationIndex:
    rows = _GEOCODED.values_list(
        "opis_id", "name", "address", "city", "state", "price", "latitude", "longitude", "location_precision"
    ).iterator()
    records = [
        StationRecord(opis_id, name, address, city, state, float(price), lat, lon, precision)
        for opis_id, name, address, city, state, price, lat, lon, precision in rows
        if lat is not None and lon is not None
    ]
    return StationIndex.from_records(records, prices_updated_at)


_index: StationIndex | None = None
_built_from: Fingerprint | None = None
_next_check = 0.0
_lock = threading.Lock()


def get_station_index() -> StationIndex:
    global _index, _built_from, _next_check
    if _index is not None and time.monotonic() < _next_check:
        return _index
    with _lock:
        if _index is not None and time.monotonic() < _next_check:
            return _index
        try:
            fingerprint = _fingerprint()
            if _index is None or fingerprint != _built_from:
                _index = build_station_index(prices_updated_at=fingerprint[1])
                _built_from = fingerprint
                logger.info("Station index built: %d stations, prices from %s", len(_index), fingerprint[1])
        except Exception:
            # Keep serving the stations we already have if the refresh fails (e.g. a locked database).
            if _index is None:
                raise
            logger.exception("Could not refresh the station index; serving the previous one")
        _next_check = time.monotonic() + settings.FUEL_ROUTE["STATION_INDEX_REFRESH_SECONDS"]
        return _index


def reset_station_index() -> None:
    global _index, _built_from, _next_check
    with _lock:
        _index, _built_from, _next_check = None, None, 0.0
