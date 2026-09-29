"""Decoder for the Google encoded polyline format (used by OSRM with `geometries=polyline6`).

Encoded polylines are ~10x smaller than GeoJSON coordinates, which keeps the
single routing API response small and fast for cross-country routes.
"""

import numpy as np


def decode_polyline(encoded: str, precision: int = 6) -> np.ndarray:
    """Decode an encoded polyline into an (n, 2) array of (latitude, longitude)."""
    factor = 10**precision
    values: list[int] = []
    index, length = 0, len(encoded)

    while index < length:
        result, shift = 0, 0
        while True:
            byte = ord(encoded[index]) - 63
            index += 1
            result |= (byte & 0x1F) << shift
            shift += 5
            if byte < 0x20:
                break
        values.append(~(result >> 1) if result & 1 else result >> 1)

    deltas = np.array(values, dtype=np.int64).reshape(-1, 2)
    return np.cumsum(deltas, axis=0) / factor
