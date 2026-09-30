from django.conf import settings
from rest_framework import serializers

from apps.routing.services.trip_planner import TripRequest


class TripRequestSerializer(serializers.Serializer):
    start = serializers.CharField(max_length=200, help_text="'City, ST' or 'lat,lng' inside the USA.")
    finish = serializers.CharField(max_length=200, help_text="'City, ST' or 'lat,lng' inside the USA.")
    corridor_miles = serializers.FloatField(
        required=False,
        min_value=0.5,
        help_text="Max distance a station may be from the route. Defaults to the server setting.",
    )
    start_fuel_gallons = serializers.FloatField(
        required=False,
        min_value=0,
        help_text="Fuel already in the tank at departure. Defaults to 0, so the total cost covers all fuel "
        "for the trip and the first fuel-up is at the start.",
    )

    def validate_corridor_miles(self, value):
        maximum = settings.FUEL_ROUTE["MAX_CORRIDOR_MILES"]
        if value > maximum:
            raise serializers.ValidationError(f"Must be at most {maximum:g} miles.")
        return value

    def validate_start_fuel_gallons(self, value):
        config = settings.FUEL_ROUTE
        tank = config["VEHICLE_RANGE_MILES"] / config["VEHICLE_MPG"]
        if value > tank:
            raise serializers.ValidationError(f"Must be at most the tank capacity ({tank:g} gallons).")
        return value

    def to_trip_request(self) -> TripRequest:
        data = self.validated_data
        return TripRequest(
            start=data["start"],
            finish=data["finish"],
            corridor_miles=data.get("corridor_miles", settings.FUEL_ROUTE["DEFAULT_CORRIDOR_MILES"]),
            start_fuel_gallons=data.get("start_fuel_gallons"),
        )
