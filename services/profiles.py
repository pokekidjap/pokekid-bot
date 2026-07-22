"""Sincronizzazione leggera tra utenti Telegram e foglio PROFILI."""
from services.cache import get_or_set, invalidate
from services.bot_db import (
    get_current_datetime,
    get_profile,
    get_profile_values,
    get_profiles_worksheet,
    write_log,
)
from services.common import (
    clean_value,
    normalize_telegram_id,
    normalize_username,
)


def sync_basic_profile(telegram_id: int | str, username: str | None) -> dict:
    """Crea il profilo minimo o aggiorna lo username.

    Restituisce un dizionario con ``created`` e ``username_changed``.
    I dati di spedizione già salvati non vengono mai sovrascritti.
    """
    telegram_id = normalize_telegram_id(telegram_id)
    username = normalize_username(username)
    if not telegram_id:
        return {"created": False, "username_changed": False}

    worksheet = get_profiles_worksheet()
    existing = get_profile(telegram_id)
    now = get_current_datetime()

    if not existing:
        worksheet.append_row(
            [telegram_id, username, "", "", "", "", "", "", "", now],
            value_input_option="USER_ENTERED",
        )
        invalidate("profiles")
        write_log(
            telegram_id=telegram_id,
            username=username,
            action="UTENTE_REGISTRATO",
            details="Profilo minimo creato automaticamente all'avvio del bot.",
        )
        return {
            "created": True,
            "username_changed": False,
            "old_username": "",
            "new_username": username,
        }

    old_username = normalize_username(existing.get("USERNAME", ""))
    if old_username == username:
        return {"created": False, "username_changed": False}

    row_number = existing["_ROW_NUMBER"]
    worksheet.update(
        range_name=f"B{row_number}:B{row_number}",
        values=[[username]],
    )
    # La colonna J è DATA_AGGIORNAMENTO nella struttura attuale.
    worksheet.update(
        range_name=f"J{row_number}:J{row_number}",
        values=[[now]],
    )
    invalidate("profiles")
    write_log(
        telegram_id=telegram_id,
        username=username,
        action="USERNAME_AGGIORNATO",
        details=f"{old_username or '(nessuno)'} -> {username or '(nessuno)'}",
    )
    return {
        "created": False,
        "username_changed": True,
        "old_username": old_username,
        "new_username": username,
        "name": clean_value(existing.get("NOME", "")),
    }


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
