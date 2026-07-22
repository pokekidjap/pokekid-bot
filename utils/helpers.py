"""Utility condivise e prive di dipendenze Telegram."""
from html import escape


def truncate(value: object, limit: int = 30) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."


def html(value: object) -> str:
    return escape(str(value or ""))
