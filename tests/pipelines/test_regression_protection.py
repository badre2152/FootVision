# tests/test_regression_protection.py
#
# Regression tests for 03_regression_protection.md ("Fixes Keep
# Disappearing" pattern). Confirmed evidence across sessions on this
# project:
#
#   1. `SquadRank` was fixed in run_gold.py's top26 export column list,
#      then found MISSING again in a later delivered archive — the code
#      had silently reverted to a local `top_cols` list without it.
#   2. run_sofifa.py's MinIO upload was fixed to a flat `results` list
#      (matching every other Bronze loader), then found reverted back to
#      the wrapped `{"run_date": ..., "players": results}` dict —
#      confirmed to zero out every sofifa_* column in Silver as a direct
#      consequence, with no exception raised anywhere.
#
# Neither regression was caught by the existing suite because no test
# asserted on either specific behavior. These tests assert the SPECIFIC
# previously-broken behavior, not just general smoke tests, per
# 03_regression_protection.md Functional Requirement #1.
#
# Run before every delivery:  MINIO_MOCK=1 pytest tests/ -q
# (MINIO_MOCK=1 avoids requiring a live MinIO/`minio` package at import
# time — see clients/minio_client.py. Not required for THIS file's tests
# specifically, since they import run_gold/run_sofifa which transitively
# import clients.minio_client, but is required for the suite as a whole.)

import sys
import os

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

os.environ.setdefault("MINIO_MOCK", "1")


# ─────────────────────────────────────────────────────────────────────────────
# Regression 1 — SquadRank missing from run_gold.py's top26 export columns
# ─────────────────────────────────────────────────────────────────────────────

class TestTopColsIncludesSquadRank:

    def test_squadrank_present_in_full_column_set(self):
        """
        THE regression test: with every possible sub-score column present,
        build_top_cols() must include "SquadRank". If someone reverts
        run_gold.py's TOP_EXPORT_COLS / build_top_cols() back to a local
        inline list that omits it (the exact way this broke before),
        this fails immediately without needing a full pipeline run.
        """
        from pipelines.gold import run_gold

        all_possible_cols = [
            "SquadRank", "Rank", "player_id", "player_name", "position",
            "club", "league", "DSSScore", "DSSScore_pct", "PerformanceScore",
            "FormScore", "MentalScore", "AvailabilityScore",
            "InternationalScore", "ChemistryScore", "PressureIndex",
            "FlexibilityScore", "SentimentScore", "PlayerDNA",
        ]
        top_cols = run_gold.build_top_cols(all_possible_cols)

        assert "SquadRank" in top_cols, (
            "SquadRank is missing from the top26 export columns. This is "
            "the exact regression documented in 03_regression_protection.md: "
            "SquadRank was confirmed fixed here before and reverted. Check "
            "run_gold.py::TOP_EXPORT_COLS."
        )
        # player_id must ALSO still be present — it was the other half of
        # the originally-confirmed fix and must not regress alongside it.
        assert "player_id" in top_cols

    def test_squadrank_is_gracefully_dropped_when_absent_from_squad_df(self):
        """
        build_top_cols() must not crash or fabricate SquadRank if the
        caller's DataFrame genuinely doesn't have it (defensive contract
        for the filter step) — it should simply be excluded, same as any
        other optional column.
        """
        from pipelines.gold import run_gold

        cols_without_squadrank = [
            "Rank", "player_id", "player_name", "position", "DSSScore",
        ]
        top_cols = run_gold.build_top_cols(cols_without_squadrank)
        assert "SquadRank" not in top_cols
        assert "Rank" in top_cols and "player_id" in top_cols

    def test_squadrank_actually_reaches_the_exported_top26_dataframe(self):
        """
        End-to-end (still no I/O) check that the column ends up selectable
        on a squad_df shaped like select_squad_with_position_cap()'s real
        output — i.e. this isn't just a list-membership test, the column
        is actually usable for df[top_cols].
        """
        from pipelines.gold import run_gold

        squad_df = pd.DataFrame({
            "SquadRank":   [1, 2, 3],
            "Rank":        [4, 1, 9],
            "player_id":   ["P001", "P002", "P003"],
            "player_name": ["A", "B", "C"],
            "position":    ["GK", "DEF", "MID"],
            "DSSScore":    [0.9, 0.8, 0.7],
        })
        top_cols = run_gold.build_top_cols(squad_df.columns)
        exported = squad_df[top_cols]

        assert "SquadRank" in exported.columns
        assert list(exported["SquadRank"]) == [1, 2, 3]


# ─────────────────────────────────────────────────────────────────────────────
# Regression 2 — run_sofifa.py MinIO upload shape (flat list, not wrapped dict)
# ─────────────────────────────────────────────────────────────────────────────

class TestSofifaUploadFormat:

    def _sample_results(self):
        return [
            {"player_id": "P003", "player_name": "Yassine Bounou", "sofifa_composure": 88},
            {"player_id": "P007", "player_name": "Achraf Hakimi", "sofifa_composure": 85},
        ]

    def test_upload_payload_is_a_flat_list_not_a_wrapped_dict(self):
        """
        THE regression test: build_minio_upload_payload() must return the
        flat per-player list unchanged — never wrap it in
        {"run_date": ..., "players": [...]}. If someone reverts this back
        to uploading the wrapped `payload` dict (the exact previous
        regression), this fails immediately.
        """
        from pipelines.bronze import run_sofifa

        results = self._sample_results()
        uploaded = run_sofifa.build_minio_upload_payload(results)

        assert isinstance(uploaded, list), (
            "MinIO upload payload must be a flat list of player dicts, got "
            f"{type(uploaded).__name__} — this is the wrapped-payload "
            "regression from 03_regression_protection.md."
        )
        assert uploaded == results
        assert not isinstance(uploaded, dict)

    def test_wrapped_payload_would_have_no_player_id_column(self):
        """
        Proves WHY the wrapped shape is broken, by replicating exactly
        what ingestion/silver_pipeline.py::join_all_bronze_sources()._read()
        does with whatever JSON comes back from MinIO:
            df = pd.DataFrame(records)
            if "player_id" not in df.columns: <merge skipped, source silently dropped>

        A flat list produces a `player_id` column (one row per player).
        The previously-reverted wrapped dict {"run_date": ..., "players": [...]}
        instead produces a DataFrame built from the dict's own top-level
        keys ("run_date", "players") — no `player_id` column exists, so
        the merge is silently skipped and 0 sofifa_* columns reach Gold,
        with no exception raised anywhere in the pipeline.
        """
        results = self._sample_results()
        run_date = "20260801_000000"

        # Correct (flat) shape — what build_minio_upload_payload() produces
        flat_df = pd.DataFrame(results)
        assert "player_id" in flat_df.columns
        assert len(flat_df) == len(results)

        # Broken (wrapped) shape — what the reverted code used to upload
        wrapped_payload = {"run_date": run_date, "players": results}
        wrapped_df = pd.DataFrame(wrapped_payload)
        assert "player_id" not in wrapped_df.columns, (
            "If this assertion fails, pandas' dict-to-DataFrame behavior "
            "changed and the original bug analysis needs re-checking."
        )

    def test_main_uploads_the_flat_results_not_the_local_file_payload(self, monkeypatch, tmp_path):
        """
        Confirms main()'s actual call site passes build_minio_upload_payload(results)
        to upload_json() — not the wrapped `payload` local-file variable —
        without driving a real Playwright browser through Cloudflare.
        Mocks Playwright + the seed CSV + upload_json, and captures exactly
        what object was handed to upload_json().
        """
        import importlib
        import types

        from pipelines.bronze import run_sofifa

        # Minimal seed CSV so main() has something to iterate.
        seed_csv = tmp_path / "players_49.csv"
        seed_csv.write_text("player_id,player_name,position\nP003,Yassine Bounou,GK\n")

        captured = {}

        def fake_upload_json(bucket, object_name, data):
            captured["bucket"] = bucket
            captured["object_name"] = object_name
            captured["data"] = data
            return True

        # Patch the MinIO client function at its source module, since
        # run_sofifa.py imports it lazily inside main() via
        # `from clients.minio_client import upload_json`.
        import clients.minio_client as minio_client_mod
        monkeypatch.setattr(minio_client_mod, "upload_json", fake_upload_json)

        # Patch Playwright's sync_playwright so no real browser launches.
        class _FakePage:
            def goto(self, *a, **kw): pass
            def title(self): return "Yassine Bounou FC 26"
            def content(self): return "<html>" + "x" * 60000 + "</html>"

        class _FakeBrowser:
            def new_page(self): return _FakePage()
            def close(self): pass

        class _FakeChromium:
            def launch(self, headless=False): return _FakeBrowser()

        class _FakePlaywrightCtx:
            def __enter__(self):
                ns = types.SimpleNamespace(chromium=_FakeChromium())
                return ns
            def __exit__(self, *a): return False

        monkeypatch.setattr(run_sofifa, "sync_playwright", lambda: _FakePlaywrightCtx())

        class _FakeStealth:
            def use_sync(self, page): pass

        monkeypatch.setattr(run_sofifa, "Stealth", lambda: _FakeStealth())

        # fetch_attrs would try to parse real SoFIFA HTML; force it to
        # return a minimal-but-nonempty attrs dict so main() proceeds past
        # the "no attributes" early-continue branch.
        monkeypatch.setattr(
            run_sofifa, "fetch_attrs",
            lambda page, sofifa_id: {"sofifa_id": sofifa_id, "sofifa_composure": 88},
        )

        monkeypatch.chdir(tmp_path)
        argv_backup = sys.argv
        sys.argv = [
            "run_sofifa.py",
            "--seed", str(seed_csv),
            "--delay", "0",
        ]
        try:
            run_sofifa.main()
        finally:
            sys.argv = argv_backup

        assert "data" in captured, "upload_json() was never called by main()."
        assert isinstance(captured["data"], list), (
            "main() uploaded a non-list payload to MinIO — the "
            "wrapped-payload regression has returned in the actual call "
            "site, even if build_minio_upload_payload() itself is correct."
        )
        assert all("player_id" in r for r in captured["data"] if r)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
