from __future__ import annotations

import inspect
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

from tests.fakes import (
    FakeRuntime,
    FakeSheet,
    install_dependency_stubs,
)

install_dependency_stubs()

from modules import shipping_v2 as telegram_v2
from services import bot_version
from services import order_registry
from services import shipping_v2
from services import shipping_v2_schema as schema
from services import ui
from services.cache import invalidate
from services.reservations import ReservationsRepository
from services.shipping_v2_session import (
    AVAILABLE_ITEMS,
    PAGE,
    SELECTED_ITEM_IDS,
)

NOW = datetime(2026, 7, 24, 12, 0, tzinfo=ZoneInfo("Europe/Rome"))
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
]


def source_row(number: int, *, status: str = "IN MAGAZZINO") -> list[str]:
    return [
        f"{number:04d}",
        f"Oggetto {number}",
        "1",
        "10",
        "12",
        "12",
        "@alice",
        status,
        "",
        "",
    ]


class BotVersionMemoryTests(unittest.TestCase):
    def tearDown(self):
        bot_version.load_bot_version(lambda: {})

    def test_with_footer_never_reads_config(self):
        bot_version.load_bot_version(
            lambda: {"VERSIONE_BOT": {"value": "9.9.9"}}
        )
        with patch(
            "services.bot_db.get_config_values",
            side_effect=AssertionError("I/O CONFIG vietato"),
        ) as loader:
            self.assertIn("v9.9.9", ui.with_footer("Test"))
            self.assertIn("v9.9.9", ui.with_footer("Altro"))
        loader.assert_not_called()

    def test_get_is_memory_only_and_loader_runs_once(self):
        loader = Mock(
            return_value={"VERSIONE_BOT": {"value": "2.3.1-db"}}
        )
        self.assertEqual(
            bot_version.load_bot_version(loader),
            "2.3.1-db",
        )
        self.assertEqual(bot_version.get_bot_version(), "2.3.1-db")
        self.assertEqual(bot_version.get_bot_version(), "2.3.1-db")
        loader.assert_called_once_with()

    def test_load_uses_fallback_on_empty_value_or_error(self):
        self.assertEqual(
            bot_version.load_bot_version(lambda: {}),
            bot_version.BOT_VERSION_FALLBACK,
        )

        def failing_loader():
            raise RuntimeError("errore simulato")

        self.assertEqual(
            bot_version.load_bot_version(failing_loader),
            bot_version.BOT_VERSION_FALLBACK,
        )

    def test_config_change_is_visible_only_after_explicit_load(self):
        config = {"VERSIONE_BOT": {"value": "2.3.1-a"}}
        bot_version.load_bot_version(lambda: config)
        config["VERSIONE_BOT"]["value"] = "2.3.1-b"
        self.assertEqual(bot_version.get_bot_version(), "2.3.1-a")
        bot_version.load_bot_version(lambda: config)
        self.assertEqual(bot_version.get_bot_version(), "2.3.1-b")


class RegistryTimestampTests(unittest.TestCase):
    def make_repository(
        self,
        rows: list[list[str]],
        *,
        times: list[datetime],
    ):
        runtime = FakeRuntime()
        source = FakeSheet([SOURCE_HEADERS, *rows], readonly=True)
        registry = FakeSheet([list(schema.ORDER_REGISTRY_HEADERS)])
        runtime.add("SOURCE", "ORDINI", source)
        runtime.add(
            "BOT",
            "PROFILI",
            FakeSheet([["TELEGRAM_ID", "USERNAME"], ["100", "@alice"]]),
        )
        runtime.add("BOT", "ORDINI_ARTICOLI", registry)
        runtime.add(
            "BOT",
            "SPEDIZIONI_ARTICOLI",
            FakeSheet([list(schema.SHIPPING_ITEMS_HEADERS)]),
        )
        clock = iter(times)
        repository = order_registry.OrderRegistryRepository(
            source_spreadsheet_id="SOURCE",
            source_sheet="ORDINI",
            bot_db_spreadsheet_id="BOT",
            session_factory=runtime.session,
            read_operation=runtime.operation,
            now_factory=lambda: next(clock),
        )
        return runtime, source, registry, repository

    def test_second_sync_4239_rows_is_unchanged_and_writes_nothing(self):
        later = NOW + timedelta(minutes=1)
        runtime, source, registry, repository = self.make_repository(
            [source_row(number) for number in range(1, 4240)],
            times=[NOW, later],
        )
        first = repository.synchronize()
        last_seen_index = schema.ORDER_REGISTRY_HEADERS.index(
            "LAST_SEEN_AT"
        )
        original_last_seen = registry.values[1][last_seen_index]
        registry.write_calls = 0

        second = repository.synchronize()

        self.assertEqual(first["created"], 4239)
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["updated"], 0)
        self.assertEqual(second["unchanged"], 4239)
        self.assertEqual(
            registry.values[1][last_seen_index],
            original_last_seen,
        )
        self.assertEqual(registry.write_calls, 0)
        self.assertEqual(source.write_calls, 0)
        self.assertEqual(
            len(
                runtime.sheets[
                    ("BOT", "SPEDIZIONI_ARTICOLI")
                ].values
            ),
            1,
        )

    def test_owner_reassociation_updates_record_and_last_seen(self):
        later = NOW + timedelta(minutes=1)
        runtime, _, registry, repository = self.make_repository(
            [source_row(1)],
            times=[NOW, later],
        )
        repository.synchronize()
        runtime.sheets[("BOT", "PROFILI")].values[1][0] = "200"

        result = repository.synchronize()

        headers = registry.values[0]
        self.assertEqual(result["updated"], 1)
        self.assertEqual(
            registry.values[1][
                headers.index("TELEGRAM_ID_PROPRIETARIO")
            ],
            "200",
        )
        self.assertEqual(
            registry.values[1][headers.index("LAST_SEEN_AT")],
            later.isoformat(timespec="seconds"),
        )

    def test_source_status_change_updates_record_and_last_seen(self):
        later = NOW + timedelta(minutes=1)
        runtime, source, registry, repository = self.make_repository(
            [source_row(1)],
            times=[NOW, later],
        )
        repository.synchronize()
        source.values[1][SOURCE_HEADERS.index("STATO")] = "IN ATTESA"

        result = repository.synchronize()

        headers = registry.values[0]
        self.assertEqual(result["updated"], 1)
        self.assertEqual(
            registry.values[1][headers.index("STATO_ORIGINE")],
            "IN ATTESA",
        )
        self.assertEqual(
            registry.values[1][headers.index("LAST_SEEN_AT")],
            later.isoformat(timespec="seconds"),
        )


class OpeningCacheTests(unittest.TestCase):
    def setUp(self):
        invalidate(shipping_v2.OPENING_SNAPSHOT_CACHE_KEY)
        self.runtime = FakeRuntime()
        self.registry_values = [
            list(schema.ORDER_REGISTRY_HEADERS),
        ]
        self.reservation_values = [
            list(schema.SHIPPING_ITEMS_HEADERS),
        ]
        self.shipping_values = [
            list(
                schema.SHIPPING_LEGACY_HEADERS
                + schema.SHIPPING_V2_HEADERS
            ),
        ]
        self.runtime.add(
            "BOT",
            "SPEDIZIONI_ARTICOLI",
            FakeSheet(self.reservation_values),
        )
        self.repository = ReservationsRepository(
            bot_db_spreadsheet_id="BOT",
            session_factory=self.runtime.session,
        )

    def tearDown(self):
        invalidate(shipping_v2.OPENING_SNAPSHOT_CACHE_KEY)

    def test_normal_open_uses_cache_and_refresh_forces_real_snapshot(self):
        synchronize = Mock(
            return_value=order_registry.RegistrySyncSnapshot(
                summary={"updated": 0},
                registry_values=self.registry_values,
                reservation_values=self.reservation_values,
            )
        )
        read_shipping = Mock(return_value=self.shipping_values)
        valid = SimpleNamespace(valid=True, errors=[])
        with patch.object(
            shipping_v2,
            "synchronize_order_registry_with_snapshot",
            synchronize,
        ), patch.object(
            shipping_v2,
            "worksheet_operation",
            read_shipping,
        ), patch.object(
            shipping_v2,
            "validate_shipping_v2_values",
            return_value=valid,
        ), patch.object(
            shipping_v2,
            "ReservationsRepository",
            return_value=self.repository,
        ):
            shipping_v2.prepare_v2_opening_state("100", now=NOW)
            shipping_v2.prepare_v2_opening_state("100", now=NOW)
            self.assertEqual(synchronize.call_count, 1)
            self.assertEqual(read_shipping.call_count, 1)

            shipping_v2.prepare_v2_opening_state(
                "100",
                now=NOW,
                force_refresh=True,
            )

        self.assertEqual(synchronize.call_count, 2)
        self.assertEqual(read_shipping.call_count, 2)


class LocalTelegramPerformanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_open_and_refresh_propagate_distinct_cache_policy(self):
        open_query = SimpleNamespace(
            data="orders_available",
            from_user=SimpleNamespace(id=100),
            answer=AsyncMock(),
        )
        refresh_query = SimpleNamespace(
            data="orders_refresh",
            from_user=SimpleNamespace(id=100),
            answer=AsyncMock(),
        )
        context = SimpleNamespace(user_data={})
        with patch.object(
            telegram_v2,
            "is_shipping_v2_active",
            return_value=True,
        ), patch.object(
            telegram_v2,
            "_render_available",
            new=AsyncMock(),
        ) as render, self.assertLogs("perf", level="INFO") as logs:
            await telegram_v2.show_v2_available_orders(
                SimpleNamespace(callback_query=open_query),
                context,
            )
            await telegram_v2.show_v2_available_orders(
                SimpleNamespace(callback_query=refresh_query),
                context,
            )

        self.assertFalse(render.await_args_list[0].kwargs["force_refresh"])
        self.assertTrue(render.await_args_list[1].kwargs["force_refresh"])
        self.assertTrue(
            any(
                "flow=shipping_v2_open_available" in line
                for line in logs.output
            )
        )
        self.assertTrue(
            any(
                "flow=shipping_v2_refresh_available" in line
                for line in logs.output
            )
        )
        open_query.answer.assert_awaited_once()
        refresh_query.answer.assert_awaited_once()

    async def test_toggle_and_page_change_have_zero_sheets_calls(self):
        items = [{
            "ID_ARTICOLO": (
                "ART-00000000-0000-4000-8000-000000000001"
            ),
            "OGGETTO": "Carta",
            "QUANTITA": "1",
        }]
        context = SimpleNamespace(
            user_data={
                AVAILABLE_ITEMS: items,
                SELECTED_ITEM_IDS: set(),
                PAGE: 1,
            }
        )
        toggle_query = SimpleNamespace(
            data="order_v2_toggle:"
            "ART-00000000-0000-4000-8000-000000000001",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        page_query = SimpleNamespace(
            data="shipping_v2_page:1",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        with patch.object(
            telegram_v2,
            "is_shipping_v2_active",
            return_value=True,
        ), patch(
            "services.bot_db.get_config_values",
            side_effect=AssertionError("I/O CONFIG vietato"),
        ) as config_loader, self.assertLogs("perf", level="INFO") as logs:
            await telegram_v2.toggle_v2_available_item(
                SimpleNamespace(callback_query=toggle_query),
                context,
            )
            await telegram_v2.change_v2_items_page(
                SimpleNamespace(callback_query=page_query),
                context,
            )

        config_loader.assert_not_called()
        self.assertEqual(
            sum("sheets_calls=0" in line for line in logs.output),
            2,
        )
        toggle_query.answer.assert_awaited_once()
        page_query.answer.assert_awaited_once()


class AuthoritativeAndLegacyGuardTests(unittest.TestCase):
    def test_continue_and_finalization_keep_authoritative_revalidation(self):
        continue_source = inspect.getsource(
            telegram_v2.continue_v2_shipping
        )
        payment_source = inspect.getsource(
            telegram_v2.start_v2_shipping_payment
        )
        receipt_source = inspect.getsource(
            telegram_v2.receive_v2_shipping_receipt
        )
        self.assertIn("reserve_v2_items", continue_source)
        self.assertIn("validate_v2_draft_against_registry", payment_source)
        self.assertIn("validate_v2_draft_against_registry", receipt_source)

    def test_legacy_available_orders_branch_is_unchanged(self):
        from modules import orders

        source = inspect.getsource(orders.show_available_orders)
        self.assertIn("if is_shipping_v2_active()", source)
        self.assertIn("get_available_orders", source)
        self.assertIn('query.data == "orders_refresh"', source)
