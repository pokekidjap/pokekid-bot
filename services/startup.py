"""Controlli diagnostici eseguibili all'avvio senza modificare i fogli."""
from __future__ import annotations

import logging

from services.bot_db import test_bot_db_connection
from services.sheets import get_worksheet

logger = logging.getLogger(__name__)


def run_startup_checks() -> dict[str, object]:
    result: dict[str, object] = {"orders": False, "bot_db": False, "worksheets": []}
    get_worksheet()
    result["orders"] = True
    worksheets = test_bot_db_connection()
    result["bot_db"] = True
    result["worksheets"] = worksheets
    logger.info("Controlli iniziali completati: %s", result)
    return result
