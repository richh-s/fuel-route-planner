"""
Django settings for the fuel route planner.

All environment-specific values are read from environment variables so the
same settings module works locally and in production. See `.env.example`.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
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
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_SECURE_HSTS_SECONDS", str(60 * 60 * 24 * 30)))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    X_FRAME_OPTIONS = "DENY"

# HSTS preload is a permanent, domain-wide commitment; opt in per deployment, not by default.
SILENCED_SYSTEM_CHECKS = ["security.W021"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "apps.common",
    "apps.geodata",
    "apps.stations",
    "apps.routing",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("DATABASE_PATH", BASE_DIR / "db.sqlite3"),
    }
}

# Routes are cached so repeated requests (and the map page) never hit the
# routing API again. Local memory is fine for a single process; set REDIS_URL
# to share the cache between workers.
if os.environ.get("REDIS_URL"):
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": os.environ["REDIS_URL"],
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

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    # The browsable HTML API is a development aid only.
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"]
    + (["rest_framework.renderers.BrowsableAPIRenderer"] if DEBUG else []),
    "EXCEPTION_HANDLER": "apps.common.exceptions.api_exception_handler",
    "UNAUTHENTICATED_USER": None,
}

# Domain configuration for route planning.
FUEL_ROUTE = {
    # Free, keyless OSRM demo server. Point this at a self-hosted OSRM in production.
    "OSRM_BASE_URL": os.environ.get("OSRM_BASE_URL", "https://router.project-osrm.org"),
    "OSRM_TIMEOUT_SECONDS": float(os.environ.get("OSRM_TIMEOUT_SECONDS", "15")),
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
    "FUEL_PRICES_CSV": BASE_DIR / "data" / "fuel-prices-for-be-assessment.csv",
    "PLACES_GAZETTEER": BASE_DIR / "data" / "2024_Gaz_place_national.txt",
    "COUNTY_SUBDIVISIONS_GAZETTEER": BASE_DIR / "data" / "2024_Gaz_cousubs_national.txt",
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"standard": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "standard"}},
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
}
