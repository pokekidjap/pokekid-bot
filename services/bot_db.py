import time
from datetime import datetime
from zoneinfo import ZoneInfo

import gspread

from config import BOT_DB_SHEET_ID
from services.cache import get_or_set, invalidate
from services.common import (
    clean_value,
    is_truthy,
    normalize_header,
    normalize_telegram_id,
    normalize_username,
)
from services.perf import get_perf_context
from services.sheets import get_google_credentials
from services.ui import BOT_VERSION


PROFILE_WORKSHEET_NAME = "PROFILI"
ADMIN_WORKSHEET_NAME = "ADMIN"
SHIPPING_WORKSHEET_NAME = "SPEDIZIONI"
CONFIG_WORKSHEET_NAME = "CONFIG"
LOG_WORKSHEET_NAME = "LOG"

ITALY_TIMEZONE = ZoneInfo("Europe/Rome")


def _cached_values(cache_key: str, worksheet_factory, force: bool = False) -> list[list[str]]:
    def loader() -> list[list[str]]:
        perf = get_perf_context()
        start = time.perf_counter()
        values = worksheet_factory().get_all_values()
        if perf is not None:
            perf.sheet_call((time.perf_counter() - start) * 1000.0)
        return values

    return get_or_set(cache_key, loader, force=force)


def get_profile_values(force_refresh: bool = False) -> list[list[str]]:
    return _cached_values("profiles", get_profiles_worksheet, force=force_refresh)


def get_shipping_values(force_refresh: bool = False) -> list[list[str]]:
    return _cached_values("shipping", get_shipping_worksheet, force=force_refresh)


def _parse_shipping_headers(values: list[list[str]]) -> list[str]:
    return [clean_value(header).upper() for header in values[0]]


def _build_shipping_row(
    headers: list[str],
    row_values: list[str],
    row_number: int,
) -> dict | None:
    row = {}

    for index, header in enumerate(headers):
        if not header:
            continue

        value = row_values[index] if index < len(row_values) else ""
        row[header] = clean_value(value)

    if not row.get("ID"):
        return None

    row["_ROW_NUMBER"] = row_number
    return row


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


def get_admin_worksheet() -> gspread.Worksheet:
    """
    Restituisce la scheda ADMIN.
    """
    return get_bot_db_worksheet(
        ADMIN_WORKSHEET_NAME
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
        ADMIN_WORKSHEET_NAME,
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


def get_admins(
    active_only: bool = True,
) -> list[dict]:
    """
    Legge il foglio ADMIN.

    Se active_only è True, restituisce solamente
    gli amministratori con ATTIVO impostato su TRUE.
    """
    worksheet = get_admin_worksheet()
    values = _cached_values("admins", get_admin_worksheet)

    if len(values) < 2:
        return []

    headers = [
        clean_value(header).upper()
        for header in values[0]
    ]

    admins = []

    active_values = {
        "TRUE",
        "VERO",
        "SI",
        "SÌ",
        "1",
        "YES",
    }

    for row_number, row_values in enumerate(
        values[1:],
        start=2,
    ):
        row = {}

        for index, header in enumerate(headers):
            if not header:
                continue

            value = (
                row_values[index]
                if index < len(row_values)
                else ""
            )

            row[header] = clean_value(value)

        telegram_id = normalize_telegram_id(
            row.get("TELEGRAM_ID", "")
        )

        if not telegram_id:
            continue

        row["TELEGRAM_ID"] = telegram_id
        row["USERNAME"] = normalize_username(
            row.get("USERNAME", "")
        )
        row["RUOLO"] = clean_value(
            row.get("RUOLO", "")
        ).upper()
        row["ATTIVO"] = clean_value(
            row.get("ATTIVO", "")
        ).upper()
        row["_ROW_NUMBER"] = row_number

        if (
            active_only
            and row["ATTIVO"] not in active_values
        ):
            continue

        admins.append(row)

    return admins


def get_admin(
    telegram_id: int | str,
) -> dict | None:
    """
    Restituisce i dati di un amministratore attivo
    tramite Telegram ID, oppure None.
    """
    telegram_id = normalize_telegram_id(
        telegram_id
    )

    if not telegram_id:
        return None

    for admin in get_admins(
        active_only=True
    ):
        if (
            admin.get("TELEGRAM_ID")
            == telegram_id
        ):
            return admin

    return None


def is_admin(
    telegram_id: int | str,
) -> bool:
    """
    Controlla se il Telegram ID appartiene
    a un amministratore attivo.
    """
    return get_admin(
        telegram_id
    ) is not None


def is_owner(
    telegram_id: int | str,
) -> bool:
    """
    Controlla se il Telegram ID appartiene
    a un OWNER attivo.
    """
    admin = get_admin(
        telegram_id
    )

    if not admin:
        return False

    return admin.get(
        "RUOLO",
        "",
    ) == "OWNER"


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
    values = get_profile_values()

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

    invalidate("profiles")

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
    invalidate("logs")
def get_config_values() -> dict:
    """
    Legge il foglio CONFIG e restituisce
    tutte le configurazioni in un dizionario.

    Accetta intestazioni come:
    A - CHIAVE | B - VALORE | C - ATTIVO
    oppure:
    CHIAVE | VALORE | ATTIVO
    """
    worksheet = get_config_worksheet()
    values = _cached_values("config", get_config_worksheet)

    if len(values) < 2:
        return {}

    headers = []

    for header in values[0]:
        normalized_header = clean_value(
            header
        ).upper()

        if "-" in normalized_header:
            normalized_header = normalized_header.split(
                "-",
                1,
            )[1].strip()

        headers.append(
            normalized_header
        )

    config = {}

    for row_values in values[1:]:
        row = {}

        for index, header in enumerate(headers):
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

        key = row.get(
            "CHIAVE",
            "",
        ).upper()

        if not key:
            continue

        config[key] = {
            "value": row.get(
                "VALORE",
                "",
            ),
            "active": row.get(
                "ATTIVO",
                "",
            ),
        }

    return config


def get_active_shipping_methods() -> list[dict]:
    """
    Restituisce tutti i corrieri attivi presenti nel CONFIG.

    Esempio:
    CORRIERE_BRT | 10 | TRUE
    """
    config = get_config_values()
    shipping_methods = []

    for key, item in config.items():
        if not key.startswith("CORRIERE_"):
            continue

        active = is_truthy(item.get("active", ""))
        if not active:
            continue

        carrier_name = key.replace(
            "CORRIERE_",
            "",
            1,
        ).strip()

        price_text = clean_value(
            item.get("value", "")
        ).replace(",", ".")

        try:
            price = float(price_text)

        except ValueError:
            continue

        shipping_methods.append(
            {
                "name": carrier_name,
                "price": price,
            }
        )

    return shipping_methods


def get_paypal_email() -> str:
    """
    Restituisce l'email PayPal configurata.
    """
    config = get_config_values()

    paypal_config = config.get(
        "PAYPAL_EMAIL",
        {},
    )

    return clean_value(
        paypal_config.get("value", "")
    )


def generate_shipping_id() -> str:
    """
    Genera un ID spedizione progressivo giornaliero.

    Esempio:
    SP-20260722-001
    """
    worksheet = get_shipping_worksheet()
    perf = get_perf_context()
    start = time.perf_counter()
    values = worksheet.col_values(1)
    if perf is not None:
        perf.sheet_call((time.perf_counter() - start) * 1000.0)

    date_prefix = datetime.now(
        ITALY_TIMEZONE
    ).strftime(
        "SP-%Y%m%d"
    )

    progressive = 1

    for existing_id in values[1:]:
        existing_id = clean_value(existing_id)

        if not existing_id.startswith(date_prefix):
            continue

        try:
            existing_progressive = int(
                existing_id.rsplit(
                    "-",
                    1,
                )[1]
            )
            progressive = max(
                progressive,
                existing_progressive + 1,
            )

        except (
            IndexError,
            ValueError,
        ):
            continue

    return (
        f"{date_prefix}-"
        f"{progressive:03d}"
    )


def create_shipping_request(
    telegram_id: int | str,
    username: str | None,
    products: str,
    carrier: str,
    shipping_cost: float,
    payment_file_id: str,
    profile: dict,
    notes: str = "",
) -> dict:
    """
    Crea una nuova richiesta di spedizione.
    """
    telegram_id = normalize_telegram_id(
        telegram_id
    )

    username = normalize_username(
        username
    )

    products = clean_value(
        products
    )

    carrier = clean_value(
        carrier
    ).upper()

    payment_file_id = clean_value(
        payment_file_id
    )

    notes = clean_value(
        notes
    )

    if not telegram_id:
        raise ValueError(
            "Telegram ID mancante."
        )

    if not products:
        raise ValueError(
            "Prodotti mancanti."
        )

    if not carrier:
        raise ValueError(
            "Corriere mancante."
        )

    if not payment_file_id:
        raise ValueError(
            "Allegato pagamento mancante."
        )

    required_profile_fields = {
        "NOME",
        "EMAIL",
        "TELEFONO",
        "INDIRIZZO",
        "CAP",
        "CITTA",
        "PROVINCIA",
    }

    missing_fields = [
        field
        for field in required_profile_fields
        if not clean_value(
            profile.get(field, "")
        )
    ]

    if missing_fields:
        raise ValueError(
            "Profilo incompleto. Campi mancanti: "
            + ", ".join(
                sorted(missing_fields)
            )
        )

    shipping_id = generate_shipping_id()
    current_datetime = get_current_datetime()

    row_data = [
        shipping_id,
        current_datetime,
        telegram_id,
        username,
        products,
        "IN_ATTESA",
        carrier,
        "",
        payment_file_id,
        notes,
        "",
        current_datetime,
        "",
        clean_value(
            profile.get("NOME", "")
        ),
        clean_value(
            profile.get("EMAIL", "")
        ),
        clean_value(
            profile.get("TELEFONO", "")
        ),
        clean_value(
            profile.get("INDIRIZZO", "")
        ),
        clean_value(
            profile.get("CAP", "")
        ),
        clean_value(
            profile.get("CITTA", "")
        ),
        clean_value(
            profile.get("PROVINCIA", "")
        ).upper(),
        shipping_cost,
    ]

    worksheet = get_shipping_worksheet()
    perf = get_perf_context()
    start = time.perf_counter()
    worksheet.append_row(
        row_data,
        value_input_option="USER_ENTERED",
    )
    if perf is not None:
        perf.sheet_call((time.perf_counter() - start) * 1000.0)

    invalidate("shipping")
    write_log(
        telegram_id=telegram_id,
        username=username,
        action="RICHIESTA_SPEDIZIONE_CREATA",
        details=(
            f"Richiesta {shipping_id} creata "
            f"con corriere {carrier}."
        ),
    )

    return {
        "ID": shipping_id,
        "DATA": current_datetime,
        "TELEGRAM_ID": telegram_id,
        "USERNAME": username,
        "PRODOTTI": products,
        "STATO": "IN_ATTESA",
        "CORRIERE": carrier,
        "TRACKING": "",
        "PAYMENT_FILE_ID": payment_file_id,
        "NOTE": notes,
        "DATA_SPEDIZIONE": "",
        "ULTIMO_AGGIORNAMENTO": current_datetime,
        "ADMIN": "",
        "NOME": clean_value(
            profile.get("NOME", "")
        ),
        "EMAIL": clean_value(
            profile.get("EMAIL", "")
        ),
        "TELEFONO": clean_value(
            profile.get("TELEFONO", "")
        ),
        "INDIRIZZO": clean_value(
            profile.get("INDIRIZZO", "")
        ),
        "CAP": clean_value(
            profile.get("CAP", "")
        ),
        "CITTA": clean_value(
            profile.get("CITTA", "")
        ),
        "PROVINCIA": clean_value(
            profile.get("PROVINCIA", "")
        ).upper(),
        "COSTO_SPEDIZIONE": shipping_cost,
    }


def get_user_shipping_requests(
    telegram_id: int | str,
) -> list[dict]:
    """
    Restituisce tutte le spedizioni associate
    a un Telegram ID.
    """
    telegram_id = normalize_telegram_id(
        telegram_id
    )

    if not telegram_id:
        return []

    values = get_shipping_values()

    if len(values) < 2:
        return []

    headers = _parse_shipping_headers(values)
    shipping_requests = []

    for row_number, row_values in enumerate(
        values[1:],
        start=2,
    ):
        row = _build_shipping_row(
            headers,
            row_values,
            row_number,
        )

        if not row:
            continue

        if row.get("TELEGRAM_ID") != telegram_id:
            continue

        shipping_requests.append(row)

    shipping_requests.reverse()

    return shipping_requests

def get_all_shipping_requests(
    statuses: set[str] | None = None,
) -> list[dict]:
    """
    Restituisce tutte le richieste di spedizione.

    Se statuses è valorizzato, restituisce solamente
    le richieste con uno degli stati indicati.
    """
    values = get_shipping_values()

    if len(values) < 2:
        return []

    headers = _parse_shipping_headers(values)

    normalized_statuses = None

    if statuses is not None:
        normalized_statuses = {
            clean_value(status).upper()
            for status in statuses
            if clean_value(status)
        }

    shipping_requests = []

    for row_number, row_values in enumerate(
        values[1:],
        start=2,
    ):
        row = _build_shipping_row(
            headers,
            row_values,
            row_number,
        )

        if not row:
            continue

        status = clean_value(
            row.get("STATO", "")
        ).upper()

        if (
            normalized_statuses is not None
            and status not in normalized_statuses
        ):
            continue

        shipping_requests.append(row)

    shipping_requests.reverse()

    return shipping_requests


def get_shipping_request(
    shipping_id: str,
) -> dict | None:
    """
    Cerca una richiesta tramite il relativo ID.
    """
    shipping_id = clean_value(
        shipping_id
    ).upper()

    if not shipping_id:
        return None

    values = get_shipping_values()

    if len(values) < 2:
        return None

    headers = _parse_shipping_headers(values)

    for row_number, row_values in enumerate(
        values[1:],
        start=2,
    ):
        row = _build_shipping_row(
            headers,
            row_values,
            row_number,
        )

        if not row:
            continue

        if clean_value(
            row.get("ID", "")
        ).upper() == shipping_id:
            return row

    return None


def complete_shipping_request(
    shipping_id: str,
    tracking: str,
    admin: str,
) -> dict:
    """
    Salva il tracking e imposta la richiesta come SPEDITO.
    """
    shipping_id = clean_value(
        shipping_id
    ).upper()

    tracking = clean_value(
        tracking
    )

    admin = clean_value(
        admin
    )

    if not shipping_id:
        raise ValueError(
            "ID richiesta mancante."
        )

    if not tracking:
        raise ValueError(
            "Tracking mancante."
        )

    existing_request = get_shipping_request(
        shipping_id
    )

    if not existing_request:
        raise ValueError(
            "Richiesta di spedizione non trovata."
        )

    row_number = existing_request[
        "_ROW_NUMBER"
    ]

    current_datetime = get_current_datetime()

    # Colonne F:M:
    # STATO, CORRIERE, TRACKING, PAYMENT_FILE_ID,
    # NOTE, DATA_SPEDIZIONE, ULTIMO_AGGIORNAMENTO, ADMIN
    updated_values = [[
        "SPEDITO",
        existing_request.get("CORRIERE", ""),
        tracking,
        existing_request.get("PAYMENT_FILE_ID", ""),
        existing_request.get("NOTE", ""),
        current_datetime,
        current_datetime,
        admin,
    ]]

    worksheet = get_shipping_worksheet()

    worksheet.update(
        range_name=(
            f"F{row_number}:M{row_number}"
        ),
        values=updated_values,
        value_input_option="USER_ENTERED",
    )

    invalidate("shipping")
    write_log(
        telegram_id=existing_request.get(
            "TELEGRAM_ID",
            "",
        ),
        username=existing_request.get(
            "USERNAME",
            "",
        ),
        action="SPEDIZIONE_COMPLETATA",
        details=(
            f"Richiesta {shipping_id} impostata come "
            f"SPEDITO. Tracking: {tracking}."
        ),
        admin=admin,
    )

    existing_request.update(
        {
            "STATO": "SPEDITO",
            "TRACKING": tracking,
            "DATA_SPEDIZIONE": current_datetime,
            "ULTIMO_AGGIORNAMENTO": current_datetime,
            "ADMIN": admin,
        }
    )

    return existing_request


def set_config_value(key: str, value: str, active: str | bool | None = None) -> None:
    key = clean_value(key).upper()
    worksheet = get_config_worksheet()
    values = worksheet.get_all_values()
    if not values:
        worksheet.append_row(["A - CHIAVE", "B - VALORE", "C - ATTIVO"])
        values = worksheet.get_all_values()

    for row_number, row in enumerate(values[1:], start=2):
        current_key = clean_value(row[0] if row else "").upper()
        if current_key == key:
            current_active = row[2] if len(row) > 2 else ""
            final_active = current_active if active is None else ("TRUE" if active is True else "FALSE" if active is False else clean_value(active))
            worksheet.update(range_name=f"A{row_number}:C{row_number}", values=[[key, clean_value(value), final_active]])
            invalidate("config")
            return

    final_active = "" if active is None else ("TRUE" if active is True else "FALSE" if active is False else clean_value(active))
    worksheet.append_row([key, clean_value(value), final_active], value_input_option="USER_ENTERED")
    invalidate("config")


def is_sorting_active() -> bool:
    item = get_config_values().get("SMISTAMENTO", {})
    return is_truthy(item.get("value", "")) or is_truthy(item.get("active", ""))


def set_sorting_status(active: bool, admin: str = "") -> None:
    set_config_value("SMISTAMENTO", "TRUE" if active else "FALSE")
    write_log(action="SMISTAMENTO_AVVIATO" if active else "SMISTAMENTO_COMPLETATO", details="Stato smistamento aggiornato dal pannello admin.", admin=admin)


def get_bot_status() -> dict:
    profile_values = get_profile_values()
    shipping_values = get_shipping_values()
    shipments = []
    if len(shipping_values) >= 2:
        headers = [clean_value(header).upper() for header in shipping_values[0]]
        for row_values in shipping_values[1:]:
            row = {
                header: clean_value(row_values[index] if index < len(row_values) else "")
                for index, header in enumerate(headers)
                if header
            }
            if row.get("ID"):
                shipments.append(row)
    return {
        "profiles": max(len(profile_values) - 1, 0),
        "admins": len(get_admins()),
        "shipping_pending": sum(1 for x in shipments if x.get("STATO") == "IN_ATTESA"),
        "shipping_sent": sum(1 for x in shipments if x.get("STATO") == "SPEDITO"),
        "sorting": is_sorting_active(),
        "version": BOT_VERSION,
    }


def get_recent_logs(limit: int = 15) -> list[dict]:
    """Restituisce gli ultimi eventi del foglio LOG, dal più recente."""
    worksheet = get_log_worksheet()
    values = _cached_values("logs", get_log_worksheet)
    if len(values) < 2:
        return []
    headers = [clean_value(value).upper() for value in values[0]]
    result = []
    for row_values in reversed(values[1:]):
        row = {
            header: clean_value(row_values[index] if index < len(row_values) else "")
            for index, header in enumerate(headers)
            if header
        }
        if any(row.values()):
            result.append(row)
        if len(result) >= max(1, min(limit, 50)):
            break
    return result
