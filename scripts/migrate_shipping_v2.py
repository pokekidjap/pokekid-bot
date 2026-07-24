#!/usr/bin/env python3
"""Migrazione manuale Spedizioni v2.1 nel solo DATABASE BOT.

Default: dry-run. ``--apply`` richiede anche ``--confirm-production``.
Il gestionale sorgente viene aperto esclusivamente con ``get_all_values``.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import gspread

from config import BOT_DB_SHEET_ID, SPREADSHEET_ID, WORKSHEET_NAME
from services.common import clean_value
from services.google_runtime import (
    spreadsheet_operation,
    worksheet_operation,
    worksheet_session,
)
from services.order_registry import (
    OrderRegistryRepository,
    build_sync_plan,
    parse_source_orders,
)
from services.shipping_v2_schema import (
    LIVE_RESERVATION_STATES,
    ORDER_REGISTRY_HEADERS,
    ORDER_REGISTRY_WORKSHEET_NAME,
    SHIPPING_ITEMS_HEADERS,
    SHIPPING_ITEMS_WORKSHEET_NAME,
    SHIPPING_LEGACY_HEADERS,
    SHIPPING_V2_HEADERS,
    normalized_headers,
    rows_as_dicts,
    validate_shipping_v2_values,
)

ITALY_TIMEZONE = ZoneInfo("Europe/Rome")
MODIFIABLE_BOT_DB_SHEETS = (
    ORDER_REGISTRY_WORKSHEET_NAME,
    SHIPPING_ITEMS_WORKSHEET_NAME,
    "SPEDIZIONI",
)


class MigrationError(RuntimeError):
    pass


@dataclass
class MigrationTargets:
    source_spreadsheet_id: str
    source_sheet: str
    bot_db_spreadsheet_id: str
    profiles_sheet: str = "PROFILI"
    shipping_sheet: str = "SPEDIZIONI"
    registry_sheet: str = ORDER_REGISTRY_WORKSHEET_NAME
    reservations_sheet: str = SHIPPING_ITEMS_WORKSHEET_NAME


def validate_targets(targets: MigrationTargets) -> list[str]:
    errors = []
    if not targets.source_spreadsheet_id:
        errors.append("Source spreadsheet ID mancante.")
    if not targets.bot_db_spreadsheet_id:
        errors.append("DATABASE BOT spreadsheet ID mancante.")
    if not targets.source_sheet:
        errors.append("Worksheet sorgente ORDINI mancante.")
    if (
        targets.source_spreadsheet_id
        and targets.source_spreadsheet_id
        == targets.bot_db_spreadsheet_id
    ):
        errors.append(
            "Source spreadsheet e DATABASE BOT devono essere differenti."
        )
    if targets.source_sheet.upper() in {
        name.upper() for name in MODIFIABLE_BOT_DB_SHEETS
    }:
        errors.append(
            "La worksheet sorgente non può essere inclusa tra i fogli "
            "modificabili."
        )
    if "ORDINI" in {
        targets.registry_sheet.upper(),
        targets.reservations_sheet.upper(),
        targets.shipping_sheet.upper(),
    }:
        errors.append("ORDINI non può essere un target di scrittura.")
    if len(
        {
            targets.registry_sheet.upper(),
            targets.reservations_sheet.upper(),
            targets.shipping_sheet.upper(),
        }
    ) != 3:
        errors.append("I tre fogli destinazione devono avere nomi distinti.")
    return errors


def _logical_digest(values: list[list[Any]]) -> str:
    payload = json.dumps(
        values,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _sheet_plan(
    values: list[list[Any]] | None,
    expected_headers: tuple[str, ...],
    name: str,
) -> dict[str, Any]:
    if values is None:
        return {
            "create": True,
            "write_headers": True,
            "missing_headers": list(expected_headers),
            "errors": [],
        }
    headers = normalized_headers(values)
    data_present = any(
        any(clean_value(value) for value in row)
        for row in values[1:]
    )
    if tuple(headers) == expected_headers:
        return {
            "create": False,
            "write_headers": False,
            "missing_headers": [],
            "errors": [],
        }
    if not data_present and tuple(headers) == expected_headers[: len(headers)]:
        return {
            "create": False,
            "write_headers": True,
            "missing_headers": list(expected_headers[len(headers):]),
            "errors": [],
        }
    return {
        "create": False,
        "write_headers": False,
        "missing_headers": [],
        "errors": [
            f"{name}: intestazioni o dati esistenti incompatibili."
        ],
    }


def _shipping_plan(values: list[list[Any]]) -> dict[str, Any]:
    headers = normalized_headers(values)
    errors = []
    if tuple(headers[:21]) != SHIPPING_LEGACY_HEADERS:
        errors.append(
            "SPEDIZIONI: le colonne A:U non coincidono con lo schema legacy."
        )
        return {
            "existing_v2_headers": 0,
            "missing_headers": [],
            "errors": errors,
        }
    suffix = headers[21:]
    if len(suffix) > 3 or tuple(suffix) != SHIPPING_V2_HEADERS[: len(suffix)]:
        errors.append(
            "SPEDIZIONI: intestazioni V:X esistenti incompatibili."
        )
    max_width = max((len(row) for row in values), default=0)
    if max_width > 24 and any(
        clean_value(value)
        for row in values
        for value in row[24:]
    ):
        errors.append("SPEDIZIONI contiene dati incompatibili oltre X.")
    for column_index in range(21 + len(suffix), 24):
        if any(
            clean_value(row[column_index])
            for row in values[1:]
            if column_index < len(row)
        ):
            errors.append(
                "SPEDIZIONI contiene dati in V:X senza intestazione compatibile."
            )
            break
    return {
        "existing_v2_headers": len(suffix),
        "missing_headers": list(SHIPPING_V2_HEADERS[len(suffix):]),
        "errors": errors,
    }


class GoogleMigrationBackend:
    """Tutte le operazioni passano dal runtime Google condiviso."""

    @staticmethod
    def inspect_spreadsheet(spreadsheet_id: str) -> dict[str, Any]:
        return spreadsheet_operation(
            spreadsheet_id,
            lambda spreadsheet: {
                "title": clean_value(spreadsheet.title),
                "worksheets": [
                    clean_value(worksheet.title)
                    for worksheet in spreadsheet.worksheets()
                ],
            },
            operation_name="ispezione spreadsheet per migrazione v2",
        )

    @staticmethod
    def read_values(spreadsheet_id: str, worksheet_name: str):
        return worksheet_operation(
            spreadsheet_id,
            worksheet_name,
            lambda worksheet: worksheet.get_all_values(),
            operation_name=f"lettura read-only {worksheet_name}",
        )

    @staticmethod
    def read_optional(spreadsheet_id: str, worksheet_name: str):
        try:
            return GoogleMigrationBackend.read_values(
                spreadsheet_id,
                worksheet_name,
            )
        except gspread.exceptions.WorksheetNotFound:
            return None

    @staticmethod
    def ensure_sheet(
        spreadsheet_id: str,
        worksheet_name: str,
        columns: int,
    ) -> bool:
        def ensure(spreadsheet):
            try:
                spreadsheet.worksheet(worksheet_name)
                return False
            except gspread.exceptions.WorksheetNotFound:
                spreadsheet.add_worksheet(
                    title=worksheet_name,
                    rows=1000,
                    cols=columns,
                )
                return True

        return spreadsheet_operation(
            spreadsheet_id,
            ensure,
            operation_name=f"creazione {worksheet_name}",
        )

    @staticmethod
    def write_headers(
        spreadsheet_id: str,
        worksheet_name: str,
        headers: tuple[str, ...],
    ) -> None:
        with worksheet_session(
            spreadsheet_id,
            worksheet_name,
        ) as session:
            values = session.call(
                lambda worksheet: worksheet.get_all_values(),
                operation_name=f"rilettura {worksheet_name}",
            )
            plan = _sheet_plan(values, headers, worksheet_name)
            if plan["errors"]:
                raise MigrationError(plan["errors"][0])
            if not plan["write_headers"]:
                return
            end = _a1_column(len(headers))
            session.call(
                lambda worksheet: worksheet.update(
                    range_name=f"A1:{end}1",
                    values=[list(headers)],
                    value_input_option="USER_ENTERED",
                ),
                operation_name=f"scrittura intestazioni {worksheet_name}",
            )

    @staticmethod
    def extend_shipping(
        spreadsheet_id: str,
        worksheet_name: str,
    ) -> None:
        with worksheet_session(
            spreadsheet_id,
            worksheet_name,
        ) as session:
            values = session.call(
                lambda worksheet: worksheet.get_all_values(),
                operation_name="rilettura SPEDIZIONI A:X",
            )
            plan = _shipping_plan(values)
            if plan["errors"]:
                raise MigrationError(plan["errors"][0])
            if not plan["missing_headers"]:
                return
            start_number = 22 + plan["existing_v2_headers"]
            start_column = _a1_column(start_number)
            end_column = _a1_column(
                start_number + len(plan["missing_headers"]) - 1
            )
            session.call(
                lambda worksheet: worksheet.update(
                    range_name=f"{start_column}1:{end_column}1",
                    values=[plan["missing_headers"]],
                    value_input_option="USER_ENTERED",
                ),
                operation_name="estensione intestazioni SPEDIZIONI V:X",
            )

    @staticmethod
    def synchronize_registry(targets: MigrationTargets):
        return OrderRegistryRepository(
            source_spreadsheet_id=targets.source_spreadsheet_id,
            source_sheet=targets.source_sheet,
            bot_db_spreadsheet_id=targets.bot_db_spreadsheet_id,
            registry_sheet=targets.registry_sheet,
            profiles_sheet=targets.profiles_sheet,
            reservations_sheet=targets.reservations_sheet,
        ).synchronize()


def _a1_column(number: int) -> str:
    result = ""
    value = number
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _build_dry_sync_summary(
    *,
    targets: MigrationTargets,
    source_values,
    profiles_values,
    registry_values,
    reservations_values,
) -> dict[str, Any]:
    simulated_registry = (
        registry_values
        if registry_values is not None
        else [list(ORDER_REGISTRY_HEADERS)]
    )
    active_ids = set()
    if (
        reservations_values is not None
        and tuple(normalized_headers(reservations_values))
        == SHIPPING_ITEMS_HEADERS
    ):
        active_ids = {
            record["ID_ARTICOLO"]
            for record in rows_as_dicts(
                reservations_values,
                SHIPPING_ITEMS_HEADERS,
            )
            if record["STATO_PRENOTAZIONE"] in LIVE_RESERVATION_STATES
        }
    source_rows = parse_source_orders(
        source_values,
        source_spreadsheet_id=targets.source_spreadsheet_id,
        source_sheet=targets.source_sheet,
    )
    plan = build_sync_plan(
        source_rows=source_rows,
        registry_values=simulated_registry,
        profile_values=profiles_values,
        source_spreadsheet_id=targets.source_spreadsheet_id,
        source_sheet=targets.source_sheet,
        active_reserved_item_ids=active_ids,
        now=datetime.now(ITALY_TIMEZONE).isoformat(timespec="seconds"),
    )
    return plan.summary()


def _csv_text(values: list[list[Any]]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerows(values)
    return stream.getvalue()


def write_pre_migration_backups(
    *,
    backup_dir: Path,
    bot_info: dict[str, Any],
    shipping_values: list[list[Any]],
    registry_values: list[list[Any]] | None,
    reservations_values: list[list[Any]] | None,
    pre_report: dict[str, Any],
) -> list[str]:
    backup_dir.mkdir(parents=True, exist_ok=False)
    snapshots = {
        "SPEDIZIONI_A_X": [row[:24] for row in shipping_values],
        "ORDINI_ARTICOLI": registry_values or [],
        "SPEDIZIONI_ARTICOLI": reservations_values or [],
    }
    written = []
    sheet_list = bot_info.get("worksheets", [])
    list_json = backup_dir / "worksheet_list.json"
    list_csv = backup_dir / "worksheet_list.csv"
    list_json.write_text(
        json.dumps(sheet_list, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    list_csv.write_text(
        _csv_text([["WORKSHEET"], *[[name] for name in sheet_list]]),
        encoding="utf-8-sig",
    )
    written.extend([str(list_json), str(list_csv)])
    for name, values in snapshots.items():
        json_path = backup_dir / f"{name}.json"
        csv_path = backup_dir / f"{name}.csv"
        json_path.write_text(
            json.dumps(values, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        csv_path.write_text(_csv_text(values), encoding="utf-8-sig")
        written.extend([str(json_path), str(csv_path)])
    report_json = backup_dir / "pre_migration_report.json"
    report_text = backup_dir / "pre_migration_report.txt"
    report_json.write_text(
        json.dumps(pre_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report_text.write_text(render_text_report(pre_report), encoding="utf-8")
    written.extend([str(report_json), str(report_text)])
    return written


def run_migration(
    *,
    targets: MigrationTargets,
    apply: bool = False,
    validate_only: bool = False,
    confirm_production: bool = False,
    backup_dir: Path | None = None,
    backend=None,
) -> dict[str, Any]:
    if apply and validate_only:
        raise ValueError("apply e validate-only sono incompatibili.")
    backend = backend or GoogleMigrationBackend()
    safety_errors = validate_targets(targets)
    if apply and not confirm_production:
        safety_errors.append(
            "--apply richiede anche --confirm-production."
        )
    mode = "validate-only" if validate_only else "apply" if apply else "dry-run"
    report: dict[str, Any] = {
        "phase": (
            "Spedizioni v2.1.1 — Hardening fondamenta dati e prenotazioni"
        ),
        "mode": mode,
        "generated_at": datetime.now(ITALY_TIMEZONE).isoformat(
            timespec="seconds"
        ),
        "targets": asdict(targets),
        "modifiable_sheets": list(MODIFIABLE_BOT_DB_SHEETS),
        "source_read_only": True,
        "errors": list(safety_errors),
        "applied": False,
        "backup_files": [],
        "write_state": "NO_WRITES",
        "could_be_partially_applied": False,
        "schema_attualmente_installato": {},
        "piano_migrazione_valido": False,
        "schema_finale_previsto": {},
    }
    if safety_errors:
        report["safe_to_apply"] = False
        return report

    writes_started = False
    try:
        source_info = backend.inspect_spreadsheet(
            targets.source_spreadsheet_id
        )
        bot_info = backend.inspect_spreadsheet(
            targets.bot_db_spreadsheet_id
        )
        report["source_spreadsheet"] = source_info
        report["destination_spreadsheet"] = bot_info
        source_values = backend.read_values(
            targets.source_spreadsheet_id,
            targets.source_sheet,
        )
        source_digest_before = _logical_digest(source_values)
        profiles_values = backend.read_values(
            targets.bot_db_spreadsheet_id,
            targets.profiles_sheet,
        )
        shipping_values = backend.read_values(
            targets.bot_db_spreadsheet_id,
            targets.shipping_sheet,
        )
        registry_values = backend.read_optional(
            targets.bot_db_spreadsheet_id,
            targets.registry_sheet,
        )
        reservations_values = backend.read_optional(
            targets.bot_db_spreadsheet_id,
            targets.reservations_sheet,
        )
        registry_plan = _sheet_plan(
            registry_values,
            ORDER_REGISTRY_HEADERS,
            targets.registry_sheet,
        )
        reservations_plan = _sheet_plan(
            reservations_values,
            SHIPPING_ITEMS_HEADERS,
            targets.reservations_sheet,
        )
        shipping_plan = _shipping_plan(shipping_values)
        report["schema_plan"] = {
            "registry": registry_plan,
            "reservations": reservations_plan,
            "shipping": shipping_plan,
        }
        report["errors"].extend(registry_plan["errors"])
        report["errors"].extend(reservations_plan["errors"])
        report["errors"].extend(shipping_plan["errors"])

        registry_installed = (
            registry_values is not None
            and tuple(normalized_headers(registry_values))
            == ORDER_REGISTRY_HEADERS
        )
        reservations_installed = (
            reservations_values is not None
            and tuple(normalized_headers(reservations_values))
            == SHIPPING_ITEMS_HEADERS
        )
        shipping_installed = (
            tuple(normalized_headers(shipping_values)[21:24])
            == SHIPPING_V2_HEADERS
        )
        completely_installed = (
            registry_installed
            and reservations_installed
            and shipping_installed
        )
        report["schema_attualmente_installato"] = {
            "ordini_articoli": registry_installed,
            "spedizioni_articoli": reservations_installed,
            "spedizioni_v_x": shipping_installed,
            "completo": completely_installed,
        }

        if not report["errors"]:
            report["sync_preview"] = _build_dry_sync_summary(
                targets=targets,
                source_values=source_values,
                profiles_values=profiles_values,
                registry_values=registry_values,
                reservations_values=reservations_values,
            )
        report["piano_migrazione_valido"] = not report["errors"]
        report["schema_finale_previsto"] = {
            "valid": not report["errors"],
            "ordini_articoli_headers": list(ORDER_REGISTRY_HEADERS),
            "spedizioni_articoli_headers": list(SHIPPING_ITEMS_HEADERS),
            "spedizioni_v_x_headers": list(SHIPPING_V2_HEADERS),
        }

        if validate_only:
            if completely_installed:
                actual_validation = validate_shipping_v2_values(
                    registry_values,
                    shipping_values,
                    reservations_values,
                ).as_dict()
                report["schema_validation"] = actual_validation
                report["schema_finale_previsto"] = actual_validation
                if not actual_validation["valid"]:
                    report["errors"].extend(
                        actual_validation.get("errors", [])
                    )
            else:
                report["schema_validation"] = {
                    "valid": not report["errors"],
                    "mode": "MIGRATION_PLAN",
                    "errors": list(report["errors"]),
                    "note": (
                        "Le schede v2 mancanti sono normali prima della "
                        "migrazione; è stato validato il piano previsto."
                    ),
                }
            report["piano_migrazione_valido"] = not report["errors"]
            report["safe_to_apply"] = not report["errors"]
            return report
        if not apply:
            report["safe_to_apply"] = not report["errors"]
            return report
        if report["errors"]:
            report["safe_to_apply"] = False
            return report

        if backup_dir is None:
            stamp = datetime.now(ITALY_TIMEZONE).strftime("%Y%m%d_%H%M%S")
            backup_dir = Path("shipping_v2_backups") / stamp
        report["backup_files"] = write_pre_migration_backups(
            backup_dir=backup_dir,
            bot_info=bot_info,
            shipping_values=shipping_values,
            registry_values=registry_values,
            reservations_values=reservations_values,
            pre_report=report,
        )

        # Da questo punto qualunque errore è riportato come applicazione
        # potenzialmente parziale. L'eccezione interrompe subito la sequenza.
        writes_started = True
        if registry_plan["create"]:
            backend.ensure_sheet(
                targets.bot_db_spreadsheet_id,
                targets.registry_sheet,
                len(ORDER_REGISTRY_HEADERS),
            )
        backend.write_headers(
            targets.bot_db_spreadsheet_id,
            targets.registry_sheet,
            ORDER_REGISTRY_HEADERS,
        )
        if reservations_plan["create"]:
            backend.ensure_sheet(
                targets.bot_db_spreadsheet_id,
                targets.reservations_sheet,
                len(SHIPPING_ITEMS_HEADERS),
            )
        backend.write_headers(
            targets.bot_db_spreadsheet_id,
            targets.reservations_sheet,
            SHIPPING_ITEMS_HEADERS,
        )
        backend.extend_shipping(
            targets.bot_db_spreadsheet_id,
            targets.shipping_sheet,
        )
        report["sync_result"] = backend.synchronize_registry(targets)

        final_registry = backend.read_values(
            targets.bot_db_spreadsheet_id,
            targets.registry_sheet,
        )
        final_reservations = backend.read_values(
            targets.bot_db_spreadsheet_id,
            targets.reservations_sheet,
        )
        final_shipping = backend.read_values(
            targets.bot_db_spreadsheet_id,
            targets.shipping_sheet,
        )
        final_source = backend.read_values(
            targets.source_spreadsheet_id,
            targets.source_sheet,
        )
        report["source_digest_before"] = source_digest_before
        report["source_digest_after"] = _logical_digest(final_source)
        report["source_unchanged"] = (
            report["source_digest_before"] == report["source_digest_after"]
        )
        if not report["source_unchanged"]:
            report["errors"].append(
                "Il contenuto logico della sorgente è cambiato durante "
                "la migrazione."
            )
        validation = validate_shipping_v2_values(
            final_registry,
            final_shipping,
            final_reservations,
        )
        report["schema_validation"] = validation.as_dict()
        report["schema_finale_previsto"] = validation.as_dict()
        if not validation.valid:
            report["errors"].extend(validation.errors)
        report["applied"] = True
        report["write_state"] = "COMPLETED"
        report["safe_to_apply"] = not report["errors"]
        return report
    except Exception as error:
        report["errors"].append(
            f"{type(error).__name__}: {error}"
        )
        report["operational_error"] = {
            "type": type(error).__name__,
            "message": str(error),
        }
        report["write_state"] = (
            "POSSIBLY_PARTIAL" if writes_started else "NO_WRITES"
        )
        report["could_be_partially_applied"] = writes_started
        report["safe_to_apply"] = False
        report["applied"] = False
        return report


def render_text_report(report: dict[str, Any]) -> str:
    lines = [
        report["phase"],
        f"Modalità: {report['mode']}",
        f"Generato: {report['generated_at']}",
        "Gestionale sorgente: SOLA LETTURA",
        "Fogli modificabili nel DATABASE BOT: "
        + ", ".join(report["modifiable_sheets"]),
        f"Applicato: {'sì' if report['applied'] else 'no'}",
        f"Stato scritture: {report.get('write_state', 'NO_WRITES')}",
        "Schema attualmente installato: "
        + (
            "completo"
            if report.get("schema_attualmente_installato", {}).get(
                "completo"
            )
            else "non completo"
        ),
        "Piano migrazione valido: "
        + (
            "sì"
            if report.get("piano_migrazione_valido")
            else "no"
        ),
    ]
    destination = report.get("destination_spreadsheet")
    if destination:
        lines.append(
            "DATABASE BOT destinazione: "
            f"{destination.get('title', '')} "
            f"({report['targets']['bot_db_spreadsheet_id']})"
        )
    summary = report.get("sync_result") or report.get("sync_preview")
    if summary:
        lines.extend(
            [
                "",
                "SINCRONIZZAZIONE",
                f"- righe lette: {summary.get('source_rows', 0)}",
                f"- create: {summary.get('created', 0)}",
                f"- aggiornate: {summary.get('updated', 0)}",
                f"- ambigue: {summary.get('ambiguous', 0)}",
                f"- inattive: {summary.get('inactive', 0)}",
                f"- non associate: {summary.get('unassociated', 0)}",
            ]
        )
    if report.get("backup_files"):
        lines.extend(["", "BACKUP LOCALI"])
        lines.extend(f"- {path}" for path in report["backup_files"])
    if report["errors"]:
        lines.extend(["", "ERRORI"])
        lines.extend(f"- {error}" for error in report["errors"])
    return "\n".join(lines) + "\n"


def write_reports(report: dict[str, Any], prefix: Path) -> tuple[Path, Path]:
    json_path = prefix.with_suffix(".json")
    text_path = prefix.with_suffix(".txt")
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    text_path.write_text(render_text_report(report), encoding="utf-8")
    return json_path, text_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Migrazione Shipping v2.1; il gestionale resta sempre read-only."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--validate-only", action="store_true")
    parser.add_argument(
        "--confirm-production",
        action="store_true",
        help="Conferma esplicita obbligatoria insieme a --apply.",
    )
    parser.add_argument(
        "--source-spreadsheet-id",
        default=SPREADSHEET_ID,
    )
    parser.add_argument("--source-sheet", default=WORKSHEET_NAME)
    parser.add_argument(
        "--bot-db-spreadsheet-id",
        default=BOT_DB_SHEET_ID,
    )
    parser.add_argument("--profiles-sheet", default="PROFILI")
    parser.add_argument("--shipping-sheet", default="SPEDIZIONI")
    parser.add_argument(
        "--registry-sheet",
        default=ORDER_REGISTRY_WORKSHEET_NAME,
    )
    parser.add_argument(
        "--reservations-sheet",
        default=SHIPPING_ITEMS_WORKSHEET_NAME,
    )
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument(
        "--report-prefix",
        type=Path,
        default=Path("migration_shipping_v2_report"),
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    targets = MigrationTargets(
        source_spreadsheet_id=clean_value(args.source_spreadsheet_id),
        source_sheet=clean_value(args.source_sheet),
        bot_db_spreadsheet_id=clean_value(args.bot_db_spreadsheet_id),
        profiles_sheet=clean_value(args.profiles_sheet),
        shipping_sheet=clean_value(args.shipping_sheet),
        registry_sheet=clean_value(args.registry_sheet),
        reservations_sheet=clean_value(args.reservations_sheet),
    )
    print(
        "DATABASE BOT che verrà modificato solo con conferma valida: "
        f"{targets.bot_db_spreadsheet_id or '<mancante>'}"
    )
    print(
        "Gestionale sorgente in sola lettura: "
        f"{targets.source_spreadsheet_id or '<mancante>'} / "
        f"{targets.source_sheet or '<mancante>'}"
    )
    report = run_migration(
        targets=targets,
        apply=args.apply,
        validate_only=args.validate_only,
        confirm_production=args.confirm_production,
        backup_dir=args.backup_dir,
    )
    json_path, text_path = write_reports(report, args.report_prefix)
    if report.get("operational_error"):
        print(
            "ERRORE OPERATIVO: "
            f"{report['operational_error']['type']}: "
            f"{report['operational_error']['message']}",
            file=sys.stderr,
        )
        if report.get("could_be_partially_applied"):
            print(
                "La migrazione potrebbe essere parzialmente applicata; "
                "non è stato tentato alcun rollback automatico.",
                file=sys.stderr,
            )
    print(render_text_report(report), end="")
    print(f"Report JSON: {json_path}")
    print(f"Report testo: {text_path}")
    return 0 if not report["errors"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
