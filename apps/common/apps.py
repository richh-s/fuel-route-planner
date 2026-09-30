from django.apps import AppConfig


class CommonConfig(AppConfig):
    name = "apps.common"
    label = "common"

    def ready(self):
        # Registers the deploy checks and the OpenAPI description of API-key authentication.
        from apps.common import auth, checks  # noqa: F401
