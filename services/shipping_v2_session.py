"""Stato locale e separato della procedura Telegram Shipping v2."""
from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any
from uuid import uuid4

AVAILABLE_ITEMS = "shipping_v2_available_items"
SELECTED_ITEM_IDS = "shipping_v2_selected_item_ids"
PAGE = "shipping_v2_page"
DRAFT_UUID = "shipping_v2_draft_uuid"
IDEMPOTENCY_KEY = "shipping_v2_idempotency_key"
SELECTED_CARRIER = "shipping_v2_selected_carrier"
PROFILE = "shipping_v2_profile"
METHODS = "shipping_v2_methods"
WAITING_RECEIPT = "shipping_v2_waiting_receipt"
ITEM_CALLBACK_PREFIX = "order_v2_toggle:"
PAGE_CALLBACK_PREFIX = "shipping_v2_page:"
V2_ITEMS_PER_PAGE = 8

ALL_KEYS = (
    AVAILABLE_ITEMS,
    SELECTED_ITEM_IDS,
    PAGE,
    DRAFT_UUID,
    IDEMPOTENCY_KEY,
    SELECTED_CARRIER,
    PROFILE,
    METHODS,
    WAITING_RECEIPT,
)

DOWNSTREAM_KEYS = (
    DRAFT_UUID,
    IDEMPOTENCY_KEY,
    SELECTED_CARRIER,
    PROFILE,
    METHODS,
    WAITING_RECEIPT,
)


def clear_shipping_v2_session(user_data: MutableMapping[str, Any]) -> None:
    for key in ALL_KEYS:
        user_data.pop(key, None)


def clear_shipping_v2_after_selection(
    user_data: MutableMapping[str, Any],
) -> None:
    for key in DOWNSTREAM_KEYS:
        user_data.pop(key, None)


def selected_item_ids(user_data: MutableMapping[str, Any]) -> set[str]:
    raw = user_data.get(SELECTED_ITEM_IDS, set())
    if not isinstance(raw, (set, list, tuple)):
        return set()
    return {
        str(item_id).strip().upper()
        for item_id in raw
        if str(item_id).strip()
    }


def page_count(items: list[dict]) -> int:
    return max(1, (len(items) + V2_ITEMS_PER_PAGE - 1) // V2_ITEMS_PER_PAGE)


def current_page(
    user_data: MutableMapping[str, Any],
    items: list[dict] | None = None,
) -> int:
    records = items if items is not None else user_data.get(AVAILABLE_ITEMS, [])
    try:
        requested = int(user_data.get(PAGE, 1))
    except (TypeError, ValueError):
        requested = 1
    page = min(max(1, requested), page_count(records))
    user_data[PAGE] = page
    return page


def set_page(
    user_data: MutableMapping[str, Any],
    page: int | str,
) -> int:
    try:
        requested = int(page)
    except (TypeError, ValueError):
        requested = 1
    user_data[PAGE] = requested
    return current_page(user_data)


def paginated_items(
    items: list[dict],
    page: int,
) -> list[dict]:
    valid_page = min(max(1, int(page)), page_count(items))
    start = (valid_page - 1) * V2_ITEMS_PER_PAGE
    return items[start:start + V2_ITEMS_PER_PAGE]


def set_available_items(
    user_data: MutableMapping[str, Any],
    items: list[dict],
    *,
    preserve_selection: bool = True,
) -> set[str]:
    valid_ids = {
        str(item.get("ID_ARTICOLO", "")).strip().upper()
        for item in items
        if str(item.get("ID_ARTICOLO", "")).strip()
    }
    previous = selected_item_ids(user_data) if preserve_selection else set()
    selected = previous.intersection(valid_ids)
    if selected != previous:
        clear_shipping_v2_after_selection(user_data)
    user_data[AVAILABLE_ITEMS] = items
    user_data[SELECTED_ITEM_IDS] = selected
    current_page(user_data, items)
    return selected


def toggle_item(
    user_data: MutableMapping[str, Any],
    item_id: str,
) -> set[str]:
    target = str(item_id or "").strip().upper()
    valid_ids = {
        str(item.get("ID_ARTICOLO", "")).strip().upper()
        for item in user_data.get(AVAILABLE_ITEMS, [])
        if str(item.get("ID_ARTICOLO", "")).strip()
    }
    if not target or target not in valid_ids:
        raise ValueError("Articolo v2 non disponibile.")

    selected = selected_item_ids(user_data)
    if target in selected:
        selected.remove(target)
    else:
        selected.add(target)
    clear_shipping_v2_after_selection(user_data)
    user_data[SELECTED_ITEM_IDS] = selected
    return selected


def ensure_idempotency_key(
    user_data: MutableMapping[str, Any],
    *,
    uuid_factory=uuid4,
) -> str:
    current = str(user_data.get(IDEMPOTENCY_KEY, "")).strip()
    if current:
        return current
    current = f"SHIP-V2-{uuid_factory()}"
    user_data[IDEMPOTENCY_KEY] = current
    return current


def item_callback_data(item_id: str) -> str:
    callback_data = (
        ITEM_CALLBACK_PREFIX
        + str(item_id or "").strip()
    )
    if len(callback_data.encode("utf-8")) > 64:
        raise ValueError("Callback articolo v2 oltre il limite Telegram.")
    return callback_data


def page_callback_data(page: int) -> str:
    callback_data = PAGE_CALLBACK_PREFIX + str(max(1, int(page)))
    if len(callback_data.encode("utf-8")) > 64:
        raise ValueError("Callback pagina v2 oltre il limite Telegram.")
    return callback_data
