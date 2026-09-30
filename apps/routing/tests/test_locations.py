from django.test import SimpleTestCase, TestCase

from apps.common.exceptions import InvalidLocationError
from apps.common.geo import Coordinates
from apps.geodata.models import Place
from apps.routing.services.locations import is_in_usa, resolve_location


class CoordinateInputTests(SimpleTestCase):
    def test_parses_coordinates_with_spaces(self):
        location = resolve_location("  40.7128 , -74.0060 ")
        self.assertEqual(location.coordinates, Coordinates(40.7128, -74.006))
        self.assertEqual(location.label, "40.71280, -74.00600")

    def test_rejects_coordinates_outside_the_usa(self):
        with self.assertRaises(InvalidLocationError) as raised:
            resolve_location("48.8566,2.3522")
        self.assertIn("outside the USA", raised.exception.message)

    def test_usa_bounds_cover_alaska_and_hawaii_but_not_neighbours(self):
        self.assertTrue(is_in_usa(Coordinates(61.2181, -149.9003)))  # Anchorage
        self.assertTrue(is_in_usa(Coordinates(21.3069, -157.8583)))  # Honolulu
        self.assertFalse(is_in_usa(Coordinates(19.4326, -99.1332)))  # Mexico City
        self.assertFalse(is_in_usa(Coordinates(53.5461, -113.4938)))  # Edmonton


class CityInputTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Place.objects.create(state="MO", name="St. Louis", key="saintlouis", latitude=38.63, longitude=-90.2)
        Place.objects.create(state="TX", name="Austin", key="austin", latitude=30.27, longitude=-97.74)

    def test_city_and_state_abbreviation(self):
        location = resolve_location("Austin, TX")
        self.assertEqual(location.label, "Austin, TX")
        self.assertEqual(location.coordinates, Coordinates(30.27, -97.74))

    def test_full_state_name_and_saint_spelling(self):
        self.assertEqual(resolve_location("Saint Louis, Missouri").label, "St. Louis, MO")

    def test_country_suffix_and_case_are_ignored(self):
        self.assertEqual(resolve_location("austin, tx, USA").label, "Austin, TX")

    def test_state_without_a_comma(self):
        self.assertEqual(resolve_location("Austin TX").label, "Austin, TX")

    def test_unknown_city(self):
        with self.assertRaises(InvalidLocationError) as raised:
            resolve_location("Atlantis, TX")
        self.assertIn("Unknown US city", raised.exception.message)

    def test_unparseable_input(self):
        for text in ("Austin", ", TX", "Austin, Narnia", ""):
            with self.subTest(text=text), self.assertRaises(InvalidLocationError):
                resolve_location(text)
