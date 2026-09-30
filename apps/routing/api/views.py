import logging
import time
from urllib.parse import urlencode

from django.conf import settings
from django.core.cache import cache
from django.shortcuts import render
from django.urls import reverse
from django.views import View
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.auth import ClientRateThrottle, map_signature_is_valid, sign_map_params
from apps.common.exceptions import ServiceError
from apps.common.geo import Coordinates
from apps.routing.api.presenters import present_trip_plan
from apps.routing.api.schema import (
    ErrorResponseSchema,
    HealthResponseSchema,
    LivenessResponseSchema,
    TripPlanResponseSchema,
)
from apps.routing.api.serializers import TripRequestSerializer
from apps.routing.clients.osrm import OSRMClient
from apps.routing.services.trip_planner import get_trip_planner
from apps.stations.index import get_station_index

logger = logging.getLogger(__name__)


def _map_url(request, params: dict) -> str:
    query = {key: value for key, value in params.items() if value is not None}
    if settings.API_KEYS:
        # A browser cannot send the API key, so the link itself carries a signature.
        query["sig"] = sign_map_params(query)
    return request.build_absolute_uri(f"{reverse('routing:trip-map')}?{urlencode(query)}")


def _plan(request, data):
    serializer = TripRequestSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    started = time.perf_counter()
    plan = get_trip_planner().plan(serializer.to_trip_request())
    elapsed_ms = (time.perf_counter() - started) * 1000
    return present_trip_plan(plan, _map_url(request, serializer.validated_data), elapsed_ms)


TRIP_PLAN_THROTTLE_SCOPE = "trip_plan"

_ERROR_RESPONSES = {
    400: OpenApiResponse(ErrorResponseSchema, description="Invalid parameters (`validation_error`)."),
    401: OpenApiResponse(
        ErrorResponseSchema,
        description="Missing or invalid API key (`not_authenticated`, `authentication_failed`). "
        "Only when the server enforces API keys.",
    ),
    422: OpenApiResponse(
        ErrorResponseSchema,
        description="Unknown or non-US location (`invalid_location`), no drivable route (`route_not_found`), "
        "or no fuel plan possible within the vehicle range (`no_fuel_plan`).",
    ),
    429: OpenApiResponse(ErrorResponseSchema, description="Rate limit exceeded (`throttled`); see Retry-After."),
    502: OpenApiResponse(ErrorResponseSchema, description="Routing service failure (`routing_service_unavailable`)."),
    503: OpenApiResponse(ErrorResponseSchema, description="Station data not loaded (`station_data_unavailable`)."),
}
_TRIP_DESCRIPTION = (
    "Plans a driving route between two US locations and picks where to refuel so the trip is as cheap "
    "as possible for a vehicle with a 500-mile range at 10 mpg. The vehicle starts with an empty tank unless "
    "`start_fuel_gallons` is given, so the total cost covers all fuel for the trip. Locations are `City, ST` "
    "(e.g. `Dallas, TX`) or `lat,lng`. The response includes the route geometry (GeoJSON), each fuel stop, "
    "the total fuel cost and a `map_url` to an interactive map. Exactly one routing API call is made per new "
    "route; repeated routes are served from cache."
)
_TRIP_RESPONSES = {200: TripPlanResponseSchema, **_ERROR_RESPONSES}


class TripPlanView(APIView):
    """Plan a US road trip with the cheapest fuel stops.

    GET  /api/v1/route/?start=Dallas, TX&finish=Chicago, IL
    POST /api/v1/route/  {"start": "Dallas, TX", "finish": "Chicago, IL"}

    Optional: corridor_miles, start_fuel_gallons.
    """

    throttle_scope = TRIP_PLAN_THROTTLE_SCOPE

    @extend_schema(
        operation_id="plan_trip",
        summary="Plan a trip (query parameters)",
        description=_TRIP_DESCRIPTION,
        parameters=[TripRequestSerializer],
        responses=_TRIP_RESPONSES,
        tags=["Trips"],
    )
    def get(self, request):
        return Response(_plan(request, request.query_params))

    @extend_schema(
        operation_id="plan_trip_post",
        summary="Plan a trip (JSON body)",
        description=_TRIP_DESCRIPTION,
        request=TripRequestSerializer,
        responses=_TRIP_RESPONSES,
        tags=["Trips"],
        examples=[
            OpenApiExample("Cross-country", value={"start": "Los Angeles, CA", "finish": "New York, NY"}),
            OpenApiExample(
                "Coordinates, half tank",
                value={"start": "32.7767,-96.7970", "finish": "41.8781,-87.6298", "start_fuel_gallons": 25},
            ),
        ],
    )
    def post(self, request):
        return Response(_plan(request, request.data))


class LivenessView(APIView):
    """Is the process up? Touches no dependencies, so an outage elsewhere never restarts healthy workers."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []  # orchestrators poll this frequently

    @extend_schema(summary="Liveness probe", responses=LivenessResponseSchema, tags=["Operations"])
    def get(self, request):
        return Response({"status": "ok"})


# Two points a few miles apart in Kansas City: a cheap request that proves the routing API answers.
_ROUTING_PROBE = (Coordinates(39.0997, -94.5786), Coordinates(39.1141, -94.6275))


class ReadinessView(APIView):
    """Can this instance serve trips?

    503 only when station data is missing, since then no trip can be planned.
    A cache outage reports `degraded` but stays 200: requests still work, and
    taking every instance out of rotation would turn a slowdown into an outage.
    `?deep=true` also probes the routing API (for dashboards, not load balancers).
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []  # load balancers poll this frequently

    @extend_schema(
        summary="Readiness probe",
        responses={200: HealthResponseSchema, 503: HealthResponseSchema},
        tags=["Operations"],
    )
    def get(self, request):
        checks = {}
        stations_loaded, prices_updated_at = 0, None
        try:
            index = get_station_index()
            stations_loaded, prices_updated_at = len(index), index.prices_updated_at
        except Exception:
            logger.exception("Readiness: could not load station data")
        checks["stations"] = "ok" if stations_loaded else "unavailable"

        # Only the Redis backend has ping(); the in-process cache cannot be down.
        ping = getattr(cache, "ping", None)
        checks["cache"] = "ok" if ping is None or ping() else "unavailable"

        if request.query_params.get("deep", "").lower() in {"1", "true", "yes"}:
            checks["routing"] = self._probe_routing()

        if checks["stations"] != "ok":
            status = "unavailable"
        elif any(result != "ok" for result in checks.values()):
            status = "degraded"
        else:
            status = "ok"
        body = {
            "status": status,
            "stations_loaded": stations_loaded,
            "prices_updated_at": prices_updated_at.isoformat() if prices_updated_at else None,
            "checks": checks,
        }
        return Response(body, status=503 if status == "unavailable" else 200)

    @staticmethod
    def _probe_routing() -> str:
        config = settings.FUEL_ROUTE
        client = OSRMClient(config["OSRM_BASE_URL"], timeout=2.0, connect_timeout=2.0, retries=0)
        try:
            client.route(*_ROUTING_PROBE)
        except ServiceError:
            return "unavailable"
        return "ok"


class TripMapView(View):
    """Interactive Leaflet map for a trip. Reuses the cached route, so it makes no routing API call.

    A plain Django view (it renders HTML), sharing the API's rate limit. When
    API keys are enforced it only accepts the signed links the API returns in
    `map_url`, since a browser cannot send a key.
    """

    template_name = "routing/trip_map.html"
    throttle_scope = TRIP_PLAN_THROTTLE_SCOPE

    def get(self, request):
        throttle = ClientRateThrottle()
        # DRF types this for its own views, but the throttle only reads `throttle_scope` from the view.
        if not throttle.allow_request(request, self):  # type: ignore[arg-type]
            response = self._error(request, "Too many requests. Please try again shortly.", status=429)
            wait = throttle.wait()
            if wait is not None:
                response["Retry-After"] = str(int(wait) + 1)
            return response

        if settings.API_KEYS and not map_signature_is_valid(request.GET, request.GET.get("sig", "")):
            return self._error(
                request, "This map link is invalid or has expired. Request the trip again for a new link.", status=403
            )

        try:
            plan = _plan(request, request.GET)
        except ServiceError as exc:
            return self._error(request, exc.message, exc.status_code)
        except ValidationError as exc:
            return self._error(request, f"Invalid request: {exc.detail}", status=400)
        return render(request, self.template_name, self._context(plan=plan))

    def _error(self, request, message: str, status: int):
        return render(request, self.template_name, self._context(error=message), status=status)

    @staticmethod
    def _context(**context) -> dict:
        return {"tile_url": settings.MAP_TILE_URL, "tile_attribution": settings.MAP_TILE_ATTRIBUTION, **context}
