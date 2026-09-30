"""Redis cache backend that degrades instead of failing requests.

The cache holds routes and rate-limit counters: both are optimisations. If
Redis is down we would rather plan the trip with an extra routing call (and
without rate limiting) than answer every request with a 500. Errors are
logged and counted so the outage is still visible.
"""

import logging
import time

from django.core.cache.backends.redis import RedisCache
from redis.exceptions import RedisError

from apps.common import metrics

logger = logging.getLogger(__name__)

_ERRORS = (RedisError, OSError)


class FailOpenRedisCache(RedisCache):
    # After a failure, skip Redis entirely for this long. A request makes several cache calls; without
    # this, a Redis that hangs (rather than refuses) would cost every one of them a full socket timeout.
    RETRY_AFTER_SECONDS = 5.0
    # Shared by all threads of the process (Django creates one backend instance per thread).
    _skip_until = 0.0

    def _attempt(self, operation: str, fallback, method, *args, **kwargs):
        if time.monotonic() < FailOpenRedisCache._skip_until:
            return fallback
        try:
            return method(*args, **kwargs)
        except _ERRORS as exc:
            FailOpenRedisCache._skip_until = time.monotonic() + self.RETRY_AFTER_SECONDS
            metrics.CACHE_ERRORS.labels(operation=operation).inc()
            logger.warning(
                "Cache %s failed; serving without cache for %.0fs: %s", operation, self.RETRY_AFTER_SECONDS, exc
            )
            return fallback

    def get(self, key, default=None, version=None):
        return self._attempt("get", default, super().get, key, default, version)

    def get_many(self, keys, version=None):
        return self._attempt("get_many", {}, super().get_many, keys, version)

    def set(self, *args, **kwargs):
        self._attempt("set", None, super().set, *args, **kwargs)

    def add(self, *args, **kwargs):
        # Callers use add() as a lock; with no cache there is nothing to coordinate with, so proceed.
        return self._attempt("add", True, super().add, *args, **kwargs)

    def delete(self, *args, **kwargs):
        return self._attempt("delete", False, super().delete, *args, **kwargs)

    def touch(self, *args, **kwargs):
        return self._attempt("touch", False, super().touch, *args, **kwargs)

    def ping(self) -> bool:
        """True when Redis answers. Used by the readiness check; never raises."""
        return bool(self._attempt("ping", False, lambda: self._cache.get_client(write=True).ping()))
