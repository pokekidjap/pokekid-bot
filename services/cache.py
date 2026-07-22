"""Cache TTL thread-safe per ridurre le letture ripetute da Google Sheets."""
from __future__ import annotations

import time
from copy import deepcopy
from threading import RLock
from typing import Any, Callable

_LOCK = RLock()
_CACHE: dict[str, tuple[float, Any]] = {}

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


from services.perf import get_perf_context


def get_or_set(
    key: str,
    loader: Callable[[], Any],
    ttl: int | None = None,
    force: bool = False,
) -> Any:
    effective_ttl = ttl_for(key) if ttl is None else max(0, ttl)
    now = time.monotonic()
    with _LOCK:
        cached = _CACHE.get(key)
        if not force and cached and now - cached[0] < effective_ttl:
            perf = get_perf_context()
            if perf is not None:
                perf.cache_hit()
            return deepcopy(cached[1])
    perf = get_perf_context()
    if perf is not None:
        perf.cache_miss()
    value = loader()
    with _LOCK:
        _CACHE[key] = (time.monotonic(), deepcopy(value))
    return deepcopy(value)


def invalidate(*keys: str) -> None:
    """Invalida chiavi esatte o interi prefissi (es. ``shipping:*``)."""
    with _LOCK:
        if not keys:
            _CACHE.clear()
            return
        for key in keys:
            if key.endswith("*"):
                prefix = key[:-1]
                for cached_key in list(_CACHE):
                    if cached_key.startswith(prefix):
                        _CACHE.pop(cached_key, None)
            else:
                _CACHE.pop(key, None)


def cache_info() -> dict[str, int]:
    with _LOCK:
        return {"entries": len(_CACHE)}
