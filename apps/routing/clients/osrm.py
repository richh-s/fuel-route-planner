"""Client for the OSRM routing API (https://project-osrm.org).

A single `/route` request returns both the distance and the full geometry of
the route, so planning a trip costs exactly one external call. Results are
cached by rounded coordinates, so repeated trips (and the map page) cost zero.
"""

import hashlib
import logging
from dataclasses import dataclass

import numpy as np
import requests
from django.conf import settings
from django.core.cache import cache

from apps.common.exceptions import RouteNotFoundError, RoutingServiceUnavailable
from apps.common.geo import METERS_PER_MILE, Coordinates
from apps.common.polyline import decode_polyline

logger = logging.getLogger(__name__)

NO_ROUTE_CODES = {"NoRoute", "NoSegment", "NoMatch"}


@dataclass(frozen=True)
class Route:
    distance_miles: float
    duration_seconds: float
    coordinates: np.ndarray  # (n, 2) array of (latitude, longitude)


class OSRMClient:
    def __init__(self, base_url: str, timeout: float, session: requests.Session | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()

    @classmethod
    def from_settings(cls) -> "OSRMClient":
        config = settings.FUEL_ROUTE
        return cls(config["OSRM_BASE_URL"], config["OSRM_TIMEOUT_SECONDS"])

    def route(self, start: Coordinates, finish: Coordinates) -> Route:
        # OSRM expects lon,lat order.
        points = f"{start.longitude},{start.latitude};{finish.longitude},{finish.latitude}"
        url = f"{self.base_url}/route/v1/driving/{points}"
        params = {"overview": "full", "geometries": "polyline6", "steps": "false", "alternatives": "false"}

        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            logger.warning("OSRM request failed: %s", exc)
            raise RoutingServiceUnavailable("The routing service could not be reached. Please retry.") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise RoutingServiceUnavailable(f"The routing service returned HTTP {response.status_code}.") from exc

        code = payload.get("code")
        if code in NO_ROUTE_CODES:
            raise RouteNotFoundError("No drivable route exists between these locations.", {"osrm_code": code})
        if response.status_code != 200 or code != "Ok" or not payload.get("routes"):
            raise RoutingServiceUnavailable(
                "The routing service returned an error.", {"status": response.status_code, "osrm_code": code}
            )

        best = payload["routes"][0]
        return Route(
            distance_miles=best["distance"] / METERS_PER_MILE,
            duration_seconds=best["duration"],
            coordinates=decode_polyline(best["geometry"], precision=6),
        )


class CachedRoutingClient:
    """Wraps a routing client with the Django cache. Reports whether the external API was called."""

    def __init__(self, client, ttl_seconds: int):
        self.client = client
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def cache_key(start: Coordinates, finish: Coordinates) -> str:
        # ~11 m precision: nearby requests share a cached route.
        raw = f"{start.latitude:.4f},{start.longitude:.4f};{finish.latitude:.4f},{finish.longitude:.4f}"
        return "route:v1:" + hashlib.sha1(raw.encode()).hexdigest()

    def route(self, start: Coordinates, finish: Coordinates) -> tuple[Route, bool]:
        key = self.cache_key(start, finish)
        cached = cache.get(key)
        if cached is not None:
            return cached, False
        route = self.client.route(start, finish)
        cache.set(key, route, self.ttl_seconds)
        return route, True


def get_routing_client() -> CachedRoutingClient:
    return CachedRoutingClient(OSRMClient.from_settings(), settings.FUEL_ROUTE["ROUTE_CACHE_TTL_SECONDS"])
