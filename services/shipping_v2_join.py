"""Unione diretta di articoli propri a una richiesta Shipping v2 esistente.

Il gestionale ORDINI non viene mai scritto. Le operazioni mutabili usano
esclusivamente il DATABASE BOT e rispettano l'ordine locale dei lock:

``ORDINI_ARTICOLI -> SPEDIZIONI_ARTICOLI -> SPEDIZIONI``.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from typing import Any, Callable, Iterable
from uuid import uuid4
from zoneinfo import ZoneInfo

from config import BOT_DB_SHEET_ID
from services.bot_db import SHIPPING_WORKSHEET_NAME, write_log
from services.cache import invalidate
from services.common import (
    clean_value,
    normalize_telegram_id,
    normalize_username,
    parse_quantity,
)
from services.google_runtime import worksheet_session
from services.order_registry import synchronize_order_registry
from services.profiles import get_profile_by_username
from services.shipping_v2 import PRODUCTS_MAX_LENGTH
from services.shipping_v2_schema import (
    OCCUPYING_RESERVATION_STATES,
    ORDER_REGISTRY_HEADERS,
    ORDER_REGISTRY_WORKSHEET_NAME,
    RESERVABLE_SYNC_STATUSES,
    SHIPPING_ITEMS_HEADERS,
    SHIPPING_ITEMS_WORKSHEET_NAME,
    SHIPPING_LEGACY_HEADERS,
    SHIPPING_V2_HEADERS,
    normalized_headers,
    rows_as_dicts,
    validate_shipping_v2_schema,
)
from services.shipping_v2_join_session import join_selection_digest

logger = logging.getLogger(__name__)
ITALY_TIMEZONE = ZoneInfo("Europe/Rome")
SHIPPING_HEADERS = SHIPPING_LEGACY_HEADERS + SHIPPING_V2_HEADERS
CONTRIBUTOR_ADDED_ACTION = "SHIPPING_V2_CONTRIBUTOR_ADDED"
ADMIN_CANCELLED_ACTION = "SHIPPING_V2_ANNULLATA_ADMIN"


class ShippingV2JoinError(RuntimeError):
    pass


class ShippingV2JoinSchemaError(ShippingV2JoinError):
    pass


class ShippingV2JoinConflictError(ShippingV2JoinError):
    pass


class ShippingV2JoinIdempotencyError(ShippingV2JoinConflictError):
    pass


class ShippingV2JoinTargetError(ShippingV2JoinError):
    pass


class ShippingV2JoinProfileNotFoundError(ShippingV2JoinTargetError):
    pass


class ShippingV2JoinInvalidProfileError(ShippingV2JoinTargetError):
    pass


class ShippingV2JoinSelfError(ShippingV2JoinTargetError):
    pass


class ShippingV2JoinNotFoundError(ShippingV2JoinTargetError):
    pass


class ShippingV2JoinMultipleTargetsError(ShippingV2JoinTargetError):
    pass


class ShippingV2AdminCancelError(ShippingV2JoinError):
    pass


def _aware_now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(ITALY_TIMEZONE)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ShippingV2JoinError("Il clock deve essere timezone-aware.")
    return current


def _iso(value: datetime) -> str:
    return _aware_now(value).isoformat(timespec="seconds")


def _display_time(value: datetime) -> str:
    return value.astimezone(ITALY_TIMEZONE).strftime("%d/%m/%Y %H:%M:%S")


def _a1_column(number: int) -> str:
    result = ""
    value = number
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _record_values(
    record: dict[str, Any],
    headers: tuple[str, ...],
) -> list[str]:
    return [clean_value(record.get(header, "")) for header in headers]


def _assert_schema(
    schema_validator: Callable[..., Any],
) -> None:
    result = schema_validator()
    if getattr(result, "valid", False):
        return
    errors = getattr(result, "errors", None) or [
        "Schema Shipping v2 non valido."
    ]
    raise ShippingV2JoinSchemaError(
        "; ".join(str(error) for error in errors)
    )


def _read_expected(
    session,
    headers: tuple[str, ...],
    label: str,
) -> tuple[list[list[Any]], list[dict[str, str]]]:
    values = session.call(
        lambda worksheet: worksheet.get_all_values(),
        operation_name=f"lettura {label}",
    )
    if tuple(normalized_headers(values)) != headers:
        raise ShippingV2JoinSchemaError(
            f"{label} non rispetta lo schema previsto."
        )
    return values, rows_as_dicts(values, headers)


def _shipping_records(
    values: list[list[Any]],
) -> list[dict[str, str]]:
    headers = normalized_headers(values)
    if tuple(headers[: len(SHIPPING_HEADERS)]) != SHIPPING_HEADERS:
        raise ShippingV2JoinSchemaError(
            "SPEDIZIONI non rispetta lo schema A:X previsto."
        )
    records = []
    for row_number, row in enumerate(values[1:], start=2):
        if not any(clean_value(value) for value in row):
            continue
        record = {
            header: clean_value(row[index] if index < len(row) else "")
            for index, header in enumerate(SHIPPING_HEADERS)
        }
        record["_ROW_NUMBER"] = str(row_number)
        records.append(record)
    return records


def _valid_telegram_id(value: int | str) -> str:
    normalized = normalize_telegram_id(value)
    try:
        parsed = int(normalized)
    except (TypeError, ValueError):
        return ""
    return normalized if parsed > 0 else ""


def _find_unique_request(
    records: list[dict[str, str]],
    *,
    shipping_id: str,
    shipping_uuid: str,
) -> dict[str, str]:
    wanted_id = clean_value(shipping_id).upper()
    wanted_uuid = clean_value(shipping_uuid)
    matches = [
        record
        for record in records
        if record.get("ID", "").upper() == wanted_id
        or (
            wanted_uuid
            and record.get("UUID_SPEDIZIONE", "") == wanted_uuid
        )
    ]
    if len(matches) != 1:
        raise ShippingV2JoinConflictError(
            "Richiesta di destinazione assente o duplicata."
        )
    request = matches[0]
    if (
        request.get("ID", "").upper() != wanted_id
        or request.get("UUID_SPEDIZIONE", "") != wanted_uuid
    ):
        raise ShippingV2JoinConflictError(
            "ID e UUID della richiesta indicano record differenti."
        )
    return request


def _linked_items(
    records: list[dict[str, str]],
    *,
    shipping_id: str,
    shipping_uuid: str,
) -> list[dict[str, str]]:
    wanted_id = clean_value(shipping_id).upper()
    wanted_uuid = clean_value(shipping_uuid)
    selected = [
        record
        for record in records
        if record.get("ID_SPEDIZIONE", "").upper() == wanted_id
        or (
            wanted_uuid
            and record.get("UUID_SPEDIZIONE", "") == wanted_uuid
        )
    ]
    if any(
        record.get("ID_SPEDIZIONE", "").upper() != wanted_id
        or record.get("UUID_SPEDIZIONE", "") != wanted_uuid
        for record in selected
    ):
        raise ShippingV2JoinConflictError(
            "Associazione articolo/spedizione incoerente."
        )
    return selected


def _validate_target(
    request: dict[str, str],
    all_items: list[dict[str, str]],
    *,
    target_id: str,
) -> tuple[str, list[dict[str, str]]]:
    if request.get("VERSIONE_SCHEMA", "").upper() != "V2":
        raise ShippingV2JoinConflictError(
            "La richiesta di destinazione non usa Shipping v2."
        )
    if request.get("STATO", "").upper() != "IN_ATTESA":
        raise ShippingV2JoinConflictError(
            "La richiesta di destinazione non è più in attesa."
        )
    if clean_value(request.get("TRACKING", "")):
        raise ShippingV2JoinConflictError(
            "La richiesta di destinazione possiede già un tracking."
        )
    if request.get("TELEGRAM_ID", "") != target_id:
        raise ShippingV2JoinConflictError(
            "La richiesta non appartiene all'utente di destinazione."
        )
    shipping_id = request.get("ID", "").upper()
    shipping_uuid = request.get("UUID_SPEDIZIONE", "")
    if not shipping_id or not shipping_uuid:
        raise ShippingV2JoinConflictError(
            "Identificativi della richiesta di destinazione mancanti."
        )
    linked = _linked_items(
        all_items,
        shipping_id=shipping_id,
        shipping_uuid=shipping_uuid,
    )
    if not linked:
        raise ShippingV2JoinConflictError(
            "La richiesta non contiene articoli collegati."
        )
    if {
        record.get("STATO_PRENOTAZIONE", "").upper()
        for record in linked
    } != {"CONFERMATO"}:
        raise ShippingV2JoinConflictError(
            "Gli articoli della richiesta non sono confermati."
        )
    draft_uuids = {
        clean_value(record.get("UUID_BOZZA", ""))
        for record in linked
    }
    if len(draft_uuids) != 1 or not next(iter(draft_uuids), ""):
        raise ShippingV2JoinConflictError(
            "La richiesta contiene UUID_BOZZA incoerenti."
        )
    titular_ids = {
        record.get("TELEGRAM_ID_PROPRIETARIO", "")
        for record in linked
        if record.get("RUOLO", "").upper() == "TITOLARE"
    }
    if titular_ids != {target_id}:
        raise ShippingV2JoinConflictError(
            "La richiesta non contiene un unico titolare coerente."
        )
    item_ids = [
        record.get("ID_ARTICOLO", "").upper()
        for record in linked
    ]
    if (
        any(not item_id for item_id in item_ids)
        or len(item_ids) != len(set(item_ids))
    ):
        raise ShippingV2JoinConflictError(
            "La richiesta contiene articoli mancanti o duplicati."
        )
    return next(iter(draft_uuids)), linked


def _products_from_snapshots(
    records: Iterable[dict[str, str]],
) -> str:
    products = []
    for record in records:
        name = clean_value(record.get("OGGETTO_SNAPSHOT", ""))
        quantity = clean_value(record.get("QUANTITA_SNAPSHOT", ""))
        row = clean_value(record.get("RIGA_ORDINE_SNAPSHOT", ""))
        if not name or not quantity:
            raise ShippingV2JoinConflictError(
                "Snapshot articolo incompleto."
            )
        text = f"{name} ×{quantity}"
        if row:
            text += f" [RIGA {row}]"
        products.append(text)
    if not products:
        raise ShippingV2JoinConflictError(
            "La richiesta non contiene articoli."
        )
    result = " | ".join(products)
    if len(result) > PRODUCTS_MAX_LENGTH:
        raise ShippingV2JoinConflictError(
            "PRODOTTI supera il limite operativo prudenziale."
        )
    return result


def _assert_unique_detail_ids(
    records: list[dict[str, str]],
) -> None:
    counts = Counter(
        clean_value(record.get("UUID_DETTAGLIO", ""))
        for record in records
        if clean_value(record.get("UUID_DETTAGLIO", ""))
    )
    if any(count > 1 for count in counts.values()):
        raise ShippingV2JoinConflictError(
            "SPEDIZIONI_ARTICOLI contiene UUID_DETTAGLIO duplicati."
        )


def _participants_from_items(
    items: Iterable[dict[str, str]],
) -> list[dict[str, str]]:
    participants: dict[str, dict[str, str]] = {}
    for item in items:
        telegram_id = _valid_telegram_id(
            item.get("TELEGRAM_ID_PROPRIETARIO", "")
        )
        if not telegram_id:
            continue
        role = clean_value(item.get("RUOLO", "")).upper()
        current = participants.get(telegram_id)
        candidate = {
            "TELEGRAM_ID": telegram_id,
            "USERNAME": normalize_username(
                item.get("USERNAME_PROPRIETARIO", "")
            ),
            "RUOLO": role,
        }
        if current is None or (
            current.get("RUOLO") != "TITOLARE"
            and role == "TITOLARE"
        ):
            participants[telegram_id] = candidate
    return sorted(
        participants.values(),
        key=lambda item: (
            0 if item.get("RUOLO") == "TITOLARE" else 1,
            item.get("USERNAME", ""),
            item.get("TELEGRAM_ID", ""),
        ),
    )


def _groups_from_items(
    items: Iterable[dict[str, str]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        owner_id = normalize_telegram_id(
            item.get("TELEGRAM_ID_PROPRIETARIO", "")
        )
        role = clean_value(item.get("RUOLO", "")).upper()
        key = (owner_id, role)
        group = grouped.setdefault(
            key,
            {
                "TELEGRAM_ID": owner_id,
                "USERNAME": normalize_username(
                    item.get("USERNAME_PROPRIETARIO", "")
                ),
                "RUOLO": role,
                "ITEMS": [],
                "NUMERO_ARTICOLI": 0,
                "QUANTITA_TOTALE": 0,
            },
        )
        group["ITEMS"].append(dict(item))
        group["NUMERO_ARTICOLI"] += 1
        group["QUANTITA_TOTALE"] += max(
            0,
            parse_quantity(item.get("QUANTITA_SNAPSHOT", "")),
        )
    return sorted(
        grouped.values(),
        key=lambda group: (
            0 if group["RUOLO"] == "TITOLARE" else 1,
            group["USERNAME"],
            group["TELEGRAM_ID"],
        ),
    )


class ShippingV2JoinCoordinator:
    def __init__(
        self,
        *,
        bot_db_spreadsheet_id: str | None = None,
        session_factory=worksheet_session,
        now_factory: Callable[[], datetime] | None = None,
        uuid_factory=uuid4,
        cache_invalidator: Callable[[str], Any] = invalidate,
        log_writer: Callable[..., Any] = write_log,
    ) -> None:
        self.spreadsheet_id = clean_value(
            bot_db_spreadsheet_id or BOT_DB_SHEET_ID
        )
        if not self.spreadsheet_id:
            raise ShippingV2JoinError("BOT_DB_SHEET_ID non configurato.")
        self._session_factory = session_factory
        self._now_factory = now_factory or (
            lambda: datetime.now(ITALY_TIMEZONE)
        )
        self._uuid_factory = uuid_factory
        self._cache_invalidator = cache_invalidator
        self._log_writer = log_writer

    def _now(self) -> datetime:
        return _aware_now(self._now_factory())

    def _log_safely(self, **kwargs) -> None:
        try:
            self._log_writer(**kwargs)
        except Exception:
            logger.exception("Log Shipping v2.3 non scritto")

    def _new_unique_uuid(self, existing: set[str]) -> str:
        for _ in range(10):
            candidate = str(self._uuid_factory())
            if candidate and candidate not in existing:
                existing.add(candidate)
                return candidate
        raise ShippingV2JoinConflictError(
            "Impossibile generare un UUID_DETTAGLIO univoco."
        )

    def find_joinable_by_owner(
        self,
        target_id: int | str,
    ) -> dict[str, str]:
        owner = _valid_telegram_id(target_id)
        if not owner:
            raise ShippingV2JoinInvalidProfileError(
                "Il profilo non contiene un Telegram ID valido."
            )
        with self._session_factory(
            self.spreadsheet_id,
            SHIPPING_ITEMS_WORKSHEET_NAME,
        ) as items_session:
            _, items = _read_expected(
                items_session,
                SHIPPING_ITEMS_HEADERS,
                "SPEDIZIONI_ARTICOLI",
            )
            with self._session_factory(
                self.spreadsheet_id,
                SHIPPING_WORKSHEET_NAME,
            ) as shipping_session:
                shipping_values = shipping_session.call(
                    lambda worksheet: worksheet.get_all_values(),
                    operation_name="ricerca spedizione V2 unibile",
                )
                requests = _shipping_records(shipping_values)
                candidates = []
                for request in requests:
                    if (
                        request.get("TELEGRAM_ID", "") != owner
                        or request.get("VERSIONE_SCHEMA", "").upper() != "V2"
                        or request.get("STATO", "").upper() != "IN_ATTESA"
                        or clean_value(request.get("TRACKING", ""))
                    ):
                        continue
                    try:
                        draft_uuid, _ = _validate_target(
                            request,
                            items,
                            target_id=owner,
                        )
                    except ShippingV2JoinConflictError:
                        continue
                    candidate = dict(request)
                    candidate["UUID_BOZZA"] = draft_uuid
                    candidates.append(candidate)
        if not candidates:
            raise ShippingV2JoinNotFoundError(
                "Nessuna spedizione V2 disponibile."
            )
        if len(candidates) > 1:
            raise ShippingV2JoinMultipleTargetsError(
                "Più spedizioni V2 disponibili per lo stesso utente."
            )
        return candidates[0]

    def get_joinable_items(
        self,
        *,
        contributor_id: int | str,
        target_id: int | str,
        shipping_id: str,
        shipping_uuid: str,
    ) -> list[dict[str, str]]:
        contributor = _valid_telegram_id(contributor_id)
        target = _valid_telegram_id(target_id)
        if not contributor or not target:
            raise ShippingV2JoinConflictError(
                "Telegram ID contribuente o titolare non valido."
            )
        if contributor == target:
            raise ShippingV2JoinSelfError(
                "Non è possibile unirsi alla propria spedizione."
            )
        with self._session_factory(
            self.spreadsheet_id,
            ORDER_REGISTRY_WORKSHEET_NAME,
        ) as registry_session:
            _, registry = _read_expected(
                registry_session,
                ORDER_REGISTRY_HEADERS,
                "ORDINI_ARTICOLI",
            )
            with self._session_factory(
                self.spreadsheet_id,
                SHIPPING_ITEMS_WORKSHEET_NAME,
            ) as items_session:
                _, items = _read_expected(
                    items_session,
                    SHIPPING_ITEMS_HEADERS,
                    "SPEDIZIONI_ARTICOLI",
                )
                with self._session_factory(
                    self.spreadsheet_id,
                    SHIPPING_WORKSHEET_NAME,
                ) as shipping_session:
                    shipping_values = shipping_session.call(
                        lambda worksheet: worksheet.get_all_values(),
                        operation_name="rilettura spedizione V2 di destinazione",
                    )
                    request = _find_unique_request(
                        _shipping_records(shipping_values),
                        shipping_id=shipping_id,
                        shipping_uuid=shipping_uuid,
                    )
                    _validate_target(request, items, target_id=target)
                    occupied = {
                        record.get("ID_ARTICOLO", "").upper()
                        for record in items
                        if record.get(
                            "STATO_PRENOTAZIONE", ""
                        ).upper() in OCCUPYING_RESERVATION_STATES
                    }
                    return [
                        dict(record)
                        for record in registry
                        if (
                            record.get("IS_ACTIVE", "").upper() == "TRUE"
                            and record.get("SYNC_STATUS", "")
                            in RESERVABLE_SYNC_STATUSES
                            and record.get(
                                "STATO_ORIGINE", ""
                            ).upper() == "IN MAGAZZINO"
                            and record.get(
                                "TELEGRAM_ID_PROPRIETARIO", ""
                            ) == contributor
                            and record.get(
                                "ID_ARTICOLO", ""
                            ).upper() not in occupied
                        )
                    ]

    @staticmethod
    def _validate_key_rows(
        key_rows: list[dict[str, str]],
        *,
        contributor_id: str,
        target_request: dict[str, str],
        draft_uuid: str,
        requested_ids: set[str],
        allow_partial: bool,
    ) -> set[str]:
        if not key_rows:
            return set()
        expected_shipping_id = target_request.get("ID", "").upper()
        expected_shipping_uuid = target_request.get(
            "UUID_SPEDIZIONE", ""
        )
        for row in key_rows:
            if (
                row.get("TELEGRAM_ID_PROPRIETARIO", "") != contributor_id
                or row.get("RUOLO", "").upper() != "CONTRIBUENTE"
                or row.get("UUID_BOZZA", "") != draft_uuid
                or row.get("ID_SPEDIZIONE", "").upper()
                != expected_shipping_id
                or row.get("UUID_SPEDIZIONE", "")
                != expected_shipping_uuid
                or row.get("STATO_PRENOTAZIONE", "").upper()
                != "CONFERMATO"
            ):
                raise ShippingV2JoinIdempotencyError(
                    "Idempotency key associata a un'unione differente."
                )
        present = {
            row.get("ID_ARTICOLO", "").upper()
            for row in key_rows
        }
        if (
            not present
            or not present.issubset(requested_ids)
            or (not allow_partial and present != requested_ids)
        ):
            raise ShippingV2JoinIdempotencyError(
                "Idempotency key riutilizzata con articoli differenti."
            )
        if len(present) != len(key_rows):
            raise ShippingV2JoinIdempotencyError(
                "Idempotency key contiene articoli duplicati."
            )
        return present

    def add_contributor_items(
        self,
        *,
        contributor_id: int | str,
        contributor_username: str | None,
        target_id: int | str,
        target_username: str | None,
        shipping_id: str,
        shipping_uuid: str,
        item_ids: Iterable[str],
        idempotency_key: str,
    ) -> dict[str, Any]:
        contributor = _valid_telegram_id(contributor_id)
        target = _valid_telegram_id(target_id)
        contributor_name = normalize_username(contributor_username)
        target_name = normalize_username(target_username)
        key = clean_value(idempotency_key)
        requested = [
            clean_value(item_id).upper()
            for item_id in item_ids
            if clean_value(item_id)
        ]
        if (
            not contributor
            or not target
            or not key
            or not requested
        ):
            raise ShippingV2JoinConflictError(
                "Dati obbligatori dell'unione mancanti."
            )
        if contributor == target:
            raise ShippingV2JoinSelfError(
                "Non è possibile unirsi alla propria spedizione."
            )
        if len(requested) != len(set(requested)):
            raise ShippingV2JoinConflictError(
                "La selezione contiene ID_ARTICOLO duplicati."
            )
        requested_set = set(requested)
        if (
            not key.startswith("JOIN-V2-")
            or key.rsplit("-", 1)[-1]
            != join_selection_digest(requested_set)
        ):
            raise ShippingV2JoinIdempotencyError(
                "Idempotency key non coerente con gli articoli richiesti."
            )
        now = self._now()
        timestamp = _iso(now)
        display_time = _display_time(now)

        with self._session_factory(
            self.spreadsheet_id,
            ORDER_REGISTRY_WORKSHEET_NAME,
        ) as registry_session:
            _, registry = _read_expected(
                registry_session,
                ORDER_REGISTRY_HEADERS,
                "ORDINI_ARTICOLI",
            )
            registry_by_id = {
                record.get("ID_ARTICOLO", "").upper(): record
                for record in registry
                if record.get("ID_ARTICOLO", "")
            }
            selected_registry = []
            for item_id in requested:
                item = registry_by_id.get(item_id)
                if not (
                    item
                    and item.get("IS_ACTIVE", "").upper() == "TRUE"
                    and item.get("SYNC_STATUS", "")
                    in RESERVABLE_SYNC_STATUSES
                    and item.get("STATO_ORIGINE", "").upper()
                    == "IN MAGAZZINO"
                    and item.get("TELEGRAM_ID_PROPRIETARIO", "")
                    == contributor
                ):
                    raise ShippingV2JoinConflictError(
                        "Uno o più articoli non sono più disponibili."
                    )
                selected_registry.append(item)

            with self._session_factory(
                self.spreadsheet_id,
                SHIPPING_ITEMS_WORKSHEET_NAME,
            ) as items_session:
                _, items = _read_expected(
                    items_session,
                    SHIPPING_ITEMS_HEADERS,
                    "SPEDIZIONI_ARTICOLI",
                )
                _assert_unique_detail_ids(items)
                with self._session_factory(
                    self.spreadsheet_id,
                    SHIPPING_WORKSHEET_NAME,
                ) as shipping_session:
                    shipping_values = shipping_session.call(
                        lambda worksheet: worksheet.get_all_values(),
                        operation_name="rilettura SPEDIZIONI per unione V2",
                    )
                    request = _find_unique_request(
                        _shipping_records(shipping_values),
                        shipping_id=shipping_id,
                        shipping_uuid=shipping_uuid,
                    )
                    draft_uuid, linked_before = _validate_target(
                        request,
                        items,
                        target_id=target,
                    )

                    key_rows = [
                        row
                        for row in items
                        if row.get("IDEMPOTENCY_KEY", "") == key
                    ]
                    present = self._validate_key_rows(
                        key_rows,
                        contributor_id=contributor,
                        target_request=request,
                        draft_uuid=draft_uuid,
                        requested_ids=requested_set,
                        allow_partial=True,
                    )
                    occupying: dict[str, list[dict[str, str]]] = {}
                    for row in items:
                        if row.get(
                            "STATO_PRENOTAZIONE", ""
                        ).upper() not in OCCUPYING_RESERVATION_STATES:
                            continue
                        occupying.setdefault(
                            row.get("ID_ARTICOLO", "").upper(),
                            [],
                        ).append(row)
                    for item_id in requested:
                        conflicts = [
                            row
                            for row in occupying.get(item_id, [])
                            if row.get("IDEMPOTENCY_KEY", "") != key
                        ]
                        if conflicts:
                            raise ShippingV2JoinConflictError(
                                "Uno o più articoli sono già occupati."
                            )

                    existing_detail_ids = {
                        row.get("UUID_DETTAGLIO", "")
                        for row in items
                        if row.get("UUID_DETTAGLIO", "")
                    }
                    new_by_id = {}
                    for item in selected_registry:
                        item_id = item["ID_ARTICOLO"].upper()
                        if item_id in present:
                            continue
                        new_by_id[item_id] = {
                            "UUID_DETTAGLIO": self._new_unique_uuid(
                                existing_detail_ids
                            ),
                            "UUID_BOZZA": draft_uuid,
                            "UUID_SPEDIZIONE": request["UUID_SPEDIZIONE"],
                            "ID_SPEDIZIONE": request["ID"],
                            "ID_ARTICOLO": item["ID_ARTICOLO"],
                            "TELEGRAM_ID_PROPRIETARIO": contributor,
                            "USERNAME_PROPRIETARIO": (
                                contributor_name
                                or normalize_username(item.get("USERNAME", ""))
                            ),
                            "RUOLO": "CONTRIBUENTE",
                            "OGGETTO_SNAPSHOT": item["OGGETTO"],
                            "QUANTITA_SNAPSHOT": item["QUANTITA"],
                            "RIGA_ORDINE_SNAPSHOT": item["SOURCE_ROW"],
                            "STATO_PRENOTAZIONE": "CONFERMATO",
                            "PRENOTATO_IL": "",
                            "PRENOTATO_FINO_AL": "",
                            "CONFERMATO_IL": timestamp,
                            "SPEDITO_IL": "",
                            "RILASCIATO_IL": "",
                            "MOTIVO_RILASCIO": "",
                            "IDEMPOTENCY_KEY": key,
                            "ULTIMO_AGGIORNAMENTO": timestamp,
                            "VERSIONE": "V1",
                        }

                    projected = list(linked_before) + list(new_by_id.values())
                    _products_from_snapshots(projected)

                    def append_or_reconcile(worksheet):
                        latest = rows_as_dicts(
                            worksheet.get_all_values(),
                            SHIPPING_ITEMS_HEADERS,
                        )
                        _assert_unique_detail_ids(latest)
                        latest_key_rows = [
                            row
                            for row in latest
                            if row.get("IDEMPOTENCY_KEY", "") == key
                        ]
                        latest_present = self._validate_key_rows(
                            latest_key_rows,
                            contributor_id=contributor,
                            target_request=request,
                            draft_uuid=draft_uuid,
                            requested_ids=requested_set,
                            allow_partial=True,
                        )
                        latest_occupying = {
                            row.get("ID_ARTICOLO", "").upper(): row
                            for row in latest
                            if row.get(
                                "STATO_PRENOTAZIONE", ""
                            ).upper() in OCCUPYING_RESERVATION_STATES
                            and row.get("IDEMPOTENCY_KEY", "") != key
                        }
                        if requested_set.intersection(latest_occupying):
                            raise ShippingV2JoinConflictError(
                                "Uno o più articoli sono già occupati."
                            )
                        missing = [
                            new_by_id[item_id]
                            for item_id in requested
                            if item_id not in latest_present
                        ]
                        if missing:
                            worksheet.append_rows(
                                [
                                    _record_values(
                                        record,
                                        SHIPPING_ITEMS_HEADERS,
                                    )
                                    for record in missing
                                ],
                                value_input_option="USER_ENTERED",
                            )
                        return missing

                    items_session.call(
                        append_or_reconcile,
                        operation_name=(
                            "aggiunta idempotente contributor Shipping v2"
                        ),
                    )

                    _, items_after_append = _read_expected(
                        items_session,
                        SHIPPING_ITEMS_HEADERS,
                        "SPEDIZIONI_ARTICOLI",
                    )
                    _assert_unique_detail_ids(items_after_append)
                    final_key_rows = [
                        row
                        for row in items_after_append
                        if row.get("IDEMPOTENCY_KEY", "") == key
                    ]
                    self._validate_key_rows(
                        final_key_rows,
                        contributor_id=contributor,
                        target_request=request,
                        draft_uuid=draft_uuid,
                        requested_ids=requested_set,
                        allow_partial=False,
                    )
                    final_linked = _linked_items(
                        items_after_append,
                        shipping_id=request["ID"],
                        shipping_uuid=request["UUID_SPEDIZIONE"],
                    )
                    products = _products_from_snapshots(final_linked)

                    latest_shipping = _shipping_records(
                        shipping_session.call(
                            lambda worksheet: worksheet.get_all_values(),
                            operation_name=(
                                "rilettura destinazione prima di PRODOTTI"
                            ),
                        )
                    )
                    latest_request = _find_unique_request(
                        latest_shipping,
                        shipping_id=request["ID"],
                        shipping_uuid=request["UUID_SPEDIZIONE"],
                    )
                    _validate_target(
                        latest_request,
                        items_after_append,
                        target_id=target,
                    )
                    shipping_row = latest_request["_ROW_NUMBER"]
                    shipping_session.call(
                        lambda worksheet: worksheet.batch_update(
                            [
                                {
                                    "range": f"E{shipping_row}:E{shipping_row}",
                                    "values": [[products]],
                                },
                                {
                                    "range": f"L{shipping_row}:L{shipping_row}",
                                    "values": [[display_time]],
                                },
                            ],
                            value_input_option="USER_ENTERED",
                        ),
                        operation_name="aggiornamento PRODOTTI unione V2",
                    )

                    verified_shipping = _shipping_records(
                        shipping_session.call(
                            lambda worksheet: worksheet.get_all_values(),
                            operation_name="verifica finale SPEDIZIONI unione V2",
                        )
                    )
                    verified_request = _find_unique_request(
                        verified_shipping,
                        shipping_id=request["ID"],
                        shipping_uuid=request["UUID_SPEDIZIONE"],
                    )
                    if (
                        verified_request.get("STATO", "").upper()
                        != "IN_ATTESA"
                        or clean_value(verified_request.get("TRACKING", ""))
                        or verified_request.get("PRODOTTI", "") != products
                        or verified_request.get("TELEGRAM_ID", "") != target
                    ):
                        raise ShippingV2JoinConflictError(
                            "Verifica finale della richiesta non coerente."
                        )
                    _, verified_items = _read_expected(
                        items_session,
                        SHIPPING_ITEMS_HEADERS,
                        "SPEDIZIONI_ARTICOLI",
                    )
                    verified_key_rows = [
                        row
                        for row in verified_items
                        if row.get("IDEMPOTENCY_KEY", "") == key
                    ]
                    self._validate_key_rows(
                        verified_key_rows,
                        contributor_id=contributor,
                        target_request=verified_request,
                        draft_uuid=draft_uuid,
                        requested_ids=requested_set,
                        allow_partial=False,
                    )
                    verified_linked = _linked_items(
                        verified_items,
                        shipping_id=verified_request["ID"],
                        shipping_uuid=verified_request["UUID_SPEDIZIONE"],
                    )
                    if (
                        _products_from_snapshots(verified_linked)
                        != verified_request["PRODOTTI"]
                    ):
                        raise ShippingV2JoinConflictError(
                            "PRODOTTI non coincide con gli articoli collegati."
                        )

        self._cache_invalidator("shipping")
        self._log_safely(
            telegram_id=contributor,
            username=contributor_name,
            action=CONTRIBUTOR_ADDED_ACTION,
            details=(
                f"Richiesta {verified_request['ID']}; "
                f"titolare_id={target}; titolare={target_name}; "
                f"contribuente_id={contributor}; "
                f"contribuente={contributor_name}; "
                f"articoli={len(verified_key_rows)}"
            ),
        )
        return {
            "shipping": dict(verified_request),
            "added_items": [dict(row) for row in verified_key_rows],
            "all_items": [dict(row) for row in verified_linked],
            "participants": _participants_from_items(verified_linked),
            "created_count": len(new_by_id),
        }

    def get_items_for_shipping(
        self,
        *,
        shipping_id: str,
        shipping_uuid: str = "",
    ) -> list[dict[str, str]]:
        with self._session_factory(
            self.spreadsheet_id,
            SHIPPING_ITEMS_WORKSHEET_NAME,
        ) as session:
            _, items = _read_expected(
                session,
                SHIPPING_ITEMS_HEADERS,
                "SPEDIZIONI_ARTICOLI",
            )
        resolved_uuid = clean_value(shipping_uuid)
        if not resolved_uuid:
            uuids = {
                row.get("UUID_SPEDIZIONE", "")
                for row in items
                if (
                    row.get("ID_SPEDIZIONE", "").upper()
                    == clean_value(shipping_id).upper()
                    and row.get("UUID_SPEDIZIONE", "")
                )
            }
            if len(uuids) != 1:
                raise ShippingV2JoinConflictError(
                    "UUID della richiesta assente o ambiguo."
                )
            resolved_uuid = next(iter(uuids))
        selected = _linked_items(
            items,
            shipping_id=shipping_id,
            shipping_uuid=resolved_uuid,
        )
        if not selected:
            raise ShippingV2JoinConflictError(
                "Nessun articolo collegato alla richiesta V2."
            )
        return selected

    def cancel_by_admin(
        self,
        *,
        shipping_id: str,
        admin: str,
    ) -> dict[str, Any]:
        wanted_id = clean_value(shipping_id).upper()
        admin_id = clean_value(admin)
        if not wanted_id or not admin_id:
            raise ShippingV2AdminCancelError(
                "ID richiesta e admin sono obbligatori."
            )
        now = self._now()
        timestamp = _iso(now)
        display_time = _display_time(now)
        with self._session_factory(
            self.spreadsheet_id,
            SHIPPING_ITEMS_WORKSHEET_NAME,
        ) as items_session:
            _, items = _read_expected(
                items_session,
                SHIPPING_ITEMS_HEADERS,
                "SPEDIZIONI_ARTICOLI",
            )
            with self._session_factory(
                self.spreadsheet_id,
                SHIPPING_WORKSHEET_NAME,
            ) as shipping_session:
                requests = _shipping_records(
                    shipping_session.call(
                        lambda worksheet: worksheet.get_all_values(),
                        operation_name="lettura richiesta da annullare",
                    )
                )
                matches = [
                    request
                    for request in requests
                    if request.get("ID", "").upper() == wanted_id
                ]
                if len(matches) != 1:
                    raise ShippingV2AdminCancelError(
                        "Richiesta assente o duplicata."
                    )
                request = matches[0]
                if request.get("VERSIONE_SCHEMA", "").upper() != "V2":
                    raise ShippingV2AdminCancelError(
                        "Le richieste legacy non sono annullabili qui."
                    )
                state = request.get("STATO", "").upper()
                if state == "SPEDITO":
                    raise ShippingV2AdminCancelError(
                        "Una richiesta spedita non è annullabile."
                    )
                if state not in {"IN_ATTESA", "ANNULLATO"}:
                    raise ShippingV2AdminCancelError(
                        "Stato della richiesta non annullabile."
                    )
                if clean_value(request.get("TRACKING", "")):
                    raise ShippingV2AdminCancelError(
                        "Una richiesta con tracking non è annullabile."
                    )
                shipping_uuid = request.get("UUID_SPEDIZIONE", "")
                linked = _linked_items(
                    items,
                    shipping_id=wanted_id,
                    shipping_uuid=shipping_uuid,
                )
                if not linked:
                    raise ShippingV2AdminCancelError(
                        "Nessun articolo collegato alla richiesta."
                    )
                states = {
                    row.get("STATO_PRENOTAZIONE", "").upper()
                    for row in linked
                }
                if "SPEDITO" in states:
                    raise ShippingV2AdminCancelError(
                        "Sono presenti articoli già spediti."
                    )
                if not states.issubset(
                    {"PRENOTATO", "CONFERMATO", "RILASCIATO"}
                ):
                    raise ShippingV2AdminCancelError(
                        "Stati articolo non annullabili."
                    )
                to_release = [
                    row
                    for row in linked
                    if row.get(
                        "STATO_PRENOTAZIONE", ""
                    ).upper() in {"PRENOTATO", "CONFERMATO"}
                ]
                for row in to_release:
                    row["STATO_PRENOTAZIONE"] = "RILASCIATO"
                    row["PRENOTATO_FINO_AL"] = ""
                    row["RILASCIATO_IL"] = timestamp
                    row["MOTIVO_RILASCIO"] = (
                        f"ANNULLATA_ADMIN:{admin_id}"
                    )
                    row["ULTIMO_AGGIORNAMENTO"] = timestamp
                if to_release:
                    end = _a1_column(len(SHIPPING_ITEMS_HEADERS))
                    payload = [
                        {
                            "range": (
                                f"A{row['_ROW_NUMBER']}:"
                                f"{end}{row['_ROW_NUMBER']}"
                            ),
                            "values": [[
                                clean_value(row.get(header, ""))
                                for header in SHIPPING_ITEMS_HEADERS
                            ]],
                        }
                        for row in to_release
                    ]
                    items_session.call(
                        lambda worksheet: worksheet.batch_update(
                            payload,
                            value_input_option="USER_ENTERED",
                        ),
                        operation_name="rilascio articoli annullamento admin",
                    )

                if state != "ANNULLATO" or to_release:
                    row_number = request["_ROW_NUMBER"]
                    shipping_session.call(
                        lambda worksheet: worksheet.batch_update(
                            [
                                {
                                    "range": f"F{row_number}:F{row_number}",
                                    "values": [["ANNULLATO"]],
                                },
                                {
                                    "range": f"L{row_number}:M{row_number}",
                                    "values": [[display_time, admin_id]],
                                },
                            ],
                            value_input_option="USER_ENTERED",
                        ),
                        operation_name="annullamento richiesta Shipping v2",
                    )

                verified_requests = _shipping_records(
                    shipping_session.call(
                        lambda worksheet: worksheet.get_all_values(),
                        operation_name="verifica richiesta annullata",
                    )
                )
                verified = [
                    row
                    for row in verified_requests
                    if row.get("ID", "").upper() == wanted_id
                ]
                _, verified_items = _read_expected(
                    items_session,
                    SHIPPING_ITEMS_HEADERS,
                    "SPEDIZIONI_ARTICOLI",
                )
                verified_linked = _linked_items(
                    verified_items,
                    shipping_id=wanted_id,
                    shipping_uuid=shipping_uuid,
                )
                if (
                    len(verified) != 1
                    or verified[0].get("STATO", "").upper() != "ANNULLATO"
                    or clean_value(verified[0].get("TRACKING", ""))
                    or not verified_linked
                    or {
                        row.get("STATO_PRENOTAZIONE", "").upper()
                        for row in verified_linked
                    } != {"RILASCIATO"}
                ):
                    raise ShippingV2AdminCancelError(
                        "Annullamento cross-worksheet non coerente."
                    )
                final_request = verified[0]

        self._cache_invalidator("shipping")
        self._log_safely(
            telegram_id=final_request.get("TELEGRAM_ID", ""),
            username=final_request.get("USERNAME", ""),
            action=ADMIN_CANCELLED_ACTION,
            details=(
                f"Richiesta {wanted_id} annullata; "
                f"articoli={len(verified_linked)}"
            ),
            admin=admin_id,
        )
        return {
            "shipping": dict(final_request),
            "items": [dict(row) for row in verified_linked],
            "participants": _participants_from_items(verified_linked),
            "already_coherent": (
                state == "ANNULLATO" and not to_release
            ),
        }


def _coordinator() -> ShippingV2JoinCoordinator:
    return ShippingV2JoinCoordinator()


def find_joinable_v2_shipping_by_username(
    username: str | None,
    contributor_id: int | str,
    *,
    profile_getter: Callable[[str | None], dict | None] = (
        get_profile_by_username
    ),
    coordinator: ShippingV2JoinCoordinator | None = None,
) -> dict[str, str]:
    normalized = normalize_username(username)
    if not normalized:
        raise ShippingV2JoinProfileNotFoundError(
            "Username Telegram mancante."
        )
    profile = profile_getter(normalized)
    if not profile:
        raise ShippingV2JoinProfileNotFoundError(
            "Username Telegram non presente in PROFILI."
        )
    target_id = _valid_telegram_id(profile.get("TELEGRAM_ID", ""))
    if not target_id:
        raise ShippingV2JoinInvalidProfileError(
            "Il profilo non contiene un Telegram ID valido."
        )
    contributor = _valid_telegram_id(contributor_id)
    if target_id == contributor:
        raise ShippingV2JoinSelfError(
            "Non è possibile unirsi alla propria spedizione."
        )
    service = coordinator or _coordinator()
    request = service.find_joinable_by_owner(target_id)
    result = dict(request)
    result["TARGET_TELEGRAM_ID"] = target_id
    result["TARGET_USERNAME"] = normalized
    return result


def get_joinable_items_for_contributor(
    *,
    contributor_id: int | str,
    target_id: int | str,
    shipping_id: str,
    shipping_uuid: str,
    schema_validator: Callable[..., Any] = validate_shipping_v2_schema,
    synchronize: Callable[..., Any] = synchronize_order_registry,
    coordinator: ShippingV2JoinCoordinator | None = None,
) -> list[dict[str, str]]:
    _assert_schema(schema_validator)
    synchronize()
    return (coordinator or _coordinator()).get_joinable_items(
        contributor_id=contributor_id,
        target_id=target_id,
        shipping_id=shipping_id,
        shipping_uuid=shipping_uuid,
    )


def add_contributor_items_to_v2_shipping(
    *,
    contributor_id: int | str,
    contributor_username: str | None,
    target_id: int | str,
    target_username: str | None,
    shipping_id: str,
    shipping_uuid: str,
    item_ids: Iterable[str],
    idempotency_key: str,
    schema_validator: Callable[..., Any] = validate_shipping_v2_schema,
    synchronize: Callable[..., Any] = synchronize_order_registry,
    coordinator: ShippingV2JoinCoordinator | None = None,
) -> dict[str, Any]:
    _assert_schema(schema_validator)
    synchronize()
    return (coordinator or _coordinator()).add_contributor_items(
        contributor_id=contributor_id,
        contributor_username=contributor_username,
        target_id=target_id,
        target_username=target_username,
        shipping_id=shipping_id,
        shipping_uuid=shipping_uuid,
        item_ids=item_ids,
        idempotency_key=idempotency_key,
    )


def get_v2_shipping_participants(
    *,
    shipping_id: str,
    shipping_uuid: str = "",
    coordinator: ShippingV2JoinCoordinator | None = None,
) -> list[dict[str, str]]:
    items = (coordinator or _coordinator()).get_items_for_shipping(
        shipping_id=shipping_id,
        shipping_uuid=shipping_uuid,
    )
    return _participants_from_items(items)


def get_v2_shipping_items_grouped_by_owner(
    *,
    shipping_id: str,
    shipping_uuid: str = "",
    coordinator: ShippingV2JoinCoordinator | None = None,
) -> list[dict[str, Any]]:
    items = (coordinator or _coordinator()).get_items_for_shipping(
        shipping_id=shipping_id,
        shipping_uuid=shipping_uuid,
    )
    return _groups_from_items(items)


def cancel_v2_shipping_request_by_admin(
    *,
    shipping_id: str,
    admin: str,
    coordinator: ShippingV2JoinCoordinator | None = None,
) -> dict[str, Any]:
    return (coordinator or _coordinator()).cancel_by_admin(
        shipping_id=shipping_id,
        admin=admin,
    )
