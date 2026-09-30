"""
Django settings for the fuel route planner.

All environment-specific values are read from environment variables so the
same settings module works locally and in production. See `.env.example`.
"""

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured
from django.utils.csp import CSP
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Local development convenience: values in .env are used unless already set in the environment.
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


# Secure by default: debug is off unless explicitly enabled.
DEBUG = env_bool("DJANGO_DEBUG", False)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is off.")
    SECRET_KEY = "django-insecure-local-development-only"

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
    # Load balancers and orchestrators probe over plain HTTP from inside the network.
    SECURE_REDIRECT_EXEMPT = [r"^api/v1/health/", r"^metrics$"]
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_SECURE_HSTS_SECONDS", str(60 * 60 * 24 * 30)))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    X_FRAME_OPTIONS = "DENY"

# HSTS preload is a permanent, domain-wide commitment; opt in per deployment, not by default.
SILENCED_SYSTEM_CHECKS = ["security.W021", *env_list("DJANGO_SILENCED_SYSTEM_CHECKS")]

# The admin is off by default: the API itself is stateless, and the bundled SQLite database is
# rebuilt with every image, so admin users and sessions would not survive a deploy. Only enable
# it with a persistent database (mount a volume at DATABASE_PATH).
ADMIN_ENABLED = env_bool("DJANGO_ADMIN_ENABLED", False)

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "drf_spectacular",
    "apps.common",
    "apps.geodata",
    "apps.stations",
    "apps.routing",
]

MIDDLEWARE = [
    "apps.common.middleware.RequestContextMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

_TEMPLATE_CONTEXT_PROCESSORS = ["django.template.context_processors.request"]

if ADMIN_ENABLED:
    INSTALLED_APPS += ["django.contrib.admin", "django.contrib.sessions", "django.contrib.messages"]
    _csrf = MIDDLEWARE.index("django.middleware.csrf.CsrfViewMiddleware")
    MIDDLEWARE.insert(_csrf + 1, "django.contrib.auth.middleware.AuthenticationMiddleware")
    MIDDLEWARE.insert(_csrf + 2, "django.contrib.messages.middleware.MessageMiddleware")
    MIDDLEWARE.insert(
        MIDDLEWARE.index("django.middleware.common.CommonMiddleware"),
        "django.contrib.sessions.middleware.SessionMiddleware",
    )
    _TEMPLATE_CONTEXT_PROCESSORS += [
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": _TEMPLATE_CONTEXT_PROCESSORS,
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("DATABASE_PATH", BASE_DIR / "db.sqlite3"),
        # Wait for a concurrent price import to finish instead of failing with "database is locked".
        "OPTIONS": {"timeout": 10},
    }
}

# Routes are cached so repeated requests (and the map page) never hit the
# routing API again. Local memory is fine for a single process; set REDIS_URL
# to share the cache between workers.
REDIS_URL = os.environ.get("REDIS_URL", "")
if REDIS_URL:
    _redis_timeout = float(os.environ.get("REDIS_TIMEOUT_SECONDS", "1"))
    CACHES = {
        "default": {
            # Fails open: if Redis is unreachable, requests are served without cache or rate limiting.
            "BACKEND": "apps.common.cache.FailOpenRedisCache",
            "LOCATION": REDIS_URL,
            # Short timeouts so a hung Redis cannot stall request threads.
            "OPTIONS": {"socket_connect_timeout": _redis_timeout, "socket_timeout": _redis_timeout},
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "fuel-route-planner",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # Served by WhiteNoise from gunicorn; no separate static file server needed.
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Content Security Policy. All page assets are served from this origin; the only
# third-party resource is the map's tile server.
MAP_TILE_URL = os.environ.get("MAP_TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png")
MAP_TILE_ATTRIBUTION = os.environ.get("MAP_TILE_ATTRIBUTION", "© OpenStreetMap contributors")
_tiles = urlsplit(MAP_TILE_URL)
_tile_origin = f"{_tiles.scheme}://{_tiles.netloc.replace('{s}.', '*.')}"

SECURE_CSP: dict[str, list[str]] = {
    "default-src": [CSP.SELF],
    "script-src": [CSP.SELF],
    "style-src": [CSP.SELF],
    "img-src": [CSP.SELF, "data:", _tile_origin],
    "connect-src": [CSP.SELF],
    "object-src": [CSP.NONE],
    "base-uri": [CSP.SELF],
    "form-action": [CSP.SELF],
    "frame-ancestors": [CSP.NONE],
}
# Swagger UI and ReDoc load their bundles from a CDN and use inline scripts; only the docs pages get this policy.
DOCS_CSP: dict[str, list[str]] = {
    "default-src": [CSP.SELF],
    "script-src": [CSP.SELF, CSP.UNSAFE_INLINE, "https://cdn.jsdelivr.net"],
    "style-src": [CSP.SELF, CSP.UNSAFE_INLINE, "https://cdn.jsdelivr.net", "https://fonts.googleapis.com"],
    "font-src": [CSP.SELF, "https://fonts.gstatic.com"],
    "img-src": [CSP.SELF, "data:", "https://cdn.jsdelivr.net", "https://cdn.redoc.ly"],
    "worker-src": [CSP.SELF, "blob:"],
    "connect-src": [CSP.SELF],
    "object-src": [CSP.NONE],
    "base-uri": [CSP.SELF],
    "frame-ancestors": [CSP.NONE],
}

# Browser frontends on other origins may call the API only if listed here.
CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS")
CORS_URLS_REGEX = r"^/api/"
CORS_ALLOW_METHODS = ["GET", "POST", "OPTIONS"]
CORS_ALLOW_HEADERS = ["accept", "authorization", "content-type", "x-api-key", "x-request-id"]
CORS_EXPOSE_HEADERS = ["Retry-After", "X-Request-ID"]

# Access control. With API_KEYS empty the API is open; otherwise trip endpoints require a key.
API_KEYS = env_list("API_KEYS")
# How long a signed map link (handed out in `map_url` when keys are enforced) stays valid.
MAP_LINK_MAX_AGE_SECONDS = int(os.environ.get("MAP_LINK_MAX_AGE_SECONDS", str(60 * 60 * 24 * 7)))
# Bearer token for the Prometheus endpoint (/metrics). The endpoint is disabled when unset.
METRICS_TOKEN = os.environ.get("METRICS_TOKEN", "")

REST_FRAMEWORK: dict[str, Any] = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["apps.common.auth.APIKeyAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["apps.common.auth.APIKeyRequired"],
    # The browsable HTML API is a development aid only.
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"]
    + (["rest_framework.renderers.BrowsableAPIRenderer"] if DEBUG else []),
    "EXCEPTION_HANDLER": "apps.common.exceptions.api_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # Per-client rate limits (keyed by API key, else IP). Views opt in with `throttle_scope`.
    # Counters live in the default cache: set REDIS_URL so all workers share them.
    "DEFAULT_THROTTLE_CLASSES": ["apps.common.auth.ClientRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"trip_plan": os.environ.get("TRIP_PLAN_RATE_LIMIT", "30/minute")},
    # 0 = use the socket address; only trust X-Forwarded-For when behind a known number of proxies.
    "NUM_PROXIES": int(os.environ.get("NUM_PROXIES", "0")),
    "UNAUTHENTICATED_USER": None,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Fuel Route Planner API",
    "DESCRIPTION": "Plan US driving routes with the cheapest fuel stops for a 500-mile-range, 10 mpg vehicle.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# Domain configuration for route planning.
FUEL_ROUTE: dict[str, Any] = {
    # Free, keyless OSRM demo server. Point this at a self-hosted OSRM in production.
    "OSRM_BASE_URL": os.environ.get("OSRM_BASE_URL", "https://router.project-osrm.org"),
    # Worst case per trip is (retries + 1) x (connect + read timeout) plus backoff: ~15 s with these
    # defaults. Keep that well under the gunicorn worker timeout (30 s) so callers get a clean 502.
    "OSRM_CONNECT_TIMEOUT_SECONDS": float(os.environ.get("OSRM_CONNECT_TIMEOUT_SECONDS", "2")),
    "OSRM_TIMEOUT_SECONDS": float(os.environ.get("OSRM_TIMEOUT_SECONDS", "5")),
    "OSRM_RETRIES": int(os.environ.get("OSRM_RETRIES", "1")),
    "ROUTE_CACHE_TTL_SECONDS": int(os.environ.get("ROUTE_CACHE_TTL_SECONDS", str(60 * 60 * 24))),
    "VEHICLE_RANGE_MILES": float(os.environ.get("VEHICLE_RANGE_MILES", "500")),
    "VEHICLE_MPG": float(os.environ.get("VEHICLE_MPG", "10")),
    # How far from the route a station may be and still count as "on the way".
    # Stations are geocoded to their city centroid, so this is intentionally generous.
    "DEFAULT_CORRIDOR_MILES": float(os.environ.get("DEFAULT_CORRIDOR_MILES", "10")),
    "MAX_CORRIDOR_MILES": float(os.environ.get("MAX_CORRIDOR_MILES", "50")),
    # Only the cheapest station in each stretch of this length is considered.
    "STATION_STRETCH_MILES": float(os.environ.get("STATION_STRETCH_MILES", "25")),
    # Extra cost (USD) the optimizer assigns to every stop, so it avoids stopping to save pennies.
    # It shapes the plan only; the reported fuel cost is real money.
    "FUEL_STOP_PENALTY_USD": float(os.environ.get("FUEL_STOP_PENALTY_USD", "15")),
    # Spacing of the resampled route polyline used for corridor search and the response geometry.
    "ROUTE_SAMPLE_STEP_MILES": float(os.environ.get("ROUTE_SAMPLE_STEP_MILES", "1")),
    # How often each worker checks the database for re-imported prices.
    "STATION_INDEX_REFRESH_SECONDS": float(os.environ.get("STATION_INDEX_REFRESH_SECONDS", "60")),
    # A price import that would drop more than this share of stations is refused (truncated feed?).
    "MAX_IMPORT_SHRINK_RATIO": float(os.environ.get("MAX_IMPORT_SHRINK_RATIO", "0.5")),
    "FUEL_PRICES_CSV": BASE_DIR / "data" / "fuel-prices-for-be-assessment.csv",
    "PLACES_GAZETTEER": BASE_DIR / "data" / "2024_Gaz_place_national.txt",
    "COUNTY_SUBDIVISIONS_GAZETTEER": BASE_DIR / "data" / "2024_Gaz_cousubs_national.txt",
}

# One JSON object per line in production (for log aggregators), readable text in development.
LOG_FORMAT = os.environ.get("LOG_FORMAT", "text" if DEBUG else "json")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"request_id": {"()": "apps.common.log.RequestIdFilter"}},
    "formatters": {
        "text": {"format": "%(asctime)s %(levelname)s %(name)s [%(request_id)s]: %(message)s"},
        "json": {"()": "apps.common.log.JsonFormatter"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": LOG_FORMAT, "filters": ["request_id"]},
    },
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
}

# Error tracking. Off unless SENTRY_DSN is set.
SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
if SENTRY_DSN:
    import sentry_sdk

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=os.environ.get("SENTRY_ENVIRONMENT", "production"),
        release=os.environ.get("APP_VERSION") or None,
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0")),
        send_default_pii=False,
    )
