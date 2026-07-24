"""Protezione dei log HTTP da URL Telegram contenenti il token del bot."""
from __future__ import annotations

import logging
import re

_TELEGRAM_BOT_URL = re.compile(
    r"(https?://api\.telegram\.org/bot)[^/\s]+",
    re.IGNORECASE,
)


def redact_telegram_token(value: object) -> str:
    return _TELEGRAM_BOT_URL.sub(
        r"\1<redacted>",
        str(value),
    )


class TelegramTokenRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = redact_telegram_token(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


_TOKEN_FILTER = TelegramTokenRedactionFilter()


def configure_http_logging_security() -> None:
    """Riduce il rumore HTTP e redige token Telegram prima dell'output."""
    for logger_name in ("httpx", "httpcore"):
        http_logger = logging.getLogger(logger_name)
        http_logger.setLevel(logging.WARNING)
        if _TOKEN_FILTER not in http_logger.filters:
            http_logger.addFilter(_TOKEN_FILTER)
    for handler in logging.getLogger().handlers:
        if _TOKEN_FILTER not in handler.filters:
            handler.addFilter(_TOKEN_FILTER)
