from datetime import UTC, datetime
from unittest import mock

from django.test import SimpleTestCase
from django.urls import reverse

from apps.common.exceptions import RoutingServiceUnavailable
from apps.stations.index import StationIndex, StationRecord

UPDATED = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
LOADED = StationIndex.from_records([StationRecord(1, "Stop", "I-40", "Town", "TX", 3.0, 35.2, -100.0)], UPDATED)
EMPTY = StationIndex.from_records([])


def stations(index):
    return mock.patch("apps.routing.api.views.get_station_index", return_value=index)


class HealthTests(SimpleTestCase):
    def test_liveness_needs_no_dependencies(self):
        with mock.patch("apps.routing.api.views.get_station_index", side_effect=RuntimeError("db down")):
            response = self.client.get(reverse("routing:health-live"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_ready_when_stations_are_loaded(self):
        with stations(LOADED):
            response = self.client.get(reverse("routing:health-ready"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "ok",
                "stations_loaded": 1,
                "prices_updated_at": "2026-09-01T12:00:00+00:00",
                "checks": {"stations": "ok", "cache": "ok"},
            },
        )

    def test_legacy_health_url_still_works(self):
        with stations(LOADED):
            self.assertEqual(self.client.get(reverse("routing:health")).json()["stations_loaded"], 1)

    def test_not_ready_without_station_data(self):
        with stations(EMPTY):
            response = self.client.get(reverse("routing:health-ready"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "unavailable")

    def test_not_ready_when_the_database_fails(self):
        with (
            mock.patch("apps.routing.api.views.get_station_index", side_effect=RuntimeError("db down")),
            self.assertLogs("apps.routing.api.views", level="ERROR"),
        ):
            response = self.client.get(reverse("routing:health-ready"))
        self.assertEqual(response.status_code, 503)

    def test_cache_outage_is_degraded_but_still_ready(self):
        with stations(LOADED), mock.patch("apps.routing.api.views.cache") as cache:
            cache.ping.return_value = False
            response = self.client.get(reverse("routing:health-ready"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "degraded")
        self.assertEqual(response.json()["checks"]["cache"], "unavailable")

    def test_deep_check_probes_the_routing_api(self):
        with stations(LOADED), mock.patch("apps.routing.api.views.OSRMClient") as client:
            client.return_value.route.side_effect = RoutingServiceUnavailable("down")
            response = self.client.get(reverse("routing:health-ready"), {"deep": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "degraded")
        self.assertEqual(response.json()["checks"]["routing"], "unavailable")
