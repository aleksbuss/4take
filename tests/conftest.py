import sys

import pytest

from fourtake.rate_limiter import PersistentTokenBucket


def pytest_collection_modifyitems(items):
    """Skip, with the reason given, what a test checks and Windows does not have."""
    if sys.platform != "win32":
        return
    for item in items:
        marker = item.get_closest_marker("posix_only")
        if marker:
            item.add_marker(pytest.mark.skip(reason=f"POSIX only, {marker.args[0]}"))


@pytest.fixture(autouse=True)
def isolate_test_cache(tmp_path, monkeypatch):
    """Isolate review cache and rate limiter state file to a temporary directory during tests."""
    import fourtake.cache

    fourtake.cache._orig_get_cache_dir = fourtake.cache.get_cache_dir
    monkeypatch.setattr("fourtake.cache.get_cache_dir", lambda: tmp_path / "reviews")
    (tmp_path / "reviews").mkdir(parents=True, exist_ok=True)

    # Telemetry is written on every real run, so without this the suite would append to the
    # developer's own ~/.cache/4take/stats.json and then rank models from it.
    monkeypatch.setattr("fourtake.telemetry.stats_path", lambda: tmp_path / "stats.json")

    # A key for every test. `main()` stops before dispatch without one and CI has none, so the
    # tests that drive the CLI end to end passed only on machines with a key configured — nine
    # of them failed on a clean checkout — and on those machines anything left unmocked could
    # spend the real key. The environment wins over the config file, so this also keeps the
    # developer's own key out of the suite. Tests about a missing key build their own config.
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test-suite-not-a-real-key")
    monkeypatch.delenv("FOURTAKE_API_KEY", raising=False)

    # The free-model allowance is read from OpenRouter, and a suite that asked would make a real
    # request with a fake key on every run that gets as far as a council. "Unknown" is what the
    # production code does with doubt, so it is the default; tests of the check bring their own HTTP.
    async def no_quota(*args, **kwargs):
        return None

    monkeypatch.setattr("fourtake.preflight.fetch_free_quota", no_quota)
    monkeypatch.setattr("fourtake.quota.fetch_free_quota", no_quota)

    orig_init = PersistentTokenBucket.__init__

    def patched_init(self, rpm=10):
        orig_init(self, rpm=rpm)
        self.state_file = tmp_path / "bucket.json"

    monkeypatch.setattr(PersistentTokenBucket, "__init__", patched_init)
