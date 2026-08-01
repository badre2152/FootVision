# tests/test_dashboard_caching.py
#
# Regression tests for 22_dashboard_caching.md.
#
# Proves the two things the spec's Acceptance Criteria actually care
# about: (1) a second call within the TTL window does NOT re-read disk,
# and (2) clearing the cache (the same mechanism a TTL expiry uses
# internally) picks up a newer file — i.e. caching is real and
# invalidation isn't silently broken.
#
# Does NOT try to test the ttl=300 wall-clock expiry itself (waiting 5
# real minutes in a test suite is a bad idea) — that gap is called out
# explicitly below, matching this project's "don't claim more than what
# was actually verified" convention (see CHANGELOG.md's "Not verified
# end-to-end" notes on other specs).

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard.data_loader as dl

_CACHED_LOADERS = [
    dl.load_latest_top26,
    dl.load_latest_players_scored,
    dl.load_latest_squad_summary,
    dl.load_latest_selection_audit,
    dl.load_latest_injury_predictor,
    dl.load_latest_chemistry_pairs,
    dl.load_all_players_scored_history,
]


@pytest.fixture(autouse=True)
def _clear_all_caches_before_and_after():
    """Every test in this file starts and ends with a clean cache slate —
    this file is specifically ABOUT cache state, so leakage between tests
    here would be especially misleading."""
    for fn in _CACHED_LOADERS:
        fn.clear()
    yield
    for fn in _CACHED_LOADERS:
        fn.clear()


@pytest.fixture
def fake_dirs(tmp_path, monkeypatch):
    gold_dir = tmp_path / "gold"
    bronze_dir = tmp_path / "bronze"
    gold_dir.mkdir()
    bronze_dir.mkdir()
    monkeypatch.setattr(dl, "GOLD_DIR", gold_dir)
    monkeypatch.setattr(dl, "BRONZE_DIR", bronze_dir)
    return gold_dir, bronze_dir


class TestCacheAvoidsRedundantDiskReads:

    def test_second_call_does_not_reread_disk(self, fake_dirs, monkeypatch):
        """Core acceptance criterion: repeated calls within the TTL window
        must not hit disk again. Verified the same way the spec suggests
        ('add a temporary log line ... confirm it's NOT called on every
        single widget interaction') — except here we replace pd.read_csv
        itself with a call counter instead of grepping logs."""
        gold_dir, _ = fake_dirs
        pd.DataFrame({"player_name": ["A", "B"]}).to_csv(gold_dir / "top26_20260721_090000.csv", index=False)

        call_count = {"n": 0}
        real_read_csv = pd.read_csv

        def counting_read_csv(*args, **kwargs):
            call_count["n"] += 1
            return real_read_csv(*args, **kwargs)

        monkeypatch.setattr(pd, "read_csv", counting_read_csv)

        first = dl.load_latest_top26()
        second = dl.load_latest_top26()
        third = dl.load_latest_top26()

        assert call_count["n"] == 1, "disk should only be read once across 3 calls within the TTL window"
        assert len(first) == len(second) == len(third) == 2

    def test_cached_dataframe_copy_is_independent_per_call(self, fake_dirs):
        """st.cache_data returns a fresh copy each call (not the same
        object) — a caller mutating their copy must not corrupt what the
        next caller gets back."""
        gold_dir, _ = fake_dirs
        pd.DataFrame({"player_name": ["A", "B"]}).to_csv(gold_dir / "top26_20260721_090000.csv", index=False)

        first = dl.load_latest_top26()
        first["player_name"] = "MUTATED"

        second = dl.load_latest_top26()
        assert list(second["player_name"]) == ["A", "B"], "cache must not leak mutations between calls"

    def test_source_file_attr_survives_caching(self, fake_dirs):
        """df.attrs (used for the source_file tag every loader sets) must
        survive Streamlit's cache copy — confirmed manually to work via
        Streamlit's own copy mechanism, this pins that behavior."""
        gold_dir, _ = fake_dirs
        pd.DataFrame({"player_name": ["A"]}).to_csv(gold_dir / "top26_20260721_090000.csv", index=False)

        df = dl.load_latest_top26()
        assert df.attrs["source_file"] == "top26_20260721_090000.csv"


class TestCacheInvalidatesOnClear:

    def test_clearing_cache_picks_up_a_newer_file(self, fake_dirs):
        """Models what a TTL expiry does internally: once the cache entry
        is gone, the next call re-reads disk and picks up whatever's
        newest THEN — proving invalidation actually works, not just that
        caching exists. The spec's real ttl=300 wall-clock expiry itself
        is NOT re-tested here (see module docstring) — this test instead
        pins the underlying mechanism (.clear() forces a fresh read),
        which is exactly what TTL expiry triggers once 5 minutes pass."""
        gold_dir, _ = fake_dirs
        pd.DataFrame({"player_name": ["A"]}).to_csv(gold_dir / "top26_20260721_090000.csv", index=False)

        first = dl.load_latest_top26()
        assert len(first) == 1

        # Simulate a fresh run_gold.py producing a newer snapshot.
        pd.DataFrame({"player_name": ["A", "B", "C"]}).to_csv(gold_dir / "top26_20260722_090000.csv", index=False)

        # Without clearing, still-cached stale result (proves cache is real).
        still_cached = dl.load_latest_top26()
        assert len(still_cached) == 1, "should still be serving the cached (stale) value before invalidation"

        dl.load_latest_top26.clear()

        refreshed = dl.load_latest_top26()
        assert len(refreshed) == 3, "after cache clear, must read the new file, not the old cached one"

    def test_all_cached_loaders_expose_clear(self):
        """Every loader wrapped for spec #22 must actually be a
        st.cache_data-decorated function (i.e. has .clear()) — catches
        the decorator silently being dropped in a future edit."""
        for fn in _CACHED_LOADERS:
            assert hasattr(fn, "clear"), f"{fn.__name__} is missing @st.cache_data — spec #22 requires it"


# ─────────────────────────────────────────────────────────────────────────
# NOT covered here (documented per this project's "don't overclaim"
# convention — see CHANGELOG.md):
#   - The real ttl=300 (5-minute) wall-clock expiry itself. Testing that
#     for real means either waiting 5 minutes (too slow for a test suite)
#     or monkeypatching Streamlit's internal cache-timing implementation
#     (too coupled to Streamlit internals to be a stable test). The
#     invalidation MECHANISM is proven above (test_clearing_cache_picks_
#     up_a_newer_file) — TTL expiry uses that same mechanism internally,
#     just triggered by a clock instead of an explicit .clear() call.
#   - Behavior inside an actual running Streamlit app/browser session
#     (multiple users, real widget reruns) — needs a live
#     `streamlit run dashboard/app.py` session to verify per the spec's
#     own Acceptance Criteria ("verify via a quick manual check").
# ─────────────────────────────────────────────────────────────────────────
