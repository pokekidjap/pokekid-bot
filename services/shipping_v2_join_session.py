"""Stato locale della procedura Telegram per l'unione Shipping v2."""
from __future__ import annotations

from collections.abc import MutableMapping
from hashlib import sha256
from typing import Any
from uuid import uuid4

JOIN_TARGET_ID = "shipping_v2_join_target_id"
JOIN_TARGET_USERNAME = "shipping_v2_join_target_username"
JOIN_SHIPPING_ID = "shipping_v2_join_shipping_id"
JOIN_SHIPPING_UUID = "shipping_v2_join_shipping_uuid"
JOIN_AVAILABLE_ITEMS = "shipping_v2_join_available_items"
JOIN_SELECTED_ITEM_IDS = "shipping_v2_join_selected_item_ids"
JOIN_PAGE = "shipping_v2_join_page"
JOIN_IDEMPOTENCY_KEY = "shipping_v2_join_idempotency_key"

JOIN_ITEM_CALLBACK_PREFIX = "join_v2_toggle:"
JOIN_PAGE_CALLBACK_PREFIX = "join_v2_page:"
JOIN_ITEMS_PER_PAGE = 8

JOIN_ALL_KEYS = (
    JOIN_TARGET_ID,
    JOIN_TARGET_USERNAME,
    JOIN_SHIPPING_ID,
    JOIN_SHIPPING_UUID,
    JOIN_AVAILABLE_ITEMS,
    JOIN_SELECTED_ITEM_IDS,
    JOIN_PAGE,
    JOIN_IDEMPOTENCY_KEY,
)


def clear_shipping_v2_join_session(
    user_data: MutableMapping[str, Any],
) -> None:
    for key in JOIN_ALL_KEYS:
        user_data.pop(key, None)


def initialize_shipping_v2_join_session(
    user_data: MutableMapping[str, Any],
) -> None:
    clear_shipping_v2_join_session(user_data)
    user_data[JOIN_AVAILABLE_ITEMS] = []
    user_data[JOIN_SELECTED_ITEM_IDS] = set()
    user_data[JOIN_PAGE] = 1


def join_selected_item_ids(
    user_data: MutableMapping[str, Any],
) -> set[str]:
    raw = user_data.get(JOIN_SELECTED_ITEM_IDS, set())
    if not isinstance(raw, (set, list, tuple)):
        return set()
    return {
        str(item_id).strip().upper()
        for item_id in raw
        if str(item_id).strip()
    }


def join_page_count(items: list[dict]) -> int:
    return max(
        1,
        (len(items) + JOIN_ITEMS_PER_PAGE - 1) // JOIN_ITEMS_PER_PAGE,
    )


def current_join_page(
    user_data: MutableMapping[str, Any],
    items: list[dict] | None = None,
) -> int:
    records = (
        items
        if items is not None
        else user_data.get(JOIN_AVAILABLE_ITEMS, [])
    )
    try:
        requested = int(user_data.get(JOIN_PAGE, 1))
    except (TypeError, ValueError):
        requested = 1
    page = min(max(1, requested), join_page_count(records))
    user_data[JOIN_PAGE] = page
    return page


def set_join_page(
    user_data: MutableMapping[str, Any],
    page: int | str,
) -> int:
    try:
        user_data[JOIN_PAGE] = int(page)
    except (TypeError, ValueError):
        user_data[JOIN_PAGE] = 1
    return current_join_page(user_data)


def paginated_join_items(
    items: list[dict],
    page: int,
) -> list[dict]:
    valid_page = min(max(1, int(page)), join_page_count(items))
    start = (valid_page - 1) * JOIN_ITEMS_PER_PAGE
    return items[start:start + JOIN_ITEMS_PER_PAGE]


def set_join_available_items(
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
    previous = (
        join_selected_item_ids(user_data)
        if preserve_selection
        else set()
    )
    selected = previous.intersection(valid_ids)
    if selected != previous:
        user_data.pop(JOIN_IDEMPOTENCY_KEY, None)
    user_data[JOIN_AVAILABLE_ITEMS] = list(items)
    user_data[JOIN_SELECTED_ITEM_IDS] = selected
    current_join_page(user_data, items)
    return selected


def toggle_join_item(
    user_data: MutableMapping[str, Any],
    item_id: str,
) -> set[str]:
    target = str(item_id or "").strip().upper()
    valid_ids = {
        str(item.get("ID_ARTICOLO", "")).strip().upper()
        for item in user_data.get(JOIN_AVAILABLE_ITEMS, [])
        if str(item.get("ID_ARTICOLO", "")).strip()
    }
    if not target or target not in valid_ids:
        raise ValueError("Articolo non disponibile per l'unione.")

    selected = join_selected_item_ids(user_data)
    if target in selected:
        selected.remove(target)
    else:
        selected.add(target)
    user_data[JOIN_SELECTED_ITEM_IDS] = selected
    user_data.pop(JOIN_IDEMPOTENCY_KEY, None)
    return selected


def ensure_join_idempotency_key(
    user_data: MutableMapping[str, Any],
    *,
    uuid_factory=uuid4,
) -> str:
    current = str(user_data.get(JOIN_IDEMPOTENCY_KEY, "")).strip()
    if current:
        return current
    selected = join_selected_item_ids(user_data)
    if not selected:
        raise ValueError("Selezione vuota per la idempotency key.")
    current = (
        f"JOIN-V2-{uuid_factory()}-"
        f"{join_selection_digest(selected)}"
    )
    user_data[JOIN_IDEMPOTENCY_KEY] = current
    return current


def join_selection_digest(item_ids) -> str:
    canonical = "\n".join(
        sorted(
            str(item_id).strip().upper()
            for item_id in item_ids
            if str(item_id).strip()
        )
    )
    return sha256(canonical.encode("utf-8")).hexdigest()[:16]


def join_item_callback_data(item_id: str) -> str:
    callback_data = (
        JOIN_ITEM_CALLBACK_PREFIX
        + str(item_id or "").strip()
    )
    if len(callback_data.encode("utf-8")) > 64:
        raise ValueError("Callback articolo unione oltre il limite Telegram.")
    return callback_data


def join_page_callback_data(page: int) -> str:
    callback_data = (
        JOIN_PAGE_CALLBACK_PREFIX
        + str(max(1, int(page)))
    )
    if len(callback_data.encode("utf-8")) > 64:
        raise ValueError("Callback pagina unione oltre il limite Telegram.")
    return callback_data
