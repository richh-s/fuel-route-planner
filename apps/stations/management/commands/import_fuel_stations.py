from collections import Counter
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.geodata.models import Place
from apps.stations.importer import import_fuel_prices
from apps.stations.index import reset_station_index


class Command(BaseCommand):
    help = "Import fuel stations and prices from the OPIS CSV, geocoding each station to its city."

    def add_arguments(self, parser):
        parser.add_argument(
            "--path",
            type=Path,
            default=settings.FUEL_ROUTE["FUEL_PRICES_CSV"],
            help="Path to the fuel prices CSV.",
        )

    def handle(self, *args, path: Path, **options):
        if not path.exists():
            raise CommandError(f"Fuel price file not found: {path}")
        if not Place.objects.exists():
            raise CommandError("No places loaded. Run `python manage.py load_places` first.")

        report = import_fuel_prices(path)
        reset_station_index()

        self.stdout.write(f"Rows read:              {report.rows_read}")
        self.stdout.write(f"Skipped (non-US):       {report.skipped_non_us}")
        self.stdout.write(f"Skipped (invalid):      {report.skipped_invalid}")
        self.stdout.write(f"Unique US stations:     {report.stations}")
        self.stdout.write(self.style.SUCCESS(f"Geocoded:               {report.geocoded}"))
        if report.ungeocoded:
            self.stdout.write(self.style.WARNING(f"Not geocoded:           {report.ungeocoded_count}"))
            for place, count in Counter(report.ungeocoded).most_common(15):
                self.stdout.write(f"  {place} ({count})")
