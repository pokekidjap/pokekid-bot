"""Formattazione compatta e sicura dei testi Telegram Shipping v2."""
from __future__ import annotations

from html import escape
from typing import Any, Iterable

from services.common import clean_value, parse_quantity

TELEGRAM_V2_TEXT_BUDGET = 3800


class ShippingV2TextBudgetError(ValueError):
    """Il contenuto fisso non lascia spazio a un messaggio Telegram sicuro."""


def _item_values(item: dict[str, Any], source: str) -> tuple[str, str]:
    if source == "draft":
        name = item.get("OGGETTO_SNAPSHOT", "")
        quantity = item.get("QUANTITA_SNAPSHOT", "")
    else:
        name = item.get("OGGETTO", "")
        quantity = item.get("QUANTITA", "")
    return clean_value(name), clean_value(quantity) or "1"


def item_totals(
    items: Iterable[dict[str, Any]],
    *,
    source: str,
) -> tuple[int, int]:
    records = list(items)
    units = sum(
        max(0, parse_quantity(_item_values(item, source)[1]))
        for item in records
    )
    return len(records), units


def compact_item_message(
    *,
    prefix: str,
    items: Iterable[dict[str, Any]],
    source: str,
    suffix: str = "",
    budget: int = TELEGRAM_V2_TEXT_BUDGET,
) -> str:
    """Compone il testo senza troncare stringhe HTML già formattate.

    Le righe articolo sono aggiunte soltanto per intero. Se non entrano tutte,
    viene aggiunto un riepilogo testuale con il numero di articoli omessi.
    """
    records = list(items)
    total_items, total_units = item_totals(records, source=source)
    summary = (
        f"📦 Totale articoli: <b>{total_items}</b>\n"
        f"🔢 Totale unità: <b>{total_units}</b>"
    )
    lines = []
    for item in records:
        name, quantity = _item_values(item, source)
        lines.append(
            "🎴 "
            f"<b>{escape(name)}</b> "
            f"×{escape(quantity)}"
        )

    def compose(visible: list[str]) -> str:
        parts = [part for part in (prefix, summary) if part]
        if visible:
            parts.append("\n".join(visible))
        remaining = len(lines) - len(visible)
        if remaining:
            parts.append(f"… e altri {remaining} articoli")
        if suffix:
            parts.append(suffix)
        return "\n\n".join(parts)

    for visible_count in range(len(lines), -1, -1):
        result = compose(lines[:visible_count])
        if len(result) <= budget:
            return result
    raise ShippingV2TextBudgetError(
        "Il contenuto fisso supera il budget Telegram Shipping v2."
    )


def ensure_v2_text_budget(
    text: str,
    *,
    budget: int = TELEGRAM_V2_TEXT_BUDGET,
) -> str:
    if len(text) > budget:
        raise ShippingV2TextBudgetError(
            "Testo Telegram Shipping v2 oltre il budget consentito."
        )
    return text
