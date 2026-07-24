import contextvars
import dataclasses
import logging
import time
from functools import wraps
from typing import Awaitable, Callable, TypeVar

logger = logging.getLogger("perf")

_current_perf_context: contextvars.ContextVar["PerfContext | None"] = contextvars.ContextVar(
    "current_perf_context",
    default=None,
)


@dataclasses.dataclass
class PerfContext:
    flow_name: str
    start_time: float = dataclasses.field(default_factory=time.perf_counter)
    sheets_count: int = 0
    sheets_time: float = 0.0
    cache_hits: int = 0
    cache_misses: int = 0
    notes: list[str] = dataclasses.field(default_factory=list)

    def sheet_call(self, duration_ms: float) -> None:
        self.sheets_count += 1
        self.sheets_time += duration_ms

    def cache_hit(self) -> None:
        self.cache_hits += 1

    def cache_miss(self) -> None:
        self.cache_misses += 1

    def add_note(self, note: str) -> None:
        self.notes.append(note)

    def summary(self) -> str:
        return (
            f"flow={self.flow_name} "
            f"total={self.total_ms():.1f}ms "
            f"sheets={self.sheets_time:.1f}ms "
            f"sheets_calls={self.sheets_count} "
            f"cache_hits={self.cache_hits} "
            f"cache_misses={self.cache_misses} "
            f"notes={','.join(self.notes) if self.notes else 'none'}"
        )

    def total_ms(self) -> float:
        return (time.perf_counter() - self.start_time) * 1000.0


class perf_flow:
    def __init__(self, flow_name: str) -> None:
        self.flow_name = flow_name
        self.token: contextvars.Token[PerfContext | None] | None = None
        self.context: PerfContext | None = None

    def __enter__(self) -> "PerfContext":
        self.context = PerfContext(self.flow_name)
        self.token = _current_perf_context.set(self.context)
        return self.context

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.token is not None:
            _current_perf_context.reset(self.token)
        if self.context is not None:
            log_method = (
                logger.warning
                if self.context.total_ms() > 1500
                else logger.info
            )
            log_method("perf: %s", self.context.summary())


def start_flow(flow_name: str) -> perf_flow:
    return perf_flow(flow_name)


def get_perf_context() -> PerfContext | None:
    return _current_perf_context.get()


_T = TypeVar("_T")


def track_async_flow(
    flow_name: str | Callable[..., str],
) -> Callable[[Callable[..., Awaitable[_T]]], Callable[..., Awaitable[_T]]]:
    """Registra un handler async senza modificarne il contratto pubblico."""

    def decorator(
        function: Callable[..., Awaitable[_T]],
    ) -> Callable[..., Awaitable[_T]]:
        @wraps(function)
        async def wrapped(*args, **kwargs) -> _T:
            resolved = (
                flow_name(*args, **kwargs)
                if callable(flow_name)
                else flow_name
            )
            with start_flow(resolved):
                return await function(*args, **kwargs)

        return wrapped

    return decorator
