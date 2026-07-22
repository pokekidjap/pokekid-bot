from collections import defaultdict

from services.sheets import get_sheet_records, normalize_username, parse_quantity


MANAGED_STATUS_ORDER = {
    "IN MAGAZZINO": 0,
    "GRADING": 1,
    "RESTAURO": 2,
    "ORDINATO": 3,
}


def get_orders_grouped_by_user() -> list[dict]:
    """Raggruppa per utente tutti gli articoli ancora da gestire.

    Gli articoli con stato EVASO vengono esclusi sia dalla lista sia dai
    conteggi mostrati nel pannello amministratore.
    """
    records = get_sheet_records()
    grouped: dict[str, dict] = defaultdict(
        lambda: {
            "username": "",
            "rows": [],
            "total_quantity": 0,
            "ready_quantity": 0,
            "ordered_quantity": 0,
            "grading_quantity": 0,
            "restoration_quantity": 0,
            "other_quantity": 0,
        }
    )

    for row_number, row in enumerate(records, start=2):
        username = normalize_username(row.get("UTENTI", ""))
        if not username:
            continue

        quantity = parse_quantity(row.get("QUANTITA", 0))
        status = str(row.get("STATO", "")).strip().upper()

        # Gli articoli già evasi non sono più operativi e non devono
        # comparire nel pannello admin né influire sui conteggi.
        if status == "EVASO":
            continue

        item = {
            "row_number": row_number,
            "date": str(row.get("DATA", "")).strip(),
            "name": str(row.get("OGGETTO", "")).strip(),
            "quantity": quantity,
            "status": status,
        }

        user = grouped[username]
        user["username"] = username
        user["rows"].append(item)
        user["total_quantity"] += quantity

        if status == "IN MAGAZZINO":
            user["ready_quantity"] += quantity
        elif status == "ORDINATO":
            user["ordered_quantity"] += quantity
        elif status == "GRADING":
            user["grading_quantity"] += quantity
        elif status == "RESTAURO":
            user["restoration_quantity"] += quantity
        else:
            user["other_quantity"] += quantity

    users = []
    for user in grouped.values():
        user["rows"].sort(
            key=lambda order: (
                MANAGED_STATUS_ORDER.get(order["status"], 99),
                order["name"].casefold(),
            )
        )
        users.append(user)

    return sorted(users, key=lambda user: user["username"].casefold())
