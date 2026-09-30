from django.urls import path

from apps.routing.api.views import LivenessView, ReadinessView, TripMapView, TripPlanView

app_name = "routing"

urlpatterns = [
    path("route/", TripPlanView.as_view(), name="trip-plan"),
    path("route/map/", TripMapView.as_view(), name="trip-map"),
    path("health/", ReadinessView.as_view(), name="health"),
    path("health/live/", LivenessView.as_view(), name="health-live"),
    path("health/ready/", ReadinessView.as_view(), name="health-ready"),
]
