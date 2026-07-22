Libreria
/
admin_orders.py


from collections import defaultdict

from services.sheets import get_sheet_records, normalize_username, parse_quantity


STATUS_ORDER = {
    "IN MAGAZZINO": 0,
    "GRADING": 1,
    "RESTAURO": 2,
    "ORDINATO": 3,
}


def get_orders_grouped_by_user() -> list[dict]:
    records = get_sheet_records()
    grouped: dict[str, dict] = defaultdict(lambda: {
        "username": "",
        "rows": [],
        "total_quantity": 0,
        "ready_quantity": 0,
        "grading_quantity": 0,
        "restoration_quantity": 0,
        "ordered_quantity": 0,
    })

    for row_number, row in enumerate(records, start=2):
        username = normalize_username(row.get("UTENTI", ""))
        if not username:
            continue

        quantity = parse_quantity(row.get("QUANTITA", 0))
        status = str(row.get("STATO", "")).strip().upper()

        # Gli articoli già evasi non interessano nel pannello operativo Admin.
        if status == "EVASO":
            continue

        item = {
            "row_number": row_number,
            "date": str(row.get("DATA", "")).strip(),
            "name": str(row.get("OGGETTO", "")).strip(),
            "quantity": quantity,
            "status": status,
        }

        user_data = grouped[username]
        user_data["username"] = username
        user_data["rows"].append(item)
        user_data["total_quantity"] += quantity

        if status == "IN MAGAZZINO":
            user_data["ready_quantity"] += quantity
        elif status == "GRADING":
            user_data["grading_quantity"] += quantity
        elif status == "RESTAURO":
            user_data["restoration_quantity"] += quantity
        elif status == "ORDINATO":
            user_data["ordered_quantity"] += quantity

    users = []
    for user_data in grouped.values():
        user_data["rows"].sort(
            key=lambda order: (
                STATUS_ORDER.get(order["status"], 99),
                order["name"].casefold(),
            )
        )
        users.append(user_data)

    return sorted(users, key=lambda user: user["username"].casefold())