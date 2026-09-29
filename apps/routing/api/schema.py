"""Response shapes for the OpenAPI schema.

These serializers only document what `presenters.present_trip_plan` returns;
they are not used to build responses.
"""

from rest_framework import serializers


class LocationSchema(serializers.Serializer):
    query = serializers.CharField(help_text="The input as given.")
    label = serializers.CharField(help_text="Resolved place, e.g. 'Dallas, TX'.")
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()


class GeometrySchema(serializers.Serializer):
    type = serializers.ChoiceField(choices=["LineString"])
    coordinates = serializers.ListField(
        child=serializers.ListField(child=serializers.FloatField(), min_length=2, max_length=2),
        help_text="[longitude, latitude] pairs (GeoJSON order), roughly one per mile.",
    )


class RouteSchema(serializers.Serializer):
    distance_miles = serializers.FloatField()
    duration_hours = serializers.FloatField()
    geometry = GeometrySchema(help_text="GeoJSON LineString of the route; draw it on any map.")


class StationSchema(serializers.Serializer):
    opis_id = serializers.IntegerField()
    name = serializers.CharField()
    address = serializers.CharField()
    city = serializers.CharField()
    state = serializers.CharField()
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()


class FuelStopSchema(serializers.Serializer):
    stop_number = serializers.IntegerField()
    mile_marker = serializers.FloatField(help_text="Approximate distance from the start along the route.")
    distance_from_route_miles = serializers.FloatField()
    station = StationSchema()
    price_per_gallon = serializers.FloatField()
    gallons = serializers.FloatField(help_text="Gallons to buy at this stop.")
    cost_usd = serializers.FloatField()
    fuel_on_arrival_gallons = serializers.FloatField()


class FuelSchema(serializers.Serializer):
    total_cost_usd = serializers.FloatField(help_text="Money spent on fuel along the route.")
    total_gallons_purchased = serializers.FloatField()
    total_gallons_used = serializers.FloatField(help_text="Fuel burned over the whole trip (distance / mpg).")
    number_of_stops = serializers.IntegerField()
    stops = FuelStopSchema(many=True)


class VehicleSchema(serializers.Serializer):
    range_miles = serializers.FloatField()
    miles_per_gallon = serializers.FloatField()
    tank_capacity_gallons = serializers.FloatField()
    start_fuel_gallons = serializers.FloatField()


class MetaSchema(serializers.Serializer):
    routing_api_calls = serializers.IntegerField(help_text="1 on a fresh route, 0 when served from cache.")
    stations_along_route = serializers.IntegerField()
    elapsed_ms = serializers.FloatField()


class TripPlanResponseSchema(serializers.Serializer):
    start = LocationSchema()
    finish = LocationSchema()
    route = RouteSchema()
    fuel = FuelSchema()
    vehicle = VehicleSchema()
    assumptions = serializers.ListField(child=serializers.CharField())
    map_url = serializers.URLField(help_text="Interactive map of this trip (HTML).")
    meta = MetaSchema()


class ErrorDetailSchema(serializers.Serializer):
    code = serializers.CharField(help_text="Machine-readable error code, e.g. 'invalid_location'.")
    message = serializers.CharField()
    details = serializers.DictField()


class ErrorResponseSchema(serializers.Serializer):
    error = ErrorDetailSchema()


class HealthResponseSchema(serializers.Serializer):
    status = serializers.CharField()
    stations_loaded = serializers.IntegerField()
