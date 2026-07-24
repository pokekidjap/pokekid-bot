from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from tests.fakes import (
    FakeRuntime,
    FakeSheet,
    install_dependency_stubs,
)

install_dependency_stubs()

from scripts import migrate_shipping_v2 as migration
from services import shipping_v2_schema as schema
from services.order_registry import OrderRegistryRepository

NOW = datetime(2026, 7, 23, 12, 0, tzinfo=ZoneInfo("Europe/Rome"))
SOURCE_HEADERS = [
    "DATA",
    "OGGETTO",
    "QUANTITA",
    "COSTO",
    "VENDITA",
    "TOT. VENDITA",
    "UTENTI",
    "STATO",
    "DATA SPEDIZIONE",
    "NOTE",
    "EXTRA",
]
SOURCE_ROWS = [
    [
        "01/07",
        "Carta A",
        "1",
        "10",
        "12",
        "12",
        "@alice",
        "IN MAGAZZINO",
        "",
        "",
        "x",
    ],
]
PROFILES = [["TELEGRAM_ID", "USERNAME"], ["100", "@alice"]]
SHIPPING = [
    list(schema.SHIPPING_LEGACY_HEADERS),
    ["SP-1", "ieri", "100"] + [""] * 18,
]


class FakeMigrationBackend:
    def __init__(self, runtime: FakeRuntime):
        self.runtime = runtime
        self.write_steps = []
        self.fail_first_write = False

    def inspect_spreadsheet(self, spreadsheet_id):
        return {
            "title": (
                "Gestione vendite gruppo"
                if spreadsheet_id == "SOURCE"
                else "DATABASE BOT"
            ),
            "worksheets": sorted(
                name
                for sheet_id, name in self.runtime.sheets
                if sheet_id == spreadsheet_id
            ),
        }

    def read_values(self, spreadsheet_id, worksheet_name):
        return self.runtime.sheets[
            (spreadsheet_id, worksheet_name)
        ].get_all_values()

    def read_optional(self, spreadsheet_id, worksheet_name):
        sheet = self.runtime.sheets.get(
            (spreadsheet_id, worksheet_name)
        )
        return None if sheet is None else sheet.get_all_values()

    def ensure_sheet(self, spreadsheet_id, worksheet_name, columns):
        self.write_steps.append(f"ensure:{worksheet_name}")
        if self.fail_first_write:
            raise RuntimeError("errore operativo simulato")
        if (spreadsheet_id, worksheet_name) in self.runtime.sheets:
            return False
        self.runtime.add(spreadsheet_id, worksheet_name, FakeSheet())
        return True

    def write_headers(self, spreadsheet_id, worksheet_name, headers):
        self.write_steps.append(f"headers:{worksheet_name}")
        sheet = self.runtime.sheets[(spreadsheet_id, worksheet_name)]
        plan = migration._sheet_plan(
            sheet.get_all_values(),
            headers,
            worksheet_name,
        )
        if plan["errors"]:
            raise migration.MigrationError(plan["errors"][0])
        if plan["write_headers"]:
            sheet.update(
                range_name=f"A1:{migration._a1_column(len(headers))}1",
                values=[list(headers)],
            )

    def extend_shipping(self, spreadsheet_id, worksheet_name):
        self.write_steps.append("extend:SPEDIZIONI")
        sheet = self.runtime.sheets[(spreadsheet_id, worksheet_name)]
        plan = migration._shipping_plan(sheet.get_all_values())
        if plan["errors"]:
            raise migration.MigrationError(plan["errors"][0])
        if plan["missing_headers"]:
            start_number = 22 + plan["existing_v2_headers"]
            sheet.update(
                range_name=(
                    f"{migration._a1_column(start_number)}1:"
                    f"{migration._a1_column(start_number + len(plan['missing_headers']) - 1)}1"
                ),
                values=[plan["missing_headers"]],
            )

    def synchronize_registry(self, targets):
        self.write_steps.append("sync:ORDINI_ARTICOLI")
        return OrderRegistryRepository(
            source_spreadsheet_id=targets.source_spreadsheet_id,
            source_sheet=targets.source_sheet,
            bot_db_spreadsheet_id=targets.bot_db_spreadsheet_id,
            registry_sheet=targets.registry_sheet,
            profiles_sheet=targets.profiles_sheet,
            reservations_sheet=targets.reservations_sheet,
            session_factory=self.runtime.session,
            read_operation=self.runtime.operation,
            now_factory=lambda: NOW,
        ).synchronize()


class MigrationHardeningTests(unittest.TestCase):
    def setUp(self):
        self.runtime = FakeRuntime()
        self.source = FakeSheet(
            [SOURCE_HEADERS, *SOURCE_ROWS],
            readonly=True,
        )
        self.runtime.add("SOURCE", "ORDINI", self.source)
        self.runtime.add("BOT", "PROFILI", FakeSheet(PROFILES))
        self.runtime.add("BOT", "SPEDIZIONI", FakeSheet(SHIPPING))
        self.backend = FakeMigrationBackend(self.runtime)
        self.targets = migration.MigrationTargets(
            source_spreadsheet_id="SOURCE",
            source_sheet="ORDINI",
            bot_db_spreadsheet_id="BOT",
        )

    def test_validate_only_before_migration_validates_plan(self):
        report = migration.run_migration(
            targets=self.targets,
            validate_only=True,
            backend=self.backend,
        )
        self.assertTrue(report["safe_to_apply"])
        self.assertTrue(report["piano_migrazione_valido"])
        self.assertFalse(
            report["schema_attualmente_installato"]["completo"]
        )
        self.assertTrue(report["schema_finale_previsto"]["valid"])
        self.assertEqual(report["write_state"], "NO_WRITES")
        self.assertEqual(self.source.write_calls, 0)
        self.assertEqual(self.backend.write_steps, [])

    def test_dry_run_never_writes(self):
        report = migration.run_migration(
            targets=self.targets,
            backend=self.backend,
        )
        self.assertTrue(report["safe_to_apply"])
        self.assertFalse(report["applied"])
        self.assertEqual(self.backend.write_steps, [])
        self.assertEqual(self.source.write_calls, 0)

    def test_validate_only_after_simulated_migration_validates_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            backup = Path(temporary) / "backup"
            applied = migration.run_migration(
                targets=self.targets,
                apply=True,
                confirm_production=True,
                backup_dir=backup,
                backend=self.backend,
            )
            self.assertTrue(applied["applied"])
            self.assertFalse(applied["errors"])
            validated = migration.run_migration(
                targets=self.targets,
                validate_only=True,
                backend=self.backend,
            )
        self.assertTrue(
            validated["schema_attualmente_installato"]["completo"]
        )
        self.assertTrue(validated["schema_validation"]["valid"])
        self.assertTrue(validated["safe_to_apply"])
        self.assertEqual(self.source.write_calls, 0)

    def test_operational_error_produces_reports_and_stops(self):
        self.backend.fail_first_write = True
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = migration.run_migration(
                targets=self.targets,
                apply=True,
                confirm_production=True,
                backup_dir=root / "backup",
                backend=self.backend,
            )
            json_path, text_path = migration.write_reports(
                report,
                root / "result",
            )
            self.assertTrue(json_path.exists())
            self.assertTrue(text_path.exists())
        self.assertIn("operational_error", report)
        self.assertEqual(report["write_state"], "POSSIBLY_PARTIAL")
        self.assertTrue(report["could_be_partially_applied"])
        self.assertTrue(report["backup_files"])
        self.assertEqual(
            self.backend.write_steps,
            ["ensure:ORDINI_ARTICOLI"],
        )
        self.assertFalse(report["applied"])

    def test_source_and_destination_equal_are_blocked(self):
        unsafe = migration.MigrationTargets(
            source_spreadsheet_id="BOT",
            source_sheet="ORDINI",
            bot_db_spreadsheet_id="BOT",
        )
        report = migration.run_migration(
            targets=unsafe,
            validate_only=True,
            backend=self.backend,
        )
        self.assertFalse(report["safe_to_apply"])
        self.assertEqual(self.backend.write_steps, [])

    def test_source_sheet_receives_no_write_calls(self):
        with tempfile.TemporaryDirectory() as temporary:
            migration.run_migration(
                targets=self.targets,
                apply=True,
                confirm_production=True,
                backup_dir=Path(temporary) / "backup",
                backend=self.backend,
            )
        self.assertEqual(self.source.write_calls, 0)


if __name__ == "__main__":
    unittest.main()

