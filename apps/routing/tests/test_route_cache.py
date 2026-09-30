import threading
import time
from unittest import mock

import numpy as np
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from apps.common.exceptions import RoutingServiceUnavailable
from apps.common.geo import Coordinates
from apps.routing.clients.osrm import CachedRoutingClient, Route

START = Coordinates(35.2, -101.83)
FINISH = Coordinates(36.16, -86.78)
ROUTE = Route(1000.0, 3600.0, np.array([[35.2, -101.83], [36.16, -86.78]]))


class SlowClient:
    def __init__(self, delay=0.2, error=None):
        self.calls = 0
        self.delay = delay
        self.error = error
        self._lock = threading.Lock()

    def route(self, start, finish):
        with self._lock:
            self.calls += 1
        time.sleep(self.delay)
        if self.error:
            raise self.error
        return ROUTE


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class RouteCacheStampedeTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def run_concurrently(self, client, count=6):
        results, errors = [], []

        def worker():
            try:
                results.append(client.route(START, FINISH))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        return results, errors

    def test_concurrent_requests_for_one_route_make_a_single_api_call(self):
        upstream = SlowClient()
        results, errors = self.run_concurrently(CachedRoutingClient(upstream, ttl_seconds=60))

        self.assertEqual(errors, [])
        self.assertEqual(upstream.calls, 1)
        self.assertEqual(len(results), 6)
        self.assertEqual(sum(called_api for _, called_api in results), 1)

    def test_waiters_retry_themselves_when_the_first_request_fails(self):
        upstream = SlowClient(error=RoutingServiceUnavailable("down"))
        results, errors = self.run_concurrently(CachedRoutingClient(upstream, ttl_seconds=60), count=3)

        self.assertEqual(results, [])
        self.assertEqual(len(errors), 3)
        self.assertIsNone(cache.get(CachedRoutingClient.cache_key(START, FINISH) + ":lock"))

    def test_gives_up_waiting_and_calls_the_api_after_the_lock_timeout(self):
        key = CachedRoutingClient.cache_key(START, FINISH)
        cache.set(f"{key}:lock", 1, 60)  # another request holds the lock and never finishes
        upstream = SlowClient(delay=0)

        route, called_api = CachedRoutingClient(upstream, ttl_seconds=60, lock_seconds=0.2).route(START, FINISH)

        self.assertTrue(called_api)
        self.assertEqual(route, ROUTE)
        self.assertIsNotNone(cache.get(f"{key}:lock"))  # someone else's lock is left alone

    def test_cache_outage_still_returns_a_route(self):
        upstream = SlowClient(delay=0)
        with (
            mock.patch("apps.routing.clients.osrm.cache.get", return_value=None),
            mock.patch("apps.routing.clients.osrm.cache.add", return_value=True),
            mock.patch("apps.routing.clients.osrm.cache.set"),
            mock.patch("apps.routing.clients.osrm.cache.delete"),
        ):
            _, called_api = CachedRoutingClient(upstream, ttl_seconds=60).route(START, FINISH)
        self.assertTrue(called_api)
