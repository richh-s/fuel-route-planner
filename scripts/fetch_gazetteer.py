#!/usr/bin/env python3
"""Download the US Census Gazetteer files used for offline geocoding into data/.

Files that are already present with the expected checksum are left alone, so
the Docker build (and re-runs) need no network once the files are in place.
Standard library only: this runs before the project's dependencies are needed.

    python scripts/fetch_gazetteer.py
"""

import hashlib
import io
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

BASE_URL = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer"
# SHA-256 of each extracted file. A mismatch means Census republished the file: review and update.
FILES = {
    "2024_Gaz_place_national": "ba197a3c0cef828d47981ed7435d820d040d3199bf39a719cd9b11412c59afa6",
    "2024_Gaz_cousubs_national": "d51c213f1ca004202810729192441a9aeb88049d1064813345904fad7f844d30",
}
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ATTEMPTS = 4


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str) -> bytes:
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                return response.read()
        except OSError as exc:
            if attempt == ATTEMPTS:
                raise SystemExit(f"Could not download {url}: {exc}") from exc
            wait = 2**attempt
            print(f"  attempt {attempt} failed ({exc}); retrying in {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise AssertionError("unreachable")


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    for name, expected in FILES.items():
        target = DATA_DIR / f"{name}.txt"
        if target.exists() and sha256(target) == expected:
            print(f"{target.name}: present")
            continue

        print(f"{target.name}: downloading")
        with zipfile.ZipFile(io.BytesIO(download(f"{BASE_URL}/{name}.zip"))) as archive:
            target.write_bytes(archive.read(f"{name}.txt"))
        actual = sha256(target)
        if actual != expected:
            target.unlink()
            raise SystemExit(f"{target.name}: checksum mismatch (expected {expected}, got {actual})")


if __name__ == "__main__":
    main()
