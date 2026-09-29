from decimal import Decimal

from django.test import SimpleTestCase

from apps.common.geo import Coordinates
from apps.stations.importer import build_stations

PLACES = {("OK", "bigcabin"): Coordinates(36.54, -95.22), ("MI", "bridgeport"): Coordinates(43.36, -83.88)}


def row(opis_id, city, state, price, name="STOP"):
    return {
        "OPIS Truckstop ID": str(opis_id),
        "Truckstop Name": name,
        "Address": "I-44, EXIT 283",
        "City": city,
        "State": state,
        "Rack ID": "307",
        "Retail Price": str(price),
    }


class BuildStationsTests(SimpleTestCase):
    def test_keeps_lowest_price_per_station(self):
        rows = [row(105, "Bridgeport", "MI", "3.339"), row(105, "Bridgeport", "MI", "3.269")]
        stations, report = build_stations(rows, PLACES)

        self.assertEqual(len(stations), 1)
        self.assertEqual(stations[0].price, Decimal("3.2690"))
        self.assertEqual(report.rows_read, 2)

    def test_skips_canadian_rows_and_invalid_prices(self):
        rows = [row(629, "Edmonton", "AB", "4.39"), row(7, "Big Cabin", "OK", "not-a-number")]
        stations, report = build_stations(rows, PLACES)

        self.assertEqual(stations, [])
        self.assertEqual(report.skipped_non_us, 1)
        self.assertEqual(report.skipped_invalid, 1)

    def test_geocodes_padded_city_names_and_reports_misses(self):
        rows = [row(7, "Big Cabin                    ", "OK", "3.007"), row(8, "Nowhere", "OK", "3.1")]
        stations, report = build_stations(rows, PLACES)

        by_id = {s.opis_id: s for s in stations}
        self.assertEqual((by_id[7].latitude, by_id[7].longitude), (36.54, -95.22))
        self.assertEqual(by_id[7].city, "Big Cabin")
        self.assertIsNone(by_id[8].latitude)
        self.assertEqual(report.ungeocoded, ["Nowhere, OK"])
