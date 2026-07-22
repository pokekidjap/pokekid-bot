"""Retry sincrono per operazioni Google soggette a errori temporanei."""
from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import TypeVar

import gspread

T = TypeVar("T")
logger = logging.getLogger(__name__)

_RETRYABLE = (
    TimeoutError,
    ConnectionError,
    gspread.exceptions.APIError,
)


def call_with_retry(
    operation: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay: float = 0.6,
    operation_name: str = "operazione Google Sheets",
) -> T:
    """Esegue un'operazione con backoff esponenziale e piccolo jitter."""
    last_error: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return operation()
        except _RETRYABLE as error:
            last_error = error
            if attempt >= attempts:
                break
            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.2)
            logger.warning(
                "%s fallita (%s/%s): %s. Nuovo tentativo tra %.2fs",
                operation_name,
                attempt,
                attempts,
                error,
                delay,
            )
            time.sleep(delay)
    assert last_error is not None
    raise last_error
