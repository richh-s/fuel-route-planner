from unittest import mock

import requests
from django.test import SimpleTestCase

from apps.common.exceptions import RouteNotFoundError, RoutingServiceUnavailable
from apps.common.geo import Coordinates
from apps.routing.clients.osrm import OSRMClient, build_session

START = Coordinates(35.2, -101.83)
FINISH = Coordinates(36.16, -86.78)


def fake_response(status, payload):
    response = mock.Mock(status_code=status)
    response.json.return_value = payload
    return response


class OSRMClientTests(SimpleTestCase):
    def client_returning(self, response):
        session = mock.Mock()
        session.get.return_value = response
        return OSRMClient("https://osrm.example", timeout=5, session=session), session

    def test_parses_route(self):
        # "_ibE_seK_ibE_ibE" is the polyline6 encoding of (0.1, 0.2), (0.2, 0.3)
        payload = {"code": "Ok", "routes": [{"distance": 1609.344, "duration": 60, "geometry": "_ibE_seK_ibE_ibE"}]}
        client, session = self.client_returning(fake_response(200, payload))

        route = client.route(START, FINISH)

        self.assertAlmostEqual(route.distance_miles, 1.0)
        self.assertEqual(route.coordinates.tolist(), [[0.1, 0.2], [0.2, 0.3]])
        url = session.get.call_args.args[0]
        self.assertIn("-101.83,35.2;-86.78,36.16", url)  # OSRM wants lon,lat

    def test_no_route(self):
        client, _ = self.client_returning(fake_response(400, {"code": "NoRoute"}))
        with self.assertRaises(RouteNotFoundError):
            client.route(START, FINISH)

    def test_server_error(self):
        client, _ = self.client_returning(fake_response(500, {"code": "Error"}))
        with self.assertRaises(RoutingServiceUnavailable):
            client.route(START, FINISH)

    def test_rate_limited_by_osrm(self):
        response = mock.Mock(status_code=429)
        response.json.side_effect = ValueError("not json")
        client, _ = self.client_returning(response)
        with self.assertRaises(RoutingServiceUnavailable):
            client.route(START, FINISH)

    def test_network_error(self):
        session = mock.Mock()
        session.get.side_effect = requests.ConnectionError("down")
        with self.assertRaises(RoutingServiceUnavailable):
            OSRMClient("https://osrm.example", timeout=5, session=session).route(START, FINISH)

    def test_default_session_retries_transient_errors_only(self):
        retry = build_session().get_adapter("https://router.project-osrm.org").max_retries
        self.assertEqual(retry.total, 2)
        self.assertEqual(set(retry.status_forcelist), {502, 503, 504})
