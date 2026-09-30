"""Orchestrates a trip plan: resolve locations -> one routing call -> corridor search -> fuel optimization."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from django.conf import settings

from apps.common.exceptions import NoFuelPlanError, StationDataUnavailable
from apps.common.geo import resample_polyline
from apps.routing.clients.osrm import CachedRoutingClient, get_routing_client
from apps.routing.services.corridor import (
    StationOnRoute,
    cheapest_per_stretch,
    departure_station,
    find_stations_along_route,
)
from apps.routing.services.fuel_optimizer import FuelPlan, InfeasibleRouteError, plan_fuel_stops
from apps.routing.services.locations import ResolvedLocation, resolve_location
from apps.stations.index import StationIndex, get_station_index


@dataclass(frozen=True)
class VehicleProfile:
    range_miles: float
    miles_per_gallon: float

    @property
    def tank_gallons(self) -> float:
        return self.range_miles / self.miles_per_gallon


@dataclass(frozen=True)
class TripRequest:
    start: str
    finish: str
    corridor_miles: float
    start_fuel_gallons: float | None = None  # None = empty tank: the trip's fuel is all bought (and costed)


@dataclass(frozen=True)
class TripPlan:
    start: ResolvedLocation
    finish: ResolvedLocation
    distance_miles: float
    duration_seconds: float
    route_latitudes: np.ndarray
    route_longitudes: np.ndarray
    vehicle: VehicleProfile
    start_fuel_gallons: float
    corridor_miles: float
    stations_on_route: tuple[StationOnRoute, ...]
    fuel_plan: FuelPlan
    routing_api_calls: int
    prices_updated_at: datetime | None = None

    @property
    def total_fuel_needed_gallons(self) -> float:
        return self.distance_miles / self.vehicle.miles_per_gallon


class TripPlanner:
    def __init__(
        self,
        routing_client: CachedRoutingClient,
        station_index: Callable[[], StationIndex],
        vehicle: VehicleProfile,
        sample_step_miles: float = 1.0,
        station_stretch_miles: float = 0.0,
        stop_penalty: float = 0.0,
    ):
        self.routing_client = routing_client
        self.station_index = station_index
        self.vehicle = vehicle
        self.sample_step_miles = sample_step_miles
        self.station_stretch_miles = station_stretch_miles
        self.stop_penalty = stop_penalty

    def plan(self, request: TripRequest) -> TripPlan:
        start = resolve_location(request.start)
        finish = resolve_location(request.finish)

        index = self.station_index()
        if len(index) == 0:
            raise StationDataUnavailable("No fuel stations are loaded. Run the import commands first.")

        route, called_api = self.routing_client.route(start.coordinates, finish.coordinates)

        latitudes, longitudes, miles = resample_polyline(
            route.coordinates[:, 0], route.coordinates[:, 1], self.sample_step_miles
        )
        # Align mile markers with the distance the routing engine reports.
        if miles[-1] > 0:
            miles = miles * (route.distance_miles / miles[-1])

        stations = find_stations_along_route(index, latitudes, longitudes, miles, request.corridor_miles)

        start_fuel = request.start_fuel_gallons or 0.0
        candidates = cheapest_per_stretch(stations, self.station_stretch_miles)
        # The vehicle may fuel up before leaving; with an empty tank it has to.
        departure = departure_station(index, start.coordinates, request.corridor_miles)
        if departure is not None:
            candidates = [departure, *candidates]
        try:
            fuel_plan = plan_fuel_stops(
                candidates,
                total_miles=route.distance_miles,
                tank_range_miles=self.vehicle.range_miles,
                miles_per_gallon=self.vehicle.miles_per_gallon,
                initial_fuel_miles=start_fuel * self.vehicle.miles_per_gallon,
                stop_penalty=self.stop_penalty,
            )
        except InfeasibleRouteError as exc:
            raise NoFuelPlanError(
                "The route has a stretch with no reachable fuel station within the vehicle's range. "
                "Try a larger corridor_miles.",
                {"stranded_at_mile": round(exc.stranded_at_mile, 1), "next_station_mile": exc.next_station_mile},
            ) from exc

        return TripPlan(
            start=start,
            finish=finish,
            distance_miles=route.distance_miles,
            duration_seconds=route.duration_seconds,
            route_latitudes=latitudes,
            route_longitudes=longitudes,
            vehicle=self.vehicle,
            start_fuel_gallons=start_fuel,
            corridor_miles=request.corridor_miles,
            stations_on_route=tuple(stations),
            fuel_plan=fuel_plan,
            routing_api_calls=int(called_api),
            prices_updated_at=index.prices_updated_at,
        )


def get_trip_planner() -> TripPlanner:
    config = settings.FUEL_ROUTE
    return TripPlanner(
        routing_client=get_routing_client(),
        station_index=get_station_index,
        vehicle=VehicleProfile(config["VEHICLE_RANGE_MILES"], config["VEHICLE_MPG"]),
        sample_step_miles=config["ROUTE_SAMPLE_STEP_MILES"],
        station_stretch_miles=config["STATION_STRETCH_MILES"],
        stop_penalty=config["FUEL_STOP_PENALTY_USD"],
    )
