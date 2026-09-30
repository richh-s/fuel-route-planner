from unittest import mock

from django.test import SimpleTestCase
from redis.exceptions import ConnectionError as RedisConnectionError

from apps.common.cache import FailOpenRedisCache


class FailOpenRedisCacheTests(SimpleTestCase):
    """Nothing listens on this port, so every operation hits a connection error."""

    def setUp(self):
        options = {"socket_connect_timeout": 0.2, "socket_timeout": 0.2}
        self.cache = FailOpenRedisCache("redis://127.0.0.1:1/0", {"OPTIONS": options})
        self.reset_breaker()
        self.addCleanup(self.reset_breaker)

    @staticmethod
    def reset_breaker():
        FailOpenRedisCache._skip_until = 0.0

    def test_reads_behave_like_a_miss(self):
        with self.assertLogs("apps.common.cache", level="WARNING"):
            self.assertEqual(self.cache.get("route", "fallback"), "fallback")
        self.assertIsNone(self.cache.get("route"))
        self.assertEqual(self.cache.get_many(["a", "b"]), {})

    def test_writes_are_skipped_without_raising(self):
        with self.assertLogs("apps.common.cache", level="WARNING"):
            self.cache.set("route", 1, 60)
        self.assertFalse(self.cache.delete("route"))
        self.assertFalse(self.cache.touch("route", 60))

    def test_add_reports_success_so_lock_users_proceed(self):
        with self.assertLogs("apps.common.cache", level="WARNING"):
            self.assertTrue(self.cache.add("route:lock", 1, 20))

    def test_ping_reports_the_outage(self):
        with self.assertLogs("apps.common.cache", level="WARNING"):
            self.assertFalse(self.cache.ping())

    def test_redis_is_left_alone_for_a_while_after_a_failure(self):
        target = "django.core.cache.backends.redis.RedisCache.get"
        with mock.patch(target, side_effect=RedisConnectionError("down")) as redis_get:
            with self.assertLogs("apps.common.cache", level="WARNING") as captured:
                for _ in range(5):
                    self.cache.get("route")
            self.assertEqual(redis_get.call_count, 1)
            self.assertEqual(len(captured.records), 1)

            self.reset_breaker()  # the pause is over
            with self.assertLogs("apps.common.cache", level="WARNING"):
                self.cache.get("route")
            self.assertEqual(redis_get.call_count, 2)

    def test_recovers_when_redis_is_back(self):
        target = "django.core.cache.backends.redis.RedisCache.get"
        with mock.patch(target, side_effect=RedisConnectionError("down")), self.assertLogs("apps.common.cache"):
            self.cache.get("route")
        self.reset_breaker()
        with mock.patch(target, return_value="cached route"):
            self.assertEqual(self.cache.get("route"), "cached route")

    def test_errors_are_counted(self):
        with (
            mock.patch("apps.common.cache.metrics.CACHE_ERRORS") as counter,
            mock.patch("django.core.cache.backends.redis.RedisCache.get", side_effect=RedisConnectionError("down")),
            self.assertLogs("apps.common.cache", level="WARNING"),
        ):
            self.cache.get("route")
        counter.labels.assert_called_once_with(operation="get")
