"""Cache TTL thread-safe per ridurre le letture ripetute da Google Sheets."""
from __future__ import annotations

import time
from copy import deepcopy
from dataclasses import dataclass
from threading import Condition, RLock
from typing import Any, Callable

from services.perf import get_perf_context

_LOCK = RLock()
_CONDITION = Condition(_LOCK)
_CACHE: dict[str, tuple[float, Any]] = {}
_KEY_GENERATIONS: dict[str, int] = {}
_GLOBAL_GENERATION = 0
_COALESCED_WAITS = 0


@dataclass
class _LoadState:
    generation: tuple[int, int]
    done: bool = False
    value: Any = None
    error: BaseException | None = None


_LOADING: dict[str, _LoadState] = {}

TTL_BY_PREFIX = {
    "orders": 30,
    "profiles": 60,
    "shipping": 30,
    "logs": 30,
    "config": 300,
    "admins": 600,
    "grading": 60,
    "status": 30,
}
DEFAULT_TTL = 30


def ttl_for(key: str) -> int:
    prefix = key.split(":", 1)[0].lower()
    return TTL_BY_PREFIX.get(prefix, DEFAULT_TTL)


def get_or_set(
    key: str,
    loader: Callable[[], Any],
    ttl: int | None = None,
    force: bool = False,
) -> Any:
    global _COALESCED_WAITS

    effective_ttl = ttl_for(key) if ttl is None else max(0, ttl)
    now = time.monotonic()
    perf = get_perf_context()

    with _CONDITION:
        loading = _LOADING.get(key)
        if loading is not None:
            _COALESCED_WAITS += 1
            if perf is not None:
                perf.cache_miss()
            while not loading.done:
                _CONDITION.wait()
            if loading.error is not None:
                raise loading.error
            return deepcopy(loading.value)

        cached = _CACHE.get(key)
        if not force and cached and now - cached[0] < effective_ttl:
            if perf is not None:
                perf.cache_hit()
            return deepcopy(cached[1])

        if perf is not None:
            perf.cache_miss()

        loading = _LoadState(
            generation=(
                _GLOBAL_GENERATION,
                _KEY_GENERATIONS.get(key, 0),
            )
        )
        _LOADING[key] = loading

    try:
        value = loader()
        stored_value = deepcopy(value)
    except BaseException as error:
        with _CONDITION:
            loading.error = error
            loading.done = True
            if _LOADING.get(key) is loading:
                _LOADING.pop(key, None)
            _CONDITION.notify_all()
        raise

    with _CONDITION:
        current_generation = (
            _GLOBAL_GENERATION,
            _KEY_GENERATIONS.get(key, 0),
        )
        if loading.generation == current_generation:
            _CACHE[key] = (
                time.monotonic(),
                deepcopy(stored_value),
            )
        loading.value = stored_value
        loading.done = True
        if _LOADING.get(key) is loading:
            _LOADING.pop(key, None)
        _CONDITION.notify_all()

    return deepcopy(stored_value)


def invalidate(*keys: str) -> None:
    """Invalida chiavi esatte o interi prefissi (es. ``shipping:*``)."""
    global _GLOBAL_GENERATION

    with _CONDITION:
        if not keys:
            _CACHE.clear()
            _KEY_GENERATIONS.clear()
            _GLOBAL_GENERATION += 1
            return

        for key in keys:
            if key.endswith("*"):
                prefix = key[:-1]
                matching_keys = {
                    candidate
                    for candidate in (
                        set(_CACHE)
                        | set(_LOADING)
                        | set(_KEY_GENERATIONS)
                    )
                    if candidate.startswith(prefix)
                }
                for matching_key in matching_keys:
                    _CACHE.pop(matching_key, None)
                    _KEY_GENERATIONS[matching_key] = (
                        _KEY_GENERATIONS.get(matching_key, 0)
                        + 1
                    )
            else:
                _CACHE.pop(key, None)
                _KEY_GENERATIONS[key] = (
                    _KEY_GENERATIONS.get(key, 0)
                    + 1
                )


def cache_info() -> dict[str, int]:
    with _CONDITION:
        entries = len(_CACHE)
        return {
            "entries": entries,
            "keys": entries,
            "loads_in_progress": len(_LOADING),
            "coalesced_waits": _COALESCED_WAITS,
        }
