from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.geodata.services import load_places_from_gazetteers

GAZETTEER_BASE_URL = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/"


class Command(BaseCommand):
    help = "Load US city/town coordinates from the Census Gazetteer files (used for offline geocoding)."

    def add_arguments(self, parser):
        config = settings.FUEL_ROUTE
        parser.add_argument("--places", type=Path, default=config["PLACES_GAZETTEER"])
        parser.add_argument("--county-subdivisions", type=Path, default=config["COUNTY_SUBDIVISIONS_GAZETTEER"])

    def handle(self, *args, places: Path, county_subdivisions: Path, **options):
        if not places.exists():
            raise CommandError(
                f"Places file not found at {places}.\n"
                f"Download {GAZETTEER_BASE_URL}{places.stem}.zip and unzip it into {places.parent}/"
            )
        subdivisions: Path | None = county_subdivisions
        if not county_subdivisions.exists():
            self.stdout.write(
                self.style.WARNING(
                    f"County subdivisions file not found at {county_subdivisions}; townships will not be matched."
                )
            )
            subdivisions = None

        count = load_places_from_gazetteers(places, subdivisions)
        self.stdout.write(self.style.SUCCESS(f"Loaded {count} places."))
