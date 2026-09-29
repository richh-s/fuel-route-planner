from unittest import mock

import numpy as np
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.common.exceptions import RouteNotFoundError
from apps.geodata.models import Place
from apps.routing.clients.osrm import CachedRoutingClient, Route
from apps.routing.services.trip_planner import TripPlanner, VehicleProfile
from apps.stations.index import StationIndex, StationRecord

# Straight line Amarillo, TX -> roughly 1,000 miles east, 1 point per ~0.5 mile.
ROUTE_LON = np.linspace(-101.83, -84.0, 2000)
ROUTE_LAT = np.full_like(ROUTE_LON, 35.2)


class FakeOSRM:
    def __init__(self, error=None):
        self.calls = 0
        self.error = error

    def route(self, start, finish):
        self.calls += 1
        if self.error:
            raise self.error
        return Route(1000.0, 15 * 3600, np.column_stack((ROUTE_LAT, ROUTE_LON)))


def station(opis_id, lon, price):
    return StationRecord(opis_id, f"Stop {opis_id}", "I-40", "Town", "TX", price, 35.2, lon)


STATIONS = StationIndex.from_records(
    [
        station(1, -99.0, 3.50),
        station(2, -95.0, 2.90),
        station(3, -90.0, 3.80),
        station(4, -88.0, 3.10),
    ]
)


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class TripPlanApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Place.objects.create(state="TX", name="Amarillo", key="amarillo", latitude=35.2, longitude=-101.83)
        Place.objects.create(state="TN", name="Nashville", key="nashville", latitude=36.16, longitude=-86.78)

    def setUp(self):
        cache.clear()
        self.osrm = FakeOSRM()
        planner = TripPlanner(
            routing_client=CachedRoutingClient(self.osrm, ttl_seconds=60),
            station_index=lambda: STATIONS,
            vehicle=VehicleProfile(range_miles=500, miles_per_gallon=10),
            sample_step_miles=1,
        )
        patcher = mock.patch("apps.routing.api.views.get_trip_planner", return_value=planner)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_get_returns_plan_with_stops_cost_and_map(self):
        response = self.client.get(reverse("routing:trip-plan"), {"start": "Amarillo, TX", "finish": "Nashville, TN"})

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["route"]["distance_miles"], 1000.0)
        self.assertEqual(body["route"]["geometry"]["type"], "LineString")
        self.assertGreater(body["fuel"]["number_of_stops"], 0)
        self.assertAlmostEqual(
            body["fuel"]["total_cost_usd"], sum(stop["cost_usd"] for stop in body["fuel"]["stops"]), places=1
        )
        self.assertIn("/api/v1/route/map/?", body["map_url"])
        self.assertEqual(body["meta"]["routing_api_calls"], 1)

    def test_post_is_supported_and_second_call_is_served_from_cache(self):
        payload = {"start": "Amarillo, TX", "finish": "Nashville, TN"}
        self.client.post(reverse("routing:trip-plan"), payload, content_type="application/json")
        response = self.client.post(reverse("routing:trip-plan"), payload, content_type="application/json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["meta"]["routing_api_calls"], 0)
        self.assertEqual(self.osrm.calls, 1)

    def test_accepts_coordinates(self):
        response = self.client.get(reverse("routing:trip-plan"), {"start": "35.2,-101.83", "finish": "36.16,-86.78"})
        self.assertEqual(response.status_code, 200)

    def test_missing_parameters_return_validation_error(self):
        response = self.client.get(reverse("routing:trip-plan"), {"start": "Amarillo, TX"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "validation_error")

    def test_unknown_city_returns_422(self):
        response = self.client.get(reverse("routing:trip-plan"), {"start": "Atlantis, TX", "finish": "Nashville, TN"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "invalid_location")

    def test_location_outside_usa_is_rejected(self):
        response = self.client.get(reverse("routing:trip-plan"), {"start": "51.5,-0.12", "finish": "Nashville, TN"})
        self.assertEqual(response.status_code, 422)

    def test_routing_errors_are_reported(self):
        self.osrm.error = RouteNotFoundError("No drivable route exists between these locations.")
        response = self.client.get(reverse("routing:trip-plan"), {"start": "Amarillo, TX", "finish": "Nashville, TN"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "route_not_found")

    def test_map_page_renders_without_extra_routing_call(self):
        params = {"start": "Amarillo, TX", "finish": "Nashville, TN"}
        self.client.get(reverse("routing:trip-plan"), params)
        response = self.client.get(reverse("routing:trip-map"), params)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "leaflet")
        self.assertEqual(self.osrm.calls, 1)
