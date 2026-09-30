import json
import logging

from django.core.checks.registry import registry
from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from apps.common.checks import check_production_configuration
from apps.common.log import JsonFormatter, RequestIdFilter, request_id_var


class RequestIdTests(SimpleTestCase):
    def test_every_response_carries_a_request_id(self):
        response = self.client.get(reverse("routing:health-live"))
        self.assertRegex(response.headers["X-Request-ID"], r"^[0-9a-f]{32}$")

    def test_incoming_request_id_is_reused(self):
        response = self.client.get(reverse("routing:health-live"), headers={"x-request-id": "trace-123"})
        self.assertEqual(response.headers["X-Request-ID"], "trace-123")

    def test_unsafe_incoming_request_id_is_replaced(self):
        response = self.client.get(reverse("routing:health-live"), headers={"x-request-id": "bad value\u2028"})
        self.assertRegex(response.headers["X-Request-ID"], r"^[0-9a-f]{32}$")

    def test_access_log_line_has_request_details(self):
        with self.assertLogs("access", level="INFO") as captured:
            self.client.get(reverse("routing:health-live"))
        record = captured.records[0]
        self.assertEqual((record.method, record.view, record.status), ("GET", "routing:health-live", 200))


class JsonFormatterTests(SimpleTestCase):
    def test_formats_one_json_object_with_request_id_and_extras(self):
        record = logging.LogRecord("app", logging.INFO, __file__, 1, "hello %s", ("world",), None)
        record.duration_ms = 12.5
        token = request_id_var.set("abc")
        try:
            RequestIdFilter().filter(record)
        finally:
            request_id_var.reset(token)

        payload = json.loads(JsonFormatter().format(record))

        self.assertEqual(payload["message"], "hello world")
        self.assertEqual(payload["request_id"], "abc")
        self.assertEqual(payload["level"], "INFO")
        self.assertEqual(payload["duration_ms"], 12.5)


class MetricsEndpointTests(SimpleTestCase):
    def test_disabled_without_a_token(self):
        self.assertEqual(self.client.get(reverse("metrics")).status_code, 404)

    @override_settings(METRICS_TOKEN="scrape-secret")
    def test_requires_the_token(self):
        self.assertEqual(self.client.get(reverse("metrics")).status_code, 403)
        wrong = self.client.get(reverse("metrics"), headers={"authorization": "Bearer nope"})
        self.assertEqual(wrong.status_code, 403)

    @override_settings(METRICS_TOKEN="scrape-secret")
    def test_exposes_request_metrics(self):
        self.client.get(reverse("routing:health-live"))
        response = self.client.get(reverse("metrics"), headers={"authorization": "Bearer scrape-secret"})
        self.assertEqual(response.status_code, 200)
        self.assertIn('http_requests_total{method="GET",status="200",view="routing:health-live"}', response.text)


class SecurityHeaderTests(SimpleTestCase):
    def test_pages_get_a_strict_content_security_policy(self):
        policy = self.client.get(reverse("routing:trip-map")).headers["Content-Security-Policy"]
        self.assertIn("script-src 'self'", policy)
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertNotIn("unsafe-inline", policy)
        self.assertIn("https://tile.openstreetmap.org", policy)

    def test_only_docs_pages_allow_the_docs_cdn(self):
        policy = self.client.get(reverse("swagger-ui")).headers["Content-Security-Policy"]
        self.assertIn("https://cdn.jsdelivr.net", policy)

    @override_settings(CORS_ALLOWED_ORIGINS=["https://app.example"])
    def test_cors_allows_only_listed_origins(self):
        allowed = self.client.get(reverse("routing:health-live"), headers={"origin": "https://app.example"})
        other = self.client.get(reverse("routing:health-live"), headers={"origin": "https://evil.example"})
        self.assertEqual(allowed.headers["Access-Control-Allow-Origin"], "https://app.example")
        self.assertNotIn("Access-Control-Allow-Origin", other.headers)


class DeployCheckTests(SimpleTestCase):
    def ids(self):
        return {warning.id for warning in check_production_configuration(None)}

    def test_flags_development_defaults(self):
        with self.settings(REDIS_URL="", API_KEYS=[]):
            self.assertLessEqual({"fuelroute.W003", "fuelroute.W004"}, self.ids())

    def test_flags_the_public_osrm_server(self):
        fuel_route = {"OSRM_BASE_URL": "https://router.project-osrm.org"}
        with self.settings(FUEL_ROUTE=fuel_route):
            self.assertIn("fuelroute.W001", self.ids())

    def test_passes_for_a_production_configuration(self):
        with self.settings(
            FUEL_ROUTE={"OSRM_BASE_URL": "http://osrm:5000"},
            REST_FRAMEWORK={"NUM_PROXIES": 1},
            REDIS_URL="redis://redis:6379/0",
            API_KEYS=["key"],
        ):
            self.assertEqual(self.ids(), set())

    def test_checks_only_run_for_deploy(self):
        self.assertNotIn(check_production_configuration, registry.get_checks(include_deployment_checks=False))
        self.assertIn(check_production_configuration, registry.get_checks(include_deployment_checks=True))
