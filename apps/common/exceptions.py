"""Domain errors and a DRF exception handler that renders every error the same way.

Error responses always look like:

    {"error": {"code": "route_not_found", "message": "...", "details": {...}}}
"""

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler


class ServiceError(Exception):
    """Base class for errors raised by the service layer."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code = "service_error"

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class InvalidLocationError(ServiceError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "invalid_location"


class RouteNotFoundError(ServiceError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "route_not_found"


class NoFuelPlanError(ServiceError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "no_fuel_plan"


class RoutingServiceUnavailable(ServiceError):
    status_code = status.HTTP_502_BAD_GATEWAY
    code = "routing_service_unavailable"


class StationDataUnavailable(ServiceError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "station_data_unavailable"


def _error_body(code: str, message: str, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def api_exception_handler(exc, context):
    if isinstance(exc, ServiceError):
        return Response(_error_body(exc.code, exc.message, exc.details), status=exc.status_code)

    response = exception_handler(exc, context)
    if response is None:
        return None

    code = getattr(exc, "default_code", "error")
    if response.status_code == status.HTTP_400_BAD_REQUEST:
        response.data = _error_body("validation_error", "Invalid request parameters.", response.data)
    else:
        detail = response.data.get("detail", str(exc)) if isinstance(response.data, dict) else str(exc)
        response.data = _error_body(code, str(detail))
    return response
