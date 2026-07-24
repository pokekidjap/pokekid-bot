"""Repository prenotazioni Shipping v2.1, non collegato a Telegram.

I lock di ``worksheet_session`` proteggono thread dello stesso processo. Non
costituiscono un'autorità distribuita e non rendono sicure più repliche
Railway contemporanee.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Iterable
from uuid import uuid4
from zoneinfo import ZoneInfo

from config import BOT_DB_SHEET_ID, SHIPPING_RESERVATION_TTL_MINUTES
from services.common import clean_value, normalize_telegram_id, normalize_username
from services.google_runtime import worksheet_session
from services.shipping_v2_schema import (
    OCCUPYING_RESERVATION_STATES,
    ORDER_REGISTRY_HEADERS,
    ORDER_REGISTRY_WORKSHEET_NAME,
    RESERVABLE_SYNC_STATUSES,
    SHIPPING_ITEMS_HEADERS,
    SHIPPING_ITEMS_WORKSHEET_NAME,
    normalized_headers,
    rows_as_dicts,
)

ITALY_TIMEZONE = ZoneInfo("Europe/Rome")
TERMINAL_STATES = frozenset({"SPEDITO", "RILASCIATO"})


class ReservationError(RuntimeError):
    pass


class ReservationSchemaError(ReservationError):
    pass


class ReservationConflictError(ReservationError):
    pass


class IdempotencyConflictError(ReservationError):
    pass


class ReservationStateError(ReservationError):
    pass


class ReservationNotFoundError(ReservationError):
    pass


def _aware_now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(ITALY_TIMEZONE)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ReservationError("Le date devono essere timezone-aware.")
    return current


def _parse_time(value: str) -> datetime | None:
    text = clean_value(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise ReservationError(f"Data non valida: {text!r}.") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReservationError(f"Data senza timezone: {text!r}.")
    return parsed


def _iso(value: datetime) -> str:
    return _aware_now(value).isoformat(timespec="seconds")


def _a1_column(number: int) -> str:
    result = ""
    value = number
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _record_values(record: dict[str, Any]) -> list[str]:
    return [clean_value(record.get(header, "")) for header in SHIPPING_ITEMS_HEADERS]


def _is_expired(record: dict[str, str], now: datetime) -> bool:
    if record.get("STATO_PRENOTAZIONE") != "PRENOTATO":
        return False
    expires_at = _parse_time(record.get("PRENOTATO_FINO_AL", ""))
    return expires_at is not None and expires_at <= now


def _canonical_records(
    records: Iterable[dict[str, Any]],
) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            (
                clean_value(record.get("ID_ARTICOLO", "")).upper(),
                clean_value(record.get("RUOLO", "")).upper(),
            )
            for record in records
        )
    )


def _titular_ids(records: Iterable[dict[str, Any]]) -> set[str]:
    return {
        normalize_telegram_id(
            record.get("TELEGRAM_ID_PROPRIETARIO", "")
        )
        for record in records
        if clean_value(record.get("RUOLO", "")).upper() == "TITOLARE"
        and normalize_telegram_id(
            record.get("TELEGRAM_ID_PROPRIETARIO", "")
        )
    }


class ReservationsRepository:
    def __init__(
        self,
        *,
        bot_db_spreadsheet_id: str | None = None,
        registry_sheet: str = ORDER_REGISTRY_WORKSHEET_NAME,
        reservations_sheet: str = SHIPPING_ITEMS_WORKSHEET_NAME,
        session_factory=worksheet_session,
        uuid_factory=uuid4,
    ) -> None:
        self.spreadsheet_id = clean_value(
            bot_db_spreadsheet_id or BOT_DB_SHEET_ID
        )
        if not self.spreadsheet_id:
            raise ReservationError("BOT_DB_SHEET_ID non configurato.")
        self.registry_sheet = registry_sheet
        self.reservations_sheet = reservations_sheet
        self._session_factory = session_factory
        self._uuid_factory = uuid_factory

    def _uuid(self) -> str:
        return str(self._uuid_factory())

    @staticmethod
    def _read(session, headers: tuple[str, ...], label: str):
        values = session.call(
            lambda worksheet: worksheet.get_all_values(),
            operation_name=f"lettura {label}",
        )
        if tuple(normalized_headers(values)) != headers:
            raise ReservationSchemaError(
                f"{label} non rispetta lo schema previsto."
            )
        return values, rows_as_dicts(values, headers)

    @staticmethod
    def _occupying(
        records: Iterable[dict[str, str]],
        now: datetime,
    ) -> dict[str, dict[str, str]]:
        result = {}
        for record in records:
            if record.get("STATO_PRENOTAZIONE") not in (
                OCCUPYING_RESERVATION_STATES
            ):
                continue
            if _is_expired(record, now):
                continue
            item_id = record.get("ID_ARTICOLO", "").upper()
            if item_id:
                result[item_id] = record
        return result

    @staticmethod
    def _result(records: list[dict[str, str]], *, created: bool):
        first = records[0] if records else {}
        return {
            "uuid_bozza": first.get("UUID_BOZZA", ""),
            "uuid_spedizione": first.get("UUID_SPEDIZIONE", ""),
            "id_spedizione": first.get("ID_SPEDIZIONE", ""),
            "idempotency_key": first.get("IDEMPOTENCY_KEY", ""),
            "created": created,
            "items": records,
        }

    @staticmethod
    def _batch_replace(session, records, operation_name):
        if not records:
            return
        end = _a1_column(len(SHIPPING_ITEMS_HEADERS))
        updates = [
            {
                "range": (
                    f"A{record['_ROW_NUMBER']}:"
                    f"{end}{record['_ROW_NUMBER']}"
                ),
                "values": [_record_values(record)],
            }
            for record in records
        ]
        session.call(
            lambda worksheet: worksheet.batch_update(
                updates,
                value_input_option="USER_ENTERED",
            ),
            operation_name=operation_name,
        )

    def _release_expired_locked(
        self,
        session,
        records: list[dict[str, str]],
        now: datetime,
    ) -> list[dict[str, str]]:
        expired = [record for record in records if _is_expired(record, now)]
        if not expired:
            return []
        timestamp = _iso(now)
        for record in expired:
            record["STATO_PRENOTAZIONE"] = "RILASCIATO"
            record["PRENOTATO_FINO_AL"] = ""
            record["RILASCIATO_IL"] = timestamp
            record["MOTIVO_RILASCIO"] = "TTL_SCADUTO"
            record["ULTIMO_AGGIORNAMENTO"] = timestamp
        self._batch_replace(
            session,
            expired,
            "rilascio prenotazioni scadute v2",
        )
        return expired

    def get_active_reservations(
        self,
        item_ids: Iterable[str] | None = None,
        *,
        now: datetime | None = None,
    ) -> dict[str, dict[str, str]]:
        current = _aware_now(now)
        wanted = (
            {clean_value(item).upper() for item in item_ids}
            if item_ids is not None
            else None
        )
        with self._session_factory(
            self.spreadsheet_id,
            self.reservations_sheet,
        ) as session:
            _, records = self._read(
                session,
                SHIPPING_ITEMS_HEADERS,
                "SPEDIZIONI_ARTICOLI",
            )
        return {
            record["ID_ARTICOLO"]: record
            for key, record in self._occupying(records, current).items()
            if wanted is None or key in wanted
        }

    def is_item_reservable(
        self,
        item_id: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        current = _aware_now(now)
        target = clean_value(item_id).upper()
        with self._session_factory(
            self.spreadsheet_id,
            self.registry_sheet,
        ) as registry_session:
            _, registry = self._read(
                registry_session,
                ORDER_REGISTRY_HEADERS,
                "ORDINI_ARTICOLI",
            )
            item = next(
                (
                    record for record in registry
                    if record["ID_ARTICOLO"].upper() == target
                ),
                None,
            )
            eligible = bool(
                item
                and item["IS_ACTIVE"].upper() == "TRUE"
                and item["SYNC_STATUS"] in RESERVABLE_SYNC_STATUSES
                and item["STATO_ORIGINE"].upper() == "IN MAGAZZINO"
                and item["TELEGRAM_ID_PROPRIETARIO"]
            )
            if not eligible:
                return False
            with self._session_factory(
                self.spreadsheet_id,
                self.reservations_sheet,
            ) as reservation_session:
                _, reservations = self._read(
                    reservation_session,
                    SHIPPING_ITEMS_HEADERS,
                    "SPEDIZIONI_ARTICOLI",
                )
                return target not in self._occupying(
                    reservations,
                    current,
                )

    def reserve_items(
        self,
        *,
        telegram_id: int | str,
        username: str | None,
        item_ids: Iterable[str],
        idempotency_key: str,
        roles: dict[str, str] | None = None,
        authorized_contributor_item_ids: Iterable[str] | None = None,
        ttl_minutes: int | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = _aware_now(now)
        holder_id = normalize_telegram_id(telegram_id)
        holder_username = normalize_username(username)
        key = clean_value(idempotency_key)
        requested_ids = [clean_value(item).upper() for item in item_ids]
        if not holder_id or not key or not requested_ids:
            raise ReservationError(
                "Telegram ID, idempotency key e articoli sono obbligatori."
            )
        if any(not item for item in requested_ids):
            raise ReservationError("ID_ARTICOLO vuoto.")
        if len(requested_ids) != len(set(requested_ids)):
            raise ReservationError("ID_ARTICOLO ripetuto nella richiesta.")
        role_map = {
            clean_value(item_id).upper(): clean_value(role).upper()
            for item_id, role in (roles or {}).items()
        }
        unknown_role_items = set(role_map).difference(requested_ids)
        if unknown_role_items:
            raise ReservationError(
                "Ruoli forniti per articoli non richiesti: "
                + ", ".join(sorted(unknown_role_items))
            )
        authorized_contributors = {
            clean_value(item_id).upper()
            for item_id in (authorized_contributor_item_ids or set())
            if clean_value(item_id)
        }
        ttl = (
            SHIPPING_RESERVATION_TTL_MINUTES
            if ttl_minutes is None
            else int(ttl_minutes)
        )
        if ttl <= 0:
            raise ReservationError("Il TTL deve essere positivo.")

        with self._session_factory(
            self.spreadsheet_id,
            self.registry_sheet,
        ) as registry_session:
            _, registry = self._read(
                registry_session,
                ORDER_REGISTRY_HEADERS,
                "ORDINI_ARTICOLI",
            )
            registry_by_id = {
                record["ID_ARTICOLO"].upper(): record
                for record in registry
                if record["ID_ARTICOLO"]
            }
            selected_registry = []
            rejected = []
            for item_id in requested_ids:
                item = registry_by_id.get(item_id)
                if not (
                    item
                    and item["IS_ACTIVE"].upper() == "TRUE"
                    and item["SYNC_STATUS"] in RESERVABLE_SYNC_STATUSES
                    and item["STATO_ORIGINE"].upper() == "IN MAGAZZINO"
                    and item["TELEGRAM_ID_PROPRIETARIO"]
                ):
                    rejected.append(item_id)
                else:
                    selected_registry.append(item)
            if rejected:
                raise ReservationConflictError(
                    "Articoli non prenotabili: " + ", ".join(rejected)
                )

            requested_content = []
            unauthorized_contributors = []
            for item in selected_registry:
                item_id = item["ID_ARTICOLO"].upper()
                is_holder_item = (
                    item["TELEGRAM_ID_PROPRIETARIO"] == holder_id
                )
                role = "TITOLARE" if is_holder_item else "CONTRIBUENTE"
                requested_role = role_map.get(item_id)
                if requested_role and requested_role != role:
                    raise ReservationConflictError(
                        f"Il ruolo di {item_id} deriva dal proprietario e "
                        "non può essere sovrascritto."
                    )
                if (
                    not is_holder_item
                    and item_id not in authorized_contributors
                ):
                    unauthorized_contributors.append(item_id)
                requested_content.append(
                    {"ID_ARTICOLO": item_id, "RUOLO": role}
                )
            has_titular_item = any(
                content["RUOLO"] == "TITOLARE"
                for content in requested_content
            )

            with self._session_factory(
                self.spreadsheet_id,
                self.reservations_sheet,
            ) as reservation_session:
                _, reservations = self._read(
                    reservation_session,
                    SHIPPING_ITEMS_HEADERS,
                    "SPEDIZIONI_ARTICOLI",
                )
                self._release_expired_locked(
                    reservation_session,
                    reservations,
                    current,
                )
                existing_key = [
                    record
                    for record in reservations
                    if record["IDEMPOTENCY_KEY"] == key
                ]
                if existing_key:
                    existing_holders = _titular_ids(existing_key)
                    if existing_holders != {holder_id}:
                        raise IdempotencyConflictError(
                            "Idempotency key già associata a un titolare "
                            "differente o non valido."
                        )
                    if (
                        _canonical_records(existing_key)
                        != _canonical_records(requested_content)
                    ):
                        raise IdempotencyConflictError(
                            "Idempotency key riusata con contenuto differente."
                        )
                    return self._result(existing_key, created=False)

                if unauthorized_contributors:
                    raise ReservationConflictError(
                        "Contributor non autorizzati: "
                        + ", ".join(unauthorized_contributors)
                    )
                if not has_titular_item:
                    raise ReservationConflictError(
                        "La bozza deve contenere almeno un articolo TITOLARE "
                        "appartenente al chiamante."
                    )

                active_drafts: dict[str, list[dict[str, str]]] = defaultdict(
                    list
                )
                for record in reservations:
                    if record["STATO_PRENOTAZIONE"] not in {
                        "PRENOTATO",
                        "CONFERMATO",
                    }:
                        continue
                    if _is_expired(record, current):
                        continue
                    active_drafts[record["UUID_BOZZA"]].append(record)
                conflicting_drafts = [
                    draft_uuid
                    for draft_uuid, rows in active_drafts.items()
                    if holder_id in _titular_ids(rows)
                ]
                if conflicting_drafts:
                    raise ReservationConflictError(
                        "Il titolare possiede già una bozza attiva: "
                        + ", ".join(sorted(conflicting_drafts))
                    )
                occupying = self._occupying(reservations, current)
                conflicts = [
                    item_id for item_id in requested_ids if item_id in occupying
                ]
                if conflicts:
                    raise ReservationConflictError(
                        "Articoli già prenotati: " + ", ".join(conflicts)
                    )

                draft_uuid = self._uuid()
                reserved_at = _iso(current)
                expires_at = _iso(current + timedelta(minutes=ttl))
                new_records = []
                for item, content in zip(
                    selected_registry,
                    requested_content,
                    strict=True,
                ):
                    owner_username = item["USERNAME"]
                    if (
                        item["TELEGRAM_ID_PROPRIETARIO"] == holder_id
                        and not owner_username
                    ):
                        owner_username = holder_username
                    new_records.append(
                        {
                            "UUID_DETTAGLIO": self._uuid(),
                            "UUID_BOZZA": draft_uuid,
                            "UUID_SPEDIZIONE": "",
                            "ID_SPEDIZIONE": "",
                            "ID_ARTICOLO": item["ID_ARTICOLO"],
                            "TELEGRAM_ID_PROPRIETARIO": item[
                                "TELEGRAM_ID_PROPRIETARIO"
                            ],
                            "USERNAME_PROPRIETARIO": owner_username,
                            "RUOLO": content["RUOLO"],
                            "OGGETTO_SNAPSHOT": item["OGGETTO"],
                            "QUANTITA_SNAPSHOT": item["QUANTITA"],
                            "RIGA_ORDINE_SNAPSHOT": item["SOURCE_ROW"],
                            "STATO_PRENOTAZIONE": "PRENOTATO",
                            "PRENOTATO_IL": reserved_at,
                            "PRENOTATO_FINO_AL": expires_at,
                            "CONFERMATO_IL": "",
                            "SPEDITO_IL": "",
                            "RILASCIATO_IL": "",
                            "MOTIVO_RILASCIO": "",
                            "IDEMPOTENCY_KEY": key,
                            "ULTIMO_AGGIORNAMENTO": reserved_at,
                            "VERSIONE": "V1",
                        }
                    )
                rows = [_record_values(record) for record in new_records]
                detail_ids = {
                    record["UUID_DETTAGLIO"] for record in new_records
                }

                def append_or_reconcile(worksheet):
                    latest_values = worksheet.get_all_values()
                    latest = rows_as_dicts(
                        latest_values,
                        SHIPPING_ITEMS_HEADERS,
                    )
                    found = [
                        record
                        for record in latest
                        if record["UUID_DETTAGLIO"] in detail_ids
                        or record["IDEMPOTENCY_KEY"] == key
                    ]
                    if found:
                        if (
                            len(found) == len(new_records)
                            and _canonical_records(found)
                            == _canonical_records(requested_content)
                        ):
                            return found
                        raise IdempotencyConflictError(
                            "Append ambiguo o parziale per UUID/idempotency key."
                        )
                    latest_occupying = self._occupying(latest, current)
                    latest_conflicts = [
                        item_id
                        for item_id in requested_ids
                        if item_id in latest_occupying
                    ]
                    if latest_conflicts:
                        raise ReservationConflictError(
                            "Articoli già prenotati: "
                            + ", ".join(latest_conflicts)
                        )
                    worksheet.append_rows(
                        rows,
                        value_input_option="USER_ENTERED",
                    )
                    return new_records

                written = reservation_session.call(
                    append_or_reconcile,
                    operation_name="prenotazione atomica gruppo articoli",
                )
                return self._result(written, created=True)

    create_or_get_draft = reserve_items

    def get_active_draft_for_user(
        self,
        telegram_id: int | str,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        current = _aware_now(now)
        user_id = normalize_telegram_id(telegram_id)
        with self._session_factory(
            self.spreadsheet_id,
            self.reservations_sheet,
        ) as session:
            _, records = self._read(
                session,
                SHIPPING_ITEMS_HEADERS,
                "SPEDIZIONI_ARTICOLI",
            )
        drafts: dict[str, list[dict[str, str]]] = defaultdict(list)
        for record in records:
            if record["STATO_PRENOTAZIONE"] not in {"PRENOTATO", "CONFERMATO"}:
                continue
            if _is_expired(record, current):
                continue
            drafts[record["UUID_BOZZA"]].append(record)
        candidates = [
            rows
            for rows in drafts.values()
            if any(
                row["RUOLO"] == "TITOLARE"
                and row["TELEGRAM_ID_PROPRIETARIO"] == user_id
                for row in rows
            )
        ]
        if not candidates:
            return None
        if len(candidates) > 1:
            raise ReservationConflictError(
                "Il titolare possiede più bozze attive incoerenti."
            )
        selected = max(
            candidates,
            key=lambda rows: max(
                _parse_time(row["ULTIMO_AGGIORNAMENTO"])
                or datetime.min.replace(tzinfo=ITALY_TIMEZONE)
                for row in rows
            ),
        )
        return self._result(selected, created=False)

    def get_draft(self, draft_uuid: str) -> dict[str, Any] | None:
        draft = clean_value(draft_uuid)
        if not draft:
            return None
        with self._session_factory(
            self.spreadsheet_id,
            self.reservations_sheet,
        ) as session:
            _, records = self._read(
                session,
                SHIPPING_ITEMS_HEADERS,
                "SPEDIZIONI_ARTICOLI",
            )
        selected = [
            record
            for record in records
            if record["UUID_BOZZA"] == draft
        ]
        if not selected:
            return None
        return self._result(selected, created=False)

    def _transition(
        self,
        draft_uuid: str,
        *,
        expected: str,
        target: str,
        now: datetime,
        reason: str = "",
        shipping_uuid: str = "",
        shipping_id: str = "",
    ):
        draft = clean_value(draft_uuid)
        with self._session_factory(
            self.spreadsheet_id,
            self.reservations_sheet,
        ) as session:
            _, records = self._read(
                session,
                SHIPPING_ITEMS_HEADERS,
                "SPEDIZIONI_ARTICOLI",
            )
            selected = [
                record for record in records if record["UUID_BOZZA"] == draft
            ]
            if not selected:
                raise ReservationNotFoundError("Bozza non trovata.")
            states = {record["STATO_PRENOTAZIONE"] for record in selected}
            if states == {target}:
                return self._result(selected, created=False)
            if states != {expected}:
                raise ReservationStateError(
                    f"Transizione {states} -> {target} non consentita."
                )
            if target == "CONFERMATO" and any(
                _is_expired(record, now) for record in selected
            ):
                raise ReservationStateError("La bozza è scaduta.")
            timestamp = _iso(now)
            final_shipping_uuid = shipping_uuid or (
                self._uuid() if target == "CONFERMATO" else ""
            )
            for record in selected:
                record["STATO_PRENOTAZIONE"] = target
                record["ULTIMO_AGGIORNAMENTO"] = timestamp
                if target == "CONFERMATO":
                    record["UUID_SPEDIZIONE"] = final_shipping_uuid
                    record["ID_SPEDIZIONE"] = shipping_id
                    record["PRENOTATO_FINO_AL"] = ""
                    record["CONFERMATO_IL"] = timestamp
                elif target == "RILASCIATO":
                    record["PRENOTATO_FINO_AL"] = ""
                    record["RILASCIATO_IL"] = timestamp
                    record["MOTIVO_RILASCIO"] = reason
                elif target == "SPEDITO":
                    record["SPEDITO_IL"] = timestamp
            self._batch_replace(
                session,
                selected,
                f"transizione prenotazioni a {target}",
            )
            return self._result(selected, created=False)

    def confirm_reservations(
        self,
        draft_uuid: str,
        *,
        shipping_uuid: str | None = None,
        shipping_id: str = "",
        now: datetime | None = None,
    ):
        return self._transition(
            draft_uuid,
            expected="PRENOTATO",
            target="CONFERMATO",
            now=_aware_now(now),
            shipping_uuid=clean_value(shipping_uuid),
            shipping_id=clean_value(shipping_id),
        )

    def release_draft(
        self,
        draft_uuid: str,
        *,
        reason: str,
        now: datetime | None = None,
    ):
        clean_reason = clean_value(reason)
        if not clean_reason:
            raise ReservationError("Motivo di rilascio obbligatorio.")
        return self._transition(
            draft_uuid,
            expected="PRENOTATO",
            target="RILASCIATO",
            now=_aware_now(now),
            reason=clean_reason,
        )

    def mark_items_shipped(
        self,
        draft_uuid: str,
        *,
        now: datetime | None = None,
    ):
        return self._transition(
            draft_uuid,
            expected="CONFERMATO",
            target="SPEDITO",
            now=_aware_now(now),
        )

    def release_expired_reservations(
        self,
        *,
        now: datetime | None = None,
    ) -> list[dict[str, str]]:
        current = _aware_now(now)
        with self._session_factory(
            self.spreadsheet_id,
            self.reservations_sheet,
        ) as session:
            _, records = self._read(
                session,
                SHIPPING_ITEMS_HEADERS,
                "SPEDIZIONI_ARTICOLI",
            )
            return self._release_expired_locked(session, records, current)

    def get_items_grouped_by_owner(
        self,
        *,
        draft_uuid: str | None = None,
        shipping_uuid: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        draft = clean_value(draft_uuid)
        shipping = clean_value(shipping_uuid)
        if bool(draft) == bool(shipping):
            raise ReservationError(
                "Indicare esattamente uno tra bozza e spedizione."
            )
        with self._session_factory(
            self.spreadsheet_id,
            self.reservations_sheet,
        ) as session:
            _, records = self._read(
                session,
                SHIPPING_ITEMS_HEADERS,
                "SPEDIZIONI_ARTICOLI",
            )
        selected = [
            record
            for record in records
            if (
                (draft and record["UUID_BOZZA"] == draft)
                or (shipping and record["UUID_SPEDIZIONE"] == shipping)
            )
        ]
        grouped: dict[str, dict[str, Any]] = {}
        for record in selected:
            owner_id = record["TELEGRAM_ID_PROPRIETARIO"]
            group = grouped.setdefault(
                owner_id,
                {
                    "telegram_id_proprietario": owner_id,
                    "username_proprietario": record["USERNAME_PROPRIETARIO"],
                    "ruoli": set(),
                    "items": [],
                },
            )
            group["ruoli"].add(record["RUOLO"])
            group["items"].append(record)
        for group in grouped.values():
            group["ruoli"] = sorted(group["ruoli"])
        return grouped


def _repository() -> ReservationsRepository:
    return ReservationsRepository()


def get_active_reservations(*args, **kwargs):
    return _repository().get_active_reservations(*args, **kwargs)


def is_item_reservable(*args, **kwargs):
    return _repository().is_item_reservable(*args, **kwargs)


def create_or_get_draft(**kwargs):
    return _repository().create_or_get_draft(**kwargs)


def reserve_items(**kwargs):
    return _repository().reserve_items(**kwargs)


def get_active_draft_for_user(*args, **kwargs):
    return _repository().get_active_draft_for_user(*args, **kwargs)


def get_draft(*args, **kwargs):
    return _repository().get_draft(*args, **kwargs)


def confirm_reservations(*args, **kwargs):
    return _repository().confirm_reservations(*args, **kwargs)


def release_draft(*args, **kwargs):
    return _repository().release_draft(*args, **kwargs)


def mark_items_shipped(*args, **kwargs):
    return _repository().mark_items_shipped(*args, **kwargs)


def release_expired_reservations(**kwargs):
    return _repository().release_expired_reservations(**kwargs)


def get_items_grouped_by_owner(**kwargs):
    return _repository().get_items_grouped_by_owner(**kwargs)
