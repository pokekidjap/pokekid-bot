"""Registro stabile degli articoli derivato da ORDINI in sola lettura."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from config import BOT_DB_SHEET_ID, SPREADSHEET_ID, WORKSHEET_NAME
from services.common import (
    clean_value,
    normalize_telegram_id,
    normalize_username,
)
from services.google_runtime import (
    worksheet_operation,
    worksheet_session,
)
from services.shipping_v2_schema import (
    LIVE_RESERVATION_STATES,
    ORDER_REGISTRY_HEADERS,
    ORDER_REGISTRY_WORKSHEET_NAME,
    SHIPPING_ITEMS_HEADERS,
    SHIPPING_ITEMS_WORKSHEET_NAME,
    normalized_headers,
    rows_as_dicts,
)

ITALY_TIMEZONE = ZoneInfo("Europe/Rome")
IDENTITY_SOURCE_FIELDS = (
    "DATA",
    "OGGETTO",
    "QUANTITA",
    "COSTO",
    "VENDITA",
    "TOT_VENDITA",
    "USERNAME",
)


class OrderRegistryError(RuntimeError):
    """Errore base del registro ordini."""


class OrderRegistrySchemaError(OrderRegistryError):
    """Schema sorgente o destinazione non compatibile."""


class OrderRegistryConflictError(OrderRegistryError):
    """Riconciliazione non applicabile in sicurezza."""


@dataclass
class RegistrySyncPlan:
    updated_records: list[dict[str, str]] = field(default_factory=list)
    new_records: list[dict[str, str]] = field(default_factory=list)
    unchanged_records: int = 0
    source_rows: int = 0
    created: int = 0
    updated: int = 0
    ambiguous: int = 0
    inactive: int = 0
    unassociated: int = 0
    moved: int = 0
    modified: int = 0
    unresolved_usernames: list[dict[str, Any]] = field(default_factory=list)
    locked_duplicate_groups: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "source_rows": self.source_rows,
            "created": self.created,
            "updated": self.updated,
            "ambiguous": self.ambiguous,
            "inactive": self.inactive,
            "unassociated": self.unassociated,
            "moved": self.moved,
            "modified": self.modified,
            "unchanged": self.unchanged_records,
            "unresolved_usernames": self.unresolved_usernames,
            "locked_duplicate_groups": self.locked_duplicate_groups,
        }


@dataclass
class RegistrySyncSnapshot:
    """Esito della sync con gli snapshot già letti dalla stessa azione."""

    summary: dict[str, Any]
    registry_values: list[list[Any]]
    reservation_values: list[list[Any]]


def _normalize_fingerprint_value(value: Any) -> str:
    text = unicodedata.normalize("NFKC", clean_value(value))
    return re.sub(r"\s+", " ", text).casefold()


def _fingerprint(values: list[Any]) -> str:
    canonical = json.dumps(
        [_normalize_fingerprint_value(value) for value in values],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def _source_header_alias(header: str) -> str:
    normalized = clean_value(header).upper()
    aliases = {
        "TOT. VENDITA": "TOT_VENDITA",
        "TOT VENDITA": "TOT_VENDITA",
        "TOT_VENDITA": "TOT_VENDITA",
        "UTENTI": "USERNAME",
        "STATO": "STATO_ORIGINE",
        "DATA SPEDIZIONE": "DATA_SPEDIZIONE",
        "DATA_SPEDIZIONE": "DATA_SPEDIZIONE",
    }
    return aliases.get(normalized, normalized)


def parse_source_orders(
    values: list[list[Any]],
    *,
    source_spreadsheet_id: str,
    source_sheet: str,
) -> list[dict[str, str]]:
    """Interpreta ORDINI senza offrire alcuna operazione di scrittura."""
    if not values:
        raise OrderRegistrySchemaError("ORDINI non contiene intestazioni.")
    source_headers = [
        _source_header_alias(value) for value in values[0]
    ]
    missing = sorted(
        set(IDENTITY_SOURCE_FIELDS).difference(source_headers)
    )
    if missing:
        raise OrderRegistrySchemaError(
            "ORDINI: colonne necessarie mancanti: " + ", ".join(missing)
        )
    parsed = []
    for row_number, row in enumerate(values[1:], start=2):
        first_eleven = [
            row[index] if index < len(row) else ""
            for index in range(11)
        ]
        if not any(clean_value(value) for value in first_eleven):
            continue
        by_header = {
            header: clean_value(
                row[index] if index < len(row) else ""
            )
            for index, header in enumerate(source_headers)
            if header
        }
        identity_values = [
            by_header.get(field, "") for field in IDENTITY_SOURCE_FIELDS
        ]
        parsed.append(
            {
                "SOURCE_SPREADSHEET_ID": clean_value(
                    source_spreadsheet_id
                ),
                "SOURCE_SHEET": clean_value(source_sheet),
                "SOURCE_ROW": str(row_number),
                "IDENTITY_FINGERPRINT": _fingerprint(identity_values),
                "ROW_FINGERPRINT": _fingerprint(first_eleven),
                "DATA": by_header.get("DATA", ""),
                "OGGETTO": by_header.get("OGGETTO", ""),
                "QUANTITA": by_header.get("QUANTITA", ""),
                "COSTO": by_header.get("COSTO", ""),
                "VENDITA": by_header.get("VENDITA", ""),
                "TOT_VENDITA": by_header.get("TOT_VENDITA", ""),
                "USERNAME": normalize_username(
                    by_header.get("USERNAME", "")
                ),
                "STATO_ORIGINE": clean_value(
                    by_header.get("STATO_ORIGINE", "")
                ).upper(),
                "DATA_SPEDIZIONE": by_header.get(
                    "DATA_SPEDIZIONE",
                    "",
                ),
                "NOTE": by_header.get("NOTE", ""),
            }
        )
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for item in parsed:
        groups[item["IDENTITY_FINGERPRINT"]].append(item)
    for group in groups.values():
        for index, item in enumerate(
            sorted(group, key=lambda row: int(row["SOURCE_ROW"])),
            start=1,
        ):
            item["DUPLICATE_INDEX"] = str(index)
    return parsed


def build_profile_map(
    profile_values: list[list[Any]],
) -> tuple[dict[str, str], set[str]]:
    headers = normalized_headers(profile_values)
    if not {"TELEGRAM_ID", "USERNAME"}.issubset(headers):
        raise OrderRegistrySchemaError(
            "PROFILI deve contenere TELEGRAM_ID e USERNAME."
        )
    username_index = headers.index("USERNAME")
    telegram_index = headers.index("TELEGRAM_ID")
    candidates: dict[str, set[str]] = defaultdict(set)
    for row in profile_values[1:]:
        username = normalize_username(
            row[username_index] if username_index < len(row) else ""
        )
        telegram_id = normalize_telegram_id(
            row[telegram_index] if telegram_index < len(row) else ""
        )
        if username and telegram_id:
            candidates[username].add(telegram_id)
    resolved = {
        username: next(iter(ids))
        for username, ids in candidates.items()
        if len(ids) == 1
    }
    ambiguous = {
        username for username, ids in candidates.items() if len(ids) > 1
    }
    return resolved, ambiguous


def _new_record(
    source: dict[str, str],
    *,
    item_id: str,
    first_seen: str,
    profile_map: dict[str, str],
    profile_ambiguous: set[str],
    forced_status: str | None = None,
) -> dict[str, str]:
    username = source["USERNAME"]
    owner_id = profile_map.get(username, "")
    status = forced_status or ("OK" if owner_id else "NON_ASSOCIATO")
    return {
        "ID_ARTICOLO": item_id,
        "SOURCE_SPREADSHEET_ID": source["SOURCE_SPREADSHEET_ID"],
        "SOURCE_SHEET": source["SOURCE_SHEET"],
        "SOURCE_ROW": source["SOURCE_ROW"],
        "IDENTITY_FINGERPRINT": source["IDENTITY_FINGERPRINT"],
        "ROW_FINGERPRINT": source["ROW_FINGERPRINT"],
        "DUPLICATE_INDEX": source["DUPLICATE_INDEX"],
        "DATA": source["DATA"],
        "OGGETTO": source["OGGETTO"],
        "QUANTITA": source["QUANTITA"],
        "COSTO": source["COSTO"],
        "VENDITA": source["VENDITA"],
        "TOT_VENDITA": source["TOT_VENDITA"],
        "USERNAME": username,
        "TELEGRAM_ID_PROPRIETARIO": owner_id,
        "STATO_ORIGINE": source["STATO_ORIGINE"],
        "DATA_SPEDIZIONE": source["DATA_SPEDIZIONE"],
        "NOTE": source["NOTE"],
        "FIRST_SEEN_AT": first_seen,
        "LAST_SEEN_AT": first_seen,
        "SYNC_STATUS": status,
        "IS_ACTIVE": "TRUE",
        "VERSIONE": "V1",
        "_PROFILE_AMBIGUOUS": (
            "TRUE" if username in profile_ambiguous else "FALSE"
        ),
    }


def _updated_from_source(
    existing: dict[str, str],
    source: dict[str, str],
    *,
    now: str,
    profile_map: dict[str, str],
    profile_ambiguous: set[str],
    match_kind: str,
    forced_status: str | None = None,
) -> dict[str, str]:
    record = _new_record(
        source,
        item_id=existing["ID_ARTICOLO"],
        first_seen=existing.get("FIRST_SEEN_AT") or now,
        profile_map=profile_map,
        profile_ambiguous=profile_ambiguous,
        forced_status=forced_status,
    )
    if forced_status is None and record["TELEGRAM_ID_PROPRIETARIO"]:
        record["SYNC_STATUS"] = (
            "MODIFICATO" if match_kind == "modified" else "OK"
        )
    record["LAST_SEEN_AT"] = clean_value(
        existing.get("LAST_SEEN_AT", "")
    )
    if any(
        clean_value(record.get(header, ""))
        != clean_value(existing.get(header, ""))
        for header in ORDER_REGISTRY_HEADERS
        if header != "LAST_SEEN_AT"
    ):
        record["LAST_SEEN_AT"] = now
    record["_ROW_NUMBER"] = existing["_ROW_NUMBER"]
    record["_MATCH_KIND"] = match_kind
    return record


def _public_record(record: dict[str, str]) -> dict[str, str]:
    return {
        header: clean_value(record.get(header, ""))
        for header in ORDER_REGISTRY_HEADERS
    }


def build_sync_plan(
    *,
    source_rows: list[dict[str, str]],
    registry_values: list[list[Any]],
    profile_values: list[list[Any]],
    source_spreadsheet_id: str = "",
    source_sheet: str = "",
    active_reserved_item_ids: set[str] | None = None,
    now: str,
    uuid_factory=uuid4,
) -> RegistrySyncPlan:
    if tuple(normalized_headers(registry_values)) != ORDER_REGISTRY_HEADERS:
        raise OrderRegistrySchemaError(
            "ORDINI_ARTICOLI non rispetta lo schema A:W previsto."
        )
    existing_all = rows_as_dicts(
        registry_values,
        ORDER_REGISTRY_HEADERS,
    )
    ids = [row["ID_ARTICOLO"].upper() for row in existing_all if row["ID_ARTICOLO"]]
    if len(ids) != len(set(ids)):
        raise OrderRegistryConflictError(
            "ORDINI_ARTICOLI contiene ID_ARTICOLO duplicati."
        )
    if any(
        row.get("IS_ACTIVE", "").upper() == "TRUE"
        and not row.get("ID_ARTICOLO")
        for row in existing_all
    ):
        raise OrderRegistryConflictError(
            "ORDINI_ARTICOLI contiene record attivi senza ID."
        )
    profile_map, profile_ambiguous = build_profile_map(profile_values)
    active_reserved = {
        clean_value(item_id).upper()
        for item_id in (active_reserved_item_ids or set())
    }
    source_id = (
        source_rows[0]["SOURCE_SPREADSHEET_ID"]
        if source_rows
        else clean_value(source_spreadsheet_id)
    )
    source_name = (
        source_rows[0]["SOURCE_SHEET"]
        if source_rows
        else clean_value(source_sheet)
    )
    existing = [
        row
        for row in existing_all
        if row.get("SOURCE_SPREADSHEET_ID") == source_id
        and row.get("SOURCE_SHEET") == source_name
    ]
    untouched_other = [
        row for row in existing_all if row not in existing
    ]
    del untouched_other  # Esplicita: record di altre sorgenti non sono aggiornati.

    plan = RegistrySyncPlan(source_rows=len(source_rows))
    used_ids = set(ids)
    matched_source: set[int] = set()
    matched_existing: set[int] = set()
    result_by_existing: dict[int, dict[str, str]] = {}

    old_by_identity: dict[str, list[int]] = defaultdict(list)
    new_by_identity: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(existing):
        if (
            row.get("IS_ACTIVE", "").upper() == "TRUE"
            or row.get("ID_ARTICOLO", "").upper() in active_reserved
            or row.get("SYNC_STATUS") == "AMBIGUO"
        ):
            old_by_identity[row["IDENTITY_FINGERPRINT"]].append(index)
    for index, row in enumerate(source_rows):
        new_by_identity[row["IDENTITY_FINGERPRINT"]].append(index)

    locked_identities = set()
    for identity in set(old_by_identity).union(new_by_identity):
        old_indexes = old_by_identity.get(identity, [])
        new_indexes = new_by_identity.get(identity, [])
        if max(len(old_indexes), len(new_indexes)) <= 1:
            continue
        has_reserved = any(
            existing[index].get("ID_ARTICOLO", "").upper()
            in active_reserved
            for index in old_indexes
        )
        old_shape = Counter(
            existing[index]["ROW_FINGERPRINT"] for index in old_indexes
        )
        new_shape = Counter(
            source_rows[index]["ROW_FINGERPRINT"] for index in new_indexes
        )
        if has_reserved and old_shape != new_shape:
            locked_identities.add(identity)
    plan.locked_duplicate_groups = sorted(locked_identities)

    def add_match(
        source_index: int,
        existing_index: int,
        match_kind: str,
        *,
        forced_status: str | None = None,
    ) -> None:
        matched_source.add(source_index)
        matched_existing.add(existing_index)
        result_by_existing[existing_index] = _updated_from_source(
            existing[existing_index],
            source_rows[source_index],
            now=now,
            profile_map=profile_map,
            profile_ambiguous=profile_ambiguous,
            match_kind=match_kind,
            forced_status=forced_status,
        )

    # Gruppi duplicati modificati con prenotazioni: solo corrispondenze esatte,
    # senza spostare o riassociare automaticamente gli ID.
    for identity in locked_identities:
        source_indexes = new_by_identity.get(identity, [])
        existing_indexes = old_by_identity.get(identity, [])
        existing_by_exact: dict[tuple[str, str], list[int]] = defaultdict(list)
        for index in existing_indexes:
            row = existing[index]
            existing_by_exact[
                (row["SOURCE_ROW"], row["ROW_FINGERPRINT"])
            ].append(index)
        for source_index in source_indexes:
            source = source_rows[source_index]
            candidates = existing_by_exact.get(
                (source["SOURCE_ROW"], source["ROW_FINGERPRINT"]),
                [],
            )
            available = [
                index for index in candidates if index not in matched_existing
            ]
            if len(available) == 1:
                add_match(
                    source_index,
                    available[0],
                    "exact",
                    forced_status="AMBIGUO",
                )
        for source_index in source_indexes:
            if source_index in matched_source:
                continue
            item_id = f"ART-{uuid_factory()}"
            while item_id.upper() in used_ids:
                item_id = f"ART-{uuid_factory()}"
            used_ids.add(item_id.upper())
            record = _new_record(
                source_rows[source_index],
                item_id=item_id,
                first_seen=now,
                profile_map=profile_map,
                profile_ambiguous=profile_ambiguous,
                forced_status="AMBIGUO",
            )
            plan.new_records.append(record)
            matched_source.add(source_index)

    def match_unique(
        source_key,
        existing_key,
        match_kind: str,
    ) -> None:
        source_map: dict[Any, list[int]] = defaultdict(list)
        existing_map: dict[Any, list[int]] = defaultdict(list)
        for index, row in enumerate(source_rows):
            if (
                index not in matched_source
                and row["IDENTITY_FINGERPRINT"] not in locked_identities
            ):
                source_map[source_key(row)].append(index)
        for index, row in enumerate(existing):
            if (
                index not in matched_existing
                and row["IDENTITY_FINGERPRINT"] not in locked_identities
            ):
                existing_map[existing_key(row)].append(index)
        for key, source_indexes in source_map.items():
            existing_indexes = existing_map.get(key, [])
            if len(source_indexes) == 1 and len(existing_indexes) == 1:
                add_match(
                    source_indexes[0],
                    existing_indexes[0],
                    match_kind,
                )

    match_unique(
        lambda row: (row["SOURCE_ROW"], row["ROW_FINGERPRINT"]),
        lambda row: (row["SOURCE_ROW"], row["ROW_FINGERPRINT"]),
        "exact",
    )
    match_unique(
        lambda row: row["ROW_FINGERPRINT"],
        lambda row: row["ROW_FINGERPRINT"],
        "moved",
    )
    match_unique(
        lambda row: (row["SOURCE_ROW"], row["IDENTITY_FINGERPRINT"]),
        lambda row: (row["SOURCE_ROW"], row["IDENTITY_FINGERPRINT"]),
        "modified",
    )

    remaining_source_by_identity: dict[str, list[int]] = defaultdict(list)
    remaining_existing_by_identity: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(source_rows):
        if index not in matched_source:
            remaining_source_by_identity[row["IDENTITY_FINGERPRINT"]].append(index)
    for index, row in enumerate(existing):
        if index not in matched_existing:
            remaining_existing_by_identity[
                row["IDENTITY_FINGERPRINT"]
            ].append(index)

    for identity, source_indexes in remaining_source_by_identity.items():
        existing_indexes = remaining_existing_by_identity.get(identity, [])
        forced_ambiguous = bool(existing_indexes)
        for source_index in source_indexes:
            item_id = f"ART-{uuid_factory()}"
            while item_id.upper() in used_ids:
                item_id = f"ART-{uuid_factory()}"
            used_ids.add(item_id.upper())
            record = _new_record(
                source_rows[source_index],
                item_id=item_id,
                first_seen=now,
                profile_map=profile_map,
                profile_ambiguous=profile_ambiguous,
                forced_status="AMBIGUO" if forced_ambiguous else None,
            )
            plan.new_records.append(record)
            matched_source.add(source_index)
        if forced_ambiguous:
            for existing_index in existing_indexes:
                record = dict(existing[existing_index])
                record["SYNC_STATUS"] = "AMBIGUO"
                record["IS_ACTIVE"] = "FALSE"
                if any(
                    clean_value(record.get(header, ""))
                    != clean_value(existing[existing_index].get(header, ""))
                    for header in ORDER_REGISTRY_HEADERS
                    if header != "LAST_SEEN_AT"
                ):
                    record["LAST_SEEN_AT"] = now
                result_by_existing[existing_index] = record
                matched_existing.add(existing_index)

    for existing_index, old in enumerate(existing):
        if existing_index in matched_existing:
            continue
        record = dict(old)
        record["SYNC_STATUS"] = (
            "AMBIGUO"
            if old["IDENTITY_FINGERPRINT"] in locked_identities
            else "NON_PRESENTE"
        )
        record["IS_ACTIVE"] = "FALSE"
        if any(
            clean_value(record.get(header, ""))
            != clean_value(old.get(header, ""))
            for header in ORDER_REGISTRY_HEADERS
            if header != "LAST_SEEN_AT"
        ):
            record["LAST_SEEN_AT"] = now
        result_by_existing[existing_index] = record

    for existing_index, record in sorted(result_by_existing.items()):
        public_new = _public_record(record)
        public_old = _public_record(existing[existing_index])
        if public_new == public_old:
            plan.unchanged_records += 1
        else:
            plan.updated_records.append(record)
            plan.updated += 1
        match_kind = record.get("_MATCH_KIND", "")
        if match_kind == "moved":
            plan.moved += 1
        elif match_kind == "modified":
            plan.modified += 1

    plan.created = len(plan.new_records)
    all_result_records = [
        *(_public_record(record) for record in result_by_existing.values()),
        *(_public_record(record) for record in plan.new_records),
    ]
    plan.ambiguous = sum(
        record["SYNC_STATUS"] == "AMBIGUO"
        for record in all_result_records
    )
    plan.inactive = sum(
        record["IS_ACTIVE"] == "FALSE" for record in all_result_records
    )
    plan.unassociated = sum(
        record["SYNC_STATUS"] == "NON_ASSOCIATO"
        for record in all_result_records
    )
    for source in source_rows:
        username = source["USERNAME"]
        if username not in profile_map:
            plan.unresolved_usernames.append(
                {
                    "source_row": int(source["SOURCE_ROW"]),
                    "username": username,
                    "reason": (
                        "profilo ambiguo"
                        if username in profile_ambiguous
                        else "profilo non associabile"
                    ),
                }
            )
    return plan


def read_source_orders_read_only(
    spreadsheet_id: str,
    worksheet_name: str,
) -> list[list[str]]:
    """Unico accesso al gestionale: get_all_values, mai una scrittura."""
    return worksheet_operation(
        spreadsheet_id,
        worksheet_name,
        lambda worksheet: worksheet.get_all_values(),
        operation_name="lettura read-only ORDINI per registro articoli",
    )


def _a1_column(number: int) -> str:
    result = ""
    value = number
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


class OrderRegistryRepository:
    """Sincronizza il registro mantenendo il lock della worksheet destinazione."""

    def __init__(
        self,
        *,
        source_spreadsheet_id: str | None = None,
        source_sheet: str | None = None,
        bot_db_spreadsheet_id: str | None = None,
        registry_sheet: str = ORDER_REGISTRY_WORKSHEET_NAME,
        profiles_sheet: str = "PROFILI",
        reservations_sheet: str = SHIPPING_ITEMS_WORKSHEET_NAME,
        session_factory=worksheet_session,
        read_operation=worksheet_operation,
        uuid_factory=uuid4,
        now_factory=None,
    ) -> None:
        self.source_spreadsheet_id = clean_value(
            source_spreadsheet_id or SPREADSHEET_ID
        )
        self.source_sheet = clean_value(source_sheet or WORKSHEET_NAME)
        self.bot_db_spreadsheet_id = clean_value(
            bot_db_spreadsheet_id or BOT_DB_SHEET_ID
        )
        if not all(
            (
                self.source_spreadsheet_id,
                self.source_sheet,
                self.bot_db_spreadsheet_id,
            )
        ):
            raise OrderRegistryError("Target Google Sheets non configurati.")
        if self.source_spreadsheet_id == self.bot_db_spreadsheet_id:
            raise OrderRegistryError(
                "Lo spreadsheet sorgente e il DATABASE BOT devono essere diversi."
            )
        self.registry_sheet = registry_sheet
        self.profiles_sheet = profiles_sheet
        self.reservations_sheet = reservations_sheet
        self._session_factory = session_factory
        self._read_operation = read_operation
        self._uuid_factory = uuid_factory
        self._now_factory = now_factory or (
            lambda: datetime.now(ITALY_TIMEZONE)
        )

    def _read_only(self, spreadsheet_id: str, worksheet_name: str):
        return self._read_operation(
            spreadsheet_id,
            worksheet_name,
            lambda worksheet: worksheet.get_all_values(),
            operation_name=f"lettura {worksheet_name}",
        )

    def synchronize_with_snapshot(self) -> RegistrySyncSnapshot:
        source_values = self._read_only(
            self.source_spreadsheet_id,
            self.source_sheet,
        )
        profile_values = self._read_only(
            self.bot_db_spreadsheet_id,
            self.profiles_sheet,
        )
        source_rows = parse_source_orders(
            source_values,
            source_spreadsheet_id=self.source_spreadsheet_id,
            source_sheet=self.source_sheet,
        )
        now = self._now_factory()
        if now.tzinfo is None or now.utcoffset() is None:
            raise OrderRegistryError("Il clock deve essere timezone-aware.")
        now_text = now.isoformat(timespec="seconds")

        with self._session_factory(
            self.bot_db_spreadsheet_id,
            self.registry_sheet,
        ) as registry_session:
            registry_values = registry_session.call(
                lambda worksheet: worksheet.get_all_values(),
                operation_name="lettura ORDINI_ARTICOLI",
            )
            with self._session_factory(
                self.bot_db_spreadsheet_id,
                self.reservations_sheet,
            ) as reservation_session:
                reservation_values = reservation_session.call(
                    lambda worksheet: worksheet.get_all_values(),
                    operation_name="lettura prenotazioni per sincronizzazione",
                )
            if (
                tuple(normalized_headers(reservation_values))
                != SHIPPING_ITEMS_HEADERS
            ):
                raise OrderRegistrySchemaError(
                    "SPEDIZIONI_ARTICOLI non rispetta lo schema previsto."
                )
            active_reserved = {
                record["ID_ARTICOLO"]
                for record in rows_as_dicts(
                    reservation_values,
                    SHIPPING_ITEMS_HEADERS,
                )
                if record["STATO_PRENOTAZIONE"] in LIVE_RESERVATION_STATES
            }
            plan = build_sync_plan(
                source_rows=source_rows,
                registry_values=registry_values,
                profile_values=profile_values,
                source_spreadsheet_id=self.source_spreadsheet_id,
                source_sheet=self.source_sheet,
                active_reserved_item_ids=active_reserved,
                now=now_text,
                uuid_factory=self._uuid_factory,
            )
            if plan.updated_records:
                last_column = _a1_column(len(ORDER_REGISTRY_HEADERS))
                updates = [
                    {
                        "range": (
                            f"A{record['_ROW_NUMBER']}:"
                            f"{last_column}{record['_ROW_NUMBER']}"
                        ),
                        "values": [[
                            record.get(header, "")
                            for header in ORDER_REGISTRY_HEADERS
                        ]],
                    }
                    for record in plan.updated_records
                ]
                registry_session.call(
                    lambda worksheet: worksheet.batch_update(
                        updates,
                        value_input_option="USER_ENTERED",
                    ),
                    operation_name="aggiornamento ORDINI_ARTICOLI",
                )
            if plan.new_records:
                rows = [
                    [record.get(header, "") for header in ORDER_REGISTRY_HEADERS]
                    for record in plan.new_records
                ]
                new_ids = {
                    record["ID_ARTICOLO"] for record in plan.new_records
                }

                def append_or_reconcile(worksheet):
                    latest = worksheet.get_all_values()
                    existing_by_id = {
                        record["ID_ARTICOLO"]: record
                        for record in rows_as_dicts(
                            latest,
                            ORDER_REGISTRY_HEADERS,
                        )
                        if record["ID_ARTICOLO"] in new_ids
                    }
                    if existing_by_id:
                        if set(existing_by_id) == new_ids:
                            return
                        raise OrderRegistryConflictError(
                            "Append registro con esito parziale o ambiguo."
                        )
                    worksheet.append_rows(
                        rows,
                        value_input_option="USER_ENTERED",
                    )

                registry_session.call(
                    append_or_reconcile,
                    operation_name="creazione record ORDINI_ARTICOLI",
                )
            if plan.updated_records or plan.new_records:
                registry_values = registry_session.call(
                    lambda worksheet: worksheet.get_all_values(),
                    operation_name="rilettura ORDINI_ARTICOLI dopo sincronizzazione",
                )
        return RegistrySyncSnapshot(
            summary=plan.summary(),
            registry_values=registry_values,
            reservation_values=reservation_values,
        )

    def synchronize(self) -> dict[str, Any]:
        return self.synchronize_with_snapshot().summary


def synchronize_order_registry(**kwargs) -> dict[str, Any]:
    return OrderRegistryRepository(**kwargs).synchronize()


def synchronize_order_registry_with_snapshot(
    **kwargs,
) -> RegistrySyncSnapshot:
    return OrderRegistryRepository(**kwargs).synchronize_with_snapshot()
