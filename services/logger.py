"""Facade per il log applicativo persistente."""
from services.bot_db import write_log


def log_event(action: str, details: str = "", telegram_id: int | str = "", username: str = "", admin: str = "") -> None:
    write_log(telegram_id=telegram_id, username=username, action=action, details=details, admin=admin)
