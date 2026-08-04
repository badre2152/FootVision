# pipelines/_stage_guard.py
#
# C3 (architecture review): some pipeline stages MUST run in a specific
# order because running them out of order doesn't raise a normal exception
# — it just silently produces a wrong answer. BUG-01 (see
# pipelines/gold/run_gold.py::_apply_final_scoring) is exactly this: calling
# apply_dss_gate() before compute_final_score() doesn't crash, it just makes
# the gate's effect vanish, discovered only by someone specifically checking
# the resulting DSSScore. Before C2/C4, the only thing preventing that
# reorder was a comment two functions apart inside a 220-line main().
#
# This is NOT a general pipeline/DAG framework — deliberately small. It
# gives ordered dependencies a name and turns "silently wrong" into "loudly
# wrong at the exact line that broke it", which is the actual goal: no one
# should be able to reorder two lines inside a function and have the tests
# still pass and the output quietly be incorrect.

from typing import Callable, TypeVar

T = TypeVar("T")


class StageGuard:
    """
    Usage:
        guard = StageGuard()
        df = guard.run("minutes_filter", apply_minutes_filter, df)
        df = guard.run("final_score", compute_final_score, df,
                        depends_on=("minutes_filter",))
        df = guard.run("dss_gate", apply_dss_gate, df,
                        depends_on=("final_score",))

    If a stage's declared dependency hasn't run yet (e.g. the calls above
    got reordered), `run()` raises AssertionError immediately — instead of
    silently producing a DataFrame that's missing whatever the skipped
    stage was supposed to do.
    """

    def __init__(self) -> None:
        self._completed: set[str] = set()

    def run(
        self,
        name: str,
        fn: Callable[..., T],
        *args,
        depends_on: tuple[str, ...] = (),
        **kwargs,
    ) -> T:
        missing = [d for d in depends_on if d not in self._completed]
        assert not missing, (
            f"Pipeline ordering violation: stage {name!r} declares it depends on "
            f"{missing}, which {'have' if len(missing) > 1 else 'has'} not run yet. "
            f"This stage was likely called before its dependency by mistake — "
            f"reordering pipeline stages like this can silently produce wrong "
            f"output instead of crashing (that's exactly what this check exists "
            f"to catch). Fix the call order; do not remove this check to make "
            f"the assertion go away."
        )
        result = fn(*args, **kwargs)
        self._completed.add(name)
        return result

    def has_run(self, name: str) -> bool:
        return name in self._completed
