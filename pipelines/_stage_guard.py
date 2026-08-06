from typing import Callable, TypeVar

T = TypeVar("T")


class StageGuard:

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