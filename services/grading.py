import gspread

from config import SPREADSHEET_ID
from services.sheets import (
    get_google_credentials,
    normalize_header,
)


GRADING_WORKSHEET_NAME = "GRADING"

REQUIRED_HEADERS = {
    "GRADING",
    "SUB",
    "SERVIZIO",
    "STATO",
}


def get_grading_worksheet():
    """
    Apre la scheda GRADING del file Google Sheets.
    """
    if not SPREADSHEET_ID:
        raise RuntimeError(
            "SPREADSHEET_ID non configurato."
        )

    credentials = get_google_credentials()
    client = gspread.authorize(credentials)

    spreadsheet = client.open_by_key(
        SPREADSHEET_ID
    )

    return spreadsheet.worksheet(
        GRADING_WORKSHEET_NAME
    )


def find_grading_header_row(
    values: list[list[str]],
) -> tuple[int, list[str]]:
    """
    Cerca automaticamente la riga contenente:

    GRADING | SUB | SERVIZIO | STATO

    La tabella può trovarsi in qualsiasi colonna del foglio.
    """
    for row_index, row_values in enumerate(values):
        normalized_row = [
            normalize_header(value)
            for value in row_values
        ]

        row_headers = {
            value
            for value in normalized_row
            if value
        }

        if REQUIRED_HEADERS.issubset(row_headers):
            return row_index, normalized_row

    raise RuntimeError(
        "Non è stata trovata la tabella con le intestazioni "
        "GRADING, SUB, SERVIZIO e STATO nella scheda GRADING."
    )


def get_grading_records() -> list[dict]:
    """
    Legge la tabella dello stato SUB presente nella scheda GRADING.

    La posizione della tabella non è importante:
    può trovarsi anche nelle colonne X, Y, Z e AA.
    """
    worksheet = get_grading_worksheet()
    values = worksheet.get_all_values()

    if not values:
        return []

    header_row_index, headers = find_grading_header_row(
        values
    )

    header_positions = {
        header: index
        for index, header in enumerate(headers)
        if header in REQUIRED_HEADERS
    }

    records = []

    for row_number, row_values in enumerate(
        values[header_row_index + 1:],
        start=header_row_index + 2,
    ):
        row = {}

        for header in REQUIRED_HEADERS:
            column_index = header_positions[header]

            value = (
                row_values[column_index]
                if column_index < len(row_values)
                else ""
            )

            row[header] = str(value).strip()

        grading = row["GRADING"]
        sub = row["SUB"]
        service = row["SERVIZIO"]
        status = row["STATO"]

        # Ignora le righe completamente vuote.
        if not any(
            [
                grading,
                sub,
                service,
                status,
            ]
        ):
            continue

        # Ignora eventuali righe incomplete.
        if not grading or not sub or not status:
            print(
                f"RIGA {row_number} IGNORATA: "
                "dati della SUB incompleti."
            )
            continue

        records.append(
            {
                "grading": grading.upper(),
                "sub": sub.upper(),
                "service": service.upper(),
                "status": status,
            }
        )

    print("--------------------------------")
    print("TABELLA STATO SUB TROVATA")
    print("RIGA INTESTAZIONI:", header_row_index + 1)
    print("SUB LETTE:", len(records))
    print("--------------------------------")

    return records