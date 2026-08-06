import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

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
        gold_dir, _ = fake_dirs
        pd.DataFrame({"player_name": ["A", "B"]}).to_csv(gold_dir / "top26_20260721_090000.csv", index=False)

        first = dl.load_latest_top26()
        first["player_name"] = "MUTATED"

        second = dl.load_latest_top26()
        assert list(second["player_name"]) == ["A", "B"], "cache must not leak mutations between calls"

    def test_source_file_attr_survives_caching(self, fake_dirs):
        gold_dir, _ = fake_dirs
        pd.DataFrame({"player_name": ["A"]}).to_csv(gold_dir / "top26_20260721_090000.csv", index=False)

        df = dl.load_latest_top26()
        assert df.attrs["source_file"] == "top26_20260721_090000.csv"


class TestCacheInvalidatesOnClear:

    def test_clearing_cache_picks_up_a_newer_file(self, fake_dirs):
        gold_dir, _ = fake_dirs
        pd.DataFrame({"player_name": ["A"]}).to_csv(gold_dir / "top26_20260721_090000.csv", index=False)

        first = dl.load_latest_top26()
        assert len(first) == 1

        pd.DataFrame({"player_name": ["A", "B", "C"]}).to_csv(gold_dir / "top26_20260722_090000.csv", index=False)

        still_cached = dl.load_latest_top26()
        assert len(still_cached) == 1, "should still be serving the cached (stale) value before invalidation"

        dl.load_latest_top26.clear()

        refreshed = dl.load_latest_top26()
        assert len(refreshed) == 3, "after cache clear, must read the new file, not the old cached one"

    def test_all_cached_loaders_expose_clear(self):
        for fn in _CACHED_LOADERS:
            assert hasattr(fn, "clear"), f"{fn.__name__} is missing @st.cache_data — spec #22 requires it"