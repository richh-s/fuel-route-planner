# Deployment guide

The image built by the `Dockerfile` is self-contained: the place and station tables are built into it, so a container is ready to serve as soon as it starts. It needs two things next to it: **Redis** and a **routing engine (OSRM)**.

```
client ──► load balancer (TLS) ──► app containers (gunicorn, 3 workers × 8 threads) ──► OSRM
                                        │
                                        └──► Redis (route cache, rate-limit counters)
```

## Production checklist

`python manage.py check --deploy` warns about each of these until it is set.

| Variable | Why |
|---|---|
| `DJANGO_SECRET_KEY` | Required. Also signs map links. |
| `DJANGO_ALLOWED_HOSTS` | Your domain(s). |
| `OSRM_BASE_URL` | Your own OSRM. The public demo server has no SLA and forbids heavy use (`fuelroute.W001`). |
| `REDIS_URL` | Shares the route cache and rate limits between workers and containers (`fuelroute.W003`). |
| `NUM_PROXIES` | Number of proxies in front of the app. With `0` behind a load balancer, every client shares one rate limit (`fuelroute.W002`). |
| `API_KEYS` | Comma-separated keys. Without them anyone can use the API (`fuelroute.W004`). If the API is meant to be public, set `DJANGO_SILENCED_SYSTEM_CHECKS=fuelroute.W004`. |
| `SENTRY_DSN` | Error tracking. |
| `METRICS_TOKEN` | Enables `/metrics` for Prometheus. |
| `MAP_TILE_URL` | A tile provider you are allowed to use at your traffic level. The default OpenStreetMap server is for light use only. |
| `CORS_ALLOWED_ORIGINS` | Only if a browser app on another origin calls the API. |

All settings are listed in [`.env.example`](../.env.example).

## Routing engine (OSRM)

Prepare the routing data once (and again whenever you want fresher roads). Try a single state first: the whole US extract is about 11 GB and preprocessing it needs a machine with tens of GB of RAM.

```bash
mkdir -p osrm-data && cd osrm-data
# One state:  https://download.geofabrik.de/north-america/us/texas-latest.osm.pbf
# Whole US:   https://download.geofabrik.de/north-america/us-latest.osm.pbf
curl -L -o region.osm.pbf https://download.geofabrik.de/north-america/us-latest.osm.pbf

OSRM="docker run --rm -t -v $PWD:/data ghcr.io/project-osrm/osrm-backend:v6.0.0"
$OSRM osrm-extract -p /opt/car.lua /data/region.osm.pbf
$OSRM osrm-partition /data/region.osrm
$OSRM osrm-customize /data/region.osrm
cd ..

OSRM_BASE_URL=http://osrm:5000 docker compose --profile osrm up --build
```

A hosted routing API that speaks the OSRM HTTP protocol works too: point `OSRM_BASE_URL` at it.

**Time budget.** A trip makes at most `OSRM_RETRIES + 1` attempts of `OSRM_CONNECT_TIMEOUT_SECONDS + OSRM_TIMEOUT_SECONDS` each (about 15 s with the defaults), then answers `502 routing_service_unavailable`. Keep that below your load balancer's timeout. Identical concurrent requests share one routing call.

## Redis

Used as a cache only. If Redis is unreachable the app keeps serving: each request calls the routing engine directly and rate limiting is suspended until Redis is back. The outage shows up as `cache: unavailable` in the readiness check, as warnings in the log and in the `cache_errors_total` metric. Alert on it; do not rely on noticing.

## Health checks

| Path | Use for | Behaviour |
|---|---|---|
| `/api/v1/health/live/` | Liveness (restart the container) | Always 200 while the process is up. Touches no dependency. |
| `/api/v1/health/ready/` | Readiness (send traffic) | 503 if station data is missing. A Redis outage is reported as `degraded` but stays 200. |
| `/api/v1/health/ready/?deep=true` | Dashboards, uptime monitors | Also makes a small routing request. |

Health paths are exempt from the HTTPS redirect. The probe must still send a `Host` header listed in `DJANGO_ALLOWED_HOSTS`.

## Access control

With `API_KEYS` set, `/api/v1/route/` requires `X-API-Key: <key>` (or `Authorization: Bearer <key>`), and the rate limit (`TRIP_PLAN_RATE_LIMIT`) is counted per key. The `map_url` in a response carries a signature, so it opens in a browser without the key; it expires after `MAP_LINK_MAX_AGE_SECONDS` (7 days). Rotate a key by adding the new one, moving clients over, then removing the old one. Rotating `DJANGO_SECRET_KEY` invalidates outstanding map links.

## Observability

- **Logs:** one JSON object per line on stdout, each with a `request_id`. The same ID is returned in the `X-Request-ID` response header, and an incoming `X-Request-ID` is reused.
- **Errors:** set `SENTRY_DSN`.
- **Metrics:** set `METRICS_TOKEN` and scrape `/metrics` with it as a bearer token. Worth alerting on:

| Metric | Watch for |
|---|---|
| `http_requests_total{status=~"5.."}` | Error rate |
| `http_request_duration_seconds` | Latency (p95) |
| `routing_api_requests_total{outcome="error"}` | Routing engine failures |
| `routing_api_request_duration_seconds` | Routing engine latency |
| `route_cache_lookups_total{result}` | Cache hit rate (`hit`, `miss`, `coalesced`) |
| `cache_errors_total` | Redis outages |

## Updating fuel prices

Prices are loaded from a CSV (`OPIS Truckstop ID, Truckstop Name, Address, City, State, Rack ID, Retail Price`, plus optional `Latitude` and `Longitude`).

- **Rebuild the image** with a new `data/fuel-prices-for-be-assessment.csv`. Simplest; prices are as fresh as your last deploy.
- **Or import on a schedule.** Mount a persistent volume at `/app/var` and run on a timer. (A Docker named volume is seeded with the image's database on first use; on other platforms run `migrate`, `load_places` and the import once to create it.)

  ```bash
  docker compose exec web python manage.py import_fuel_stations --url https://your-feed.example/prices.csv
  ```

  Running workers notice the new data within `STATION_INDEX_REFRESH_SECONDS` (60 s); no restart is needed. An import that would drop more than half of the stations is refused (pass `--force` if that is expected). With several containers, each needs the import, or they must share the volume.

`prices_updated_at` in the readiness check and in every trip response (`meta`) shows how old the prices are.

## Admin site

Off by default. The API is stateless, and the database inside the image is replaced by every deploy, so admin accounts would not survive. To use it, mount a persistent volume at `/app/var`, set `DJANGO_ADMIN_ENABLED=true`, and create a user with `python manage.py createsuperuser`.

## Releasing

CI (GitHub Actions) runs lint, type checks, deploy checks, tests with coverage and a dependency vulnerability scan, then builds the image and smoke-tests the running container. Pushing a tag `vX.Y.Z` also publishes the image to `ghcr.io/<owner>/<repo>:vX.Y.Z`. Deploying that tag to your platform is not automated here.

Before a release that changes capacity-relevant code, run the load test against a staging deployment:

```bash
python scripts/load_test.py --url https://staging.example --requests 1000 --concurrency 20 --api-key "$KEY"
```

## Known limitations

- **Station positions are approximate.** The OPIS file describes locations as highway exits (`I-44, EXIT 283 & US-69`), which address geocoders cannot resolve, so stations are placed at the centre of their city and marked `location_precision: city_centroid`. Distances from the route and mile markers can be off by a few miles. To fix it, supply `Latitude` and `Longitude` columns in the price feed; those stations are then marked `exact`.
- **One database file per container.** Fine for read-mostly reference data; if you need the admin or shared writes across containers, move to a server database.
- **API docs pages** (`/api/docs/`, `/api/redoc/`) load their scripts from a CDN and have a looser Content Security Policy than the rest of the site.
