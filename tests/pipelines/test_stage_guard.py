import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pipelines._stage_guard import StageGuard


class TestStageGuard:

    def test_runs_stage_with_no_dependencies(self):
        guard = StageGuard()
        result = guard.run("a", lambda x: x + 1, 5)
        assert result == 6
        assert guard.has_run("a")

    def test_runs_stage_whose_dependency_already_ran(self):
        guard = StageGuard()
        guard.run("a", lambda: None)
        result = guard.run("b", lambda x: x * 2, 10, depends_on=("a",))
        assert result == 20
        assert guard.has_run("b")

    def test_raises_when_dependency_has_not_run(self):
        guard = StageGuard()
        with pytest.raises(AssertionError, match=r"minutes_filter"):
            guard.run("dss_gate", lambda x: x, 1, depends_on=("minutes_filter",))

    def test_raises_when_dependency_declared_but_never_run_even_after_other_stages(self):
        guard = StageGuard()
        guard.run("unrelated_stage", lambda: None)
        with pytest.raises(AssertionError):
            guard.run("dss_gate", lambda x: x, 1, depends_on=("final_score",))

    def test_multiple_missing_dependencies_all_named_in_error(self):
        guard = StageGuard()
        with pytest.raises(AssertionError, match=r"minutes_filter.*final_score|final_score.*minutes_filter"):
            guard.run("dss_gate", lambda x: x, 1, depends_on=("minutes_filter", "final_score"))

    def test_reordering_two_real_stages_is_caught(self):
        guard = StageGuard()
        calls = []

        def stage_a(df):
            calls.append("a")
            return df + ["a_ran"]

        def stage_b(df):
            calls.append("b")
            return df + ["b_ran"]

        df = []
        df = guard.run("a", stage_a, df)
        df = guard.run("b", stage_b, df, depends_on=("a",))
        assert df == ["a_ran", "b_ran"]

        guard2 = StageGuard()
        with pytest.raises(AssertionError):
            guard2.run("b", stage_b, [], depends_on=("a",))

    def test_stage_function_receives_args_and_kwargs(self):
        guard = StageGuard()

        def fn(a, b, c=None):
            return (a, b, c)

        result = guard.run("s", fn, 1, 2, c=3)
        assert result == (1, 2, 3)

    def test_has_run_false_for_unrun_stage(self):
        guard = StageGuard()
        assert not guard.has_run("nonexistent")