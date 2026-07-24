import logging

from config import (
    SPREADSHEET_ID,
    WORKSHEET_NAME,
)
from services.cache import get_or_set
from services.common import normalize_header, normalize_username, parse_quantity
from services.google_runtime import (
    get_credentials,
    get_worksheet as get_runtime_worksheet,
    worksheet_operation,
)

logger = logging.getLogger(__name__)

def get_google_credentials():
    """
    Recupera le credenziali Google.

    Su Railway:
    usa la variabile GOOGLE_CREDENTIALS_JSON.

    Sul PC:
    usa il file credentials.json.
    """
    return get_credentials()


def get_worksheet():
    """
    Effettua la connessione a Google Sheets e restituisce
    la scheda indicata nella configurazione.
    """
    if not SPREADSHEET_ID:
        raise RuntimeError(
            "SPREADSHEET_ID non configurato."
        )

    if not WORKSHEET_NAME:
        raise RuntimeError(
            "WORKSHEET_NAME non configurato."
        )

    return get_runtime_worksheet(
        SPREADSHEET_ID,
        WORKSHEET_NAME,
    )


def get_sheet_records(force_refresh: bool = False) -> list[dict]:
    """Legge e normalizza il foglio ORDINI usando cache e retry."""
    def loader() -> list[dict]:
        values = worksheet_operation(
            SPREADSHEET_ID,
            WORKSHEET_NAME,
            lambda worksheet: worksheet.get_all_values(),
            operation_name="lettura foglio ordini",
        )
        if not values:
            logger.info("Il foglio ordini è vuoto.")
            return []
        headers = [normalize_header(header) for header in values[0]]
        records: list[dict] = []
        for row_values in values[1:]:
            row = {
                header: (row_values[index] if index < len(row_values) else "")
                for index, header in enumerate(headers)
                if header
            }
            records.append(row)
        logger.debug("Foglio ordini letto: %s righe", len(records))
        return records

    return get_or_set("orders:records", loader, force=force_refresh)


def get_user_orders(
    username: str | None,
    force_refresh: bool = False,
) -> list[dict]:
    """
    Cerca nel foglio tutte le righe associate
    allo username Telegram ricevuto.

    Non mostra gli ordini con stato:
    - EVASO
    - RESTAURO
    - GRADING
    """
    normalized_username = normalize_username(
        username
    )

    if not normalized_username:
        logger.info("Username Telegram assente.")
        return []

    records = get_sheet_records(
        force_refresh=force_refresh
    )

    user_orders = []

    excluded_statuses = {
        "EVASO",
        "RESTAURO",
        "GRADING",
    }

    logger.debug("Ricerca ordini per %s", normalized_username)

    for row_number, row in enumerate(
        records,
        start=2,
    ):
        sheet_username = normalize_username(
            row.get("UTENTI", "")
        )

        if (
            sheet_username
            != normalized_username
        ):
            continue

        status = str(
            row.get("STATO", "")
        ).strip().upper()

        if status in excluded_statuses:
            logger.debug(
                "Riga %s esclusa per stato %s",
                row_number,
                status,
            )
            continue

        quantity = parse_quantity(
            row.get("QUANTITA", 0)
        )

        order = {
            "row_number": row_number,
            "date": str(
                row.get("DATA", "")
            ).strip(),
            "name": str(
                row.get("OGGETTO", "")
            ).strip(),
            "quantity": quantity,
            "status": status,
        }

        user_orders.append(order)



    return user_orders
