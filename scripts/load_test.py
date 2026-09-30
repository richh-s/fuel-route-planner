#!/usr/bin/env python3
"""Small load test for the trip endpoint. Standard library only.

    python scripts/load_test.py --url http://localhost:8000 --requests 500 --concurrency 20

Cycles through a fixed set of trips, so after the first pass every route is
cached and the numbers show the service's own capacity. Use --unique to give
every request a different start point instead, which exercises the routing
API on each one (do not point that at the public OSRM demo server).
Exits non-zero if the error rate or p95 latency exceeds the given limits.
"""

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

TRIPS = [
    ("Dallas, TX", "Chicago, IL"),
    ("Los Angeles, CA", "New York, NY"),
    ("Seattle, WA", "Miami, FL"),
    ("Denver, CO", "Atlanta, GA"),
    ("Houston, TX", "Nashville, TN"),
]


def request_trip(base_url: str, number: int, unique: bool, api_key: str | None) -> tuple[int, float]:
    start, finish = TRIPS[number % len(TRIPS)]
    if unique:
        # Shift the start by ~100 m per request so no two share a cached route.
        start = f"{32.0 + number * 0.001:.4f},-97.0000"
    query = urllib.parse.urlencode({"start": start, "finish": finish})
    request = urllib.request.Request(f"{base_url.rstrip('/')}/api/v1/route/?{query}")
    if api_key:
        request.add_header("X-API-Key", api_key)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            json.load(response)
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    except OSError:
        status = 0
    return status, time.perf_counter() - started


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--unique", action="store_true", help="Every request plans a route that is not cached.")
    parser.add_argument("--api-key")
    parser.add_argument("--max-error-rate", type=float, default=0.01)
    parser.add_argument("--max-p95-ms", type=float, default=2000)
    args = parser.parse_args()

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(pool.map(lambda n: request_trip(args.url, n, args.unique, args.api_key), range(args.requests)))
    elapsed = time.perf_counter() - started

    latencies = sorted(seconds * 1000 for _, seconds in results)
    statuses: dict[int, int] = {}
    for status, _ in results:
        statuses[status] = statuses.get(status, 0) + 1
    # 429 is the rate limiter doing its job, not a failure; it is reported separately.
    errors = sum(count for status, count in statuses.items() if status not in (200, 429))
    error_rate = errors / len(results)
    percentiles = statistics.quantiles(latencies, n=100)
    p50, p95, p99 = percentiles[49], percentiles[94], percentiles[98]

    print(f"requests:    {len(results)} in {elapsed:.1f}s ({len(results) / elapsed:.1f} req/s)")
    print(f"statuses:    {dict(sorted(statuses.items()))}  (0 = connection failed)")
    print(f"latency ms:  p50 {p50:.0f}  p95 {p95:.0f}  p99 {p99:.0f}  max {latencies[-1]:.0f}")
    print(f"error rate:  {error_rate:.2%}")

    if error_rate > args.max_error_rate or p95 > args.max_p95_ms:
        print("FAILED: limits exceeded", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
