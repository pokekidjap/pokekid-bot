"""Definizioni e validatori delle fondamenta dati Spedizioni v2.1.

Il modulo non è collegato allo startup. Il gestionale ORDINI è soltanto una
sorgente di lettura: tutti gli schemi definiti qui appartengono al DATABASE
BOT, ad eccezione delle costanti usate per interpretare lo snapshot sorgente.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

import gspread

from config import BOT_DB_SHEET_ID
from services.common import clean_value, normalize_header
from services.google_runtime import worksheet_operation

ORDER_REGISTRY_WORKSHEET_NAME = "ORDINI_ARTICOLI"
ORDER_REGISTRY_HEADERS = (
    "ID_ARTICOLO",
    "SOURCE_SPREADSHEET_ID",
    "SOURCE_SHEET",
    "SOURCE_ROW",
    "IDENTITY_FINGERPRINT",
    "ROW_FINGERPRINT",
    "DUPLICATE_INDEX",
    "DATA",
    "OGGETTO",
    "QUANTITA",
    "COSTO",
    "VENDITA",
    "TOT_VENDITA",
    "USERNAME",
    "TELEGRAM_ID_PROPRIETARIO",
    "STATO_ORIGINE",
    "DATA_SPEDIZIONE",
    "NOTE",
    "FIRST_SEEN_AT",
    "LAST_SEEN_AT",
    "SYNC_STATUS",
    "IS_ACTIVE",
    "VERSIONE",
)
ORDER_SYNC_STATUSES = frozenset(
    {"OK", "MODIFICATO", "AMBIGUO", "NON_ASSOCIATO", "NON_PRESENTE"}
)
RESERVABLE_SYNC_STATUSES = frozenset({"OK", "MODIFICATO"})

SHIPPING_LEGACY_HEADERS = (
    "ID",
    "DATA",
    "TELEGRAM_ID",
    "USERNAME",
    "PRODOTTI",
    "STATO",
    "CORRIERE",
    "TRACKING",
    "PAYMENT_FILE_ID",
    "NOTE",
    "DATA_SPEDIZIONE",
    "ULTIMO_AGGIORNAMENTO",
    "ADMIN",
    "NOME",
    "EMAIL",
    "TELEFONO",
    "INDIRIZZO",
    "CAP",
    "CITTA",
    "PROVINCIA",
    "COSTO_SPEDIZIONE",
)
SHIPPING_V2_HEADERS = (
    "UUID_SPEDIZIONE",
    "IDEMPOTENCY_KEY",
    "VERSIONE_SCHEMA",
)

SHIPPING_ITEMS_WORKSHEET_NAME = "SPEDIZIONI_ARTICOLI"
SHIPPING_ITEMS_HEADERS = (
    "UUID_DETTAGLIO",
    "UUID_BOZZA",
    "UUID_SPEDIZIONE",
    "ID_SPEDIZIONE",
    "ID_ARTICOLO",
    "TELEGRAM_ID_PROPRIETARIO",
    "USERNAME_PROPRIETARIO",
    "RUOLO",
    "OGGETTO_SNAPSHOT",
    "QUANTITA_SNAPSHOT",
    "RIGA_ORDINE_SNAPSHOT",
    "STATO_PRENOTAZIONE",
    "PRENOTATO_IL",
    "PRENOTATO_FINO_AL",
    "CONFERMATO_IL",
    "SPEDITO_IL",
    "RILASCIATO_IL",
    "MOTIVO_RILASCIO",
    "IDEMPOTENCY_KEY",
    "ULTIMO_AGGIORNAMENTO",
    "VERSIONE",
)
RESERVATION_ROLES = frozenset({"TITOLARE", "CONTRIBUENTE"})
RESERVATION_STATES = frozenset(
    {"PRENOTATO", "CONFERMATO", "SPEDITO", "RILASCIATO"}
)
OCCUPYING_RESERVATION_STATES = frozenset(
    {"PRENOTATO", "CONFERMATO", "SPEDITO"}
)
LIVE_RESERVATION_STATES = frozenset({"PRENOTATO", "CONFERMATO"})


@dataclass
class SchemaValidationResult:
    valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def add_error(self, message: str) -> None:
        self.valid = False
        self.errors.append(message)

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalized_headers(values: list[list[Any]]) -> list[str]:
    if not values:
        return []
    headers = [normalize_header(value) for value in values[0]]
    while headers and not headers[-1]:
        headers.pop()
    return headers


def rows_as_dicts(
    values: list[list[Any]],
    expected_headers: tuple[str, ...],
) -> list[dict[str, str]]:
    headers = normalized_headers(values)
    if tuple(headers) != expected_headers:
        return []
    records = []
    for row_number, row in enumerate(values[1:], start=2):
        if not any(clean_value(value) for value in row):
            continue
        record = {
            header: clean_value(
                row[index] if index < len(row) else ""
            )
            for index, header in enumerate(headers)
        }
        record["_ROW_NUMBER"] = str(row_number)
        records.append(record)
    return records


def _duplicates(
    records: list[dict[str, str]],
    field_name: str,
    *,
    states: frozenset[str] | None = None,
) -> dict[str, list[int]]:
    rows_by_value: dict[str, list[int]] = defaultdict(list)
    display: dict[str, str] = {}
    for record in records:
        if states is not None and record.get(
            "STATO_PRENOTAZIONE", ""
        ).upper() not in states:
            continue
        value = clean_value(record.get(field_name, ""))
        if not value:
            continue
        key = value.upper()
        rows_by_value[key].append(int(record["_ROW_NUMBER"]))
        display.setdefault(key, value)
    return {
        display[key]: rows
        for key, rows in rows_by_value.items()
        if len(rows) > 1
    }


def _is_art_uuid4(value: str) -> bool:
    text = clean_value(value)
    if not text.startswith("ART-"):
        return False
    try:
        parsed = UUID(text[4:])
    except (ValueError, AttributeError):
        return False
    return (
        parsed.version == 4
        and str(parsed) == text[4:].lower()
    )


def _is_timezone_aware(value: str) -> bool:
    text = clean_value(value)
    if not text:
        return False
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def validate_order_registry(
    values: list[list[Any]],
) -> SchemaValidationResult:
    result = SchemaValidationResult()
    headers = normalized_headers(values)
    result.details["headers"] = headers
    if tuple(headers) != ORDER_REGISTRY_HEADERS:
        result.add_error(
            "ORDINI_ARTICOLI: intestazioni mancanti o fuori ordine."
        )
        return result
    records = rows_as_dicts(values, ORDER_REGISTRY_HEADERS)
    duplicates = _duplicates(records, "ID_ARTICOLO")
    result.details["duplicate_item_ids"] = duplicates
    if duplicates:
        result.add_error(
            "ORDINI_ARTICOLI contiene ID_ARTICOLO duplicati: "
            + ", ".join(sorted(duplicates))
        )
    empty_active = [
        int(record["_ROW_NUMBER"])
        for record in records
        if record.get("IS_ACTIVE", "").upper() == "TRUE"
        and not record.get("ID_ARTICOLO")
    ]
    result.details["active_rows_without_id"] = empty_active
    if empty_active:
        result.add_error(
            "ORDINI_ARTICOLI contiene record attivi senza ID_ARTICOLO."
        )
    invalid_item_ids = [
        int(record["_ROW_NUMBER"])
        for record in records
        if not _is_art_uuid4(record.get("ID_ARTICOLO", ""))
    ]
    result.details["invalid_item_id_format"] = invalid_item_ids
    if invalid_item_ids:
        result.add_error(
            "ORDINI_ARTICOLI contiene ID_ARTICOLO non conformi ad "
            "ART-UUIDv4."
        )
    invalid_source_rows = []
    missing_active_metadata = []
    for record in records:
        if record.get("IS_ACTIVE", "").upper() != "TRUE":
            continue
        try:
            source_row = int(record.get("SOURCE_ROW", ""))
        except ValueError:
            source_row = 0
        if source_row <= 0:
            invalid_source_rows.append(int(record["_ROW_NUMBER"]))
        if not all(
            clean_value(record.get(field, ""))
            for field in (
                "IDENTITY_FINGERPRINT",
                "ROW_FINGERPRINT",
                "VERSIONE",
            )
        ):
            missing_active_metadata.append(int(record["_ROW_NUMBER"]))
    result.details["invalid_active_source_rows"] = invalid_source_rows
    result.details["active_rows_missing_metadata"] = missing_active_metadata
    if invalid_source_rows:
        result.add_error(
            "ORDINI_ARTICOLI contiene SOURCE_ROW non numerici o non positivi "
            "nei record attivi."
        )
    if missing_active_metadata:
        result.add_error(
            "ORDINI_ARTICOLI contiene record attivi senza fingerprint o "
            "VERSIONE."
        )
    rows_without_version = [
        int(record["_ROW_NUMBER"])
        for record in records
        if not clean_value(record.get("VERSIONE", ""))
    ]
    if rows_without_version:
        result.add_error(
            "ORDINI_ARTICOLI contiene record senza VERSIONE."
        )
    invalid_status = [
        int(record["_ROW_NUMBER"])
        for record in records
        if record.get("SYNC_STATUS", "") not in ORDER_SYNC_STATUSES
    ]
    if invalid_status:
        result.add_error(
            "ORDINI_ARTICOLI contiene valori SYNC_STATUS non validi."
        )
    invalid_active = [
        int(record["_ROW_NUMBER"])
        for record in records
        if record.get("IS_ACTIVE", "").upper() not in {"TRUE", "FALSE"}
    ]
    if invalid_active:
        result.add_error(
            "ORDINI_ARTICOLI contiene valori IS_ACTIVE diversi da TRUE/FALSE."
        )
    return result


def validate_shipping_extension(
    values: list[list[Any]],
) -> SchemaValidationResult:
    result = SchemaValidationResult()
    headers = normalized_headers(values)
    result.details["headers"] = headers
    if tuple(headers[:21]) != SHIPPING_LEGACY_HEADERS:
        result.add_error(
            "SPEDIZIONI: le colonne A:U non coincidono con lo schema atteso."
        )
        return result
    if tuple(headers[21:24]) != SHIPPING_V2_HEADERS:
        result.add_error(
            "SPEDIZIONI: V:X devono essere UUID_SPEDIZIONE, "
            "IDEMPOTENCY_KEY, VERSIONE_SCHEMA."
        )
    if len(headers) > 24:
        result.add_warning(
            "SPEDIZIONI contiene colonne successive a X non gestite da v2.1."
        )
    return result


def validate_shipping_items(
    values: list[list[Any]],
) -> SchemaValidationResult:
    result = SchemaValidationResult()
    headers = normalized_headers(values)
    result.details["headers"] = headers
    if tuple(headers) != SHIPPING_ITEMS_HEADERS:
        result.add_error(
            "SPEDIZIONI_ARTICOLI: intestazioni mancanti o fuori ordine."
        )
        return result
    records = rows_as_dicts(values, SHIPPING_ITEMS_HEADERS)
    detail_duplicates = _duplicates(records, "UUID_DETTAGLIO")
    result.details["duplicate_detail_uuids"] = detail_duplicates
    if detail_duplicates:
        result.add_error(
            "SPEDIZIONI_ARTICOLI contiene UUID_DETTAGLIO duplicati."
        )
    duplicates = _duplicates(
        records,
        "ID_ARTICOLO",
        states=OCCUPYING_RESERVATION_STATES,
    )
    result.details["multiple_occupying_reservations"] = duplicates
    if duplicates:
        result.add_error(
            "SPEDIZIONI_ARTICOLI contiene più prenotazioni occupanti per: "
            + ", ".join(sorted(duplicates))
        )
    missing_fields: dict[str, list[int]] = defaultdict(list)
    invalid_roles = []
    invalid_states = []
    invalid_timestamps = []
    live_drafts: dict[str, list[dict[str, str]]] = defaultdict(list)
    for record in records:
        row_number = int(record["_ROW_NUMBER"])
        state = record.get("STATO_PRENOTAZIONE", "")
        for field_name in (
            "UUID_DETTAGLIO",
            "ID_ARTICOLO",
            "TELEGRAM_ID_PROPRIETARIO",
            "VERSIONE",
            "IDEMPOTENCY_KEY",
        ):
            if not clean_value(record.get(field_name, "")):
                missing_fields[field_name].append(row_number)
        if record.get("RUOLO") not in RESERVATION_ROLES:
            invalid_roles.append(row_number)
        if state not in RESERVATION_STATES:
            invalid_states.append(row_number)
            continue
        if state in LIVE_RESERVATION_STATES:
            if not clean_value(record.get("UUID_BOZZA", "")):
                missing_fields["UUID_BOZZA"].append(row_number)
            else:
                live_drafts[record["UUID_BOZZA"]].append(record)
        if state == "PRENOTATO":
            for field_name in ("PRENOTATO_IL", "PRENOTATO_FINO_AL"):
                if not _is_timezone_aware(record.get(field_name, "")):
                    invalid_timestamps.append(
                        {"row": row_number, "field": field_name}
                    )
        elif state == "CONFERMATO":
            if not clean_value(record.get("UUID_SPEDIZIONE", "")):
                missing_fields["UUID_SPEDIZIONE"].append(row_number)
            if not _is_timezone_aware(record.get("CONFERMATO_IL", "")):
                invalid_timestamps.append(
                    {"row": row_number, "field": "CONFERMATO_IL"}
                )
        elif state == "SPEDITO":
            if not clean_value(record.get("UUID_SPEDIZIONE", "")):
                missing_fields["UUID_SPEDIZIONE"].append(row_number)
            if not _is_timezone_aware(record.get("SPEDITO_IL", "")):
                invalid_timestamps.append(
                    {"row": row_number, "field": "SPEDITO_IL"}
                )
        elif state == "RILASCIATO":
            if not _is_timezone_aware(record.get("RILASCIATO_IL", "")):
                invalid_timestamps.append(
                    {"row": row_number, "field": "RILASCIATO_IL"}
                )
    result.details["missing_required_fields"] = dict(missing_fields)
    result.details["invalid_roles"] = invalid_roles
    result.details["invalid_states"] = invalid_states
    result.details["invalid_timestamps"] = invalid_timestamps
    if missing_fields:
        result.add_error(
            "SPEDIZIONI_ARTICOLI contiene campi obbligatori vuoti."
        )
    if invalid_roles:
        result.add_error(
            "SPEDIZIONI_ARTICOLI contiene RUOLO non validi."
        )
    if invalid_states:
        result.add_error(
            "SPEDIZIONI_ARTICOLI contiene STATO_PRENOTAZIONE non validi."
        )
    if invalid_timestamps:
        result.add_error(
            "SPEDIZIONI_ARTICOLI contiene timestamp mancanti, non validi "
            "o privi di timezone."
        )
    invalid_live_drafts = []
    for draft_uuid, draft_records in live_drafts.items():
        titular_ids = {
            record["TELEGRAM_ID_PROPRIETARIO"]
            for record in draft_records
            if record["RUOLO"] == "TITOLARE"
            and record["TELEGRAM_ID_PROPRIETARIO"]
        }
        if len(titular_ids) != 1:
            invalid_live_drafts.append(
                {
                    "uuid_bozza": draft_uuid,
                    "titular_ids": sorted(titular_ids),
                }
            )
    result.details["invalid_live_draft_holders"] = invalid_live_drafts
    if invalid_live_drafts:
        result.add_error(
            "Una bozza viva deve avere esattamente un Telegram ID titolare."
        )
    return result


def validate_shipping_v2_values(
    registry_values: list[list[Any]],
    shipping_values: list[list[Any]],
    shipping_items_values: list[list[Any]],
) -> SchemaValidationResult:
    result = SchemaValidationResult()
    checks = {
        "order_registry": validate_order_registry(registry_values),
        "shipping": validate_shipping_extension(shipping_values),
        "shipping_items": validate_shipping_items(shipping_items_values),
    }
    for name, check in checks.items():
        if not check.valid:
            result.valid = False
        result.errors.extend(check.errors)
        result.warnings.extend(check.warnings)
        result.details[name] = check.details

    registry = {
        record.get("ID_ARTICOLO", "").upper(): record
        for record in rows_as_dicts(
            registry_values,
            ORDER_REGISTRY_HEADERS,
        )
        if record.get("ID_ARTICOLO")
    }
    invalid_reservations = []
    for reservation in rows_as_dicts(
        shipping_items_values,
        SHIPPING_ITEMS_HEADERS,
    ):
        if reservation.get("STATO_PRENOTAZIONE") not in (
            LIVE_RESERVATION_STATES
        ):
            continue
        item_id = reservation.get("ID_ARTICOLO", "")
        item = registry.get(item_id.upper())
        if (
            item is None
            or item.get("IS_ACTIVE", "").upper() != "TRUE"
            or item.get("SYNC_STATUS") not in RESERVABLE_SYNC_STATUSES
            or item.get("STATO_ORIGINE", "").upper() != "IN MAGAZZINO"
            or not item.get("TELEGRAM_ID_PROPRIETARIO")
        ):
            invalid_reservations.append(
                {
                    "row": int(reservation["_ROW_NUMBER"]),
                    "item_id": item_id,
                }
            )
    result.details["invalid_live_reservations"] = invalid_reservations
    if invalid_reservations:
        result.add_error(
            "Sono presenti prenotazioni vive su articoli assenti, inattivi "
            "o ambigui."
        )
    return result


def validate_shipping_v2_schema(
    *,
    bot_db_spreadsheet_id: str | None = None,
    registry_worksheet: str = ORDER_REGISTRY_WORKSHEET_NAME,
    shipping_worksheet: str = "SPEDIZIONI",
    shipping_items_worksheet: str = SHIPPING_ITEMS_WORKSHEET_NAME,
) -> SchemaValidationResult:
    """Valida su richiesta il DATABASE BOT, senza coinvolgere lo startup."""
    spreadsheet_id = clean_value(
        bot_db_spreadsheet_id or BOT_DB_SHEET_ID
    )
    if not spreadsheet_id:
        result = SchemaValidationResult(valid=False)
        result.add_error("BOT_DB_SHEET_ID non configurato.")
        return result
    values: dict[str, list[list[Any]]] = {}
    for key, worksheet_name in (
        ("registry", registry_worksheet),
        ("shipping", shipping_worksheet),
        ("items", shipping_items_worksheet),
    ):
        try:
            values[key] = worksheet_operation(
                spreadsheet_id,
                worksheet_name,
                lambda worksheet: worksheet.get_all_values(),
                operation_name=f"validazione schema {worksheet_name}",
            )
        except gspread.exceptions.WorksheetNotFound:
            result = SchemaValidationResult(valid=False)
            result.add_error(f"La scheda {worksheet_name} non esiste.")
            return result
    return validate_shipping_v2_values(
        values["registry"],
        values["shipping"],
        values["items"],
    )
