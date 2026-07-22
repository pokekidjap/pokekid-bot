from datetime import datetime
from zoneinfo import ZoneInfo

import gspread

from config import BOT_DB_SHEET_ID
from services.sheets import get_google_credentials


PROFILE_WORKSHEET_NAME = "PROFILI"
SHIPPING_WORKSHEET_NAME = "SPEDIZIONI"
CONFIG_WORKSHEET_NAME = "CONFIG"
LOG_WORKSHEET_NAME = "LOG"

ITALY_TIMEZONE = ZoneInfo("Europe/Rome")


def get_current_datetime() -> str:
    """
    Restituisce data e ora italiane nel formato:
    22/07/2026 10:30:00
    """
    return datetime.now(
        ITALY_TIMEZONE
    ).strftime(
        "%d/%m/%Y %H:%M:%S"
    )


def clean_value(value) -> str:
    """
    Converte un valore in testo e rimuove
    eventuali spazi iniziali e finali.
    """
    if value is None:
        return ""

    return str(value).strip()


def normalize_telegram_id(
    telegram_id: int | str,
) -> str:
    """
    Uniforma il Telegram ID come stringa.
    """
    return clean_value(
        telegram_id
    )


def normalize_username(
    username: str | None,
) -> str:
    """
    Uniforma lo username Telegram.

    Esempi:
    Picco  -> @picco
    @Picco -> @picco
    """
    username = clean_value(
        username
    ).lower()

    if not username:
        return ""

    if not username.startswith("@"):
        username = f"@{username}"

    return username


def get_bot_db_spreadsheet() -> gspread.Spreadsheet:
    """
    Apre il Google Sheets dedicato al database del bot.

    Utilizza le stesse credenziali Google già usate
    per il foglio principale degli ordini.
    """
    if not BOT_DB_SHEET_ID:
        raise RuntimeError(
            "BOT_DB_SHEET_ID non configurato."
        )

    credentials = get_google_credentials()

    client = gspread.authorize(
        credentials
    )

    try:
        spreadsheet = client.open_by_key(
            BOT_DB_SHEET_ID
        )

    except gspread.exceptions.SpreadsheetNotFound as error:
        raise RuntimeError(
            "Il Google Sheets BOT DB non è stato trovato. "
            "Controlla BOT_DB_SHEET_ID e verifica che il foglio "
            "sia condiviso con l'email del Service Account."
        ) from error

    return spreadsheet


def get_bot_db_worksheet(
    worksheet_name: str,
) -> gspread.Worksheet:
    """
    Restituisce una specifica scheda del BOT DB.
    """
    spreadsheet = get_bot_db_spreadsheet()

    try:
        worksheet = spreadsheet.worksheet(
            worksheet_name
        )

    except gspread.exceptions.WorksheetNotFound as error:
        raise RuntimeError(
            f"La scheda '{worksheet_name}' non esiste "
            "nel Google Sheets BOT DB."
        ) from error

    return worksheet


def get_profiles_worksheet() -> gspread.Worksheet:
    """
    Restituisce la scheda PROFILI.
    """
    return get_bot_db_worksheet(
        PROFILE_WORKSHEET_NAME
    )


def get_shipping_worksheet() -> gspread.Worksheet:
    """
    Restituisce la scheda SPEDIZIONI.
    """
    return get_bot_db_worksheet(
        SHIPPING_WORKSHEET_NAME
    )


def get_config_worksheet() -> gspread.Worksheet:
    """
    Restituisce la scheda CONFIG.
    """
    return get_bot_db_worksheet(
        CONFIG_WORKSHEET_NAME
    )


def get_log_worksheet() -> gspread.Worksheet:
    """
    Restituisce la scheda LOG.
    """
    return get_bot_db_worksheet(
        LOG_WORKSHEET_NAME
    )


def test_bot_db_connection() -> list[str]:
    """
    Verifica il collegamento al BOT DB.

    Restituisce i nomi delle schede presenti
    e controlla che esistano tutte quelle richieste.
    """
    spreadsheet = get_bot_db_spreadsheet()

    worksheet_names = [
        worksheet.title
        for worksheet in spreadsheet.worksheets()
    ]

    required_worksheets = {
        PROFILE_WORKSHEET_NAME,
        SHIPPING_WORKSHEET_NAME,
        CONFIG_WORKSHEET_NAME,
        LOG_WORKSHEET_NAME,
    }

    missing_worksheets = (
        required_worksheets
        - set(worksheet_names)
    )

    if missing_worksheets:
        missing_text = ", ".join(
            sorted(missing_worksheets)
        )

        raise RuntimeError(
            "Nel BOT DB mancano le seguenti schede: "
            f"{missing_text}"
        )

    return worksheet_names


def get_profile(
    telegram_id: int | str,
) -> dict | None:
    """
    Cerca un profilo tramite Telegram ID.

    Restituisce un dizionario con i dati del profilo
    oppure None se l'utente non è presente.
    """
    telegram_id = normalize_telegram_id(
        telegram_id
    )

    if not telegram_id:
        return None

    worksheet = get_profiles_worksheet()
    values = worksheet.get_all_values()

    if len(values) < 2:
        return None

    headers = [
        clean_value(header).upper()
        for header in values[0]
    ]

    for row_number, row_values in enumerate(
        values[1:],
        start=2,
    ):
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

            row[header] = clean_value(
                value
            )

        if row.get("TELEGRAM_ID") == telegram_id:
            row["_ROW_NUMBER"] = row_number
            return row

    return None


def save_profile(
    telegram_id: int | str,
    username: str | None,
    name: str,
    email: str,
    phone: str,
    address: str,
    postal_code: str,
    city: str,
    province: str,
) -> dict:
    """
    Crea un nuovo profilo oppure aggiorna quello esistente.

    Il profilo viene identificato tramite TELEGRAM_ID.
    """
    telegram_id = normalize_telegram_id(
        telegram_id
    )

    username = normalize_username(
        username
    )

    name = clean_value(
        name
    )

    email = clean_value(
        email
    )

    phone = clean_value(
        phone
    )

    address = clean_value(
        address
    )

    postal_code = clean_value(
        postal_code
    )

    city = clean_value(
        city
    )

    province = clean_value(
        province
    ).upper()

    if not telegram_id:
        raise ValueError(
            "Telegram ID mancante."
        )

    if not name:
        raise ValueError(
            "Nome e cognome mancanti."
        )

    if not email:
        raise ValueError(
            "Email mancante."
        )

    if not phone:
        raise ValueError(
            "Numero di telefono mancante."
        )

    if not address:
        raise ValueError(
            "Indirizzo mancante."
        )

    if not postal_code:
        raise ValueError(
            "CAP mancante."
        )

    if not city:
        raise ValueError(
            "Città mancante."
        )

    if not province:
        raise ValueError(
            "Provincia mancante."
        )

    updated_at = get_current_datetime()

    row_data = [
        telegram_id,
        username,
        name,
        email,
        phone,
        address,
        postal_code,
        city,
        province,
        updated_at,
    ]

    worksheet = get_profiles_worksheet()
    existing_profile = get_profile(
        telegram_id
    )

    if existing_profile:
        row_number = existing_profile[
            "_ROW_NUMBER"
        ]

        worksheet.update(
            range_name=f"A{row_number}:J{row_number}",
            values=[row_data],
        )

        action = "PROFILO_AGGIORNATO"

    else:
        worksheet.append_row(
            row_data,
            value_input_option="USER_ENTERED",
        )

        action = "PROFILO_CREATO"

    write_log(
        telegram_id=telegram_id,
        username=username,
        action=action,
        details=(
            f"Dati di spedizione salvati per "
            f"{name} - {city} ({province})"
        ),
    )

    return {
        "TELEGRAM_ID": telegram_id,
        "USERNAME": username,
        "NOME": name,
        "EMAIL": email,
        "TELEFONO": phone,
        "INDIRIZZO": address,
        "CAP": postal_code,
        "CITTA": city,
        "PROVINCIA": province,
        "DATA_AGGIORNAMENTO": updated_at,
    }


def delete_profile(
    telegram_id: int | str,
    username: str | None = None,
) -> bool:
    """
    Cancella dal foglio PROFILI i dati dell'utente.

    Restituisce True se il profilo è stato eliminato,
    False se il profilo non esisteva.
    """
    telegram_id = normalize_telegram_id(
        telegram_id
    )

    if not telegram_id:
        return False

    existing_profile = get_profile(
        telegram_id
    )

    if not existing_profile:
        return False

    row_number = existing_profile[
        "_ROW_NUMBER"
    ]

    worksheet = get_profiles_worksheet()
    worksheet.delete_rows(
        row_number
    )

    log_username = (
        normalize_username(username)
        or existing_profile.get(
            "USERNAME",
            "",
        )
    )

    write_log(
        telegram_id=telegram_id,
        username=log_username,
        action="PROFILO_ELIMINATO",
        details=(
            "L'utente ha eliminato i dati "
            "di spedizione salvati."
        ),
    )

    return True


def write_log(
    telegram_id: int | str = "",
    username: str | None = "",
    action: str = "",
    details: str = "",
    admin: str = "",
) -> None:
    """
    Registra un'operazione nel foglio LOG.

    Struttura prevista:
    DATA | TELEGRAM_ID | USERNAME | AZIONE | DETTAGLI | ADMIN
    """
    worksheet = get_log_worksheet()

    row_data = [
        get_current_datetime(),
        normalize_telegram_id(
            telegram_id
        ),
        normalize_username(
            username
        ),
        clean_value(
            action
        ).upper(),
        clean_value(
            details
        ),
        clean_value(
            admin
        ),
    ]

    worksheet.append_row(
        row_data,
        value_input_option="USER_ENTERED",
    )