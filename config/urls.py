from django.conf import settings
from django.urls import include, path
from django.views.decorators.csp import csp_override
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

from apps.common.metrics import metrics_view

docs_csp = csp_override(settings.DOCS_CSP)

urlpatterns = [
    path("api/v1/", include("apps.routing.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", docs_csp(SpectacularSwaggerView.as_view(url_name="schema")), name="swagger-ui"),
    path("api/redoc/", docs_csp(SpectacularRedocView.as_view(url_name="schema")), name="redoc"),
    path("metrics", metrics_view, name="metrics"),
]

if settings.ADMIN_ENABLED:
    from django.contrib import admin

    urlpatterns.insert(0, path("admin/", admin.site.urls))
