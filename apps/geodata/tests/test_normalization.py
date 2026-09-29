from django.test import SimpleTestCase

from apps.geodata.models import Place
from apps.geodata.normalization import census_name_aliases, clean_census_name, place_key
from apps.geodata.services import build_places, county_subdivision_candidates, place_candidates

HEADER = "USPS\tGEOID\tANSICODE\tNAME\tLSAD\tFUNCSTAT\tALAND\tAWATER\tALAND_SQMI\tAWATER_SQMI\tINTPTLAT\tINTPTLONG   "
SUBDIVISION_HEADER = "USPS\tGEOID\tANSICODE\tNAME\tFUNCSTAT\tALAND\tAWATER\tALAND_SQMI\tAWATER_SQMI\tINTPTLAT\tINTPTLONG"


def place_row(state, name, lsad="25", area=10.0, lat=30.0, lon=-90.0):
    return f"{state}\t1\t1\t{name}\t{lsad}\tA\t0\t0\t{area}\t0\t{lat}\t{lon}"


def subdivision_row(state, name, area=10.0, lat=40.0, lon=-74.0):
    return f"{state}\t1\t1\t{name}\tA\t0\t0\t{area}\t0\t{lat}\t{lon}"


class PlaceKeyTests(SimpleTestCase):
    def test_equivalent_spellings_share_a_key(self):
        groups = [
            ("Saint Johns", "St. Johns", "St Johns"),
            ("Mc Calla", "McCalla"),
            ("Winston Salem", "Winston-Salem"),
            ("Mount Vernon", "Mt. Vernon"),
            ("Fort Wayne", "Ft Wayne"),
            ("New Castle                              ", "New Castle"),
            ("Bois D Arc", "Bois D'Arc"),
        ]
        for names in groups:
            keys = {place_key(name) for name in names}
            self.assertEqual(len(keys), 1, names)


class CensusNameTests(SimpleTestCase):
    def test_strips_census_descriptors(self):
        self.assertEqual(clean_census_name("Abilene city"), "Abilene")
        self.assertEqual(clean_census_name("Big Cabin town"), "Big Cabin")
        self.assertEqual(clean_census_name("Town of Pecos city"), "Pecos")
        self.assertEqual(clean_census_name("Redford charter township"), "Redford")
        self.assertEqual(clean_census_name("Nashville-Davidson metropolitan government (balance)"), "Nashville-Davidson")

    def test_names_without_descriptor_are_kept_whole(self):
        self.assertEqual(clean_census_name("Carson City", has_descriptor=False), "Carson City")

    def test_aliases(self):
        self.assertEqual(census_name_aliases("Louisville/Jefferson County"), ["Louisville"])
        self.assertEqual(census_name_aliases("Nashville-Davidson"), ["Nashville"])
        self.assertEqual(census_name_aliases("Boise City"), ["Boise"])


class BuildPlacesTests(SimpleTestCase):
    def build(self, place_rows=(), subdivision_rows=()):
        candidates = [
            *place_candidates([HEADER, *place_rows]),
            *county_subdivision_candidates([SUBDIVISION_HEADER, *subdivision_rows]),
        ]
        return {(p.state, p.key): p for p in build_places(candidates)}

    def test_places_file(self):
        places = self.build(
            [
                place_row("TN", "Nashville-Davidson metropolitan government (balance)"),
                place_row("ID", "Boise City city"),
                place_row("NV", "Carson City", lsad="00"),
                place_row("PR", "San Juan zona urbana"),
            ]
        )
        self.assertEqual(places[("TN", "nashville")].source, Place.Source.PLACE_ALIAS)
        self.assertIn(("ID", "boise"), places)
        self.assertIn(("NV", "carsoncity"), places)
        self.assertFalse(any(state == "PR" for state, _ in places))

    def test_townships_fill_gaps_but_never_override_places(self):
        places = self.build(
            [place_row("NJ", "Newark city", lat=40.72)],
            [
                subdivision_row("NJ", "Edison township"),
                subdivision_row("NJ", "Newark city", lat=0.0),
                subdivision_row("GA", "Autaugaville CCD"),
            ],
        )
        self.assertEqual(places[("NJ", "edison")].source, Place.Source.COUNTY_SUBDIVISION)
        self.assertEqual(places[("NJ", "newark")].latitude, 40.72)
        self.assertNotIn(("GA", "autaugaville"), places)
