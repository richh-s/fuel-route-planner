"""In-memory, vectorized index of geocoded stations.

Loading ~7k stations from the database takes a few milliseconds, but doing it
on every request is wasted work, so the index is built once per process and
reused. Call `reset_station_index()` after re-importing prices (the import
command does this for its own process; restart app servers to pick up a new
import).
"""

import threading
from dataclasses import dataclass

import numpy as np

from apps.common.geo import unit_vectors
from apps.stations.models import FuelStation


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


@dataclass(frozen=True)
class StationIndex:
    records: tuple[StationRecord, ...]
    latitude: np.ndarray
    longitude: np.ndarray
    price: np.ndarray
    vectors: np.ndarray  # (n, 3) unit vectors, for fast nearest-point queries

    @classmethod
    def from_records(cls, records: list[StationRecord]) -> "StationIndex":
        latitude = np.array([r.latitude for r in records], dtype=float)
        longitude = np.array([r.longitude for r in records], dtype=float)
        return cls(
            records=tuple(records),
            latitude=latitude,
            longitude=longitude,
            price=np.array([r.price for r in records], dtype=float),
            vectors=unit_vectors(latitude, longitude) if records else np.empty((0, 3)),
        )

    def __len__(self) -> int:
        return len(self.records)


def build_station_index() -> StationIndex:
    rows = (
        FuelStation.objects.filter(latitude__isnull=False, longitude__isnull=False)
        .values_list("opis_id", "name", "address", "city", "state", "price", "latitude", "longitude")
        .iterator()
    )
    records = [
        StationRecord(opis_id, name, address, city, state, float(price), lat, lon)
        for opis_id, name, address, city, state, price, lat, lon in rows
    ]
    return StationIndex.from_records(records)


_index: StationIndex | None = None
_lock = threading.Lock()


def get_station_index() -> StationIndex:
    global _index
    if _index is None:
        with _lock:
            if _index is None:
                _index = build_station_index()
    return _index


def reset_station_index() -> None:
    global _index
    with _lock:
        _index = None
