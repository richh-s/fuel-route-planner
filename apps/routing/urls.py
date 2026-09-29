from django.urls import path

from apps.routing.api.views import HealthView, TripMapView, TripPlanView

app_name = "routing"

urlpatterns = [
    path("route/", TripPlanView.as_view(), name="trip-plan"),
    path("route/map/", TripMapView.as_view(), name="trip-map"),
    path("health/", HealthView.as_view(), name="health"),
]
