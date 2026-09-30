#!/usr/bin/env python3
"""Container health check: exits 0 when the app answers its liveness probe.

Sends a Host header the app accepts (the first DJANGO_ALLOWED_HOSTS entry) and
marks the request as already-HTTPS, so it works whatever the deployment's
host and redirect settings are.
"""

import os
import sys
import urllib.request

port = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000").rsplit(":", 1)[-1]
hosts = [host.strip() for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost").split(",") if host.strip()]
host = "localhost" if hosts[0] == "*" else hosts[0].lstrip(".")

request = urllib.request.Request(
    f"http://127.0.0.1:{port}/api/v1/health/live/",
    headers={"Host": host, "X-Forwarded-Proto": "https"},
)
try:
    with urllib.request.urlopen(request, timeout=3) as response:
        sys.exit(0 if response.status == 200 else 1)
except OSError as exc:
    print(f"health check failed: {exc}", file=sys.stderr)
    sys.exit(1)
