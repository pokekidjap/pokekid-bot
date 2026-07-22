"""Calcolo centralizzato delle statistiche del pannello admin."""
from services.admin_orders import get_orders_grouped_by_user
from services.bot_db import get_bot_status


def get_admin_statistics() -> dict:
    status = get_bot_status()
    users = get_orders_grouped_by_user()
    return {
        **status,
        "active_items": sum(x["total_quantity"] for x in users),
        "ready_items": sum(x["ready_quantity"] for x in users),
        "ordered_items": sum(x["ordered_quantity"] for x in users),
        "grading_items": sum(x.get("grading_quantity", 0) for x in users),
        "restoration_items": sum(x.get("restoration_quantity", 0) for x in users),
        "users_with_orders": len(users),
    }
