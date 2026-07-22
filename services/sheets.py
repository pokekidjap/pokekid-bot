import json
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

from config import (
    GOOGLE_CREDENTIALS_JSON,
    SPREADSHEET_ID,
    WORKSHEET_NAME,
)


BASE_DIR = Path(__file__).resolve().parent.parent
CREDENTIALS_FILE = BASE_DIR / "credentials.json"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def normalize_username(username: str | None) -> str:
    """
    Uniforma gli username Telegram.

    Esempi:
    Picco  -> @picco
    @Picco -> @picco
    """
    if not username:
        return ""

    normalized = str(username).strip().lower()

    if not normalized.startswith("@"):
        normalized = f"@{normalized}"

    return normalized


def normalize_header(header: str) -> str:
    """
    Rimuove gli spazi e uniforma le intestazioni del foglio.
    """
    return str(header).strip().upper()


def parse_quantity(value) -> int:
    """
    Converte la quantità letta dal foglio in numero intero.
    """
    if value is None or value == "":
        return 0

    try:
        text = str(value).strip().replace(",", ".")
        return int(float(text))

    except (TypeError, ValueError):
        return 0


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

    client = gspread.authorize(
        credentials
    )

    spreadsheet = client.open_by_key(
        SPREADSHEET_ID
    )

    return spreadsheet.worksheet(
        WORKSHEET_NAME
    )


def get_sheet_records() -> list[dict]:
    """
    Legge tutte le righe del foglio e normalizza
    le intestazioni delle colonne.
    """
    worksheet = get_worksheet()

    values = worksheet.get_all_values()

    if not values:
        print("Il foglio è vuoto.")
        return []

    raw_headers = values[0]

    headers = [
        normalize_header(header)
        for header in raw_headers
    ]

    records = []

    for row_values in values[1:]:
        row = {}

        for index, header in enumerate(
            headers
        ):
            if not header:
                continue

            value = (
                row_values[index]
                if index < len(row_values)
                else ""
            )

            row[header] = value

        records.append(row)

    print("--------------------------------")
    print("INTESTAZIONI LETTE:", headers)
    print("RIGHE LETTE:", len(records))
    print("--------------------------------")

    return records


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
        print("Username Telegram assente.")
        return []

    records = get_sheet_records()

    user_orders = []

    excluded_statuses = {
        "EVASO",
        "RESTAURO",
        "GRADING",
    }

    print("--------------------------------")
    print(
        "USERNAME TELEGRAM:",
        normalized_username,
    )

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
            print(
                f"RIGA {row_number} ESCLUSA: "
                f"STATO {status}"
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

        print(
            f"RIGA {row_number} TROVATA:",
            order,
        )

    print(
        "ORDINI TROVATI:",
        len(user_orders),
    )
    print("--------------------------------")

    return user_orders