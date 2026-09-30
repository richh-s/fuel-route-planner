# fuel-route-planner

Django REST API that plans a US driving route and picks the cheapest fuel stops for a 500-mile-range vehicle, with a route map and total fuel cost.

```
GET /api/v1/route/?start=Los Angeles, CA&finish=New York, NY
```

returns the route (GeoJSON), where to refuel and how much to buy at each stop, the total fuel cost, and a link to an interactive map. A new route costs **one** call to the routing API; repeated routes cost **zero**.

| Trip | Distance | Fuel cost | Stops | Response |
|---|---|---|---|---|
| Los Angeles → New York | 2,811 mi | $889.14 | 7 | ~0.9 s (≈40 ms cached) |
| Seattle → Miami | 3,303 mi | $1,043.18 | 8 | ~0.9 s |
| Dallas → Chicago | 961 mi | $281.46 | 2 | ~0.6–0.9 s |

The fuel cost covers every gallon the trip burns (distance ÷ 10 mpg): the vehicle starts empty, so the first stop is the fill-up before leaving.

Nearly all of the first-request time is the routing API; planning itself takes ~20 ms.

---

## Quick start

Requires Python 3.12+.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # local settings (DEBUG on)

# Census city coordinates used for offline geocoding (public domain, checksum-verified)
python scripts/fetch_gazetteer.py

python manage.py migrate
python manage.py load_places            # ~44k US cities, towns and townships
python manage.py import_fuel_stations   # data/fuel-prices-for-be-assessment.csv
python manage.py runserver
```

Then open:

- API docs (Swagger UI): http://localhost:8000/api/docs/
- A trip: http://localhost:8000/api/v1/route/?start=Dallas,%20TX&finish=Chicago,%20IL
- Its map: http://localhost:8000/api/v1/route/map/?start=Dallas,%20TX&finish=Chicago,%20IL

Or run the production-like stack (gunicorn + Redis) with Docker: `docker compose up --build`.

A Postman collection is in [`docs/fuel-route-planner.postman_collection.json`](docs/fuel-route-planner.postman_collection.json).

---

## API

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/route/` | Plan a trip from query parameters |
| `POST` | `/api/v1/route/` | Plan a trip from a JSON body |
| `GET` | `/api/v1/route/map/` | Interactive map (HTML) for the same parameters |
| `GET` | `/api/v1/health/live/` | Liveness probe |
| `GET` | `/api/v1/health/ready/` | Readiness probe (also at `/api/v1/health/`) |
| `GET` | `/metrics` | Prometheus metrics (only when `METRICS_TOKEN` is set) |
| `GET` | `/api/docs/`, `/api/redoc/`, `/api/schema/` | OpenAPI docs and schema |

**Parameters**

| Name | Required | Description |
|---|---|---|
| `start`, `finish` | yes | `City, ST` (`Austin, TX`, `Saint Louis, Missouri`) or `lat,lng` (`40.7128,-74.0060`). Must be in the USA. |
| `corridor_miles` | no | How far off the route a station may be (default 10, max 50). |
| `start_fuel_gallons` | no | Fuel already in the tank at departure (default 0, max 50). With the default, the total cost covers all fuel for the trip. |

**Response (abridged)**

```json
{
  "start":  {"query": "Dallas, TX", "label": "Dallas, TX", "latitude": 32.793333, "longitude": -96.766513},
  "finish": {"query": "Chicago, IL", "label": "Chicago, IL", "latitude": 41.837045, "longitude": -87.684939},
  "route":  {"distance_miles": 961.0, "duration_hours": 17.04,
             "geometry": {"type": "LineString", "coordinates": [[-96.76651, 32.79333], "..."]}},
  "fuel": {
    "total_cost_usd": 281.46,
    "total_gallons_purchased": 96.1,
    "total_gallons_used": 96.1,
    "number_of_stops": 2,
    "stops": [{
      "stop_number": 1, "mile_marker": 0.0, "distance_from_route_miles": 0.0,
      "station": {"opis_id": 72773, "name": "RaceTrac #2626", "address": "I-20 Exit 472", "city": "Dallas", "state": "TX",
                  "latitude": 32.793333, "longitude": -96.766513, "location_precision": "city_centroid"},
      "price_per_gallon": 2.864, "gallons": 50.0, "cost_usd": 143.2, "fuel_on_arrival_gallons": 0.0
    }, {
      "stop_number": 2, "mile_marker": 484.0, "distance_from_route_miles": 3.3,
      "station": {"opis_id": 64617, "name": "SHELL", "address": "I-55, EXIT 48", "city": "Osceola", "state": "AR",
                  "latitude": 35.690217, "longitude": -89.988295, "location_precision": "city_centroid"},
      "price_per_gallon": 2.999, "gallons": 46.1, "cost_usd": 138.26, "fuel_on_arrival_gallons": 1.6
    }]
  },
  "vehicle": {"range_miles": 500, "miles_per_gallon": 10, "tank_capacity_gallons": 50, "start_fuel_gallons": 0},
  "assumptions": ["..."],
  "map_url": "http://localhost:8000/api/v1/route/map/?start=Dallas%2C+TX&finish=Chicago%2C+IL",
  "meta": {"routing_api_calls": 1, "stations_along_route": 189, "elapsed_ms": 890.5,
           "prices_updated_at": "2026-09-29T18:20:11+00:00"}
}
```

**Errors** always look like `{"error": {"code": "...", "message": "...", "details": {}}}`:

| Status | Code | When |
|---|---|---|
| 400 | `validation_error` | Missing or invalid parameters |
| 401 | `not_authenticated`, `authentication_failed` | Missing or wrong API key (only when the server enforces keys) |
| 422 | `invalid_location` | Unknown city or location outside the USA |
| 422 | `route_not_found` | No drivable route |
| 422 | `no_fuel_plan` | A stretch of road has no station within range |
| 429 | `throttled` | Rate limit exceeded (see `Retry-After`) |
| 502 | `routing_service_unavailable` | The routing API failed |
| 503 | `station_data_unavailable` | Station data has not been imported |

---

## How it works

```
"Dallas, TX" ──► offline geocode ──► 1× OSRM route ──► stations near route ──► optimizer ──► JSON + map
                  (Census data)        (cached)        (numpy, ~10 ms)       (exact DP)
```

**1. Routing: one call to OSRM.** The free, keyless [OSRM](https://project-osrm.org) server returns the distance and the full route geometry in a single request. The geometry is requested as an encoded polyline (about 10× smaller than GeoJSON), and the result is cached by rounded coordinates, so the map page and repeated trips make no further calls. Connection errors and 502/503/504 are retried once with backoff; 429 is never retried. When several requests need the same uncached route at the same moment, only one of them calls OSRM.

**2. Geocoding: offline, at import time.** The price file has addresses like `I-44, EXIT 283` but no coordinates. Rather than calling a geocoding API ~6,600 times (slow and rate limited), each station is placed at the centre of its city using the US Census Gazetteer (cities plus New England towns and Mid-Atlantic/Midwest townships). City names are normalized so `St. Johns` / `Saint Johns`, `Mc Calla` / `McCalla` and `Boise` / `Boise City` match. **6,437 of 6,626 US stations (97%)** are located; the rest are small unincorporated places and are listed by the import command. Route inputs like `Dallas, TX` use the same table, so a request needs no geocoding API either.

**3. Stations along the route.** The route is resampled to a point every mile. For each station near the route's bounding box, the nearest route point is found with one matrix product of unit vectors (numpy), giving how far off-route the station is and its mile marker. Stations more than `corridor_miles` away are dropped, and only the cheapest station in each 25-mile stretch is kept.

**4. Choosing fuel stops.** This is the classic gas-station problem, solved exactly with dynamic programming. An optimal plan only ever does one of two things at a stop: *buy just enough to reach the next stop* (when the next one is cheaper) or *fill the tank* (when it is pricier); see Khuller, Malekian & Mestre, *"To Fill or not to Fill: The Gas Station Problem"* (ESA 2007). That keeps the state space small: (station, fuel on arrival), with fuel levels drawn from a short list. Pure cost-minimization produces silly plans ("drive 11 more miles to save $0.006/gal"), so each stop also carries a $15 penalty in the objective. The penalty shapes the plan only; **the reported cost is real money**. With the penalty at 0, the optimizer is verified against a brute-force search on 300 random routes.

---

## Assumptions

- **Vehicle:** 500-mile range, 10 mpg, so a 50-gallon tank (configurable).
- **Starting fuel:** the vehicle starts with an empty tank, so `total_cost_usd` is the money for all the fuel the trip burns and the first stop (mile 0) is the fill-up before leaving. It fills up at the cheapest listed station within `corridor_miles` of the start. The price file lists truck stops, and some big cities have none nearby; then the nearest listed station is used as the price for that first fill-up, and the response says so in `assumptions`. Pass `start_fuel_gallons` if the vehicle already has fuel.
- **Duplicate stations** in the price file (same OPIS ID, several prices): the lowest price is used. Canadian stations are skipped.
- **Station positions** are city centres (`location_precision: city_centroid`), so mile markers are approximate. That's why the default corridor is 10 miles. A price feed with `Latitude`/`Longitude` columns gives exact positions.
- **Prices** are treated as current retail prices per gallon.

---

## Production notes

Full guide: [`docs/deployment.md`](docs/deployment.md).

- **Settings** come from environment variables (see `.env.example`). `DEBUG` is off by default, the secret key is required, and HTTPS/HSTS/secure cookies and a strict Content Security Policy are on. `manage.py check --deploy` also warns about production gaps specific to this app (public OSRM server, no Redis, no API keys, `NUM_PROXIES` unset).
- **Serving:** `docker compose up --build`, or `gunicorn -c gunicorn.conf.py config.wsgi:application` (threaded workers, so slow routing calls do not block other requests). WhiteNoise serves static files, including the vendored Leaflet.
- **OSRM:** the public demo server is for development only. Self-host OSRM and set `OSRM_BASE_URL`. A trip gives up on routing after about 15 s and returns a 502.
- **Cache:** set `REDIS_URL` so the route cache and rate-limit counters are shared by all workers. If Redis goes down the API keeps working without them.
- **Access control:** set `API_KEYS` to require an `X-API-Key` header. Rate limiting (30 requests/minute, `TRIP_PLAN_RATE_LIMIT`) is per key, or per client IP when keys are off; set `NUM_PROXIES` behind a load balancer. `CORS_ALLOWED_ORIGINS` lists browser origins allowed to call the API.
- **Observability:** JSON logs with a request ID, optional Sentry (`SENTRY_DSN`), and Prometheus metrics at `/metrics` (`METRICS_TOKEN`).
- **Station data:** `import_fuel_stations` can run on a schedule (`--path` or `--url`); workers pick up new prices within a minute, without a restart.
- **Admin:** off by default (`DJANGO_ADMIN_ENABLED`), because the bundled database is rebuilt with every image.

---

## Development

```bash
python manage.py test
coverage run manage.py test && coverage report   # fails under 90%
ruff check . && ruff format --check .
mypy
python scripts/load_test.py --url http://localhost:8000
```

Dependencies are pinned with hashes. Edit `requirements.in` / `requirements-dev.in`, then regenerate:

```bash
pip-compile --generate-hashes --strip-extras -o requirements.txt requirements.in
pip-compile --generate-hashes --strip-extras --allow-unsafe -o requirements-dev.txt requirements-dev.in
```

```
config/                 settings, URLs, WSGI/ASGI
apps/common/            error handling, API keys, fail-open cache, logging, metrics, deploy checks,
                        geodesy helpers, polyline decoding
apps/geodata/           Census places table, name normalization, load_places command
apps/stations/          FuelStation model, CSV importer, in-memory station index
apps/routing/
  clients/osrm.py       routing API client (+ cache, retries)
  services/             locations, corridor search, fuel optimizer, trip planner
  api/                  serializers, views, response presenter, OpenAPI schema
  templates/, static/   Leaflet map page (Leaflet is vendored)
scripts/                Gazetteer download, container health check, load test
docs/deployment.md      running it in production
```
