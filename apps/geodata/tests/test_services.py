import tempfile
from pathlib import Path

from django.test import SimpleTestCase, TestCase

from apps.common.geo import Coordinates
from apps.geodata.models import Place
from apps.geodata.services import (
    PlaceCandidate,
    build_places,
    county_subdivision_candidates,
    find_place,
    load_places_from_gazetteers,
    place_candidates,
    place_coordinates_lookup,
)

Source = Place.Source

# Header padding mirrors the real Census files, whose last column name has trailing spaces.
PLACES_FILE = (
    "USPS\tGEOID\tANSICODE\tNAME\tLSAD\tFUNCSTAT\tALAND\tAWATER\tALAND_SQMI\tAWATER_SQMI\tINTPTLAT\tINTPTLONG   \n"
    "TX\t4805000\t0\tAustin city\t25\tA\t0\t0\t319.9\t6.9\t30.3005\t-97.7522\n"
    "NV\t3209700\t0\tCarson City\t00\tA\t0\t0\t144.7\t12.5\t39.1511\t-119.7476\n"
    "PR\t7200100\t0\tAdjuntas zona urbana\t62\tS\t0\t0\t0.9\t0\t18.1627\t-66.7222\n"
)
SUBDIVISIONS_FILE = (
    "USPS\tGEOID\tANSICODE\tNAME\tFUNCSTAT\tALAND\tAWATER\tALAND_SQMI\tAWATER_SQMI\tINTPTLAT\tINTPTLONG\n"
    "MI\t2600100\t0\tBridgeport charter township\tA\t0\t0\t34.6\t0.3\t43.3706\t-83.8677\n"
    "TX\t4890000\t0\tAustin CCD\tS\t0\t0\t900.0\t0\t30.2\t-97.9\n"
)


class GazetteerParsingTests(SimpleTestCase):
    def test_places_drop_the_census_descriptor_and_non_state_rows(self):
        candidates = [c for c in place_candidates(PLACES_FILE.splitlines()) if c.source == Source.PLACE]
        self.assertEqual([(c.state, c.name) for c in candidates], [("TX", "Austin"), ("NV", "Carson City")])
        self.assertEqual((candidates[0].latitude, candidates[0].longitude), (30.3005, -97.7522))

    def test_only_municipal_county_subdivisions_are_kept(self):
        candidates = list(county_subdivision_candidates(SUBDIVISIONS_FILE.splitlines()))
        self.assertEqual(
            [(c.state, c.name, c.source) for c in candidates], [("MI", "Bridgeport", "county_subdivision")]
        )

    def test_census_place_beats_a_township_of_the_same_name(self):
        places = build_places([_candidate(Source.COUNTY_SUBDIVISION, 900.0, 1.0), _candidate(Source.PLACE, 5.0, 2.0)])
        self.assertEqual(len(places), 1)
        self.assertEqual((places[0].source, places[0].latitude), (Source.PLACE, 2.0))

    def test_larger_place_wins_within_one_source(self):
        places = build_places([_candidate(Source.PLACE, 5.0, 1.0), _candidate(Source.PLACE, 50.0, 2.0)])
        self.assertEqual(places[0].latitude, 2.0)


def _candidate(source, area, latitude):
    return PlaceCandidate(
        state="OH", name="Springfield", latitude=latitude, longitude=-83.8, land_area=area, source=source
    )


class LoadPlacesTests(TestCase):
    def load(self, with_subdivisions=True):
        with tempfile.TemporaryDirectory() as directory:
            places = Path(directory) / "places.txt"
            places.write_text(PLACES_FILE, encoding="utf-8")
            subdivisions = None
            if with_subdivisions:
                subdivisions = Path(directory) / "cousubs.txt"
                subdivisions.write_text(SUBDIVISIONS_FILE, encoding="utf-8")
            return load_places_from_gazetteers(places, subdivisions)

    def test_loads_places_and_townships(self):
        self.load()
        self.assertEqual(find_place("Austin", "TX").latitude, 30.3005)
        self.assertEqual(find_place("bridgeport", "MI").source, Source.COUNTY_SUBDIVISION)
        self.assertIsNone(find_place("Austin", "MI"))

    def test_reloading_replaces_existing_rows(self):
        Place.objects.create(state="TX", name="Old", key="old", latitude=1, longitude=1)
        self.load(with_subdivisions=False)
        self.assertIsNone(find_place("Old", "TX"))
        self.assertIsNone(find_place("Bridgeport", "MI"))

    def test_lookup_table_is_keyed_by_state_and_normalized_name(self):
        count = self.load()
        lookup = place_coordinates_lookup()
        self.assertEqual(len(lookup), count)
        self.assertEqual(lookup[("NV", "carsoncity")], Coordinates(39.1511, -119.7476))
