"""Deployment checks, run by `manage.py check --deploy`."""

from django.conf import settings
from django.core.checks import Tags, Warning, register

PUBLIC_OSRM_HOST = "router.project-osrm.org"


@register(Tags.security, deploy=True)
def check_production_configuration(app_configs, **kwargs):
    warnings = []
    if PUBLIC_OSRM_HOST in settings.FUEL_ROUTE["OSRM_BASE_URL"]:
        warnings.append(
            Warning(
                "OSRM_BASE_URL points at the public OSRM demo server, which has no SLA and forbids heavy use.",
                hint="Self-host OSRM (see docs/deployment.md) and set OSRM_BASE_URL.",
                id="fuelroute.W001",
            )
        )
    if settings.REST_FRAMEWORK["NUM_PROXIES"] == 0:
        warnings.append(
            Warning(
                "NUM_PROXIES is 0, so behind a load balancer every client shares one rate limit.",
                hint="Set NUM_PROXIES to the number of reverse proxies in front of the app.",
                id="fuelroute.W002",
            )
        )
    if not settings.REDIS_URL:
        warnings.append(
            Warning(
                "REDIS_URL is not set: the route cache and rate-limit counters are per worker process.",
                hint="Set REDIS_URL so all workers share them.",
                id="fuelroute.W003",
            )
        )
    if not settings.API_KEYS:
        warnings.append(
            Warning(
                "API_KEYS is empty: anyone can call the trip endpoints (IP rate limiting only).",
                hint="Set API_KEYS, or silence fuelroute.W004 if the API is meant to be public.",
                id="fuelroute.W004",
            )
        )
    return warnings
