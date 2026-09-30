"""Optional API-key access control.

With no keys configured (`API_KEYS` empty) the API is open, as in local
development. Once keys are configured, trip endpoints require one, sent as
`X-API-Key: <key>` or `Authorization: Bearer <key>`, and rate limits are
counted per key instead of per IP.
"""

import hashlib
import hmac

from django.conf import settings
from django.core import signing
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import BasePermission
from rest_framework.throttling import ScopedRateThrottle

_MAP_LINK_SALT = "routing.trip-map"
_MAP_LINK_FIELDS = ("start", "finish", "corridor_miles", "start_fuel_gallons")


def _key_id(key: str) -> str:
    """Stable, non-reversible identifier for a key: safe to use in cache keys and logs."""
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _matches_configured_key(candidate: str) -> bool:
    # Compare against every key (no early exit) so timing does not reveal which one matched.
    matched = False
    for key in settings.API_KEYS:
        matched |= hmac.compare_digest(candidate.encode(), key.encode())
    return matched


class APIKeyAuthentication(BaseAuthentication):
    keyword = "Bearer"

    def authenticate(self, request):
        supplied = request.headers.get("x-api-key", "").strip()
        if not supplied:
            scheme, _, value = request.headers.get("authorization", "").partition(" ")
            if scheme.lower() == self.keyword.lower():
                supplied = value.strip()
        if not supplied or not settings.API_KEYS:
            return None
        if not _matches_configured_key(supplied):
            raise AuthenticationFailed("Invalid API key.")
        return None, _key_id(supplied)

    def authenticate_header(self, request):
        return self.keyword


class APIKeyRequired(BasePermission):
    """Allows everything when no keys are configured; otherwise requires a valid key."""

    message = "A valid API key is required. Send it in the X-API-Key header."

    def has_permission(self, request, view):
        return not settings.API_KEYS or bool(request.auth)


class ClientRateThrottle(ScopedRateThrottle):
    """Scoped rate limit counted per API key when one is used, otherwise per client IP."""

    def get_cache_key(self, request, view):
        key_id = getattr(request, "auth", None)
        ident = f"key:{key_id}" if key_id else self.get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}


class APIKeyScheme(OpenApiAuthenticationExtension):
    target_class = "apps.common.auth.APIKeyAuthentication"
    name = "ApiKey"

    def get_security_definition(self, auto_schema):
        return {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
            "description": "Required only when the server is configured with API keys.",
        }


# The map page is opened in a browser, which cannot send an API key header. When keys are enforced,
# the API hands out map links signed with the secret key; the map view accepts those instead.


def _canonical(params) -> str:
    return "&".join(f"{name}={params[name]}" for name in _MAP_LINK_FIELDS if params.get(name) not in (None, ""))


def sign_map_params(params) -> str:
    """Return the `timestamp:signature` suffix that authorises a map link for these parameters."""
    canonical = _canonical(params)
    return signing.TimestampSigner(salt=_MAP_LINK_SALT).sign(canonical)[len(canonical) + 1 :]


def map_signature_is_valid(params, signature: str) -> bool:
    signed = f"{_canonical(params)}:{signature}"
    try:
        signing.TimestampSigner(salt=_MAP_LINK_SALT).unsign(signed, max_age=settings.MAP_LINK_MAX_AGE_SECONDS)
    except signing.BadSignature:
        return False
    return True
