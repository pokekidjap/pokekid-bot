from __future__ import annotations

import asyncio
import copy
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from tests.fakes import (
    FakeRuntime,
    FakeSheet,
    install_dependency_stubs,
    item_id,
    valid_registry_values,
    valid_reservation_record,
)

install_dependency_stubs()

from keyboards.orders import v2_available_orders_keyboard
from modules import shipping_v2 as telegram_v2
from scripts import prepare_shipping_v2_deactivation as deactivation
from services import reservations
from services import shipping_v2
from services import shipping_v2_schema as schema
from services.shipping_v2_session import (
    AVAILABLE_ITEMS,
    PAGE,
    SELECTED_ITEM_IDS,
    current_page,
    page_callback_data,
    page_count,
    selected_item_ids,
    set_available_items,
    set_page,
)
from services.shipping_v2_text import (
    TELEGRAM_V2_TEXT_BUDGET,
    compact_item_message,
)

NOW = datetime(2026, 7, 24, 12, 0, tzinfo=ZoneInfo("Europe/Rome"))
PROFILE = {
    "NOME": "Alice",
    "EMAIL": "alice@example.test",
    "TELEFONO": "0000000000",
    "INDIRIZZO": "Via Test 1",
    "CAP": "00100",
    "CITTA": "Roma",
    "PROVINCIA": "RM",
}


def schema_check():
    return SimpleNamespace(valid=True, errors=[])


def make_items(count: int, *, long_names: bool = False) -> list[dict]:
    return [
        {
            "ID_ARTICOLO": item_id(index + 1),
            "OGGETTO": (
                f"Oggetto {index + 1} " + ("X" * 120 if long_names else "")
            ),
            "QUANTITA": str((index % 3) + 1),
        }
        for index in range(count)
    ]


class ShippingV2PaginationAndTextTests(
    unittest.IsolatedAsyncioTestCase
):
    def test_pagination_boundaries_for_0_1_8_9_50_150_items(self):
        expected_pages = {0: 1, 1: 1, 8: 1, 9: 2, 50: 7, 150: 19}
        for count, expected in expected_pages.items():
            with self.subTest(count=count):
                items = make_items(count)
                self.assertEqual(page_count(items), expected)
                keyboard = v2_available_orders_keyboard(
                    items,
                    set(),
                    expected,
                )
                item_buttons = [
                    button
                    for row in keyboard.inline_keyboard
                    for button in row
                    if (button.callback_data or "").startswith(
                        "order_v2_toggle:"
                    )
                ]
                expected_visible = (
                    0
                    if count == 0
                    else min(8, count - ((expected - 1) * 8))
                )
                self.assertEqual(len(item_buttons), expected_visible)

    def test_selection_is_preserved_between_pages(self):
        items = make_items(9)
        user_data = {}
        set_available_items(user_data, items)
        user_data[SELECTED_ITEM_IDS] = {item_id(1), item_id(9)}
        set_page(user_data, 2)
        self.assertEqual(
            selected_item_ids(user_data),
            {item_id(1), item_id(9)},
        )
        self.assertEqual(current_page(user_data), 2)

    def test_global_counts_include_all_pages(self):
        items = make_items(9)
        selected = {item_id(1), item_id(9)}
        text = telegram_v2._available_text(items, selected, 2)
        self.assertIn("Disponibili: <b>18</b>", text)
        self.assertIn("Selezionati: <b>4</b>", text)
        self.assertIn("Pagina 2 di 2", text)

    async def test_page_change_does_not_create_reservations(self):
        items = make_items(9)
        query = SimpleNamespace(
            data="shipping_v2_page:2",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            user_data={
                AVAILABLE_ITEMS: items,
                SELECTED_ITEM_IDS: {item_id(1)},
                PAGE: 1,
            }
        )
        with patch.object(
            telegram_v2,
            "is_shipping_v2_active",
            return_value=True,
        ), patch.object(
            telegram_v2,
            "reserve_v2_items",
        ) as reserve:
            await telegram_v2.change_v2_items_page(
                SimpleNamespace(callback_query=query),
                context,
            )
        reserve.assert_not_called()
        query.answer.assert_awaited_once()
        self.assertEqual(context.user_data[PAGE], 2)
        self.assertEqual(
            selected_item_ids(context.user_data),
            {item_id(1)},
        )

    def test_refresh_removes_only_selection_no_longer_available(self):
        initial = make_items(3)
        user_data = {}
        set_available_items(user_data, initial)
        user_data[SELECTED_ITEM_IDS] = {item_id(1), item_id(2)}
        refreshed = [initial[1], initial[2]]
        selected = set_available_items(
            user_data,
            refreshed,
            preserve_selection=True,
        )
        self.assertEqual(selected, {item_id(2)})

    def test_page_callbacks_fit_telegram_limit(self):
        for page in (1, 2, 19, 999999999):
            callback = page_callback_data(page)
            self.assertLessEqual(len(callback.encode("utf-8")), 64)

    def test_out_of_range_page_is_clamped_after_refresh(self):
        user_data = {}
        set_available_items(user_data, make_items(50))
        set_page(user_data, 7)
        self.assertEqual(user_data[PAGE], 7)
        set_available_items(user_data, make_items(9))
        self.assertEqual(user_data[PAGE], 2)
        set_page(user_data, -100)
        self.assertEqual(user_data[PAGE], 1)

    def test_long_text_stays_within_budget_and_has_balanced_html(self):
        text = compact_item_message(
            prefix="📦 <b>Riepilogo</b>",
            items=make_items(150, long_names=True),
            source="available",
            suffix="Fine riepilogo.",
        )
        self.assertLessEqual(len(text), TELEGRAM_V2_TEXT_BUDGET)
        self.assertEqual(text.count("<b>"), text.count("</b>"))
        self.assertIn("Totale articoli: <b>150</b>", text)
        self.assertIn("Totale unità: <b>300</b>", text)
        self.assertRegex(text, r"… e altri \d+ articoli")


class ShippingV2RevalidationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runtime = FakeRuntime()
        self.runtime.add(
            "BOT",
            schema.ORDER_REGISTRY_WORKSHEET_NAME,
            FakeSheet(valid_registry_values(schema, [("100", "@alice")])),
        )
        self.runtime.add(
            "BOT",
            schema.SHIPPING_ITEMS_WORKSHEET_NAME,
            FakeSheet([list(schema.SHIPPING_ITEMS_HEADERS)]),
        )
        self.runtime.add(
            "BOT",
            "SPEDIZIONI",
            FakeSheet([
                list(
                    schema.SHIPPING_LEGACY_HEADERS
                    + schema.SHIPPING_V2_HEADERS
                )
            ]),
        )
        self.repo = reservations.ReservationsRepository(
            bot_db_spreadsheet_id="BOT",
            session_factory=self.runtime.session,
        )
        self.draft = self.repo.reserve_items(
            telegram_id="100",
            username="@alice",
            item_ids=[item_id(1)],
            idempotency_key="key-1",
            now=NOW,
        )
        self.sync_calls = []

    def mutate_registry(self, field: str, value: str) -> None:
        sheet = self.runtime.sheets[
            ("BOT", schema.ORDER_REGISTRY_WORKSHEET_NAME)
        ]
        sheet.values[1][sheet.values[0].index(field)] = value

    def validate(self):
        return shipping_v2.validate_v2_draft_against_registry(
            self.draft["uuid_bozza"],
            "100",
            now=NOW + timedelta(minutes=1),
            schema_validator=schema_check,
            synchronize=lambda: self.sync_calls.append("sync"),
            reservations_repository=self.repo,
        )

    def test_article_modified_before_summary_is_rejected(self):
        self.mutate_registry("OGGETTO", "Oggetto modificato")
        with self.assertRaises(shipping_v2.ShippingV2DraftInvalidError):
            self.validate()
        self.assertEqual(self.sync_calls, ["sync"])

    def test_article_no_longer_in_warehouse_is_rejected(self):
        self.mutate_registry("STATO_ORIGINE", "IN ATTESA")
        with self.assertRaises(shipping_v2.ShippingV2DraftInvalidError):
            self.validate()

    def test_article_becoming_ambiguous_is_rejected(self):
        self.mutate_registry("SYNC_STATUS", "AMBIGUO")
        with self.assertRaises(shipping_v2.ShippingV2DraftInvalidError):
            self.validate()

    def test_inactive_article_is_rejected(self):
        self.mutate_registry("IS_ACTIVE", "FALSE")
        with self.assertRaises(shipping_v2.ShippingV2DraftInvalidError):
            self.validate()

    def test_changed_owner_is_rejected(self):
        self.mutate_registry("TELEGRAM_ID_PROPRIETARIO", "200")
        with self.assertRaises(shipping_v2.ShippingV2DraftInvalidError):
            self.validate()

    async def test_invalid_draft_is_released_before_paypal_summary(self):
        query = SimpleNamespace(
            data="shipping_v2_carrier:0",
            from_user=SimpleNamespace(id=100, username="alice"),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            user_data={
                "shipping_v2_draft_uuid": self.draft["uuid_bozza"],
                "shipping_v2_methods": [{"name": "BRT", "price": 10.0}],
            }
        )
        with patch.object(
            telegram_v2,
            "is_shipping_v2_active",
            return_value=True,
        ), patch.object(
            telegram_v2,
            "validate_v2_draft_against_registry",
            side_effect=shipping_v2.ShippingV2DraftInvalidError(),
        ), patch.object(
            telegram_v2,
            "release_draft",
        ) as release, patch.object(
            telegram_v2,
            "_record_v2_event",
            new=AsyncMock(),
        ):
            await telegram_v2.select_v2_shipping_carrier(
                SimpleNamespace(callback_query=query),
                context,
            )
        release.assert_called_once()
        rendered = query.edit_message_text.await_args.args[0]
        self.assertIn("Disponibilità cambiata", rendered)
        self.assertNotIn("PayPal", rendered)

    def test_confirmed_draft_is_not_released_if_registry_changes(self):
        self.repo.confirm_reservations(
            self.draft["uuid_bozza"],
            shipping_uuid="shipping-1",
            shipping_id="SP-1",
            now=NOW + timedelta(minutes=1),
        )
        self.mutate_registry("IS_ACTIVE", "FALSE")
        result = self.validate()
        self.assertEqual(result["states"], {"CONFERMATO"})
        states = {
            row["STATO_PRENOTAZIONE"]
            for row in schema.rows_as_dicts(
                self.runtime.sheets[
                    ("BOT", schema.SHIPPING_ITEMS_WORKSHEET_NAME)
                ].get_all_values(),
                schema.SHIPPING_ITEMS_HEADERS,
            )
        }
        self.assertEqual(states, {"CONFERMATO"})

    def test_invalid_draft_creates_no_shipping_row(self):
        self.mutate_registry("IS_ACTIVE", "FALSE")
        with self.assertRaises(shipping_v2.ShippingV2DraftInvalidError):
            self.validate()
        shipping_sheet = self.runtime.sheets[("BOT", "SPEDIZIONI")]
        self.assertEqual(len(shipping_sheet.values), 1)


class ShippingV2RetryTests(unittest.TestCase):
    def setUp(self):
        self.runtime = FakeRuntime()
        self.runtime.add(
            "BOT",
            schema.ORDER_REGISTRY_WORKSHEET_NAME,
            FakeSheet(valid_registry_values(schema, [("100", "@alice")])),
        )
        self.runtime.add(
            "BOT",
            schema.SHIPPING_ITEMS_WORKSHEET_NAME,
            FakeSheet([list(schema.SHIPPING_ITEMS_HEADERS)]),
        )
        self.runtime.add(
            "BOT",
            "SPEDIZIONI",
            FakeSheet([
                list(
                    schema.SHIPPING_LEGACY_HEADERS
                    + schema.SHIPPING_V2_HEADERS
                )
            ]),
        )
        self.repo = reservations.ReservationsRepository(
            bot_db_spreadsheet_id="BOT",
            session_factory=self.runtime.session,
        )
        self.logs = []
        self.coordinator = shipping_v2.ShippingV2Coordinator(
            bot_db_spreadsheet_id="BOT",
            session_factory=self.runtime.session,
            now_factory=lambda: NOW + timedelta(minutes=2),
            cache_invalidator=lambda key: None,
            log_writer=lambda **kwargs: self.logs.append(kwargs),
        )

    def reserve(self):
        return self.repo.reserve_items(
            telegram_id="100",
            username="@alice",
            item_ids=[item_id(1)],
            idempotency_key="key-1",
            now=NOW,
        )

    def finalize(self, draft, **changes):
        kwargs = {
            "draft_uuid": draft["uuid_bozza"],
            "holder_id": "100",
            "username": "@alice",
            "payment_file_id": "receipt-first",
            "payment_type": "FOTO",
            "profile": PROFILE,
            "carrier": "BRT",
            "shipping_cost": 10.0,
            "idempotency_key": draft["idempotency_key"],
        }
        kwargs.update(changes)
        return self.coordinator.create_or_get(**kwargs)

    def reset_draft_to_prebooked(self, draft_uuid: str):
        sheet = self.runtime.sheets[
            ("BOT", schema.SHIPPING_ITEMS_WORKSHEET_NAME)
        ]
        headers = sheet.values[0]
        for row in sheet.values[1:]:
            if row[headers.index("UUID_BOZZA")] != draft_uuid:
                continue
            updates = {
                "STATO_PRENOTAZIONE": "PRENOTATO",
                "UUID_SPEDIZIONE": "",
                "ID_SPEDIZIONE": "",
                "PRENOTATO_FINO_AL": (
                    NOW + timedelta(hours=1)
                ).isoformat(),
                "CONFERMATO_IL": "",
            }
            for field, value in updates.items():
                row[headers.index(field)] = value

    def shipping_rows(self):
        return self.runtime.sheets[("BOT", "SPEDIZIONI")].values[1:]

    def test_created_now_status_and_timeout_after_append(self):
        self.runtime.sheets[
            ("BOT", "SPEDIZIONI")
        ].timeout_after_append_once = True
        result = self.finalize(self.reserve())
        self.assertEqual(
            result["_V2_FINALIZATION_STATUS"],
            shipping_v2.FINALIZATION_CREATED_NOW,
        )
        self.assertEqual(len(self.shipping_rows()), 1)

    def test_retry_with_different_file_and_type_keeps_first_attachment(self):
        draft = self.reserve()
        first = self.finalize(draft)
        self.reset_draft_to_prebooked(draft["uuid_bozza"])
        retry = self.finalize(
            draft,
            payment_file_id="receipt-second",
            payment_type="DOCUMENTO",
        )
        self.assertEqual(retry["ID"], first["ID"])
        self.assertEqual(retry["PAYMENT_FILE_ID"], "receipt-first")
        self.assertIn("Tipo allegato: FOTO", retry["NOTE"])
        self.assertEqual(
            retry["_V2_FINALIZATION_STATUS"],
            shipping_v2.FINALIZATION_RECONCILED_NOW,
        )
        self.assertEqual(len(self.shipping_rows()), 1)
        self.assertTrue(any(
            log["action"] == "SHIPPING_V2_RETRY_ALLEGATO_MANTENUTO"
            for log in self.logs
        ))

    def test_already_coherent_status_accepts_attachment_only_change(self):
        draft = self.reserve()
        self.finalize(draft)
        retry = self.finalize(
            draft,
            payment_file_id="receipt-new",
            payment_type="DOCUMENTO",
        )
        self.assertEqual(
            retry["_V2_FINALIZATION_STATUS"],
            shipping_v2.FINALIZATION_ALREADY_COHERENT,
        )
        self.assertEqual(retry["PAYMENT_FILE_ID"], "receipt-first")
        self.assertEqual(len(self.shipping_rows()), 1)

    def test_changed_carrier_or_cost_remains_conflict(self):
        draft = self.reserve()
        self.finalize(draft)
        for changes in (
            {"carrier": "DHL"},
            {"shipping_cost": 11.0},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(
                    shipping_v2.ShippingV2ConflictError
                ):
                    self.finalize(draft, **changes)

    def test_products_limit_blocks_all_shipping_writes(self):
        registry = self.runtime.sheets[
            ("BOT", schema.ORDER_REGISTRY_WORKSHEET_NAME)
        ]
        registry.values[1][registry.values[0].index("OGGETTO")] = (
            "X" * (shipping_v2.PRODUCTS_MAX_LENGTH + 1)
        )
        draft = self.reserve()
        with self.assertRaises(
            shipping_v2.ShippingV2ProductsLimitError
        ):
            self.finalize(draft)
        self.assertEqual(len(self.shipping_rows()), 0)
        states = {
            row["STATO_PRENOTAZIONE"]
            for row in schema.rows_as_dicts(
                self.runtime.sheets[
                    ("BOT", schema.SHIPPING_ITEMS_WORKSHEET_NAME)
                ].get_all_values(),
                schema.SHIPPING_ITEMS_HEADERS,
            )
        }
        self.assertEqual(states, {"PRENOTATO"})


class ShippingV2NotificationRecoveryTests(
    unittest.IsolatedAsyncioTestCase
):
    def request(self):
        return {
            "ID": "SP-20260724-001",
            "USERNAME": "@alice",
            "CORRIERE": "BRT",
            "COSTO_SPEDIZIONE": "10",
            "_V2_ITEM_SNAPSHOTS": [{
                "OGGETTO_SNAPSHOT": "Oggetto",
                "QUANTITA_SNAPSHOT": "1",
            }],
        }

    async def test_log_marker_uses_stable_shipping_admin_pair(self):
        runtime = FakeRuntime()
        runtime.add(
            "BOT",
            "LOG",
            FakeSheet([
                ["DATA", "TELEGRAM_ID", "USERNAME", "AZIONE", "DETTAGLI", "ADMIN"],
                [
                    "24/07/2026",
                    "",
                    "",
                    shipping_v2.ADMIN_NOTIFICATION_ACTION,
                    "shipping_id=SP-20260724-001|admin_id=1",
                    "1",
                ],
            ]),
        )
        self.assertTrue(shipping_v2.is_v2_admin_notified(
            "SP-20260724-001",
            "1",
            bot_db_spreadsheet_id="BOT",
            session_factory=runtime.session,
        ))
        self.assertFalse(shipping_v2.is_v2_admin_notified(
            "SP-20260724-001",
            "2",
            bot_db_spreadsheet_id="BOT",
            session_factory=runtime.session,
        ))
        captured = []
        shipping_v2.record_v2_admin_notification(
            "SP-20260724-001",
            "2",
            log_writer=lambda **kwargs: captured.append(kwargs),
        )
        self.assertEqual(
            captured[0]["details"],
            "shipping_id=SP-20260724-001|admin_id=2",
        )

    async def test_first_finalization_notifies_all_admins(self):
        bot = SimpleNamespace(send_message=AsyncMock())
        context = SimpleNamespace(bot=bot)
        markers = []
        with patch.object(
            telegram_v2,
            "get_admins",
            return_value=[
                {"TELEGRAM_ID": "1"},
                {"TELEGRAM_ID": "2"},
            ],
        ), patch.object(
            telegram_v2,
            "is_v2_admin_notified",
            return_value=False,
        ), patch.object(
            telegram_v2,
            "record_v2_admin_notification",
            side_effect=lambda shipping_id, admin_id: markers.append(
                (shipping_id, admin_id)
            ),
        ):
            await telegram_v2.finalize_v2_and_notify(
                context,
                finalizer=lambda **kwargs: self.request(),
                finalizer_kwargs={},
            )
        self.assertEqual(bot.send_message.await_count, 2)
        self.assertEqual(len(markers), 2)

    async def test_partial_retry_notifies_only_missing_admin(self):
        bot = SimpleNamespace(send_message=AsyncMock())
        context = SimpleNamespace(bot=bot)
        markers = {("SP-20260724-001", "1")}

        def is_notified(shipping_id, admin_id):
            return (shipping_id, str(admin_id)) in markers

        def record(shipping_id, admin_id):
            markers.add((shipping_id, str(admin_id)))

        with patch.object(
            telegram_v2,
            "get_admins",
            return_value=[
                {"TELEGRAM_ID": "1"},
                {"TELEGRAM_ID": "2"},
            ],
        ), patch.object(
            telegram_v2,
            "is_v2_admin_notified",
            side_effect=is_notified,
        ), patch.object(
            telegram_v2,
            "record_v2_admin_notification",
            side_effect=record,
        ):
            await telegram_v2._notify_v2_admins(context, self.request())
        bot.send_message.assert_awaited_once()
        self.assertEqual(
            bot.send_message.await_args.kwargs["chat_id"],
            2,
        )

    async def test_marker_prevents_already_completed_notification(self):
        bot = SimpleNamespace(send_message=AsyncMock())
        with patch.object(
            telegram_v2,
            "get_admins",
            return_value=[{"TELEGRAM_ID": "1"}],
        ), patch.object(
            telegram_v2,
            "is_v2_admin_notified",
            return_value=True,
        ):
            await telegram_v2._notify_v2_admins(
                SimpleNamespace(bot=bot),
                self.request(),
            )
        bot.send_message.assert_not_awaited()

    async def test_one_admin_failure_does_not_block_others(self):
        sent = []

        async def send_message(*, chat_id, **kwargs):
            if chat_id == 1:
                raise RuntimeError("Telegram non disponibile")
            sent.append(chat_id)

        bot = SimpleNamespace(send_message=AsyncMock(side_effect=send_message))
        markers = []
        with patch.object(
            telegram_v2,
            "get_admins",
            return_value=[
                {"TELEGRAM_ID": "1"},
                {"TELEGRAM_ID": "2"},
            ],
        ), patch.object(
            telegram_v2,
            "is_v2_admin_notified",
            return_value=False,
        ), patch.object(
            telegram_v2,
            "record_v2_admin_notification",
            side_effect=lambda shipping_id, admin_id: markers.append(
                str(admin_id)
            ),
        ), self.assertLogs(telegram_v2.logger, level="ERROR"):
            await telegram_v2._notify_v2_admins(
                SimpleNamespace(bot=bot),
                self.request(),
            )
        self.assertEqual(sent, [2])
        self.assertEqual(markers, ["2"])

    async def test_marker_is_written_only_after_successful_send(self):
        order = []

        async def send_message(**kwargs):
            order.append("send")

        def record(*args):
            order.append("marker")

        with patch.object(
            telegram_v2,
            "get_admins",
            return_value=[{"TELEGRAM_ID": "1"}],
        ), patch.object(
            telegram_v2,
            "is_v2_admin_notified",
            return_value=False,
        ), patch.object(
            telegram_v2,
            "record_v2_admin_notification",
            side_effect=record,
        ):
            await telegram_v2._notify_v2_admins(
                SimpleNamespace(
                    bot=SimpleNamespace(
                        send_message=AsyncMock(side_effect=send_message)
                    )
                ),
                self.request(),
            )
        self.assertEqual(order, ["send", "marker"])

    async def test_noncoherent_request_produces_no_notification(self):
        notifier = AsyncMock()
        with self.assertRaises(RuntimeError):
            await telegram_v2.finalize_v2_and_notify(
                SimpleNamespace(),
                finalizer=lambda **kwargs: (_ for _ in ()).throw(
                    RuntimeError("cross-worksheet non coerente")
                ),
                notifier=notifier,
                finalizer_kwargs={},
            )
        notifier.assert_not_awaited()

    async def test_two_concurrent_retries_do_not_duplicate_notifications(self):
        state = set()
        state_lock = threading.Lock()
        bot = SimpleNamespace(send_message=AsyncMock())

        def is_notified(shipping_id, admin_id):
            with state_lock:
                return (shipping_id, str(admin_id)) in state

        def record(shipping_id, admin_id):
            with state_lock:
                state.add((shipping_id, str(admin_id)))

        with patch.object(
            telegram_v2,
            "get_admins",
            return_value=[
                {"TELEGRAM_ID": "1"},
                {"TELEGRAM_ID": "2"},
            ],
        ), patch.object(
            telegram_v2,
            "is_v2_admin_notified",
            side_effect=is_notified,
        ), patch.object(
            telegram_v2,
            "record_v2_admin_notification",
            side_effect=record,
        ):
            await asyncio.gather(
                telegram_v2._notify_v2_admins(
                    SimpleNamespace(bot=bot),
                    self.request(),
                ),
                telegram_v2._notify_v2_admins(
                    SimpleNamespace(bot=bot),
                    self.request(),
                ),
            )
        self.assertEqual(bot.send_message.await_count, 2)
        self.assertEqual(len(state), 2)


class ShippingV2DeactivationTests(unittest.TestCase):
    def runtime_with(self, records):
        runtime = FakeRuntime()
        runtime.add(
            "BOT",
            schema.SHIPPING_ITEMS_WORKSHEET_NAME,
            FakeSheet([
                list(schema.SHIPPING_ITEMS_HEADERS),
                *[
                    [
                        record.get(header, "")
                        for header in schema.SHIPPING_ITEMS_HEADERS
                    ]
                    for record in records
                ],
            ]),
        )
        return runtime

    def prebooked(self, *, draft, detail, expiry):
        record = valid_reservation_record(
            schema,
            draft_uuid=draft,
            detail_uuid=detail,
            item=item_id(int(detail.rsplit("-", 1)[-1])),
            state="PRENOTATO",
        )
        record["PRENOTATO_FINO_AL"] = expiry.isoformat()
        return record

    def test_no_active_draft_is_safe_to_disable(self):
        runtime = self.runtime_with([])
        report = deactivation.inspect_shipping_v2_deactivation(
            bot_db_spreadsheet_id="BOT",
            session_factory=runtime.session,
            now=NOW,
        )
        self.assertTrue(report["safe_to_disable"])

    def test_active_prebooked_draft_is_not_safe(self):
        runtime = self.runtime_with([
            self.prebooked(
                draft="draft-active",
                detail="detail-1",
                expiry=NOW + timedelta(hours=1),
            )
        ])
        report = deactivation.inspect_shipping_v2_deactivation(
            bot_db_spreadsheet_id="BOT",
            session_factory=runtime.session,
            now=NOW,
        )
        self.assertFalse(report["safe_to_disable"])
        self.assertEqual(report["counts"]["prebooked_active"], 1)

    def test_expired_prebooked_is_reported(self):
        runtime = self.runtime_with([
            self.prebooked(
                draft="draft-expired",
                detail="detail-1",
                expiry=NOW - timedelta(minutes=1),
            )
        ])
        report = deactivation.inspect_shipping_v2_deactivation(
            bot_db_spreadsheet_id="BOT",
            session_factory=runtime.session,
            now=NOW,
        )
        self.assertTrue(report["safe_to_disable"])
        self.assertEqual(report["counts"]["prebooked_expired"], 1)

    def test_explicit_release_touches_only_prebooked(self):
        active = self.prebooked(
            draft="draft-active",
            detail="detail-1",
            expiry=NOW + timedelta(hours=1),
        )
        expired = self.prebooked(
            draft="draft-expired",
            detail="detail-2",
            expiry=NOW - timedelta(minutes=1),
        )
        confirmed = valid_reservation_record(
            schema,
            detail_uuid="detail-3",
            draft_uuid="draft-confirmed",
            item=item_id(3),
            state="CONFERMATO",
        )
        shipped = valid_reservation_record(
            schema,
            detail_uuid="detail-4",
            draft_uuid="draft-shipped",
            item=item_id(4),
            state="SPEDITO",
        )
        runtime = self.runtime_with([
            active,
            expired,
            confirmed,
            shipped,
        ])
        report = deactivation.prepare_shipping_v2_deactivation(
            release_prebooked=True,
            confirm_production=True,
            bot_db_spreadsheet_id="BOT",
            session_factory=runtime.session,
            now=NOW,
        )
        self.assertTrue(report["safe_to_disable"])
        rows = schema.rows_as_dicts(
            runtime.sheets[
                ("BOT", schema.SHIPPING_ITEMS_WORKSHEET_NAME)
            ].get_all_values(),
            schema.SHIPPING_ITEMS_HEADERS,
        )
        states = {
            row["UUID_BOZZA"]: row["STATO_PRENOTAZIONE"]
            for row in rows
        }
        self.assertEqual(states["draft-active"], "RILASCIATO")
        self.assertEqual(states["draft-expired"], "RILASCIATO")
        self.assertEqual(states["draft-confirmed"], "CONFERMATO")
        self.assertEqual(states["draft-shipped"], "SPEDITO")

    def test_release_without_confirmation_never_writes(self):
        runtime = self.runtime_with([
            self.prebooked(
                draft="draft-active",
                detail="detail-1",
                expiry=NOW + timedelta(hours=1),
            )
        ])
        sheet = runtime.sheets[
            ("BOT", schema.SHIPPING_ITEMS_WORKSHEET_NAME)
        ]
        with self.assertRaises(deactivation.DeactivationError):
            deactivation.prepare_shipping_v2_deactivation(
                release_prebooked=True,
                confirm_production=False,
                bot_db_spreadsheet_id="BOT",
                session_factory=runtime.session,
                now=NOW,
            )
        self.assertEqual(sheet.write_calls, 0)

    def test_release_reports_are_json_and_text_without_google(self):
        runtime = self.runtime_with([])
        report = deactivation.prepare_shipping_v2_deactivation(
            bot_db_spreadsheet_id="BOT",
            session_factory=runtime.session,
            now=NOW,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            json_path, text_path = deactivation.write_reports(
                report,
                Path(temp_dir),
            )
            self.assertTrue(json_path.exists())
            self.assertTrue(text_path.exists())
            self.assertIn(
                "safe_to_disable=true",
                text_path.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
