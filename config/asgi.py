import utils.net
import utils.clients
import utils.http_session
import utils.transport
import utils.session
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_asgi_application()
