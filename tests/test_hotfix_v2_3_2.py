from __future__ import annotations

import io
import logging
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
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

from telegram.error import BadRequest

from keyboards.orders import v2_available_orders_keyboard
from modules import shipping_v2 as telegram_v2
from services import shipping_v2
from services import shipping_v2_schema as schema
from services.logging_security import configure_http_logging_security
from services.reservations import (
    ReservationConflictError,
    ReservationsRepository,
)
from services.shipping_v2_session import (
    AVAILABLE_ITEMS,
    IDEMPOTENCY_KEY,
    SELECTED_ITEM_IDS,
)


NOW = datetime(2026, 7, 24, 12, 0, tzinfo=ZoneInfo("Europe/Rome"))
STALE_ITEM = {
    "ID_ARTICOLO": item_id(1),
    "OGGETTO": "Carta non più disponibile",
    "QUANTITA": "1",
}


def callback_values(markup) -> set[str]:
    return {
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if getattr(button, "callback_data", None)
    }


class ContinueConflictHotfixTests(unittest.IsolatedAsyncioTestCase):
    def make_flow(self, *, answer_side_effect=None, edit_side_effect=None):
        query = SimpleNamespace(
            data="shipping_v2_continue",
            from_user=SimpleNamespace(id=100, username="alice"),
            answer=AsyncMock(side_effect=answer_side_effect),
            edit_message_text=AsyncMock(side_effect=edit_side_effect),
        )
        context = SimpleNamespace(
            user_data={
                AVAILABLE_ITEMS: [dict(STALE_ITEM)],
                SELECTED_ITEM_IDS: {item_id(1)},
                IDEMPOTENCY_KEY: "stale-key",
            }
        )
        return query, context

    async def run_conflict(self, query, context, *, refreshed_items=None):
        refreshed = {
            "active_draft": None,
            "available_items": list(refreshed_items or []),
        }
        with patch.object(
            telegram_v2,
            "is_shipping_v2_active",
            return_value=True,
        ), patch.object(
            telegram_v2,
            "is_sorting_active",
            return_value=False,
        ), patch.object(
            telegram_v2,
            "get_profile",
            return_value={"NOME": "Alice"},
        ), patch.object(
            telegram_v2,
            "is_shipping_profile_complete",
            return_value=True,
        ), patch.object(
            telegram_v2,
            "get_active_shipping_methods",
            return_value=[{"NOME": "BRT"}],
        ), patch.object(
            telegram_v2,
            "reserve_v2_items",
            side_effect=ReservationConflictError("conflitto simulato"),
        ), patch.object(
            telegram_v2,
            "prepare_v2_opening_state",
            return_value=refreshed,
        ) as prepare, patch.object(
            telegram_v2,
            "_record_v2_error",
            new=AsyncMock(),
        ):
            await telegram_v2.continue_v2_shipping(
                SimpleNamespace(callback_query=query),
                context,
            )
        return prepare

    async def test_stale_cache_conflict_forces_refresh_and_clears_selection(self):
        query, context = self.make_flow()

        prepare = await self.run_conflict(query, context)

        prepare.assert_called_once_with(100, force_refresh=True)
        self.assertEqual(context.user_data[SELECTED_ITEM_IDS], set())
        self.assertEqual(context.user_data[AVAILABLE_ITEMS], [])
        self.assertNotIn(IDEMPOTENCY_KEY, context.user_data)
        query.answer.assert_awaited_once()
        query.edit_message_text.assert_awaited_once()
        edit_call = query.edit_message_text.await_args
        kwargs = edit_call.kwargs
        self.assertIn(
            "La disponibilità è cambiata. Seleziona nuovamente "
            "gli articoli disponibili.",
            edit_call.args[0],
        )
        self.assertNotIn(
            "shipping_v2_continue",
            callback_values(kwargs["reply_markup"]),
        )

    async def test_empty_selection_answers_once_and_stops(self):
        query, context = self.make_flow()
        context.user_data[SELECTED_ITEM_IDS] = set()
        with patch.object(
            telegram_v2,
            "is_shipping_v2_active",
            return_value=True,
        ), patch.object(
            telegram_v2,
            "reserve_v2_items",
        ) as reserve:
            await telegram_v2.continue_v2_shipping(
                SimpleNamespace(callback_query=query),
                context,
            )

        query.answer.assert_awaited_once_with(
            "Seleziona almeno un articolo.",
            show_alert=True,
        )
        reserve.assert_not_called()
        query.edit_message_text.assert_not_awaited()

    async def test_force_refresh_preserves_only_items_still_available(self):
        query, context = self.make_flow()
        still_available = {
            "ID_ARTICOLO": item_id(2),
            "OGGETTO": "Carta ancora disponibile",
            "QUANTITA": "1",
        }
        context.user_data[AVAILABLE_ITEMS].append(still_available)
        context.user_data[SELECTED_ITEM_IDS].add(item_id(2))

        await self.run_conflict(
            query,
            context,
            refreshed_items=[still_available],
        )

        self.assertEqual(
            context.user_data[SELECTED_ITEM_IDS],
            {item_id(2)},
        )
        markup = query.edit_message_text.await_args.kwargs["reply_markup"]
        self.assertIn("shipping_v2_continue", callback_values(markup))

    async def test_message_not_modified_after_conflict_is_safe(self):
        query, context = self.make_flow(
            edit_side_effect=BadRequest("Message is not modified"),
        )

        await self.run_conflict(query, context)

        query.answer.assert_awaited_once()
        query.edit_message_text.assert_awaited_once()
        self.assertEqual(context.user_data[SELECTED_ITEM_IDS], set())

    async def test_expired_callback_does_not_interrupt_conflict_refresh(self):
        query, context = self.make_flow(
            answer_side_effect=BadRequest(
                "Query is too old and response timeout expired or "
                "query id is invalid"
            ),
        )

        prepare = await self.run_conflict(query, context)

        query.answer.assert_awaited_once()
        prepare.assert_called_once_with(100, force_refresh=True)
        query.edit_message_text.assert_awaited_once()

    async def test_other_answer_bad_request_is_not_swallowed(self):
        query = SimpleNamespace(
            answer=AsyncMock(side_effect=BadRequest("Chat not found")),
        )

        with self.assertRaisesRegex(BadRequest, "Chat not found"):
            await telegram_v2._answer_query(query)

        query.answer.assert_awaited_once()

    async def test_other_edit_bad_request_is_not_swallowed(self):
        query = SimpleNamespace(
            edit_message_text=AsyncMock(
                side_effect=BadRequest("Message to edit not found"),
            ),
        )

        with self.assertRaisesRegex(BadRequest, "Message to edit not found"):
            await telegram_v2._edit_query(query, "Test")

    async def test_expired_callback_is_safe_on_available_orders_too(self):
        query = SimpleNamespace(
            data="orders_available",
            from_user=SimpleNamespace(id=100),
            answer=AsyncMock(
                side_effect=BadRequest(
                    "Query is too old and response timeout expired or "
                    "query id is invalid"
                )
            ),
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
        ) as render:
            await telegram_v2.show_v2_available_orders(
                SimpleNamespace(callback_query=query),
                context,
            )

        query.answer.assert_awaited_once()
        render.assert_awaited_once_with(
            query,
            context,
            force_refresh=False,
        )


class SelectionKeyboardHotfixTests(unittest.TestCase):
    def test_continue_depends_on_selection_not_on_available_items(self):
        without_selection = v2_available_orders_keyboard(
            [dict(STALE_ITEM)],
            set(),
            1,
        )
        with_selection = v2_available_orders_keyboard(
            [dict(STALE_ITEM)],
            {item_id(1)},
            1,
        )

        self.assertNotIn(
            "shipping_v2_continue",
            callback_values(without_selection),
        )
        self.assertIn(
            "shipping_v2_continue",
            callback_values(with_selection),
        )


class AvailabilityDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.runtime = FakeRuntime()
        registry_values = valid_registry_values(
            schema,
            [("100", "@alice")] * 6,
        )
        column = {
            header: index
            for index, header in enumerate(schema.ORDER_REGISTRY_HEADERS)
        }
        registry_values[2][column["IS_ACTIVE"]] = "FALSE"
        registry_values[3][column["SYNC_STATUS"]] = "AMBIGUO"
        registry_values[4][column["STATO_ORIGINE"]] = "IN ATTESA"
        registry_values[5][column["TELEGRAM_ID_PROPRIETARIO"]] = "200"
        self.registry_sheet = FakeSheet(registry_values)
        active = valid_reservation_record(
            schema,
            detail_uuid="detail-6",
            draft_uuid="draft-6",
            item=item_id(6),
            owner_id="100",
            idempotency_key="key-6",
        )
        reservation_values = [
            list(schema.SHIPPING_ITEMS_HEADERS),
            [
                active.get(header, "")
                for header in schema.SHIPPING_ITEMS_HEADERS
            ],
        ]
        self.reservation_sheet = FakeSheet(reservation_values)
        self.runtime.add(
            "BOT",
            schema.ORDER_REGISTRY_WORKSHEET_NAME,
            self.registry_sheet,
        )
        self.runtime.add(
            "BOT",
            schema.SHIPPING_ITEMS_WORKSHEET_NAME,
            self.reservation_sheet,
        )
        self.repository = ReservationsRepository(
            bot_db_spreadsheet_id="BOT",
            session_factory=self.runtime.session,
        )

    def test_every_reservation_predicate_has_a_diagnostic(self):
        diagnostics = shipping_v2.inspect_v2_item_availability(
            "100",
            [item_id(number) for number in range(1, 8)],
            reservations_repository=self.repository,
            now=datetime(
                2026,
                7,
                23,
                10,
                30,
                tzinfo=ZoneInfo("Europe/Rome"),
            ),
        )

        self.assertTrue(diagnostics[item_id(1)]["available"])
        self.assertIn("IS_ACTIVE", diagnostics[item_id(2)]["reasons"])
        self.assertIn("SYNC_STATUS", diagnostics[item_id(3)]["reasons"])
        self.assertIn("STATO_ORIGINE", diagnostics[item_id(4)]["reasons"])
        self.assertIn(
            "TELEGRAM_ID_PROPRIETARIO",
            diagnostics[item_id(5)]["reasons"],
        )
        self.assertIn(
            "PRENOTAZIONE_ATTIVA",
            diagnostics[item_id(6)]["reasons"],
        )
        self.assertIn("ID_ASSENTE", diagnostics[item_id(7)]["reasons"])
        self.assertEqual(self.registry_sheet.write_calls, 0)
        self.assertEqual(self.reservation_sheet.write_calls, 0)

    def test_reserve_conflict_logs_predicates_without_owner_id(self):
        with self.assertLogs("services.shipping_v2", level="WARNING") as logs:
            with self.assertRaises(ReservationConflictError):
                shipping_v2.reserve_v2_items(
                    holder_id="100",
                    username="@alice",
                    item_ids=[item_id(2)],
                    idempotency_key="key-conflict",
                    schema_validator=lambda: SimpleNamespace(
                        valid=True,
                        errors=[],
                    ),
                    synchronize=lambda: None,
                    reservations_repository=self.repository,
                    now=NOW,
                )

        combined = "\n".join(logs.output)
        self.assertIn("is_active=FALSE", combined)
        self.assertIn("motivi=IS_ACTIVE", combined)
        self.assertNotIn("proprietario=100", combined)


class HttpLoggingSecurityTests(unittest.TestCase):
    def test_httpx_is_warning_and_telegram_token_is_redacted(self):
        httpx_logger = logging.getLogger("httpx")
        old_level = httpx_logger.level
        old_handlers = list(httpx_logger.handlers)
        old_propagate = httpx_logger.propagate
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        try:
            httpx_logger.handlers = [handler]
            httpx_logger.propagate = False
            configure_http_logging_security()
            secret = "123456789" + ":" + "FAKE_SECRET_FOR_TEST"
            httpx_logger.warning(
                "POST https://api.telegram.org/bot%s/sendMessage",
                secret,
            )

            rendered = output.getvalue()
            self.assertGreaterEqual(httpx_logger.level, logging.WARNING)
            self.assertNotIn(secret, rendered)
            self.assertIn("bot<redacted>/sendMessage", rendered)
        finally:
            httpx_logger.handlers = old_handlers
            httpx_logger.propagate = old_propagate
            httpx_logger.setLevel(old_level)


if __name__ == "__main__":
    unittest.main()
