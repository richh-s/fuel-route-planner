import time
from urllib.parse import urlencode

from django.shortcuts import render
from django.urls import reverse
from django.views import View
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.common.exceptions import ServiceError
from apps.routing.api.presenters import present_trip_plan
from apps.routing.api.schema import ErrorResponseSchema, HealthResponseSchema, TripPlanResponseSchema
from apps.routing.api.serializers import TripRequestSerializer
from apps.routing.services.trip_planner import get_trip_planner
from apps.stations.index import get_station_index


def _map_url(request, params: dict) -> str:
    query = urlencode({key: value for key, value in params.items() if value is not None})
    return request.build_absolute_uri(f"{reverse('routing:trip-map')}?{query}")


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
    "as possible for a vehicle with a 500-mile range at 10 mpg. Locations are `City, ST` "
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


class HealthView(APIView):
    throttle_classes = []  # load balancers poll this frequently

    @extend_schema(summary="Health check", responses=HealthResponseSchema, tags=["Operations"])
    def get(self, request):
        return Response({"status": "ok", "stations_loaded": len(get_station_index())})


class TripMapView(View):
    """Interactive Leaflet map for a trip. Reuses the cached route, so it makes no routing API call.

    A plain Django view (it renders HTML), sharing the API's rate limit.
    """

    template_name = "routing/trip_map.html"
    throttle_scope = TRIP_PLAN_THROTTLE_SCOPE

    def get(self, request):
        throttle = ScopedRateThrottle()
        if not throttle.allow_request(request, self):
            response = self._error(request, "Too many requests. Please try again shortly.", status=429)
            wait = throttle.wait()
            if wait is not None:
                response["Retry-After"] = str(int(wait) + 1)
            return response

        try:
            plan = _plan(request, request.GET)
        except ServiceError as exc:
            return self._error(request, exc.message, exc.status_code)
        except ValidationError as exc:
            return self._error(request, f"Invalid request: {exc.detail}", status=400)
        return render(request, self.template_name, {"plan": plan})

    def _error(self, request, message: str, status: int):
        return render(request, self.template_name, {"error": message}, status=status)
