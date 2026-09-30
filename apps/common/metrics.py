"""Prometheus metrics.

Under gunicorn there are several worker processes, so metrics are written to
files in PROMETHEUS_MULTIPROC_DIR and merged when scraped (see gunicorn.conf.py).
Without that variable (runserver, tests) the default in-process registry is used.
"""

import hmac
import os

from django.conf import settings
from django.http import HttpResponse, HttpResponseForbidden, HttpResponseNotFound
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, CollectorRegistry, Counter, Histogram, generate_latest
from prometheus_client import multiprocess as prometheus_multiprocess

_LATENCY_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)

HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests handled.", ["method", "view", "status"])
HTTP_DURATION = Histogram("http_request_duration_seconds", "HTTP request latency.", ["view"], buckets=_LATENCY_BUCKETS)
ROUTING_REQUESTS = Counter("routing_api_requests_total", "Calls to the routing API by outcome.", ["outcome"])
ROUTING_DURATION = Histogram("routing_api_request_duration_seconds", "Routing API latency.", buckets=_LATENCY_BUCKETS)
ROUTE_CACHE = Counter("route_cache_lookups_total", "Route cache lookups by result.", ["result"])
CACHE_ERRORS = Counter("cache_errors_total", "Cache operations that failed and were skipped.", ["operation"])


def _registry() -> CollectorRegistry:
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        prometheus_multiprocess.MultiProcessCollector(registry)
        return registry
    return REGISTRY


def metrics_view(request):
    """Prometheus scrape endpoint. Disabled unless METRICS_TOKEN is set; requires it as a bearer token."""
    token = settings.METRICS_TOKEN
    if not token:
        return HttpResponseNotFound()
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(supplied.encode(), token.encode()):
        return HttpResponseForbidden()
    return HttpResponse(generate_latest(_registry()), content_type=CONTENT_TYPE_LATEST)
