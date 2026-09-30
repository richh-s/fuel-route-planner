import tempfile
from collections import Counter
from pathlib import Path

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.geodata.models import Place
from apps.stations.importer import ImportRejected, import_fuel_prices
from apps.stations.index import reset_station_index

DOWNLOAD_TIMEOUT_SECONDS = (5, 60)


class Command(BaseCommand):
    help = (
        "Import fuel stations and prices from the OPIS CSV, geocoding each station to its city. "
        "Safe to run on a schedule: running app servers pick up the new prices within a minute."
    )

    def add_arguments(self, parser):
        source = parser.add_mutually_exclusive_group()
        source.add_argument(
            "--path",
            type=Path,
            default=settings.FUEL_ROUTE["FUEL_PRICES_CSV"],
            help="Path to the fuel prices CSV.",
        )
        source.add_argument("--url", help="Download the fuel prices CSV from this http(s) URL instead.")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Import even if the file has far fewer stations than are currently loaded.",
        )

    def handle(self, *args, path: Path, url: str | None, force: bool, **options):
        if not Place.objects.exists():
            raise CommandError("No places loaded. Run `python manage.py load_places` first.")

        if url:
            with tempfile.TemporaryDirectory() as directory:
                report = self._import(self._download(url, Path(directory) / "fuel-prices.csv"), force)
        else:
            if not path.exists():
                raise CommandError(f"Fuel price file not found: {path}")
            report = self._import(path, force)
        reset_station_index()

        self.stdout.write(f"Rows read:              {report.rows_read}")
        self.stdout.write(f"Skipped (non-US):       {report.skipped_non_us}")
        self.stdout.write(f"Skipped (invalid):      {report.skipped_invalid}")
        self.stdout.write(f"Unique US stations:     {report.stations}")
        self.stdout.write(self.style.SUCCESS(f"Geocoded:               {report.geocoded}"))
        self.stdout.write(f"  with exact position:  {report.exact_locations}")
        if report.ungeocoded:
            self.stdout.write(self.style.WARNING(f"Not geocoded:           {report.ungeocoded_count}"))
            for place, count in Counter(report.ungeocoded).most_common(15):
                self.stdout.write(f"  {place} ({count})")

    @staticmethod
    def _import(path: Path, force: bool):
        try:
            return import_fuel_prices(path, force=force)
        except ImportRejected as exc:
            raise CommandError(str(exc)) from exc

    @staticmethod
    def _download(url: str, destination: Path) -> Path:
        if not url.startswith(("https://", "http://")):
            raise CommandError("--url must be an http(s) URL.")
        try:
            with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
                response.raise_for_status()
                with destination.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1 << 16):
                        handle.write(chunk)
        except requests.RequestException as exc:
            raise CommandError(f"Could not download {url}: {exc}") from exc
        return destination
