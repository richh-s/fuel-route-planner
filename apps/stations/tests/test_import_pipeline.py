import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from apps.common.geo import Coordinates
from apps.geodata.models import Place
from apps.stations import index as station_index
from apps.stations.importer import ImportRejected, build_stations, import_fuel_prices
from apps.stations.models import FuelStation

HEADER = "OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID,Retail Price"


def csv_file(directory, rows, header=HEADER):
    path = Path(directory) / "prices.csv"
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return path


def price_rows(count, price="3.10"):
    return [f'{n},STOP {n},"I-40, EXIT {n}",Amarillo,TX,1,{price}' for n in range(1, count + 1)]


class ExactCoordinatesTests(SimpleTestCase):
    def row(self, **extra):
        base = {
            "OPIS Truckstop ID": "7",
            "Truckstop Name": "STOP",
            "Address": "I-44, EXIT 283",
            "City": "Big Cabin",
            "State": "OK",
            "Rack ID": "307",
            "Retail Price": "3.0",
        }
        return {**base, **extra}

    def test_coordinates_in_the_feed_are_used_as_the_exact_position(self):
        places = {("OK", "bigcabin"): Coordinates(36.54, -95.22)}
        stations, report = build_stations([self.row(Latitude="36.5380", Longitude="-95.2214")], places)

        self.assertEqual((stations[0].latitude, stations[0].longitude), (36.538, -95.2214))
        self.assertEqual(stations[0].location_precision, "exact")
        self.assertEqual((report.geocoded, report.exact_locations), (1, 1))

    def test_missing_or_implausible_coordinates_fall_back_to_the_city(self):
        places = {("OK", "bigcabin"): Coordinates(36.54, -95.22)}
        for extra in (
            {},
            {"Latitude": "", "Longitude": ""},
            {"Latitude": "0", "Longitude": "0"},
            {"Latitude": "999", "Longitude": "1"},
        ):
            with self.subTest(extra=extra):
                stations, report = build_stations([self.row(**extra)], places)
                self.assertEqual((stations[0].latitude, stations[0].longitude), (36.54, -95.22))
                self.assertEqual(stations[0].location_precision, "city_centroid")
                self.assertEqual(report.exact_locations, 0)


class ImportSafetyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Place.objects.create(state="TX", name="Amarillo", key="amarillo", latitude=35.2, longitude=-101.83)

    def test_truncated_feed_is_rejected_and_data_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            import_fuel_prices(csv_file(directory, price_rows(10)))
            with self.assertRaises(ImportRejected):
                import_fuel_prices(csv_file(directory, price_rows(2)))
        self.assertEqual(FuelStation.objects.count(), 10)

    def test_force_overrides_the_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            import_fuel_prices(csv_file(directory, price_rows(10)))
            import_fuel_prices(csv_file(directory, price_rows(2)), force=True)
        self.assertEqual(FuelStation.objects.count(), 2)

    def test_command_reports_a_rejected_import(self):
        with tempfile.TemporaryDirectory() as directory:
            call_command("import_fuel_stations", path=csv_file(directory, price_rows(10)), stdout=StringIO())
            with self.assertRaises(CommandError):
                call_command("import_fuel_stations", path=csv_file(directory, price_rows(1)), stdout=StringIO())

    def test_command_can_download_the_feed(self):
        body = ("\n".join([HEADER, *price_rows(3)]) + "\n").encode()
        response = mock.MagicMock()
        response.__enter__.return_value.iter_content.return_value = [body]
        out = StringIO()
        with mock.patch("requests.get", return_value=response) as get:
            call_command("import_fuel_stations", url="https://feed.example/prices.csv", stdout=out)

        self.assertEqual(get.call_args.args[0], "https://feed.example/prices.csv")
        self.assertEqual(FuelStation.objects.count(), 3)
        self.assertIn("Unique US stations:     3", out.getvalue())

    def test_command_rejects_non_http_urls(self):
        with self.assertRaises(CommandError):
            call_command("import_fuel_stations", url="file:///etc/passwd", stdout=StringIO())


class StationIndexRefreshTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Place.objects.create(state="TX", name="Amarillo", key="amarillo", latitude=35.2, longitude=-101.83)

    def setUp(self):
        station_index.reset_station_index()
        self.addCleanup(station_index.reset_station_index)

    def import_prices(self, count, price):
        with tempfile.TemporaryDirectory() as directory:
            import_fuel_prices(csv_file(directory, price_rows(count, price)), force=True)

    def settings_with_refresh(self, seconds):
        return override_settings(FUEL_ROUTE={**settings.FUEL_ROUTE, "STATION_INDEX_REFRESH_SECONDS": seconds})

    def test_new_prices_are_picked_up_without_a_restart(self):
        with self.settings_with_refresh(0):
            self.import_prices(3, "3.10")
            before = station_index.get_station_index()
            self.import_prices(4, "2.50")
            after = station_index.get_station_index()

        self.assertEqual((len(before), before.price.max()), (3, 3.10))
        self.assertEqual((len(after), after.price.max()), (4, 2.50))
        self.assertIsNotNone(after.prices_updated_at)

    def test_unchanged_data_is_not_rebuilt(self):
        with self.settings_with_refresh(0):
            self.import_prices(3, "3.10")
            self.assertIs(station_index.get_station_index(), station_index.get_station_index())

    def test_database_is_not_queried_between_refresh_checks(self):
        with self.settings_with_refresh(3600):
            self.import_prices(3, "3.10")
            station_index.get_station_index()
            with self.assertNumQueries(0):
                station_index.get_station_index()

    def test_keeps_serving_the_old_index_if_a_refresh_fails(self):
        with self.settings_with_refresh(0):
            self.import_prices(3, "3.10")
            before = station_index.get_station_index()
            with (
                mock.patch.object(station_index, "_fingerprint", side_effect=RuntimeError("database is locked")),
                self.assertLogs("apps.stations.index", level="ERROR"),
            ):
                self.assertIs(station_index.get_station_index(), before)
