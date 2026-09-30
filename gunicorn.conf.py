"""Gunicorn configuration: `gunicorn -c gunicorn.conf.py config.wsgi:application`."""

import os
import shutil
import tempfile
from pathlib import Path

bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")
workers = int(os.environ.get("WEB_CONCURRENCY", "3"))
# Requests spend most of their time waiting on the routing API, so threads (not processes) are the
# cheap way to keep serving other requests, and health checks, while a few of those calls are slow.
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", "8"))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "30"))
graceful_timeout = 30
keepalive = 5
# Recycle workers periodically to bound memory growth.
max_requests = 2000
max_requests_jitter = 200

# The application writes its own structured access log (see RequestContextMiddleware).
accesslog = None
errorlog = "-"


# Workers share metrics through files in this directory (see apps/common/metrics.py). It is set here
# rather than in the image so that management commands keep using the plain in-process registry.
_metrics_dir = Path(
    os.environ.setdefault("PROMETHEUS_MULTIPROC_DIR", str(Path(tempfile.gettempdir()) / "fuel-route-planner-metrics"))
)
# Start empty: files left by a previous run would skew the metrics.
shutil.rmtree(_metrics_dir, ignore_errors=True)
_metrics_dir.mkdir(parents=True, exist_ok=True)


def child_exit(server, worker):
    from prometheus_client import multiprocess

    multiprocess.mark_process_dead(worker.pid)
