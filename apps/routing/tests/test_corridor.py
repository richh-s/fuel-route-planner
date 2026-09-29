import numpy as np
from django.test import SimpleTestCase

from apps.common.geo import cumulative_miles
from apps.routing.services.corridor import StationOnRoute, cheapest_per_stretch, find_stations_along_route
from apps.stations.index import StationIndex, StationRecord


def record(opis_id, lat, lon, price=3.0):
    return StationRecord(opis_id, f"Station {opis_id}", "I-40", "Town", "TX", price, lat, lon)


class CorridorTests(SimpleTestCase):
    def setUp(self):
        # A straight east-west route along latitude 35 from lon -100 to -98 (~113 miles).
        self.lon = np.linspace(-100, -98, 200)
        self.lat = np.full_like(self.lon, 35.0)
        self.miles = cumulative_miles(self.lat, self.lon)

    def test_keeps_only_stations_within_corridor_ordered_by_mile(self):
        index = StationIndex.from_records(
            [
                record(1, 35.05, -98.5),  # ~3.5 miles north, near the end
                record(2, 35.00, -99.5),  # on the route, near the start
                record(3, 36.00, -99.0),  # ~69 miles away
            ]
        )
        stations = find_stations_along_route(index, self.lat, self.lon, self.miles, corridor_miles=10)

        self.assertEqual([s.record.opis_id for s in stations], [2, 1])
        self.assertLess(stations[0].mile_marker, stations[1].mile_marker)
        self.assertAlmostEqual(stations[1].distance_from_route_miles, 3.45, delta=0.2)

    def test_empty_index(self):
        index = StationIndex.from_records([])
        self.assertEqual(find_stations_along_route(index, self.lat, self.lon, self.miles, 10), [])


class CheapestPerStretchTests(SimpleTestCase):
    def test_keeps_cheapest_station_per_stretch(self):
        def on_route(opis_id, mile, price):
            return StationOnRoute(record(opis_id, 35, -99, price), mile, 1.0)

        stations = [on_route(1, 0, 3.2), on_route(2, 10, 3.0), on_route(3, 20, 3.1), on_route(4, 40, 3.5)]
        kept = cheapest_per_stretch(stations, stretch_miles=25)
        self.assertEqual([s.record.opis_id for s in kept], [2, 4])
