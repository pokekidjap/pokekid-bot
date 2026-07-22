import json
import logging
import time
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

from config import (
    GOOGLE_CREDENTIALS_JSON,
    SPREADSHEET_ID,
    WORKSHEET_NAME,
)
from services.cache import get_or_set
from services.common import normalize_header, normalize_username, parse_quantity
from services.perf import get_perf_context
from services.retry import call_with_retry

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
CREDENTIALS_FILE = BASE_DIR / "credentials.json"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_google_credentials() -> Credentials:
    """
    Recupera le credenziali Google.

    Su Railway:
    usa la variabile GOOGLE_CREDENTIALS_JSON.

    Sul PC:
    usa il file credentials.json.
    """
    if GOOGLE_CREDENTIALS_JSON:
        try:
            credentials_info = json.loads(
                GOOGLE_CREDENTIALS_JSON
            )

        except json.JSONDecodeError as error:
            raise RuntimeError(
                "La variabile GOOGLE_CREDENTIALS_JSON "
                "non contiene un JSON valido."
            ) from error

        private_key = credentials_info.get(
            "private_key"
        )

        if private_key:
            credentials_info["private_key"] = (
                private_key.replace(
                    "\\n",
                    "\n",
                )
            )

        return Credentials.from_service_account_info(
            credentials_info,
            scopes=SCOPES,
        )

    if CREDENTIALS_FILE.exists():
        return Credentials.from_service_account_file(
            CREDENTIALS_FILE,
            scopes=SCOPES,
        )

    raise FileNotFoundError(
        "Credenziali Google non trovate.\n"
        f"In locale inserisci credentials.json in: "
        f"{CREDENTIALS_FILE}\n"
        "Su Railway configura la variabile "
        "GOOGLE_CREDENTIALS_JSON."
    )


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

    credentials = get_google_credentials()

    client = gspread.authorize(credentials)
    spreadsheet = call_with_retry(
        lambda: client.open_by_key(SPREADSHEET_ID),
        operation_name="apertura foglio ordini",
    )
    return call_with_retry(
        lambda: spreadsheet.worksheet(WORKSHEET_NAME),
        operation_name="apertura scheda ordini",
    )


def get_sheet_records(force_refresh: bool = False) -> list[dict]:
    """Legge e normalizza il foglio ORDINI usando cache e retry."""
    def loader() -> list[dict]:
        perf = get_perf_context()
        worksheet = get_worksheet()
        start = time.perf_counter()
        values = call_with_retry(
            worksheet.get_all_values,
            operation_name="lettura foglio ordini",
        )
        if perf is not None:
            perf.sheet_call((time.perf_counter() - start) * 1000.0)
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

    records = get_sheet_records()

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
