"""Versione del bot caricata una volta da CONFIG durante lo startup."""
from __future__ import annotations

from threading import RLock
from typing import Any, Callable

BOT_VERSION_FALLBACK = "2.3.1"

_LOCK = RLock()
_loaded_version = BOT_VERSION_FALLBACK


def load_bot_version(
    config_loader: Callable[[], dict[str, Any]] | None = None,
) -> str:
    """Ricarica VERSIONE_BOT da CONFIG, usando il fallback in caso di errore."""
    global _loaded_version

    try:
        if config_loader is None:
            # Import locale intenzionale: bot_db usa get_bot_version() per lo
            # stato, mentre questo modulo non deve dipenderne all'import.
            from services.bot_db import get_config_values

            config_loader = get_config_values
        item = config_loader().get("VERSIONE_BOT", {})
        value = str(item.get("value", "")).strip()
        loaded = value or BOT_VERSION_FALLBACK
    except Exception:
        loaded = BOT_VERSION_FALLBACK

    with _LOCK:
        _loaded_version = loaded
        return _loaded_version


def get_bot_version() -> str:
    """Restituisce esclusivamente la versione già presente in memoria."""
    with _LOCK:
        return _loaded_version
