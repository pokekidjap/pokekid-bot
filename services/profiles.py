"""Sincronizzazione leggera tra utenti Telegram e foglio PROFILI."""
from config import BOT_DB_SHEET_ID
from services.cache import invalidate
from services.bot_db import (
    PROFILE_WORKSHEET_NAME,
    _find_profile_in_values,
    get_current_datetime,
    get_profile_values,
    write_log,
)
from services.common import (
    clean_value,
    normalize_telegram_id,
    normalize_username,
)
from services.google_runtime import worksheet_session


SHIPPING_PROFILE_REQUIRED_FIELDS = (
    "NOME",
    "EMAIL",
    "TELEFONO",
    "INDIRIZZO",
    "CAP",
    "CITTA",
    "PROVINCIA",
)


def get_missing_shipping_profile_fields(
    profile: dict | None,
) -> list[str]:
    """Restituisce i campi obbligatori mancanti dal profilo di spedizione."""
    if not isinstance(profile, dict):
        return list(SHIPPING_PROFILE_REQUIRED_FIELDS)

    return [
        field
        for field in SHIPPING_PROFILE_REQUIRED_FIELDS
        if not clean_value(profile.get(field, ""))
    ]


def is_shipping_profile_complete(
    profile: dict | None,
) -> bool:
    """Indica se il profilo contiene tutti i dati necessari alla spedizione."""
    return not get_missing_shipping_profile_fields(profile)


def sync_basic_profile(telegram_id: int | str, username: str | None) -> dict:
    """Crea il profilo minimo o aggiorna lo username.

    Restituisce un dizionario con ``created`` e ``username_changed``.
    I dati di spedizione già salvati non vengono mai sovrascritti.
    """
    telegram_id = normalize_telegram_id(telegram_id)
    username = normalize_username(username)
    if not telegram_id:
        return {"created": False, "username_changed": False}

    with worksheet_session(
        BOT_DB_SHEET_ID,
        PROFILE_WORKSHEET_NAME,
    ) as session:
        profile_values = session.call(
            lambda worksheet: worksheet.get_all_values(),
            operation_name="lettura profilo Telegram da sincronizzare",
        )
        existing = _find_profile_in_values(
            telegram_id,
            profile_values,
        )
        now = get_current_datetime()

        if not existing:
            session.call(
                lambda worksheet: worksheet.append_row(
                    [telegram_id, username, "", "", "", "", "", "", "", now],
                    value_input_option="USER_ENTERED",
                ),
                operation_name="creazione profilo Telegram minimo",
            )
            result = {
                "created": True,
                "username_changed": False,
                "old_username": "",
                "new_username": username,
            }
            log_action = "UTENTE_REGISTRATO"
            log_details = (
                "Profilo minimo creato automaticamente "
                "all'avvio del bot."
            )
        else:
            old_username = normalize_username(
                existing.get("USERNAME", "")
            )
            if old_username == username:
                return {
                    "created": False,
                    "username_changed": False,
                }

            row_number = existing["_ROW_NUMBER"]
            session.call(
                lambda worksheet: worksheet.update(
                    range_name=f"B{row_number}:B{row_number}",
                    values=[[username]],
                ),
                operation_name="aggiornamento username profilo",
            )
            # La colonna J è DATA_AGGIORNAMENTO nella struttura attuale.
            session.call(
                lambda worksheet: worksheet.update(
                    range_name=f"J{row_number}:J{row_number}",
                    values=[[now]],
                ),
                operation_name="aggiornamento data profilo",
            )
            result = {
                "created": False,
                "username_changed": True,
                "old_username": old_username,
                "new_username": username,
                "name": clean_value(existing.get("NOME", "")),
            }
            log_action = "USERNAME_AGGIORNATO"
            log_details = (
                f"{old_username or '(nessuno)'} "
                f"-> {username or '(nessuno)'}"
            )

    invalidate("profiles")
    write_log(
        telegram_id=telegram_id,
        username=username,
        action=log_action,
        details=log_details,
    )
    return result


def get_profile_by_username(username: str | None) -> dict | None:
    """Cerca un profilo tramite username normalizzato."""
    target = normalize_username(username)
    if not target:
        return None

    values = get_profile_values()
    if len(values) < 2:
        return None

    headers = [clean_value(value).upper() for value in values[0]]
    for row_number, row_values in enumerate(values[1:], start=2):
        row = {
            header: clean_value(row_values[index] if index < len(row_values) else "")
            for index, header in enumerate(headers)
            if header
        }
        if normalize_username(row.get("USERNAME", "")) == target:
            row["_ROW_NUMBER"] = row_number
            return row
    return None


def get_all_profiles() -> list[dict]:
    """Restituisce tutti i profili con Telegram ID valido."""
    values = get_profile_values()
    if len(values) < 2:
        return []
    headers = [clean_value(value).upper() for value in values[0]]
    profiles = []
    for row_number, row_values in enumerate(values[1:], start=2):
        row = {
            header: clean_value(row_values[index] if index < len(row_values) else "")
            for index, header in enumerate(headers)
            if header
        }
        if normalize_telegram_id(row.get("TELEGRAM_ID", "")):
            row["_ROW_NUMBER"] = row_number
            row["USERNAME"] = normalize_username(row.get("USERNAME", ""))
            profiles.append(row)
    return profiles
