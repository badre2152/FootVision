# tests/pipelines/test_run_gold_main_wiring.py
#
# Added alongside the C4 refactor (pipelines/gold/run_gold.py::main() split
# from one ~220-line function into named stage functions — see that file's
# module docstring). Before the split, testing that the pipeline stages ran
# in the right order with the right data flowing between them required
# running the ENTIRE real pipeline (Silver from real/mocked Bronze, real
# scoring, etc.) — impractical to do as a fast, isolated unit test, so
# nothing tested this "wiring" directly; only individual pieces of logic
# were unit-tested (see test_gold2_components.py) and the ordering
# constraint documented in _apply_final_scoring()'s docstring (BUG-01) was
# enforced by a comment, not a test.
#
# This test mocks every stage function and asserts:
#   1. They are called in the documented order.
#   2. Each stage receives the DataFrame returned by the PREVIOUS stage
#      (not, say, the original unscored df, which was the shape of a past
#      real bug class in pipelines like this one).
#   3. args flows through to every stage that declares it needs args.
#
# No real Silver/Bronze/MinIO/network access — everything below main() is
# mocked, so this runs in milliseconds and needs nothing but MINIO_MOCK=1's
# usual test-mode assumptions (ensure_minio_buckets is itself mocked here).

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pipelines.gold import run_gold


class _FakeArgs:
    """Minimal stand-in for argparse.Namespace with just what main() reads directly."""
    output_dir = "gold"
    top_n = 26


@pytest.fixture
def call_log():
    return []


def _tag(df: pd.DataFrame, tag: str) -> pd.DataFrame:
    """Returns a NEW DataFrame carrying `tag` so later assertions can prove
    which stage's output actually reached the next stage (a plain `df` copy
    with no traceable identity wouldn't catch a wiring mistake like passing
    the wrong variable)."""
    out = df.copy()
    out["_stage"] = tag
    return out


def test_main_calls_stages_in_order_with_correct_data_flow(monkeypatch, call_log, tmp_path):
    seed_df = pd.DataFrame({"player_id": ["P001"], "player_name": ["Test Player"], "position": ["GK"]})

    monkeypatch.setattr(run_gold, "parse_args", lambda: _FakeArgs())
    monkeypatch.setattr(run_gold, "ensure_minio_buckets", lambda: call_log.append("ensure_minio_buckets"))

    def fake_build_silver(run_date, seed_path):
        call_log.append("build_silver")
        return _tag(seed_df, "silver")
    monkeypatch.setattr(run_gold, "build_silver", fake_build_silver)

    def fake_normalize_all(df):
        call_log.append(("normalize_all", df["_stage"].iloc[0]))
        return _tag(df, "normalized")
    monkeypatch.setattr(run_gold, "normalize_all", fake_normalize_all)

    def fake_enrich(df, args):
        call_log.append(("_enrich_with_clubelo", df["_stage"].iloc[0], args is not None))
        # real _enrich_with_clubelo doesn't mutate/return df — matches that.
    monkeypatch.setattr(run_gold, "_enrich_with_clubelo", fake_enrich)

    def fake_subscores(df, args):
        call_log.append(("_compute_all_subscores", df["_stage"].iloc[0]))
        return _tag(df, "subscored")
    monkeypatch.setattr(run_gold, "_compute_all_subscores", fake_subscores)

    def fake_final_scoring(df):
        call_log.append(("_apply_final_scoring", df["_stage"].iloc[0]))
        return _tag(df, "final_scored")
    monkeypatch.setattr(run_gold, "_apply_final_scoring", fake_final_scoring)

    def fake_player_dna(df, args):
        call_log.append(("_run_player_dna", df["_stage"].iloc[0]))
        return _tag(df, "dna"), {"0": "Archetype X"}, 3
    monkeypatch.setattr(run_gold, "_run_player_dna", fake_player_dna)

    def fake_export_full(df, output_dir, run_date):
        call_log.append(("_export_full_table", df["_stage"].iloc[0]))
        return tmp_path / "players_scored.csv"
    monkeypatch.setattr(run_gold, "_export_full_table", fake_export_full)

    def fake_select_and_export(df, args, output_dir, run_date):
        call_log.append(("_select_and_export_squad", df["_stage"].iloc[0]))
        return _tag(df, "squad"), ["Rank", "player_name"]
    monkeypatch.setattr(run_gold, "_select_and_export_squad", fake_select_and_export)

    def fake_build_summary(squad_df, df, args, output_dir, run_date, dna_archetype_map, dna_k):
        call_log.append((
            "_build_and_export_summary",
            squad_df["_stage"].iloc[0], df["_stage"].iloc[0],
            dna_archetype_map, dna_k,
        ))
        return {"squad_size": 26, "position_counts": {}}
    monkeypatch.setattr(run_gold, "_build_and_export_summary", fake_build_summary)

    def fake_guard_rails(summary, args):
        call_log.append(("_check_guard_rails", summary))
    monkeypatch.setattr(run_gold, "_check_guard_rails", fake_guard_rails)

    def fake_print_top(top_df, top_cols, args):
        call_log.append(("_print_top_squad", top_df["_stage"].iloc[0], top_cols))
    monkeypatch.setattr(run_gold, "_print_top_squad", fake_print_top)

    run_gold.main()

    assert call_log == [
        "ensure_minio_buckets",
        "build_silver",
        ("normalize_all", "silver"),
        ("_enrich_with_clubelo", "normalized", True),
        ("_compute_all_subscores", "normalized"),
        ("_apply_final_scoring", "subscored"),
        ("_run_player_dna", "final_scored"),
        ("_export_full_table", "dna"),
        ("_select_and_export_squad", "dna"),
        ("_build_and_export_summary", "squad", "dna", {"0": "Archetype X"}, 3),
        ("_check_guard_rails", {"squad_size": 26, "position_counts": {}}),
        ("_print_top_squad", "squad", ["Rank", "player_name"]),
    ], (
        "main()'s stage order or the data threaded between stages doesn't "
        "match what pipelines/gold/run_gold.py::main() actually does — if "
        "this fails after editing main(), a stage is being called out of "
        "order, skipped, or fed the wrong DataFrame."
    )


def test_main_passes_squad_df_not_full_df_to_print_top_squad(monkeypatch, tmp_path):
    """
    Narrower, more readable companion to the full-log test above: pins the
    specific thing build_squad_summary()'s own docstring warns is easy to
    get backwards (using the capped squad_df vs. the full candidate df) —
    here for the console-printed top-N table rather than the summary JSON.
    """
    seed_df = pd.DataFrame({"player_id": ["P001"], "player_name": ["X"], "position": ["GK"]})

    monkeypatch.setattr(run_gold, "parse_args", lambda: _FakeArgs())
    monkeypatch.setattr(run_gold, "ensure_minio_buckets", lambda: None)
    monkeypatch.setattr(run_gold, "build_silver", lambda run_date, seed_path: seed_df.copy())
    monkeypatch.setattr(run_gold, "normalize_all", lambda df: df)
    monkeypatch.setattr(run_gold, "_enrich_with_clubelo", lambda df, args: None)
    monkeypatch.setattr(run_gold, "_compute_all_subscores", lambda df, args: df)
    monkeypatch.setattr(run_gold, "_apply_final_scoring", lambda df: df)
    monkeypatch.setattr(run_gold, "_run_player_dna", lambda df, args: (df, {}, None))
    monkeypatch.setattr(run_gold, "_export_full_table", lambda df, output_dir, run_date: tmp_path / "f.csv")

    fake_squad_df = seed_df.assign(_is_squad_df=True)
    monkeypatch.setattr(
        run_gold, "_select_and_export_squad",
        lambda df, args, output_dir, run_date: (fake_squad_df, ["player_name"]),
    )
    monkeypatch.setattr(
        run_gold, "_build_and_export_summary",
        lambda squad_df, df, args, output_dir, run_date, dna_map, dna_k: {"squad_size": 26, "position_counts": {}},
    )
    monkeypatch.setattr(run_gold, "_check_guard_rails", lambda summary, args: None)

    captured = {}
    def fake_print_top(top_df, top_cols, args):
        captured["is_squad_df"] = "_is_squad_df" in top_df.columns
    monkeypatch.setattr(run_gold, "_print_top_squad", fake_print_top)

    run_gold.main()

    assert captured["is_squad_df"] is True, (
        "_print_top_squad() must receive the position-capped squad_df from "
        "_select_and_export_squad(), not the full candidate df."
    )
