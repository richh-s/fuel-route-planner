import logging
import re
import time
import uuid

from apps.common import metrics
from apps.common.log import request_id_var

access_logger = logging.getLogger("access")

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestContextMiddleware:
    """Tags each request with an ID (logs, response header) and records an access log line and metrics.

    An incoming X-Request-ID is reused when it looks sane, so IDs follow a request across services.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _SAFE_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        request.request_id = request_id
        # Deliberately not reset afterwards: Django logs 4xx/5xx responses after the middleware chain
        # returns, and those lines should carry the ID too. The next request on this thread replaces it.
        request_id_var.set(request_id)
        started = time.perf_counter()
        response = self.get_response(request)
        response["X-Request-ID"] = request_id
        self._record(request, response.status_code, time.perf_counter() - started)
        return response

    @staticmethod
    def _record(request, status: int, seconds: float) -> None:
        # Label by URL name, not raw path, to keep metric cardinality bounded.
        match = getattr(request, "resolver_match", None)
        view = (match.view_name if match else "") or "unresolved"
        metrics.HTTP_REQUESTS.labels(method=request.method, view=view, status=str(status)).inc()
        metrics.HTTP_DURATION.labels(view=view).observe(seconds)
        access_logger.info(
            "%s %s %s",
            request.method,
            request.path,
            status,
            extra={
                "method": request.method,
                "path": request.path,
                "view": view,
                "status": status,
                "duration_ms": round(seconds * 1000, 1),
            },
        )
