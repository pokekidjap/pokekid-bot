"""Snapshot e confronto dello smistamento ordini."""
import json

from services.admin_orders import get_orders_grouped_by_user
from services.bot_db import get_config_values, set_config_value

SNAPSHOT_KEY = "SMISTAMENTO_SNAPSHOT"


def _ready_items_by_user() -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for user in get_orders_grouped_by_user():
        items = {
            f"{row['row_number']}|{row['name']}|{row['quantity']}"
            for row in user.get("rows", [])
            if row.get("status") == "IN MAGAZZINO"
        }
        result[user["username"]] = items
    return result


def save_sorting_snapshot() -> int:
    snapshot = {
        username: sorted(items)
        for username, items in _ready_items_by_user().items()
    }
    set_config_value(SNAPSHOT_KEY, json.dumps(snapshot, ensure_ascii=False))
    return sum(len(items) for items in snapshot.values())


def get_users_with_new_ready_items() -> dict[str, int]:
    config = get_config_values()
    raw = config.get(SNAPSHOT_KEY, {}).get("value", "")
    try:
        previous_raw = json.loads(raw) if raw else {}
    except (TypeError, json.JSONDecodeError):
        previous_raw = {}

    previous = {
        username: set(items or [])
        for username, items in previous_raw.items()
    }
    current = _ready_items_by_user()
    changed: dict[str, int] = {}
    for username, items in current.items():
        new_items = items - previous.get(username, set())
        if new_items:
            changed[username] = len(new_items)
    return changed


def clear_sorting_snapshot() -> None:
    set_config_value(SNAPSHOT_KEY, "")
