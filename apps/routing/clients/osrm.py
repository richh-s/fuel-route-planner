"""Client for the OSRM routing API (https://project-osrm.org).

A single `/route` request returns both the distance and the full geometry of
the route, so planning a trip costs exactly one external call. Results are
cached by rounded coordinates, so repeated trips (and the map page) cost zero.
"""

import hashlib
import logging
import time
from dataclasses import dataclass

import numpy as np
import requests
from django.conf import settings
from django.core.cache import cache
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from apps.common import metrics
from apps.common.exceptions import RouteNotFoundError, RoutingServiceUnavailable
from apps.common.geo import METERS_PER_MILE, Coordinates
from apps.common.polyline import decode_polyline

logger = logging.getLogger(__name__)

NO_ROUTE_CODES = {"NoRoute", "NoSegment", "NoMatch"}


def build_session(retries: int = 1) -> requests.Session:
    """HTTP session that retries transient failures (connection errors, 502/503/504) with backoff.

    429 (rate limited) is deliberately not retried: hammering the server would make it worse.
    """
    retry = Retry(
        total=retries,
        backoff_factor=0.3,
        status_forcelist=(502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    session.headers["User-Agent"] = "fuel-route-planner/1.0"
    return session


@dataclass(frozen=True)
class Route:
    distance_miles: float
    duration_seconds: float
    coordinates: np.ndarray  # (n, 2) array of (latitude, longitude)


class OSRMClient:
    def __init__(
        self,
        base_url: str,
        timeout: float,
        session: requests.Session | None = None,
        connect_timeout: float = 2.0,
        retries: int = 1,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self.session = session or build_session(retries)

    @classmethod
    def from_settings(cls) -> "OSRMClient":
        config = settings.FUEL_ROUTE
        return cls(
            config["OSRM_BASE_URL"],
            config["OSRM_TIMEOUT_SECONDS"],
            connect_timeout=config["OSRM_CONNECT_TIMEOUT_SECONDS"],
            retries=config["OSRM_RETRIES"],
        )

    def route(self, start: Coordinates, finish: Coordinates) -> Route:
        """Fetch a route, recording the outcome and latency as metrics."""
        started = time.perf_counter()
        outcome = "error"
        try:
            route = self._fetch(start, finish)
            outcome = "ok"
            return route
        except RouteNotFoundError:
            outcome = "no_route"
            raise
        finally:
            metrics.ROUTING_REQUESTS.labels(outcome=outcome).inc()
            metrics.ROUTING_DURATION.observe(time.perf_counter() - started)

    def _fetch(self, start: Coordinates, finish: Coordinates) -> Route:
        # OSRM expects lon,lat order.
        points = f"{start.longitude},{start.latitude};{finish.longitude},{finish.latitude}"
        url = f"{self.base_url}/route/v1/driving/{points}"
        params = {"overview": "full", "geometries": "polyline6", "steps": "false", "alternatives": "false"}

        try:
            response = self.session.get(url, params=params, timeout=(self.connect_timeout, self.timeout))
        except requests.RequestException as exc:
            logger.warning("OSRM request failed: %s", exc)
            raise RoutingServiceUnavailable("The routing service could not be reached. Please retry.") from exc

        if response.status_code == 429:
            raise RoutingServiceUnavailable("The routing service is rate limiting requests. Please retry shortly.")
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
    """Wraps a routing client with the Django cache. Reports whether the external API was called.

    When several requests want the same uncached route at once, only the first
    calls the routing API; the others wait briefly for its result to appear in
    the cache instead of each making the same call.
    """

    LOCK_POLL_SECONDS = 0.05

    def __init__(self, client, ttl_seconds: int, lock_seconds: float = 20.0):
        self.client = client
        self.ttl_seconds = ttl_seconds
        # Longest a request waits for another one's routing call; also when an abandoned lock expires.
        self.lock_seconds = lock_seconds

    @staticmethod
    def cache_key(start: Coordinates, finish: Coordinates) -> str:
        # ~11 m precision: nearby requests share a cached route.
        raw = f"{start.latitude:.4f},{start.longitude:.4f};{finish.latitude:.4f},{finish.longitude:.4f}"
        return "route:v1:" + hashlib.sha1(raw.encode()).hexdigest()

    def route(self, start: Coordinates, finish: Coordinates) -> tuple[Route, bool]:
        key = self.cache_key(start, finish)
        cached = cache.get(key)
        if cached is not None:
            metrics.ROUTE_CACHE.labels(result="hit").inc()
            return cached, False

        lock_key = f"{key}:lock"
        owns_lock = cache.add(lock_key, 1, self.lock_seconds)
        if not owns_lock:
            cached = self._wait_for_other_request(key, lock_key)
            if cached is not None:
                metrics.ROUTE_CACHE.labels(result="coalesced").inc()
                return cached, False
            # The other request failed or is too slow: fall through and call the API ourselves.

        metrics.ROUTE_CACHE.labels(result="miss").inc()
        try:
            route = self.client.route(start, finish)
            cache.set(key, route, self.ttl_seconds)
        finally:
            if owns_lock:
                cache.delete(lock_key)
        return route, True

    def _wait_for_other_request(self, key: str, lock_key: str) -> Route | None:
        deadline = time.monotonic() + self.lock_seconds
        while time.monotonic() < deadline:
            time.sleep(self.LOCK_POLL_SECONDS)
            cached = cache.get(key)
            if cached is not None:
                return cached
            if cache.get(lock_key) is None:
                # Lock released without a result (the other request failed), or just after storing one.
                return cache.get(key)
        return None


def get_routing_client() -> CachedRoutingClient:
    return CachedRoutingClient(OSRMClient.from_settings(), settings.FUEL_ROUTE["ROUTE_CACHE_TTL_SECONDS"])
