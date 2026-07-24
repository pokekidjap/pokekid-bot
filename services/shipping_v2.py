"""Coordinatore Shipping v2 per spedizioni del solo titolare.

Ordine globale dei lock nello stesso processo:

1. ``ORDINI_ARTICOLI``;
2. ``SPEDIZIONI_ARTICOLI``;
3. ``SPEDIZIONI``.

Non esiste una transazione distribuita tra worksheet. Le operazioni sono
quindi idempotenti e riconciliano lo stato parziale durante i retry.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import Any, Callable, Iterable
from uuid import uuid4
from zoneinfo import ZoneInfo

from config import BOT_DB_SHEET_ID
from services.bot_db import (
    LOG_WORKSHEET_NAME,
    SHIPPING_WORKSHEET_NAME,
    complete_shipping_request as complete_legacy_shipping_request,
    write_log,
)
from services.cache import get_or_set, invalidate
from services.common import clean_value, normalize_telegram_id, normalize_username
from services.google_runtime import worksheet_operation, worksheet_session
from services.order_registry import (
    RegistrySyncSnapshot,
    synchronize_order_registry,
    synchronize_order_registry_with_snapshot,
)
from services.profiles import get_missing_shipping_profile_fields
from services.reservations import (
    ReservationConflictError,
    ReservationsRepository,
)
from services.shipping_v2_schema import (
    ORDER_REGISTRY_HEADERS,
    ORDER_REGISTRY_WORKSHEET_NAME,
    OCCUPYING_RESERVATION_STATES,
    RESERVABLE_SYNC_STATUSES,
    SHIPPING_ITEMS_HEADERS,
    SHIPPING_ITEMS_WORKSHEET_NAME,
    SHIPPING_LEGACY_HEADERS,
    SHIPPING_V2_HEADERS,
    normalized_headers,
    rows_as_dicts,
    validate_shipping_v2_schema,
    validate_shipping_v2_values,
)

logger = logging.getLogger(__name__)
ITALY_TIMEZONE = ZoneInfo("Europe/Rome")
SHIPPING_HEADERS = SHIPPING_LEGACY_HEADERS + SHIPPING_V2_HEADERS
PRODUCTS_MAX_LENGTH = 45000
FINALIZATION_CREATED_NOW = "CREATED_NOW"
FINALIZATION_RECONCILED_NOW = "RECONCILED_NOW"
FINALIZATION_ALREADY_COHERENT = "ALREADY_COHERENT"
ADMIN_NOTIFICATION_ACTION = "SHIPPING_V2_ADMIN_NOTIFIED"
OPENING_SNAPSHOT_CACHE_KEY = "shipping:v2_opening_snapshot"
OPENING_SNAPSHOT_TTL_SECONDS = 10


class ShippingV2Error(RuntimeError):
    pass


class ShippingV2SchemaError(ShippingV2Error):
    pass


class ShippingV2ConflictError(ShippingV2Error):
    pass


class ShippingV2NotFoundError(ShippingV2Error):
    pass


class ShippingV2StateError(ShippingV2Error):
    pass


class ShippingV2ExpiredError(ShippingV2StateError):
    pass


class ShippingV2DraftInvalidError(ShippingV2ConflictError):
    pass


class ShippingV2ProductsLimitError(ShippingV2ConflictError):
    pass


class ShippingV2TrackingConflictError(ShippingV2ConflictError):
    pass


def _aware_now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(ITALY_TIMEZONE)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ShippingV2Error("Il clock deve essere timezone-aware.")
    return current


def _parse_time(value: str) -> datetime | None:
    text = clean_value(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise ShippingV2StateError("Timestamp bozza non valido.") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ShippingV2StateError("Timestamp bozza privo di timezone.")
    return parsed


def _display_time(value: datetime) -> str:
    return value.astimezone(ITALY_TIMEZONE).strftime("%d/%m/%Y %H:%M:%S")


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


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


def _shipping_records(values: list[list[Any]]) -> list[dict[str, str]]:
    headers = normalized_headers(values)
    if tuple(headers[: len(SHIPPING_HEADERS)]) != SHIPPING_HEADERS:
        raise ShippingV2SchemaError(
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


def _read_expected(session, headers: tuple[str, ...], label: str):
    values = session.call(
        lambda worksheet: worksheet.get_all_values(),
        operation_name=f"lettura {label}",
    )
    if tuple(normalized_headers(values)) != headers:
        raise ShippingV2SchemaError(
            f"{label} non rispetta lo schema previsto."
        )
    return values, rows_as_dicts(values, headers)


def _assert_schema(
    schema_validator: Callable[..., Any] = validate_shipping_v2_schema,
) -> None:
    result = schema_validator()
    if not getattr(result, "valid", False):
        errors = getattr(result, "errors", None) or [
            "Schema Shipping v2 non valido."
        ]
        raise ShippingV2SchemaError("; ".join(str(error) for error in errors))


def _assert_schema_result(result: Any) -> None:
    if not getattr(result, "valid", False):
        errors = getattr(result, "errors", None) or [
            "Schema Shipping v2 non valido."
        ]
        raise ShippingV2SchemaError("; ".join(str(error) for error in errors))


def _uniform_value(records: list[dict[str, str]], field: str) -> str:
    values = {
        clean_value(record.get(field, ""))
        for record in records
    }
    if len(values) != 1:
        raise ShippingV2ConflictError(
            f"La bozza contiene valori incoerenti per {field}."
        )
    return next(iter(values))


def _optional_uniform_value(
    records: list[dict[str, str]],
    field: str,
) -> str:
    values = {
        clean_value(record.get(field, ""))
        for record in records
        if clean_value(record.get(field, ""))
    }
    if len(values) > 1:
        raise ShippingV2ConflictError(
            f"La bozza contiene valori incoerenti per {field}."
        )
    return next(iter(values)) if values else ""


def _validate_holder_rows(
    records: list[dict[str, str]],
    holder_id: int | str,
    *,
    now: datetime,
    allowed_states: set[str],
    allow_expired: bool = False,
) -> dict[str, Any]:
    if not records:
        raise ShippingV2NotFoundError("Bozza non trovata.")
    holder = normalize_telegram_id(holder_id)
    if not holder:
        raise ShippingV2ConflictError("Titolare mancante.")
    if any(
        record.get("RUOLO") != "TITOLARE"
        or record.get("TELEGRAM_ID_PROPRIETARIO") != holder
        for record in records
    ):
        raise ShippingV2ConflictError(
            "La bozza non appartiene interamente al titolare."
        )
    states = {
        clean_value(record.get("STATO_PRENOTAZIONE", "")).upper()
        for record in records
    }
    if not states.issubset(allowed_states):
        raise ShippingV2StateError(
            "Stato della bozza non compatibile con l'operazione."
        )
    if "PRENOTATO" in states and not allow_expired:
        expires = [
            _parse_time(record.get("PRENOTATO_FINO_AL", ""))
            for record in records
            if record.get("STATO_PRENOTAZIONE") == "PRENOTATO"
        ]
        if any(expiry is None or expiry <= now for expiry in expires):
            raise ShippingV2ExpiredError("La prenotazione è scaduta.")
    return {
        "uuid_bozza": _uniform_value(records, "UUID_BOZZA"),
        "uuid_spedizione": _optional_uniform_value(
            records,
            "UUID_SPEDIZIONE",
        ),
        "id_spedizione": _optional_uniform_value(
            records,
            "ID_SPEDIZIONE",
        ),
        "idempotency_key": _uniform_value(records, "IDEMPOTENCY_KEY"),
        "states": states,
        "items": records,
    }


def validate_v2_draft_for_holder(
    draft_uuid: str,
    holder_id: int | str,
    *,
    now: datetime | None = None,
    reservations_repository: ReservationsRepository | None = None,
    allowed_states: Iterable[str] = ("PRENOTATO", "CONFERMATO"),
    allow_expired: bool = False,
) -> dict[str, Any]:
    repository = reservations_repository or ReservationsRepository()
    draft = repository.get_draft(draft_uuid)
    if draft is None:
        raise ShippingV2NotFoundError("Bozza non trovata.")
    return _validate_holder_rows(
        draft["items"],
        holder_id,
        now=_aware_now(now),
        allowed_states={
            clean_value(state).upper()
            for state in allowed_states
        },
        allow_expired=allow_expired,
    )


def validate_v2_draft_against_registry(
    draft_uuid: str,
    holder_id: int | str,
    *,
    now: datetime | None = None,
    schema_validator: Callable[..., Any] = validate_shipping_v2_schema,
    synchronize: Callable[..., Any] = synchronize_order_registry,
    reservations_repository: ReservationsRepository | None = None,
) -> dict[str, Any]:
    """Sincronizza e rivalida la bozza con lock registro -> prenotazioni."""
    _assert_schema(schema_validator)
    synchronize()
    repository = reservations_repository or ReservationsRepository()
    current = _aware_now(now)
    holder = normalize_telegram_id(holder_id)
    with repository._session_factory(
        repository.spreadsheet_id,
        repository.registry_sheet,
    ) as registry_session:
        with repository._session_factory(
            repository.spreadsheet_id,
            repository.reservations_sheet,
        ) as reservation_session:
            _, registry = _read_expected(
                registry_session,
                ORDER_REGISTRY_HEADERS,
                "ORDINI_ARTICOLI",
            )
            _, reservations = _read_expected(
                reservation_session,
                SHIPPING_ITEMS_HEADERS,
                "SPEDIZIONI_ARTICOLI",
            )
            draft_rows = [
                record
                for record in reservations
                if record["UUID_BOZZA"] == clean_value(draft_uuid)
            ]
            draft = _validate_holder_rows(
                draft_rows,
                holder,
                now=current,
                allowed_states={"PRENOTATO", "CONFERMATO"},
            )
            # Una conferma già avvenuta è autorevole: non può essere rilasciata
            # per una successiva variazione del gestionale.
            if "CONFERMATO" in draft["states"]:
                return draft
            by_id = {
                record["ID_ARTICOLO"]: record
                for record in registry
                if record["ID_ARTICOLO"]
            }
            invalid = []
            for row in draft_rows:
                item = by_id.get(row["ID_ARTICOLO"])
                if not (
                    item
                    and item["IS_ACTIVE"].upper() == "TRUE"
                    and item["SYNC_STATUS"] in RESERVABLE_SYNC_STATUSES
                    and item["STATO_ORIGINE"].upper() == "IN MAGAZZINO"
                    and item["TELEGRAM_ID_PROPRIETARIO"] == holder
                    and item["OGGETTO"]
                    == row["OGGETTO_SNAPSHOT"]
                    and item["QUANTITA"]
                    == row["QUANTITA_SNAPSHOT"]
                    and item["SOURCE_ROW"]
                    == row["RIGA_ORDINE_SNAPSHOT"]
                ):
                    invalid.append(row["ID_ARTICOLO"])
            if invalid:
                raise ShippingV2DraftInvalidError(
                    "La disponibilità della bozza è cambiata."
                )
            return draft


def _list_available_from_repository(
    holder_id: int | str,
    repository: ReservationsRepository,
    *,
    now: datetime | None = None,
) -> list[dict[str, str]]:
    holder = normalize_telegram_id(holder_id)
    with repository._session_factory(  # stessa factory iniettata nei test
        repository.spreadsheet_id,
        repository.registry_sheet,
    ) as session:
        _, registry = _read_expected(
            session,
            ORDER_REGISTRY_HEADERS,
            "ORDINI_ARTICOLI",
        )
    occupied = repository.get_active_reservations(now=now)
    return [
        record
        for record in registry
        if record["IS_ACTIVE"].upper() == "TRUE"
        and record["SYNC_STATUS"] in RESERVABLE_SYNC_STATUSES
        and record["STATO_ORIGINE"].upper() == "IN MAGAZZINO"
        and record["TELEGRAM_ID_PROPRIETARIO"] == holder
        and record["ID_ARTICOLO"] not in occupied
    ]


def list_v2_available_items(
    holder_id: int | str,
    *,
    reservations_repository: ReservationsRepository | None = None,
    now: datetime | None = None,
) -> list[dict[str, str]]:
    repository = reservations_repository or ReservationsRepository()
    return _list_available_from_repository(
        holder_id,
        repository,
        now=_aware_now(now),
    )


def _read_opening_reservations(
    repository: ReservationsRepository,
) -> list[list[Any]]:
    with repository._session_factory(
        repository.spreadsheet_id,
        repository.reservations_sheet,
    ) as session:
        values, _ = _read_expected(
            session,
            SHIPPING_ITEMS_HEADERS,
            "SPEDIZIONI_ARTICOLI",
        )
    return values


def _has_expired_reservations(
    reservation_values: list[list[Any]],
    *,
    now: datetime,
) -> bool:
    return any(
        record["STATO_PRENOTAZIONE"] == "PRENOTATO"
        and (expiry := _parse_time(record["PRENOTATO_FINO_AL"])) is not None
        and expiry <= now
        for record in rows_as_dicts(
            reservation_values,
            SHIPPING_ITEMS_HEADERS,
        )
    )


def _opening_state_from_values(
    holder_id: int | str,
    *,
    registry_values: list[list[Any]],
    reservation_values: list[list[Any]],
    now: datetime,
) -> dict[str, Any]:
    holder = normalize_telegram_id(holder_id)
    registry = rows_as_dicts(
        registry_values,
        ORDER_REGISTRY_HEADERS,
    )
    reservations = rows_as_dicts(
        reservation_values,
        SHIPPING_ITEMS_HEADERS,
    )
    drafts: dict[str, list[dict[str, str]]] = defaultdict(list)
    occupied: set[str] = set()
    for record in reservations:
        state = record["STATO_PRENOTAZIONE"]
        expired = (
            state == "PRENOTATO"
            and (expiry := _parse_time(record["PRENOTATO_FINO_AL"]))
            is not None
            and expiry <= now
        )
        if state in OCCUPYING_RESERVATION_STATES and not expired:
            occupied.add(record["ID_ARTICOLO"].upper())
        if state in {"PRENOTATO", "CONFERMATO"} and not expired:
            drafts[record["UUID_BOZZA"]].append(record)

    candidates = [
        rows
        for rows in drafts.values()
        if any(
            row["RUOLO"] == "TITOLARE"
            and row["TELEGRAM_ID_PROPRIETARIO"] == holder
            for row in rows
        )
    ]
    if len(candidates) > 1:
        raise ReservationConflictError(
            "Il titolare possiede più bozze attive incoerenti."
        )
    if candidates:
        selected = max(
            candidates,
            key=lambda rows: max(
                _parse_time(row["ULTIMO_AGGIORNAMENTO"])
                or datetime.min.replace(tzinfo=ITALY_TIMEZONE)
                for row in rows
            ),
        )
        return {
            "active_draft": ReservationsRepository._result(
                selected,
                created=False,
            ),
            "available_items": [],
        }
    return {
        "active_draft": None,
        "available_items": [
            record
            for record in registry
            if record["IS_ACTIVE"].upper() == "TRUE"
            and record["SYNC_STATUS"] in RESERVABLE_SYNC_STATUSES
            and record["STATO_ORIGINE"].upper() == "IN MAGAZZINO"
            and record["TELEGRAM_ID_PROPRIETARIO"] == holder
            and record["ID_ARTICOLO"].upper() not in occupied
        ],
    }


def _load_opening_snapshot() -> dict[str, Any]:
    snapshot: RegistrySyncSnapshot = (
        synchronize_order_registry_with_snapshot()
    )
    shipping_values = worksheet_operation(
        BOT_DB_SHEET_ID,
        SHIPPING_WORKSHEET_NAME,
        lambda worksheet: worksheet.get_all_values(),
        operation_name="lettura SPEDIZIONI per apertura Shipping v2",
    )
    _assert_schema_result(
        validate_shipping_v2_values(
            snapshot.registry_values,
            shipping_values,
            snapshot.reservation_values,
        )
    )
    return {
        "registry_values": snapshot.registry_values,
        "reservation_values": snapshot.reservation_values,
        "shipping_values": shipping_values,
        "sync_summary": snapshot.summary,
    }


def prepare_v2_opening_state(
    holder_id: int | str,
    *,
    schema_validator: Callable[..., Any] = validate_shipping_v2_schema,
    synchronize: Callable[..., Any] = synchronize_order_registry,
    reservations_repository: ReservationsRepository | None = None,
    now: datetime | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Valida, sincronizza, rilascia le scadenze e ricostruisce lo stato."""
    # Le dipendenze iniettate mantengono il percorso storico dei test e degli
    # strumenti offline. Il percorso runtime riusa invece gli snapshot già
    # letti nella stessa azione.
    if (
        schema_validator is not validate_shipping_v2_schema
        or synchronize is not synchronize_order_registry
        or reservations_repository is not None
    ):
        _assert_schema(schema_validator)
        synchronize()
        repository = reservations_repository or ReservationsRepository()
        current = _aware_now(now)
        repository.release_expired_reservations(now=current)
        active_draft = repository.get_active_draft_for_user(
            holder_id,
            now=current,
        )
        return {
            "active_draft": active_draft,
            "available_items": (
                []
                if active_draft is not None
                else _list_available_from_repository(
                    holder_id,
                    repository,
                    now=current,
                )
            ),
        }

    loaded_now = False

    def loader() -> dict[str, Any]:
        nonlocal loaded_now
        loaded_now = True
        return _load_opening_snapshot()

    snapshot = get_or_set(
        OPENING_SNAPSHOT_CACHE_KEY,
        loader,
        ttl=OPENING_SNAPSHOT_TTL_SECONDS,
        force=force_refresh,
    )
    repository = ReservationsRepository()
    current = _aware_now(now)
    reservation_values = (
        snapshot["reservation_values"]
        if loaded_now
        else _read_opening_reservations(repository)
    )
    if not loaded_now:
        _assert_schema_result(
            validate_shipping_v2_values(
                snapshot["registry_values"],
                snapshot["shipping_values"],
                reservation_values,
            )
        )
    if _has_expired_reservations(reservation_values, now=current):
        repository.release_expired_reservations(now=current)
        reservation_values = _read_opening_reservations(repository)
    return _opening_state_from_values(
        holder_id,
        registry_values=snapshot["registry_values"],
        reservation_values=reservation_values,
        now=current,
    )


def reserve_v2_items(
    *,
    holder_id: int | str,
    username: str | None,
    item_ids: Iterable[str],
    idempotency_key: str,
    schema_validator: Callable[..., Any] = validate_shipping_v2_schema,
    synchronize: Callable[..., Any] = synchronize_order_registry,
    reservations_repository: ReservationsRepository | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Crea la bozza solo al comando Continua, con contenuto tutto-o-niente."""
    _assert_schema(schema_validator)
    synchronize()
    repository = reservations_repository or ReservationsRepository()
    current = _aware_now(now)
    repository.release_expired_reservations(now=current)
    selected = [
        clean_value(item_id).upper()
        for item_id in item_ids
        if clean_value(item_id)
    ]
    available = {
        item["ID_ARTICOLO"]
        for item in _list_available_from_repository(
            holder_id,
            repository,
            now=current,
        )
    }
    unavailable = [item_id for item_id in selected if item_id not in available]
    if unavailable:
        raise ReservationConflictError(
            "Articoli non più disponibili: " + ", ".join(unavailable)
        )
    return repository.reserve_items(
        telegram_id=holder_id,
        username=username,
        item_ids=selected,
        idempotency_key=idempotency_key,
        authorized_contributor_item_ids=set(),
        now=current,
    )


def _products_from_snapshots(records: list[dict[str, str]]) -> str:
    products = []
    for record in records:
        name = clean_value(record.get("OGGETTO_SNAPSHOT", ""))
        quantity = clean_value(record.get("QUANTITA_SNAPSHOT", ""))
        row = clean_value(record.get("RIGA_ORDINE_SNAPSHOT", ""))
        if not name or not quantity:
            raise ShippingV2ConflictError(
                "Snapshot articolo incompleto nella bozza."
            )
        text = f"{name} ×{quantity}"
        if row:
            text += f" [RIGA {row}]"
        products.append(text)
    if not products:
        raise ShippingV2ConflictError("La bozza non contiene articoli.")
    result = " | ".join(products)
    if len(result) > PRODUCTS_MAX_LENGTH:
        raise ShippingV2ProductsLimitError(
            "PRODOTTI supera il limite operativo prudenziale."
        )
    return result


def _next_shipping_id(
    records: list[dict[str, str]],
    now: datetime,
) -> str:
    prefix = now.astimezone(ITALY_TIMEZONE).strftime("SP-%Y%m%d")
    progressive = 1
    for record in records:
        shipping_id = clean_value(record.get("ID", ""))
        if not shipping_id.startswith(prefix):
            continue
        try:
            progressive = max(
                progressive,
                int(shipping_id.rsplit("-", 1)[1]) + 1,
            )
        except (IndexError, ValueError):
            continue
    return f"{prefix}-{progressive:03d}"


def _same_price(left: Any, right: Any) -> bool:
    try:
        return abs(float(left) - float(right)) < 0.000001
    except (TypeError, ValueError):
        return False


def _assert_shipping_payload(
    record: dict[str, str],
    expected: dict[str, Any],
    *,
    authoritative_attachment: bool = False,
) -> None:
    if record.get("VERSIONE_SCHEMA") != "V2":
        raise ShippingV2ConflictError(
            "Idempotency key associata a una richiesta non V2."
        )
    if record.get("TELEGRAM_ID") != expected["TELEGRAM_ID"]:
        raise ShippingV2ConflictError(
            "Idempotency key associata a un titolare differente."
        )
    fields = (
        "USERNAME",
        "PRODOTTI",
        "CORRIERE",
        "NOME",
        "EMAIL",
        "TELEFONO",
        "INDIRIZZO",
        "CAP",
        "CITTA",
        "PROVINCIA",
        "IDEMPOTENCY_KEY",
        "VERSIONE_SCHEMA",
    )
    if not authoritative_attachment:
        fields += ("PAYMENT_FILE_ID", "NOTE")
    if any(record.get(field, "") != clean_value(expected[field]) for field in fields):
        raise ShippingV2ConflictError(
            "Idempotency key riutilizzata con payload differente."
        )
    if authoritative_attachment and not record.get("PAYMENT_FILE_ID", ""):
        raise ShippingV2ConflictError(
            "La richiesta esistente non contiene l'allegato autorevole."
        )
    if not _same_price(
        record.get("COSTO_SPEDIZIONE", ""),
        expected["COSTO_SPEDIZIONE"],
    ):
        raise ShippingV2ConflictError(
            "Idempotency key riutilizzata con costo differente."
        )


def _find_unique(
    records: list[dict[str, str]],
    field: str,
    value: str,
) -> dict[str, str] | None:
    if not value:
        return None
    found = [record for record in records if record.get(field) == value]
    if len(found) > 1:
        raise ShippingV2ConflictError(
            f"Valore duplicato in SPEDIZIONI per {field}."
        )
    return found[0] if found else None


def _assert_unique_shipping_identifiers(
    records: list[dict[str, str]],
) -> None:
    for field in ("UUID_SPEDIZIONE", "IDEMPOTENCY_KEY"):
        counts: dict[str, int] = {}
        for record in records:
            value = clean_value(record.get(field, ""))
            if value:
                counts[value] = counts.get(value, 0) + 1
        if any(count > 1 for count in counts.values()):
            raise ShippingV2ConflictError(
                f"SPEDIZIONI contiene valori duplicati per {field}."
            )


def _admin_notification_details(
    shipping_id: str,
    admin_id: int | str,
) -> str:
    shipping = clean_value(shipping_id).upper()
    admin = normalize_telegram_id(admin_id)
    if not shipping or not admin:
        raise ShippingV2Error("ID spedizione e admin sono obbligatori.")
    return f"shipping_id={shipping}|admin_id={admin}"


def is_v2_admin_notified(
    shipping_id: str,
    admin_id: int | str,
    *,
    bot_db_spreadsheet_id: str | None = None,
    session_factory=worksheet_session,
) -> bool:
    """Legge direttamente LOG per il marker stabile della coppia richiesta/admin."""
    spreadsheet_id = clean_value(bot_db_spreadsheet_id or BOT_DB_SHEET_ID)
    details = _admin_notification_details(shipping_id, admin_id)
    with session_factory(spreadsheet_id, LOG_WORKSHEET_NAME) as session:
        values = session.call(
            lambda worksheet: worksheet.get_all_values(),
            operation_name="verifica notifica admin Shipping v2",
        )
    headers = normalized_headers(values)
    if not headers:
        return False
    required = {"AZIONE", "DETTAGLI"}
    if not required.issubset(set(headers)):
        raise ShippingV2SchemaError("LOG non rispetta lo schema previsto.")
    for row in values[1:]:
        record = {
            header: clean_value(row[index] if index < len(row) else "")
            for index, header in enumerate(headers)
            if header
        }
        if (
            record.get("AZIONE", "").upper() == ADMIN_NOTIFICATION_ACTION
            and record.get("DETTAGLI", "") == details
        ):
            return True
    return False


def record_v2_admin_notification(
    shipping_id: str,
    admin_id: int | str,
    *,
    log_writer: Callable[..., Any] = write_log,
) -> None:
    details = _admin_notification_details(shipping_id, admin_id)
    log_writer(
        action=ADMIN_NOTIFICATION_ACTION,
        details=details,
        admin=normalize_telegram_id(admin_id),
    )


class ShippingV2Coordinator:
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
            raise ShippingV2Error("BOT_DB_SHEET_ID non configurato.")
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
            logger.exception("Log applicativo Shipping v2 non scritto")

    def _read_draft_locked(self, session, draft_uuid: str):
        _, records = _read_expected(
            session,
            SHIPPING_ITEMS_HEADERS,
            "SPEDIZIONI_ARTICOLI",
        )
        selected = [
            record
            for record in records
            if record["UUID_BOZZA"] == clean_value(draft_uuid)
        ]
        return records, selected

    def _validate_registry_locked(
        self,
        session,
        draft_rows: list[dict[str, str]],
        holder_id: str,
    ) -> None:
        _, registry = _read_expected(
            session,
            ORDER_REGISTRY_HEADERS,
            "ORDINI_ARTICOLI",
        )
        by_id = {
            record["ID_ARTICOLO"]: record
            for record in registry
            if record["ID_ARTICOLO"]
        }
        invalid = []
        for draft_row in draft_rows:
            item = by_id.get(draft_row["ID_ARTICOLO"])
            if not (
                item
                and item["IS_ACTIVE"].upper() == "TRUE"
                and item["SYNC_STATUS"] in RESERVABLE_SYNC_STATUSES
                and item["STATO_ORIGINE"].upper() == "IN MAGAZZINO"
                and item["TELEGRAM_ID_PROPRIETARIO"] == holder_id
                and item["OGGETTO"]
                == draft_row["OGGETTO_SNAPSHOT"]
                and item["QUANTITA"]
                == draft_row["QUANTITA_SNAPSHOT"]
                and item["SOURCE_ROW"]
                == draft_row["RIGA_ORDINE_SNAPSHOT"]
            ):
                invalid.append(draft_row["ID_ARTICOLO"])
        if invalid:
            raise ShippingV2ConflictError(
                "Articoli non più coerenti nel registro: "
                + ", ".join(invalid)
            )

    def create_or_get(
        self,
        *,
        draft_uuid: str,
        holder_id: int | str,
        username: str | None,
        payment_file_id: str,
        payment_type: str,
        profile: dict,
        carrier: str,
        shipping_cost: float,
        idempotency_key: str,
    ) -> dict[str, Any]:
        now = self._now()
        holder = normalize_telegram_id(holder_id)
        current_username = normalize_username(username)
        payment = clean_value(payment_file_id)
        attachment_type = clean_value(payment_type).upper()
        carrier_name = clean_value(carrier).upper()
        key = clean_value(idempotency_key)
        if not all((draft_uuid, holder, payment, carrier_name, key)):
            raise ShippingV2Error(
                "Bozza, titolare, allegato, corriere e key sono obbligatori."
            )
        missing_profile = get_missing_shipping_profile_fields(profile)
        if missing_profile:
            raise ShippingV2ConflictError(
                "Profilo di spedizione incompleto."
            )
        try:
            cost = float(shipping_cost)
        except (TypeError, ValueError) as error:
            raise ShippingV2ConflictError("Costo spedizione non valido.") from error
        if cost < 0:
            raise ShippingV2ConflictError("Costo spedizione non valido.")

        with self._session_factory(
            self.spreadsheet_id,
            ORDER_REGISTRY_WORKSHEET_NAME,
        ) as registry_session:
            with self._session_factory(
                self.spreadsheet_id,
                SHIPPING_ITEMS_WORKSHEET_NAME,
            ) as reservation_session:
                all_reservations, draft_rows = self._read_draft_locked(
                    reservation_session,
                    draft_uuid,
                )
                draft = _validate_holder_rows(
                    draft_rows,
                    holder,
                    now=now,
                    allowed_states={"PRENOTATO", "CONFERMATO"},
                )
                if draft["idempotency_key"] != key:
                    raise ShippingV2ConflictError(
                        "La key non coincide con quella della bozza."
                    )
                key_drafts = {
                    record["UUID_BOZZA"]
                    for record in all_reservations
                    if record["IDEMPOTENCY_KEY"] == key
                }
                if key_drafts != {draft["uuid_bozza"]}:
                    raise ShippingV2ConflictError(
                        "Idempotency key associata a una bozza differente."
                    )
                self._validate_registry_locked(
                    registry_session,
                    draft_rows,
                    holder,
                )
                products = _products_from_snapshots(draft_rows)
                notes = (
                    "Ricevuta inviata tramite bot. "
                    f"Tipo allegato: {attachment_type}."
                )
                expected = {
                    "TELEGRAM_ID": holder,
                    "USERNAME": current_username,
                    "PRODOTTI": products,
                    "CORRIERE": carrier_name,
                    "PAYMENT_FILE_ID": payment,
                    "NOTE": notes,
                    "NOME": clean_value(profile.get("NOME", "")),
                    "EMAIL": clean_value(profile.get("EMAIL", "")),
                    "TELEFONO": clean_value(profile.get("TELEFONO", "")),
                    "INDIRIZZO": clean_value(profile.get("INDIRIZZO", "")),
                    "CAP": clean_value(profile.get("CAP", "")),
                    "CITTA": clean_value(profile.get("CITTA", "")),
                    "PROVINCIA": clean_value(
                        profile.get("PROVINCIA", "")
                    ).upper(),
                    "COSTO_SPEDIZIONE": cost,
                    "IDEMPOTENCY_KEY": key,
                    "VERSIONE_SCHEMA": "V2",
                }

                with self._session_factory(
                    self.spreadsheet_id,
                    SHIPPING_WORKSHEET_NAME,
                ) as shipping_session:
                    shipping_values = shipping_session.call(
                        lambda worksheet: worksheet.get_all_values(),
                        operation_name="lettura SPEDIZIONI per finalizzazione v2",
                    )
                    shipping_records = _shipping_records(shipping_values)
                    _assert_unique_shipping_identifiers(shipping_records)
                    existing = _find_unique(
                        shipping_records,
                        "IDEMPOTENCY_KEY",
                        key,
                    )
                    existed_before_attempt = existing is not None
                    draft_shipping_uuid = draft["uuid_spedizione"]
                    if draft_shipping_uuid:
                        by_uuid = _find_unique(
                            shipping_records,
                            "UUID_SPEDIZIONE",
                            draft_shipping_uuid,
                        )
                        if existing and by_uuid and existing != by_uuid:
                            raise ShippingV2ConflictError(
                                "UUID e idempotency key indicano righe diverse."
                            )
                        existing = existing or by_uuid
                        existed_before_attempt = existing is not None

                    already_coherent = False
                    attachment_reconciled = False
                    if existing:
                        _assert_shipping_payload(
                            existing,
                            expected,
                            authoritative_attachment=True,
                        )
                        attachment_reconciled = (
                            existing.get("PAYMENT_FILE_ID", "") != payment
                            or existing.get("NOTE", "") != notes
                        )
                        # Sul retry il primo allegato salvato resta autorevole.
                        expected["PAYMENT_FILE_ID"] = existing.get(
                            "PAYMENT_FILE_ID",
                            "",
                        )
                        expected["NOTE"] = existing.get("NOTE", "")
                        shipping_uuid = existing["UUID_SPEDIZIONE"]
                        shipping_id = existing["ID"]
                        if not shipping_uuid or not shipping_id:
                            raise ShippingV2ConflictError(
                                "Riga SPEDIZIONI v2 parziale."
                            )
                        already_coherent = (
                            draft["states"] == {"CONFERMATO"}
                            and all(
                                row["UUID_SPEDIZIONE"] == shipping_uuid
                                and row["ID_SPEDIZIONE"] == shipping_id
                                for row in draft_rows
                            )
                        )
                    else:
                        if "CONFERMATO" in draft["states"]:
                            raise ShippingV2ConflictError(
                                "Bozza confermata senza riga SPEDIZIONI."
                            )
                        shipping_uuid = str(self._uuid_factory())
                        shipping_id = _next_shipping_id(
                            shipping_records,
                            now,
                        )
                        current_datetime = _display_time(now)
                        row = [
                            shipping_id,
                            current_datetime,
                            holder,
                            current_username,
                            products,
                            "IN_ATTESA",
                            carrier_name,
                            "",
                            payment,
                            notes,
                            "",
                            current_datetime,
                            "",
                            expected["NOME"],
                            expected["EMAIL"],
                            expected["TELEFONO"],
                            expected["INDIRIZZO"],
                            expected["CAP"],
                            expected["CITTA"],
                            expected["PROVINCIA"],
                            cost,
                            shipping_uuid,
                            key,
                            "V2",
                        ]

                        def append_or_reconcile(worksheet):
                            latest = _shipping_records(
                                worksheet.get_all_values()
                            )
                            _assert_unique_shipping_identifiers(latest)
                            key_match = _find_unique(
                                latest,
                                "IDEMPOTENCY_KEY",
                                key,
                            )
                            uuid_match = _find_unique(
                                latest,
                                "UUID_SPEDIZIONE",
                                shipping_uuid,
                            )
                            if key_match or uuid_match:
                                if (
                                    key_match
                                    and uuid_match
                                    and key_match != uuid_match
                                ):
                                    raise ShippingV2ConflictError(
                                        "Append v2 con UUID/key conflittuali."
                                    )
                                found = key_match or uuid_match
                                _assert_shipping_payload(found, expected)
                                if found["ID"] != shipping_id:
                                    raise ShippingV2ConflictError(
                                        "Append v2 riconciliato con ID diverso."
                                    )
                                return found
                            worksheet.append_rows(
                                [row],
                                value_input_option="USER_ENTERED",
                            )
                            return {
                                header: clean_value(row[index])
                                for index, header in enumerate(SHIPPING_HEADERS)
                            }

                        existing = shipping_session.call(
                            append_or_reconcile,
                            operation_name="creazione idempotente SPEDIZIONI v2",
                        )

                    timestamp = _iso(now)
                    updates = []
                    for record in draft_rows:
                        state = record["STATO_PRENOTAZIONE"]
                        if state == "CONFERMATO":
                            if (
                                record["UUID_SPEDIZIONE"] != shipping_uuid
                                or record["ID_SPEDIZIONE"] != shipping_id
                            ):
                                raise ShippingV2ConflictError(
                                    "Prenotazioni confermate con spedizione diversa."
                                )
                            continue
                        if state != "PRENOTATO":
                            raise ShippingV2StateError(
                                "Stato prenotazione non finalizzabile."
                            )
                        record["STATO_PRENOTAZIONE"] = "CONFERMATO"
                        record["UUID_SPEDIZIONE"] = shipping_uuid
                        record["ID_SPEDIZIONE"] = shipping_id
                        record["PRENOTATO_FINO_AL"] = ""
                        record["CONFERMATO_IL"] = timestamp
                        record["ULTIMO_AGGIORNAMENTO"] = timestamp
                        updates.append(record)
                    if updates:
                        end = _a1_column(len(SHIPPING_ITEMS_HEADERS))
                        payload = [
                            {
                                "range": (
                                    f"A{record['_ROW_NUMBER']}:"
                                    f"{end}{record['_ROW_NUMBER']}"
                                ),
                                "values": [[
                                    clean_value(record.get(header, ""))
                                    for header in SHIPPING_ITEMS_HEADERS
                                ]],
                            }
                            for record in updates
                        ]
                        reservation_session.call(
                            lambda worksheet: worksheet.batch_update(
                                payload,
                                value_input_option="USER_ENTERED",
                            ),
                            operation_name="conferma prenotazioni v2",
                        )

                    final_shipping = _shipping_records(
                        shipping_session.call(
                            lambda worksheet: worksheet.get_all_values(),
                            operation_name="verifica finale SPEDIZIONI v2",
                        )
                    )
                    _assert_unique_shipping_identifiers(final_shipping)
                    final_request = _find_unique(
                        final_shipping,
                        "IDEMPOTENCY_KEY",
                        key,
                    )
                    if final_request is None:
                        raise ShippingV2ConflictError(
                            "Riga SPEDIZIONI assente dopo la scrittura."
                        )
                    _assert_shipping_payload(final_request, expected)
                    final_uuid_matches = [
                        record
                        for record in final_shipping
                        if record["UUID_SPEDIZIONE"] == shipping_uuid
                    ]
                    if len(final_uuid_matches) != 1:
                        raise ShippingV2ConflictError(
                            "UUID spedizione assente o duplicato."
                        )
                    _, final_draft_rows = self._read_draft_locked(
                        reservation_session,
                        draft_uuid,
                    )
                    if (
                        not final_draft_rows
                        or {
                            row["STATO_PRENOTAZIONE"]
                            for row in final_draft_rows
                        } != {"CONFERMATO"}
                        or {
                            row["UUID_SPEDIZIONE"]
                            for row in final_draft_rows
                        } != {shipping_uuid}
                        or {
                            row["ID_SPEDIZIONE"]
                            for row in final_draft_rows
                        } != {shipping_id}
                    ):
                        raise ShippingV2ConflictError(
                            "Conferma cross-worksheet non coerente."
                        )

        self._cache_invalidator("shipping")
        self._log_safely(
            telegram_id=holder,
            username=current_username,
            action="RICHIESTA_SPEDIZIONE_V2_CREATA",
            details=f"Richiesta {shipping_id} finalizzata con Shipping v2.",
        )
        if attachment_reconciled:
            self._log_safely(
                telegram_id=holder,
                username=current_username,
                action="SHIPPING_V2_RETRY_ALLEGATO_MANTENUTO",
                details=(
                    f"Richiesta {shipping_id}: durante il retry è stato "
                    "mantenuto il primo allegato ricevuto."
                ),
            )
        result = dict(final_request)
        result["_V2_ALREADY_COHERENT"] = already_coherent
        result["_V2_FINALIZATION_STATUS"] = (
            FINALIZATION_CREATED_NOW
            if not existed_before_attempt
            else (
                FINALIZATION_ALREADY_COHERENT
                if already_coherent
                else FINALIZATION_RECONCILED_NOW
            )
        )
        result["_V2_ITEM_SNAPSHOTS"] = [
            {
                "ID_ARTICOLO": row.get("ID_ARTICOLO", ""),
                "OGGETTO_SNAPSHOT": row.get("OGGETTO_SNAPSHOT", ""),
                "QUANTITA_SNAPSHOT": row.get("QUANTITA_SNAPSHOT", ""),
            }
            for row in final_draft_rows
        ]
        return result

    def get_by_draft(self, draft_uuid: str) -> dict[str, str] | None:
        with self._session_factory(
            self.spreadsheet_id,
            SHIPPING_ITEMS_WORKSHEET_NAME,
        ) as reservation_session:
            _, draft_rows = self._read_draft_locked(
                reservation_session,
                draft_uuid,
            )
            if not draft_rows:
                return None
            key = _uniform_value(draft_rows, "IDEMPOTENCY_KEY")
            shipping_uuid = _uniform_value(draft_rows, "UUID_SPEDIZIONE")
            shipping_id = _uniform_value(draft_rows, "ID_SPEDIZIONE")
            states = {
                row["STATO_PRENOTAZIONE"]
                for row in draft_rows
            }
            if states not in ({"CONFERMATO"}, {"SPEDITO"}):
                raise ShippingV2ConflictError(
                    "Bozza non coerente per il recupero della richiesta."
                )
            with self._session_factory(
                self.spreadsheet_id,
                SHIPPING_WORKSHEET_NAME,
            ) as shipping_session:
                records = _shipping_records(
                    shipping_session.call(
                        lambda worksheet: worksheet.get_all_values(),
                        operation_name="lettura spedizione v2 tramite bozza",
                    )
                )
                _assert_unique_shipping_identifiers(records)
                by_key = _find_unique(records, "IDEMPOTENCY_KEY", key)
                by_uuid = _find_unique(
                    records,
                    "UUID_SPEDIZIONE",
                    shipping_uuid,
                )
                if by_key and by_uuid and by_key != by_uuid:
                    raise ShippingV2ConflictError(
                        "Bozza associata a righe SPEDIZIONI conflittuali."
                    )
                result = by_key or by_uuid
                if result and result.get("VERSIONE_SCHEMA") != "V2":
                    raise ShippingV2ConflictError(
                        "La bozza punta a una richiesta non V2."
                    )
                if result and (
                    result.get("UUID_SPEDIZIONE") != shipping_uuid
                    or result.get("ID") != shipping_id
                    or result.get("IDEMPOTENCY_KEY") != key
                ):
                    raise ShippingV2ConflictError(
                        "Richiesta e articoli non sono coerenti."
                    )
                return result

    def complete(
        self,
        shipping_id: str,
        tracking: str,
        admin: str,
    ) -> dict[str, str]:
        shipping_id = clean_value(shipping_id).upper()
        tracking = clean_value(tracking)
        admin = clean_value(admin)
        if not shipping_id or not tracking:
            raise ShippingV2Error("ID e tracking sono obbligatori.")
        now = self._now()
        current_datetime = _display_time(now)
        timestamp = _iso(now)

        with self._session_factory(
            self.spreadsheet_id,
            SHIPPING_ITEMS_WORKSHEET_NAME,
        ) as reservation_session:
            _, all_items = _read_expected(
                reservation_session,
                SHIPPING_ITEMS_HEADERS,
                "SPEDIZIONI_ARTICOLI",
            )
            with self._session_factory(
                self.spreadsheet_id,
                SHIPPING_WORKSHEET_NAME,
            ) as shipping_session:
                shipping_records = _shipping_records(
                    shipping_session.call(
                        lambda worksheet: worksheet.get_all_values(),
                        operation_name="lettura SPEDIZIONI da completare v2",
                    )
                )
                _assert_unique_shipping_identifiers(shipping_records)
                matches = [
                    record
                    for record in shipping_records
                    if record["ID"].upper() == shipping_id
                ]
                if len(matches) != 1:
                    raise ShippingV2NotFoundError(
                        "Richiesta v2 assente o duplicata."
                    )
                request = matches[0]
                if request.get("VERSIONE_SCHEMA") != "V2":
                    raise ShippingV2ConflictError(
                        "La richiesta non usa Shipping v2."
                    )
                shipping_uuid = request.get("UUID_SPEDIZIONE", "")
                items = [
                    record
                    for record in all_items
                    if record["ID_SPEDIZIONE"].upper() == shipping_id
                    or (
                        shipping_uuid
                        and record["UUID_SPEDIZIONE"] == shipping_uuid
                    )
                ]
                if not items:
                    raise ShippingV2ConflictError(
                        "Nessun articolo associato alla spedizione v2."
                    )
                if any(
                    record["ID_SPEDIZIONE"].upper() != shipping_id
                    or record["UUID_SPEDIZIONE"] != shipping_uuid
                    for record in items
                ):
                    raise ShippingV2ConflictError(
                        "Associazione articoli/spedizione incoerente."
                    )
                current_state = request.get("STATO", "").upper()
                current_tracking = request.get("TRACKING", "")
                if (
                    current_state == "SPEDITO"
                    and current_tracking
                    and current_tracking != tracking
                ):
                    raise ShippingV2TrackingConflictError(
                        "La richiesta è già spedita con tracking differente."
                    )
                if current_state not in {"IN_ATTESA", "SPEDITO"}:
                    raise ShippingV2StateError(
                        "Stato della richiesta non completabile."
                    )
                invalid_states = {
                    record["STATO_PRENOTAZIONE"]
                    for record in items
                    if record["STATO_PRENOTAZIONE"] not in {
                        "CONFERMATO",
                        "SPEDITO",
                    }
                }
                if invalid_states:
                    raise ShippingV2StateError(
                        "Gli articoli non sono confermati."
                    )

                if (
                    current_state != "SPEDITO"
                    or current_tracking != tracking
                ):
                    row_number = request["_ROW_NUMBER"]
                    shipping_session.call(
                        lambda worksheet: worksheet.update(
                            range_name=f"F{row_number}:M{row_number}",
                            values=[[
                                "SPEDITO",
                                request.get("CORRIERE", ""),
                                tracking,
                                request.get("PAYMENT_FILE_ID", ""),
                                request.get("NOTE", ""),
                                current_datetime,
                                current_datetime,
                                admin,
                            ]],
                            value_input_option="USER_ENTERED",
                        ),
                        operation_name="completamento SPEDIZIONI v2",
                    )

                updates = []
                for record in items:
                    if record["STATO_PRENOTAZIONE"] == "SPEDITO":
                        continue
                    record["STATO_PRENOTAZIONE"] = "SPEDITO"
                    record["SPEDITO_IL"] = timestamp
                    record["ULTIMO_AGGIORNAMENTO"] = timestamp
                    updates.append(record)
                if updates:
                    end = _a1_column(len(SHIPPING_ITEMS_HEADERS))
                    payload = [
                        {
                            "range": (
                                f"A{record['_ROW_NUMBER']}:"
                                f"{end}{record['_ROW_NUMBER']}"
                            ),
                            "values": [[
                                clean_value(record.get(header, ""))
                                for header in SHIPPING_ITEMS_HEADERS
                            ]],
                        }
                        for record in updates
                    ]
                    reservation_session.call(
                        lambda worksheet: worksheet.batch_update(
                            payload,
                            value_input_option="USER_ENTERED",
                        ),
                        operation_name="articoli SPEDITO v2",
                    )

                final_requests = _shipping_records(
                    shipping_session.call(
                        lambda worksheet: worksheet.get_all_values(),
                        operation_name="verifica finale SPEDIZIONI spedita v2",
                    )
                )
                _assert_unique_shipping_identifiers(final_requests)
                final_matches = [
                    record
                    for record in final_requests
                    if record["ID"].upper() == shipping_id
                ]
                _, final_items = _read_expected(
                    reservation_session,
                    SHIPPING_ITEMS_HEADERS,
                    "SPEDIZIONI_ARTICOLI",
                )
                final_linked = [
                    record
                    for record in final_items
                    if record["ID_SPEDIZIONE"].upper() == shipping_id
                ]
                if (
                    len(final_matches) != 1
                    or final_matches[0]["STATO"] != "SPEDITO"
                    or final_matches[0]["TRACKING"] != tracking
                    or not final_linked
                    or {
                        record["STATO_PRENOTAZIONE"]
                        for record in final_linked
                    } != {"SPEDITO"}
                ):
                    raise ShippingV2ConflictError(
                        "Completamento cross-worksheet non coerente."
                    )
                result = final_matches[0]

        participants = {}
        for record in final_linked:
            telegram_id = clean_value(
                record.get("TELEGRAM_ID_PROPRIETARIO", "")
            )
            if not telegram_id:
                continue
            candidate = {
                "TELEGRAM_ID": telegram_id,
                "USERNAME": normalize_username(
                    record.get("USERNAME_PROPRIETARIO", "")
                ),
                "RUOLO": clean_value(record.get("RUOLO", "")).upper(),
            }
            current = participants.get(telegram_id)
            if (
                current is None
                or (
                    current.get("RUOLO") != "TITOLARE"
                    and candidate["RUOLO"] == "TITOLARE"
                )
            ):
                participants[telegram_id] = candidate
        result = dict(result)
        result["_V2_PARTICIPANTS"] = sorted(
            participants.values(),
            key=lambda participant: (
                0 if participant["RUOLO"] == "TITOLARE" else 1,
                participant["TELEGRAM_ID"],
            ),
        )
        self._cache_invalidator("shipping")
        self._log_safely(
            telegram_id=result.get("TELEGRAM_ID", ""),
            username=result.get("USERNAME", ""),
            action="SPEDIZIONE_V2_COMPLETATA",
            details=(
                f"Richiesta {shipping_id} impostata come SPEDITO. "
                f"Tracking: {tracking}."
            ),
            admin=admin,
        )
        return result


def _coordinator() -> ShippingV2Coordinator:
    return ShippingV2Coordinator()


def create_or_get_v2_shipping_request(**kwargs) -> dict[str, Any]:
    return _coordinator().create_or_get(**kwargs)


def get_v2_shipping_request_by_draft(
    draft_uuid: str,
) -> dict[str, str] | None:
    return _coordinator().get_by_draft(draft_uuid)


def complete_v2_shipping_request(
    shipping_id: str,
    tracking: str,
    admin: str,
) -> dict[str, str]:
    return _coordinator().complete(shipping_id, tracking, admin)


def complete_shipping_request_by_version(
    request: dict[str, Any],
    tracking: str,
    admin: str,
    *,
    legacy_complete: Callable[..., dict] = complete_legacy_shipping_request,
    v2_complete: Callable[..., dict] | None = None,
) -> dict:
    """Instrada il completamento senza alterare il servizio legacy."""
    completion = (
        v2_complete or complete_v2_shipping_request
        if request.get("VERSIONE_SCHEMA") == "V2"
        else legacy_complete
    )
    return completion(request.get("ID", ""), tracking, admin)
