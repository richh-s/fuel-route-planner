from unittest import mock

import numpy as np
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.common.auth import ClientRateThrottle, map_signature_is_valid, sign_map_params
from apps.geodata.models import Place
from apps.routing.clients.osrm import CachedRoutingClient, Route
from apps.routing.services.trip_planner import TripPlanner, VehicleProfile
from apps.stations.index import StationIndex, StationRecord

ROUTE_LON = np.linspace(-101.83, -98.0, 400)
ROUTE = Route(200.0, 3 * 3600, np.column_stack((np.full_like(ROUTE_LON, 35.2), ROUTE_LON)))
STATIONS = StationIndex.from_records([StationRecord(1, "Stop", "I-40", "Amarillo", "TX", 3.0, 35.2, -101.83)])
TRIP = {"start": "Amarillo, TX", "finish": "35.2,-98.0"}


class FakeOSRM:
    def route(self, start, finish):
        return ROUTE


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class ApiKeyTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        Place.objects.create(state="TX", name="Amarillo", key="amarillo", latitude=35.2, longitude=-101.83)

    def setUp(self):
        cache.clear()
        planner = TripPlanner(
            routing_client=CachedRoutingClient(FakeOSRM(), ttl_seconds=60),
            station_index=lambda: STATIONS,
            vehicle=VehicleProfile(range_miles=500, miles_per_gallon=10),
        )
        patcher = mock.patch("apps.routing.api.views.get_trip_planner", return_value=planner)
        patcher.start()
        self.addCleanup(patcher.stop)


class OpenApiTests(ApiKeyTestCase):
    def test_no_key_is_needed_when_none_are_configured(self):
        response = self.client.get(reverse("routing:trip-plan"), TRIP)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("sig=", response.json()["map_url"])


@override_settings(API_KEYS=["first-key", "second-key"])
class EnforcedApiKeyTests(ApiKeyTestCase):
    def test_missing_key_is_rejected(self):
        response = self.client.get(reverse("routing:trip-plan"), TRIP)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "not_authenticated")
        self.assertIn("WWW-Authenticate", response.headers)

    def test_wrong_key_is_rejected(self):
        response = self.client.get(reverse("routing:trip-plan"), TRIP, headers={"x-api-key": "nope"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "authentication_failed")

    def test_any_configured_key_works_in_either_header(self):
        by_header = self.client.get(reverse("routing:trip-plan"), TRIP, headers={"x-api-key": "second-key"})
        by_bearer = self.client.get(reverse("routing:trip-plan"), TRIP, headers={"authorization": "Bearer first-key"})
        self.assertEqual((by_header.status_code, by_bearer.status_code), (200, 200))

    def test_health_and_docs_stay_open(self):
        with mock.patch("apps.routing.api.views.get_station_index", return_value=STATIONS):
            self.assertEqual(self.client.get(reverse("routing:health-ready")).status_code, 200)
        self.assertEqual(self.client.get(reverse("routing:health-live")).status_code, 200)
        self.assertEqual(self.client.get(reverse("schema")).status_code, 200)

    def test_map_link_from_the_api_opens_without_a_key(self):
        map_url = self.client.get(reverse("routing:trip-plan"), TRIP, headers={"x-api-key": "first-key"}).json()[
            "map_url"
        ]
        self.assertIn("sig=", map_url)
        self.assertEqual(self.client.get(map_url).status_code, 200)

    def test_map_rejects_unsigned_and_tampered_links(self):
        map_url = self.client.get(reverse("routing:trip-plan"), TRIP, headers={"x-api-key": "first-key"}).json()[
            "map_url"
        ]
        self.assertEqual(self.client.get(reverse("routing:trip-map"), TRIP).status_code, 403)
        self.assertEqual(self.client.get(map_url.replace("Amarillo", "Dallas")).status_code, 403)

    @override_settings(MAP_LINK_MAX_AGE_SECONDS=60)
    def test_map_links_expire(self):
        with mock.patch("time.time", return_value=1_000_000):
            signature = sign_map_params(TRIP)
        with mock.patch("time.time", return_value=1_000_030):
            self.assertTrue(map_signature_is_valid(TRIP, signature))
        with mock.patch("time.time", return_value=1_000_090):
            self.assertFalse(map_signature_is_valid(TRIP, signature))

    def test_rate_limit_is_counted_per_key_not_per_ip(self):
        url = reverse("routing:trip-plan")
        with mock.patch.object(ClientRateThrottle, "THROTTLE_RATES", {"trip_plan": "2/minute"}):
            first = [self.client.get(url, TRIP, headers={"x-api-key": "first-key"}).status_code for _ in range(3)]
            second = self.client.get(url, TRIP, headers={"x-api-key": "second-key"}).status_code
        self.assertEqual(first, [200, 200, 429])
        self.assertEqual(second, 200)
