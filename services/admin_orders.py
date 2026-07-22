from collections import defaultdict

from services.sheets import get_sheet_records, normalize_username, parse_quantity


def get_orders_grouped_by_user() -> list[dict]:
    records = get_sheet_records()
    grouped: dict[str, dict] = defaultdict(lambda: {
        "username": "",
        "rows": [],
        "total_quantity": 0,
        "ready_quantity": 0,
    })

    for row_number, row in enumerate(records, start=2):
        username = normalize_username(row.get("UTENTI", ""))
        if not username:
            continue

        quantity = parse_quantity(row.get("QUANTITA", 0))
        status = str(row.get("STATO", "")).strip().upper()
        item = {
            "row_number": row_number,
            "date": str(row.get("DATA", "")).strip(),
            "name": str(row.get("OGGETTO", "")).strip(),
            "quantity": quantity,
            "status": status,
        }
        grouped[username]["username"] = username
        grouped[username]["rows"].append(item)
        grouped[username]["total_quantity"] += quantity
        if status == "IN MAGAZZINO":
            grouped[username]["ready_quantity"] += quantity

    return sorted(grouped.values(), key=lambda x: x["username"])
