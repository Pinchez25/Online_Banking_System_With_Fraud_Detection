from unittest import mock

import fakeredis
from django.test.runner import DiscoverRunner


class TestRunner(DiscoverRunner):
    """Runs the suite against an in-process Redis for fraud profiles."""

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        self._profile_redis = mock.patch(
            "fraud.profiles.get_profile_redis", return_value=fakeredis.FakeRedis(decode_responses=True),
        )
        self._profile_redis.start()

    def teardown_test_environment(self, **kwargs):
        self._profile_redis.stop()
        super().teardown_test_environment(**kwargs)
