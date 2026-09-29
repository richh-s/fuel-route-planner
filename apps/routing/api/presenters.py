"""Convert a TripPlan into the public JSON response shape."""

from apps.routing.services.locations import ResolvedLocation
from apps.routing.services.trip_planner import TripPlan


def _money(value: float) -> float:
    return round(value, 2)


def _location(location: ResolvedLocation) -> dict:
    return {
        "query": location.query,
        "label": location.label,
        "latitude": round(location.coordinates.latitude, 6),
        "longitude": round(location.coordinates.longitude, 6),
    }


def present_trip_plan(plan: TripPlan, map_url: str | None = None, elapsed_ms: float | None = None) -> dict:
    stops = []
    for number, stop in enumerate(plan.fuel_plan.stops, start=1):
        record = stop.station.record
        stops.append(
            {
                "stop_number": number,
                "mile_marker": round(stop.station.mile_marker, 1),
                "distance_from_route_miles": round(stop.station.distance_from_route_miles, 1),
                "station": {
                    "opis_id": record.opis_id,
                    "name": record.name,
                    "address": record.address,
                    "city": record.city,
                    "state": record.state,
                    "latitude": record.latitude,
                    "longitude": record.longitude,
                },
                "price_per_gallon": round(record.price, 3),
                "gallons": round(stop.gallons, 2),
                "cost_usd": _money(stop.cost),
                "fuel_on_arrival_gallons": round(stop.fuel_on_arrival_gallons, 2),
            }
        )

    coordinates = [
        [round(float(lon), 5), round(float(lat), 5)]
        for lat, lon in zip(plan.route_latitudes, plan.route_longitudes, strict=True)
    ]

    return {
        "start": _location(plan.start),
        "finish": _location(plan.finish),
        "route": {
            "distance_miles": round(plan.distance_miles, 1),
            "duration_hours": round(plan.duration_seconds / 3600, 2),
            "geometry": {"type": "LineString", "coordinates": coordinates},
        },
        "fuel": {
            "total_cost_usd": _money(plan.fuel_plan.total_cost),
            "total_gallons_purchased": round(plan.fuel_plan.total_gallons_purchased, 2),
            "total_gallons_used": round(plan.total_fuel_needed_gallons, 2),
            "number_of_stops": len(stops),
            "stops": stops,
        },
        "vehicle": {
            "range_miles": plan.vehicle.range_miles,
            "miles_per_gallon": plan.vehicle.miles_per_gallon,
            "tank_capacity_gallons": round(plan.vehicle.tank_gallons, 2),
            "start_fuel_gallons": round(plan.start_fuel_gallons, 2),
        },
        "assumptions": [
            "The vehicle departs with start_fuel_gallons in the tank (a full tank by default); "
            "total_cost_usd is the money spent at fuel stops along the way.",
            f"Only stations within {plan.corridor_miles:g} miles of the route are considered.",
            "Stations are located at the centre of their city, so mile markers are approximate.",
        ],
        "map_url": map_url,
        "meta": {
            "routing_api_calls": plan.routing_api_calls,
            "stations_along_route": len(plan.stations_on_route),
            "elapsed_ms": round(elapsed_ms, 1) if elapsed_ms is not None else None,
        },
    }
