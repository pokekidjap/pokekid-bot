from __future__ import annotations

import asyncio
import copy
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from zoneinfo import ZoneInfo

from tests.fakes import (
    FakeRuntime,
    FakeSheet,
    install_dependency_stubs,
    item_id,
    valid_registry_values,
)

install_dependency_stubs()

import config
from modules import admin as admin_module
from modules import orders as orders_module
from modules import shipping_v2 as shipping_v2_module
from services import reservations
from services import shipping_v2
from services import shipping_v2_schema as schema
from services.shipping_engine import ShippingEngine, get_shipping_engine
from services.shipping_v2_session import (
    IDEMPOTENCY_KEY,
    SELECTED_ITEM_IDS,
    clear_shipping_v2_session,
    ensure_idempotency_key,
    item_callback_data,
    toggle_item,
)

NOW = datetime(2026, 7, 23, 12, 0, tzinfo=ZoneInfo("Europe/Rome"))
PROFILE = {
    "NOME": "Alice",
    "EMAIL": "alice@example.test",
    "TELEFONO": "0000000000",
    "INDIRIZZO": "Via Test 1",
    "CAP": "00100",
    "CITTA": "Roma",
    "PROVINCIA": "RM",
}


def schema_check(valid=True, errors=None):
    return SimpleNamespace(
        valid=valid,
        errors=list(errors or []),
    )


class ShippingV2IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.runtime = FakeRuntime()
        self.runtime.add(
            "BOT",
            schema.ORDER_REGISTRY_WORKSHEET_NAME,
            FakeSheet(
                valid_registry_values(
                    schema,
                    [("100", "@alice")] * 8
                    + [("200", "@bob")] * 4,
                )
            ),
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
            now_factory=lambda: NOW + timedelta(minutes=5),
            cache_invalidator=lambda key: None,
            log_writer=lambda **kwargs: self.logs.append(kwargs),
        )

    def reserve(
        self,
        *,
        number=1,
        holder="100",
        username="@alice",
        key="key-1",
        ttl=60,
        now=NOW,
    ):
        return self.repo.reserve_items(
            telegram_id=holder,
            username=username,
            item_ids=[item_id(number)],
            idempotency_key=key,
            ttl_minutes=ttl,
            now=now,
        )

    def finalize(self, draft, **overrides):
        kwargs = {
            "draft_uuid": draft["uuid_bozza"],
            "holder_id": "100",
            "username": "@alice",
            "payment_file_id": "receipt-1",
            "payment_type": "FOTO",
            "profile": PROFILE,
            "carrier": "BRT",
            "shipping_cost": 10.0,
            "idempotency_key": draft["idempotency_key"],
        }
        kwargs.update(overrides)
        return self.coordinator.create_or_get(**kwargs)

    def shipping_records(self):
        return shipping_v2._shipping_records(
            self.runtime.sheets[("BOT", "SPEDIZIONI")].get_all_values()
        )

    def reservation_records(self):
        return schema.rows_as_dicts(
            self.runtime.sheets[
                ("BOT", schema.SHIPPING_ITEMS_WORKSHEET_NAME)
            ].get_all_values(),
            schema.SHIPPING_ITEMS_HEADERS,
        )

    def set_reservation_fields(self, draft_uuid, **fields):
        sheet = self.runtime.sheets[
            ("BOT", schema.SHIPPING_ITEMS_WORKSHEET_NAME)
        ]
        header = sheet.values[0]
        for row in sheet.values[1:]:
            if row[header.index("UUID_BOZZA")] != draft_uuid:
                continue
            for field, value in fields.items():
                row[header.index(field)] = value

    def set_shipping_fields(self, shipping_id, **fields):
        sheet = self.runtime.sheets[("BOT", "SPEDIZIONI")]
        header = sheet.values[0]
        for row in sheet.values[1:]:
            if row[header.index("ID")] != shipping_id:
                continue
            for field, value in fields.items():
                row[header.index(field)] = value

    def test_feature_flags_select_engine(self):
        combinations = (
            (False, False, ShippingEngine.LEGACY),
            (True, False, ShippingEngine.LEGACY),
            (False, True, ShippingEngine.LEGACY),
            (True, True, ShippingEngine.V2),
        )
        for enabled, acknowledged, expected in combinations:
            with self.subTest(enabled=enabled, acknowledged=acknowledged):
                with patch.object(config, "SHIPPING_V2_ENABLED", enabled), patch.object(
                    config,
                    "SHIPPING_V2_SINGLE_INSTANCE_ACK",
                    acknowledged,
                ):
                    self.assertEqual(get_shipping_engine(), expected)

    def test_invalid_schema_stops_before_sync_without_fallback(self):
        calls = []
        with self.assertRaises(shipping_v2.ShippingV2SchemaError):
            shipping_v2.prepare_v2_opening_state(
                "100",
                schema_validator=lambda: schema_check(
                    False,
                    ["schema non valido"],
                ),
                synchronize=lambda: calls.append("sync"),
                reservations_repository=self.repo,
                now=NOW,
            )
        self.assertEqual(calls, [])

    def test_available_items_use_stable_ids_and_filters(self):
        sheet = self.runtime.sheets[
            ("BOT", schema.ORDER_REGISTRY_WORKSHEET_NAME)
        ]
        headers = sheet.values[0]
        sheet.values[3][headers.index("IS_ACTIVE")] = "FALSE"
        sheet.values[4][headers.index("SYNC_STATUS")] = "AMBIGUO"
        sheet.values[5][headers.index("STATO_ORIGINE")] = "IN ATTESA"
        available = shipping_v2.list_v2_available_items(
            "100",
            reservations_repository=self.repo,
            now=NOW,
        )
        ids = {record["ID_ARTICOLO"] for record in available}
        self.assertIn(item_id(1), ids)
        self.assertIn(item_id(2), ids)
        self.assertNotIn(item_id(3), ids)
        self.assertNotIn(item_id(4), ids)
        self.assertNotIn(item_id(5), ids)
        self.assertNotIn(item_id(9), ids)
        self.assertTrue(all(value.startswith("ART-") for value in ids))

    def test_occupied_item_is_excluded(self):
        self.reserve(number=1)
        available = shipping_v2.list_v2_available_items(
            "100",
            reservations_repository=self.repo,
            now=NOW,
        )
        self.assertNotIn(
            item_id(1),
            {record["ID_ARTICOLO"] for record in available},
        )

    def test_prepare_returns_existing_prebooked_draft(self):
        draft = self.reserve()
        state = shipping_v2.prepare_v2_opening_state(
            "100",
            schema_validator=lambda: schema_check(),
            synchronize=lambda: None,
            reservations_repository=self.repo,
            now=NOW,
        )
        self.assertEqual(
            state["active_draft"]["uuid_bozza"],
            draft["uuid_bozza"],
        )
        self.assertEqual(state["available_items"], [])

    def test_multiple_active_drafts_are_blocked_as_inconsistent(self):
        self.reserve()
        sheet = self.runtime.sheets[
            ("BOT", schema.SHIPPING_ITEMS_WORKSHEET_NAME)
        ]
        headers = sheet.values[0]
        duplicate = copy.deepcopy(sheet.values[1])
        duplicate[headers.index("UUID_DETTAGLIO")] = "detail-duplicate"
        duplicate[headers.index("UUID_BOZZA")] = "draft-duplicate"
        duplicate[headers.index("ID_ARTICOLO")] = item_id(2)
        duplicate[headers.index("IDEMPOTENCY_KEY")] = "key-duplicate"
        sheet.values.append(duplicate)
        with self.assertRaises(reservations.ReservationConflictError):
            self.repo.get_active_draft_for_user("100", now=NOW)

    def test_confirmed_draft_blocks_new_request(self):
        draft = self.reserve()
        self.repo.confirm_reservations(
            draft["uuid_bozza"],
            shipping_uuid="shipping-1",
            shipping_id="SP-20260723-001",
            now=NOW + timedelta(minutes=1),
        )
        state = shipping_v2.prepare_v2_opening_state(
            "100",
            schema_validator=lambda: schema_check(),
            synchronize=lambda: None,
            reservations_repository=self.repo,
            now=NOW + timedelta(minutes=2),
        )
        self.assertEqual(
            {
                item["STATO_PRENOTAZIONE"]
                for item in state["active_draft"]["items"]
            },
            {"CONFERMATO"},
        )
        with self.assertRaises(reservations.ReservationConflictError):
            self.reserve(
                number=2,
                key="second",
                now=NOW + timedelta(minutes=2),
            )

    def test_expired_draft_is_released_when_opening(self):
        draft = self.reserve(ttl=1)
        state = shipping_v2.prepare_v2_opening_state(
            "100",
            schema_validator=lambda: schema_check(),
            synchronize=lambda: None,
            reservations_repository=self.repo,
            now=NOW + timedelta(minutes=2),
        )
        self.assertIsNone(state["active_draft"])
        released = [
            record
            for record in self.reservation_records()
            if record["UUID_BOZZA"] == draft["uuid_bozza"]
        ]
        self.assertEqual(
            {record["STATO_PRENOTAZIONE"] for record in released},
            {"RILASCIATO"},
        )

    def test_reservation_created_only_by_continue_service(self):
        user_data = {
            "shipping_v2_available_items": [
                {"ID_ARTICOLO": item_id(1)}
            ],
            SELECTED_ITEM_IDS: set(),
        }
        toggle_item(user_data, item_id(1))
        self.assertEqual(self.reservation_records(), [])
        draft = shipping_v2.reserve_v2_items(
            holder_id="100",
            username="@alice",
            item_ids=[item_id(1)],
            idempotency_key="continue-key",
            schema_validator=lambda: schema_check(),
            synchronize=lambda: None,
            reservations_repository=self.repo,
            now=NOW,
        )
        self.assertTrue(draft["created"])
        self.assertEqual(
            {record["RUOLO"] for record in draft["items"]},
            {"TITOLARE"},
        )

    def test_reservation_conflict_is_all_or_nothing(self):
        self.reserve(number=1, key="occupied")
        with self.assertRaises(reservations.ReservationConflictError):
            shipping_v2.reserve_v2_items(
                holder_id="200",
                username="@bob",
                item_ids=[item_id(9), item_id(1)],
                idempotency_key="conflict",
                schema_validator=lambda: schema_check(),
                synchronize=lambda: None,
                reservations_repository=self.repo,
                now=NOW,
            )
        self.assertEqual(len(self.reservation_records()), 1)

    def test_final_row_has_exactly_a_to_x_and_v2(self):
        draft = self.reserve()
        request = self.finalize(draft)
        sheet = self.runtime.sheets[("BOT", "SPEDIZIONI")]
        self.assertEqual(len(sheet.values[1]), 24)
        self.assertEqual(
            tuple(sheet.values[0]),
            schema.SHIPPING_LEGACY_HEADERS + schema.SHIPPING_V2_HEADERS,
        )
        self.assertEqual(request["VERSIONE_SCHEMA"], "V2")
        self.assertEqual(request["STATO"], "IN_ATTESA")
        self.assertTrue(request["UUID_SPEDIZIONE"])
        self.assertEqual(request["IDEMPOTENCY_KEY"], "key-1")

    def test_a_to_u_remain_legacy_compatible(self):
        request = self.finalize(self.reserve())
        for header in schema.SHIPPING_LEGACY_HEADERS:
            self.assertIn(header, request)
        self.assertEqual(request["TELEGRAM_ID"], "100")
        self.assertEqual(request["USERNAME"], "@alice")
        self.assertEqual(request["CORRIERE"], "BRT")
        self.assertEqual(request["PAYMENT_FILE_ID"], "receipt-1")

    def test_products_are_derived_from_reservation_snapshots(self):
        draft = self.reserve()
        request = self.finalize(draft)
        self.assertEqual(
            request["PRODOTTI"],
            "Oggetto 1 ×1 [RIGA 2]",
        )

    def test_same_key_same_payload_returns_same_request(self):
        draft = self.reserve()
        first = self.finalize(draft)
        second = self.finalize(draft)
        self.assertEqual(first["ID"], second["ID"])
        self.assertEqual(len(self.shipping_records()), 1)

    def test_same_key_different_payload_is_conflict(self):
        draft = self.reserve()
        self.finalize(draft)
        with self.assertRaises(shipping_v2.ShippingV2ConflictError):
            self.finalize(draft, carrier="DHL")
        self.assertEqual(len(self.shipping_records()), 1)

    def test_same_key_different_holder_is_conflict(self):
        first = self.finalize(self.reserve())
        self.repo.mark_items_shipped(
            self.reservation_records()[0]["UUID_BOZZA"],
            now=NOW + timedelta(minutes=6),
        )
        second = self.reserve(
            number=9,
            holder="200",
            username="@bob",
            key="other-key",
            now=NOW + timedelta(minutes=7),
        )
        self.set_reservation_fields(
            second["uuid_bozza"],
            IDEMPOTENCY_KEY=first["IDEMPOTENCY_KEY"],
        )
        with self.assertRaises(shipping_v2.ShippingV2ConflictError):
            self.coordinator.create_or_get(
                draft_uuid=second["uuid_bozza"],
                holder_id="200",
                username="@bob",
                payment_file_id="receipt-1",
                payment_type="FOTO",
                profile=PROFILE,
                carrier="BRT",
                shipping_cost=10.0,
                idempotency_key=first["IDEMPOTENCY_KEY"],
            )

    def test_same_key_different_draft_is_conflict(self):
        first_draft = self.reserve()
        first = self.finalize(first_draft)
        self.repo.mark_items_shipped(
            first_draft["uuid_bozza"],
            now=NOW + timedelta(minutes=6),
        )
        second = self.reserve(
            number=2,
            key="second-key",
            now=NOW + timedelta(minutes=7),
        )
        self.set_reservation_fields(
            second["uuid_bozza"],
            IDEMPOTENCY_KEY=first["IDEMPOTENCY_KEY"],
        )
        with self.assertRaises(shipping_v2.ShippingV2ConflictError):
            self.coordinator.create_or_get(
                draft_uuid=second["uuid_bozza"],
                holder_id="100",
                username="@alice",
                payment_file_id="receipt-1",
                payment_type="FOTO",
                profile=PROFILE,
                carrier="BRT",
                shipping_cost=10.0,
                idempotency_key=first["IDEMPOTENCY_KEY"],
            )

    def test_timeout_after_shipping_append_does_not_duplicate(self):
        sheet = self.runtime.sheets[("BOT", "SPEDIZIONI")]
        sheet.timeout_after_append_once = True
        request = self.finalize(self.reserve())
        self.assertTrue(request["ID"])
        self.assertEqual(len(self.shipping_records()), 1)

    def test_main_present_prebooked_rows_are_reconciled(self):
        draft = self.reserve()
        first = self.finalize(draft)
        self.set_reservation_fields(
            draft["uuid_bozza"],
            STATO_PRENOTAZIONE="PRENOTATO",
            UUID_SPEDIZIONE="",
            ID_SPEDIZIONE="",
            PRENOTATO_FINO_AL=(NOW + timedelta(hours=1)).isoformat(),
            CONFERMATO_IL="",
        )
        second = self.finalize(draft)
        self.assertEqual(second["ID"], first["ID"])
        self.assertEqual(
            {
                record["STATO_PRENOTAZIONE"]
                for record in self.reservation_records()
            },
            {"CONFERMATO"},
        )
        self.assertEqual(len(self.shipping_records()), 1)

    def test_mixed_partial_confirmation_is_reconciled(self):
        draft = self.repo.reserve_items(
            telegram_id="100",
            username="@alice",
            item_ids=[item_id(1), item_id(2)],
            idempotency_key="mixed-key",
            now=NOW,
        )
        self.finalize(draft)
        sheet = self.runtime.sheets[
            ("BOT", schema.SHIPPING_ITEMS_WORKSHEET_NAME)
        ]
        headers = sheet.values[0]
        row = sheet.values[1]
        row[headers.index("STATO_PRENOTAZIONE")] = "PRENOTATO"
        row[headers.index("UUID_SPEDIZIONE")] = ""
        row[headers.index("ID_SPEDIZIONE")] = ""
        row[headers.index("CONFERMATO_IL")] = ""
        row[headers.index("PRENOTATO_FINO_AL")] = (
            NOW + timedelta(hours=1)
        ).isoformat()
        request = self.finalize(draft)
        self.assertTrue(request["ID"])
        self.assertEqual(
            {
                record["STATO_PRENOTAZIONE"]
                for record in self.reservation_records()
            },
            {"CONFERMATO"},
        )
        self.assertEqual(len(self.shipping_records()), 1)

    def test_double_receipt_simultaneous_creates_one_request(self):
        draft = self.reserve()

        def attempt(_):
            return self.finalize(draft)["ID"]

        with ThreadPoolExecutor(max_workers=10) as pool:
            ids = list(pool.map(attempt, range(10)))
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(len(self.shipping_records()), 1)

    def test_progressive_id_is_unique_under_concurrency(self):
        runtime = FakeRuntime()
        owners = [
            (str(100 + index), f"@user{index}")
            for index in range(6)
        ]
        runtime.add(
            "BOT",
            schema.ORDER_REGISTRY_WORKSHEET_NAME,
            FakeSheet(valid_registry_values(schema, owners)),
        )
        runtime.add(
            "BOT",
            schema.SHIPPING_ITEMS_WORKSHEET_NAME,
            FakeSheet([list(schema.SHIPPING_ITEMS_HEADERS)]),
        )
        runtime.add(
            "BOT",
            "SPEDIZIONI",
            FakeSheet([
                list(
                    schema.SHIPPING_LEGACY_HEADERS
                    + schema.SHIPPING_V2_HEADERS
                )
            ]),
        )
        repo = reservations.ReservationsRepository(
            bot_db_spreadsheet_id="BOT",
            session_factory=runtime.session,
        )
        drafts = []
        for index in range(6):
            drafts.append(
                repo.reserve_items(
                    telegram_id=str(100 + index),
                    username=f"@user{index}",
                    item_ids=[item_id(index + 1)],
                    idempotency_key=f"key-{index}",
                    now=NOW,
                )
            )
        coordinator = shipping_v2.ShippingV2Coordinator(
            bot_db_spreadsheet_id="BOT",
            session_factory=runtime.session,
            now_factory=lambda: NOW + timedelta(minutes=5),
            cache_invalidator=lambda key: None,
            log_writer=lambda **kwargs: None,
        )

        def finalize_index(index):
            draft = drafts[index]
            return coordinator.create_or_get(
                draft_uuid=draft["uuid_bozza"],
                holder_id=str(100 + index),
                username=f"@user{index}",
                payment_file_id=f"receipt-{index}",
                payment_type="FOTO",
                profile=PROFILE,
                carrier="BRT",
                shipping_cost=10.0,
                idempotency_key=draft["idempotency_key"],
            )["ID"]

        with ThreadPoolExecutor(max_workers=6) as pool:
            ids = list(pool.map(finalize_index, range(6)))
        self.assertEqual(len(set(ids)), 6)
        self.assertEqual(
            sorted(ids),
            [f"SP-20260723-{number:03d}" for number in range(1, 7)],
        )

    def test_duplicate_shipping_uuid_is_blocked(self):
        draft = self.reserve()
        request = self.finalize(draft)
        sheet = self.runtime.sheets[("BOT", "SPEDIZIONI")]
        duplicate = copy.deepcopy(sheet.values[1])
        duplicate[0] = "SP-20260723-999"
        duplicate[22] = "different-key"
        sheet.values.append(duplicate)
        with self.assertRaises(shipping_v2.ShippingV2ConflictError):
            self.finalize(draft)
        self.assertEqual(request["UUID_SPEDIZIONE"], duplicate[21])

    def test_duplicate_idempotency_key_is_blocked(self):
        draft = self.reserve()
        self.finalize(draft)
        sheet = self.runtime.sheets[("BOT", "SPEDIZIONI")]
        duplicate = copy.deepcopy(sheet.values[1])
        duplicate[0] = "SP-20260723-999"
        duplicate[21] = str(uuid4())
        sheet.values.append(duplicate)
        with self.assertRaises(shipping_v2.ShippingV2ConflictError):
            self.finalize(draft)

    def test_expired_reservation_creates_no_shipping_row(self):
        draft = self.reserve(ttl=1)
        coordinator = shipping_v2.ShippingV2Coordinator(
            bot_db_spreadsheet_id="BOT",
            session_factory=self.runtime.session,
            now_factory=lambda: NOW + timedelta(minutes=2),
            cache_invalidator=lambda key: None,
            log_writer=lambda **kwargs: None,
        )
        with self.assertRaises(shipping_v2.ShippingV2ExpiredError):
            coordinator.create_or_get(
                draft_uuid=draft["uuid_bozza"],
                holder_id="100",
                username="@alice",
                payment_file_id="receipt-1",
                payment_type="FOTO",
                profile=PROFILE,
                carrier="BRT",
                shipping_cost=10,
                idempotency_key=draft["idempotency_key"],
            )
        self.assertEqual(self.shipping_records(), [])

    def test_completion_updates_main_and_items(self):
        request = self.finalize(self.reserve())
        completed = self.coordinator.complete(
            request["ID"],
            "TRACK123",
            "@admin",
        )
        self.assertEqual(completed["STATO"], "SPEDITO")
        self.assertEqual(completed["TRACKING"], "TRACK123")
        self.assertEqual(
            {
                record["STATO_PRENOTAZIONE"]
                for record in self.reservation_records()
            },
            {"SPEDITO"},
        )

    def test_completion_same_tracking_is_idempotent(self):
        request = self.finalize(self.reserve())
        first = self.coordinator.complete(
            request["ID"],
            "TRACK123",
            "@admin",
        )
        second = self.coordinator.complete(
            request["ID"],
            "TRACK123",
            "@admin",
        )
        self.assertEqual(first["TRACKING"], second["TRACKING"])

    def test_completion_different_tracking_is_conflict(self):
        request = self.finalize(self.reserve())
        self.coordinator.complete(request["ID"], "TRACK123", "@admin")
        with self.assertRaises(
            shipping_v2.ShippingV2TrackingConflictError
        ):
            self.coordinator.complete(
                request["ID"],
                "DIFFERENT",
                "@admin",
            )

    def test_completion_repairs_main_shipped_items_confirmed(self):
        request = self.finalize(self.reserve())
        self.coordinator.complete(request["ID"], "TRACK123", "@admin")
        self.set_reservation_fields(
            self.reservation_records()[0]["UUID_BOZZA"],
            STATO_PRENOTAZIONE="CONFERMATO",
            SPEDITO_IL="",
        )
        self.coordinator.complete(request["ID"], "TRACK123", "@admin")
        self.assertEqual(
            {
                record["STATO_PRENOTAZIONE"]
                for record in self.reservation_records()
            },
            {"SPEDITO"},
        )

    def test_completion_repairs_items_shipped_main_pending(self):
        request = self.finalize(self.reserve())
        self.coordinator.complete(request["ID"], "TRACK123", "@admin")
        self.set_shipping_fields(
            request["ID"],
            STATO="IN_ATTESA",
            TRACKING="",
            DATA_SPEDIZIONE="",
        )
        completed = self.coordinator.complete(
            request["ID"],
            "TRACK123",
            "@admin",
        )
        self.assertEqual(completed["STATO"], "SPEDITO")
        self.assertEqual(completed["TRACKING"], "TRACK123")

    def test_completion_dispatch_preserves_legacy_and_routes_v2(self):
        calls = []

        def legacy(*args):
            calls.append(("legacy", args))
            return {"engine": "legacy"}

        def v2(*args):
            calls.append(("v2", args))
            return {"engine": "v2"}

        legacy_result = shipping_v2.complete_shipping_request_by_version(
            {"ID": "LEG-1", "VERSIONE_SCHEMA": ""},
            "T1",
            "admin",
            legacy_complete=legacy,
            v2_complete=v2,
        )
        v2_result = shipping_v2.complete_shipping_request_by_version(
            {"ID": "V2-1", "VERSIONE_SCHEMA": "V2"},
            "T2",
            "admin",
            legacy_complete=legacy,
            v2_complete=v2,
        )
        self.assertEqual(legacy_result["engine"], "legacy")
        self.assertEqual(v2_result["engine"], "v2")
        self.assertEqual([call[0] for call in calls], ["legacy", "v2"])


class ShippingV2SessionTests(unittest.TestCase):
    def test_stable_callback_fits_telegram_limit(self):
        callback = item_callback_data(item_id(1))
        self.assertEqual(callback, f"order_v2_toggle:{item_id(1)}")
        self.assertLessEqual(len(callback.encode("utf-8")), 64)

    def test_too_long_callback_is_rejected(self):
        with self.assertRaises(ValueError):
            item_callback_data("ART-" + "x" * 60)

    def test_toggle_uses_id_and_invalidates_local_key(self):
        user_data = {
            "shipping_v2_available_items": [
                {"ID_ARTICOLO": item_id(1)}
            ],
            SELECTED_ITEM_IDS: set(),
            IDEMPOTENCY_KEY: "old-key",
        }
        selected = toggle_item(user_data, item_id(1))
        self.assertEqual(selected, {item_id(1)})
        self.assertNotIn(IDEMPOTENCY_KEY, user_data)
        selected = toggle_item(user_data, item_id(1))
        self.assertEqual(selected, set())

    def test_idempotency_key_is_generated_once_on_confirmation(self):
        user_data = {}
        first = ensure_idempotency_key(user_data)
        second = ensure_idempotency_key(user_data)
        self.assertEqual(first, second)

    def test_clear_removes_only_v2_keys(self):
        user_data = {
            SELECTED_ITEM_IDS: {item_id(1)},
            IDEMPOTENCY_KEY: "key",
            "legacy": "keep",
        }
        clear_shipping_v2_session(user_data)
        self.assertEqual(user_data, {"legacy": "keep"})


class ShippingV2TelegramReleaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_feature_off_uses_legacy_available_orders_without_v2(self):
        user = SimpleNamespace(id=100, username="alice")
        query = SimpleNamespace(
            data="orders_available",
            from_user=user,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(user_data={})
        with patch.object(
            orders_module,
            "is_shipping_v2_active",
            return_value=False,
        ), patch.object(
            orders_module,
            "show_v2_available_orders",
            new=AsyncMock(),
        ) as v2_handler, patch.object(
            orders_module,
            "get_available_orders",
            return_value=[],
        ) as legacy_reader:
            await orders_module.show_available_orders(update, context)
        legacy_reader.assert_called_once()
        v2_handler.assert_not_awaited()
        query.answer.assert_awaited_once()

    async def test_feature_on_uses_only_v2_available_orders(self):
        user = SimpleNamespace(id=100, username="alice")
        query = SimpleNamespace(
            data="orders_available",
            from_user=user,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(user_data={})
        with patch.object(
            orders_module,
            "is_shipping_v2_active",
            return_value=True,
        ), patch.object(
            orders_module,
            "show_v2_available_orders",
            new=AsyncMock(),
        ) as v2_handler, patch.object(
            orders_module,
            "get_available_orders",
            return_value=[],
        ) as legacy_reader:
            await orders_module.show_available_orders(update, context)
        v2_handler.assert_awaited_once_with(update, context)
        legacy_reader.assert_not_called()

    async def test_release_failure_keeps_context(self):
        user = SimpleNamespace(id=100, username="alice")
        context = SimpleNamespace(
            user_data={
                "shipping_v2_draft_uuid": "draft-1",
                "shipping_v2_idempotency_key": "key-1",
            }
        )
        with patch.object(
            shipping_v2_module,
            "validate_v2_draft_for_holder",
            return_value={"uuid_bozza": "draft-1"},
        ), patch.object(
            shipping_v2_module,
            "release_draft",
            side_effect=RuntimeError("write failed"),
        ):
            with self.assertRaises(RuntimeError):
                await shipping_v2_module._release_user_draft(
                    user,
                    context,
                    reason="TEST",
                )
        self.assertEqual(
            context.user_data["shipping_v2_draft_uuid"],
            "draft-1",
        )

    async def test_release_success_clears_context(self):
        user = SimpleNamespace(id=100, username="alice")
        context = SimpleNamespace(
            user_data={
                "shipping_v2_draft_uuid": "draft-1",
                "shipping_v2_idempotency_key": "key-1",
            }
        )
        with patch.object(
            shipping_v2_module,
            "validate_v2_draft_for_holder",
            return_value={"uuid_bozza": "draft-1"},
        ), patch.object(
            shipping_v2_module,
            "release_draft",
            return_value={"uuid_bozza": "draft-1"},
        ):
            await shipping_v2_module._release_user_draft(
                user,
                context,
                reason="TEST",
            )
        self.assertEqual(context.user_data, {})

    async def test_no_admin_notification_before_finalizer_success(self):
        notifications = []
        context = SimpleNamespace()

        def failing_finalizer(**kwargs):
            raise RuntimeError("partial")

        async def notifier(*args):
            notifications.append(args)

        with self.assertRaises(RuntimeError):
            await shipping_v2_module.finalize_v2_and_notify(
                context,
                finalizer=failing_finalizer,
                notifier=notifier,
                finalizer_kwargs={},
            )
        self.assertEqual(notifications, [])

    async def test_retry_of_coherent_request_does_not_notify_twice(self):
        notifications = []
        context = SimpleNamespace()

        async def notifier(*args):
            if not notifications:
                notifications.append(args)

        await shipping_v2_module.finalize_v2_and_notify(
            context,
            finalizer=lambda **kwargs: {
                "ID": "SP-1",
                "_V2_FINALIZATION_STATUS": "CREATED_NOW",
            },
            notifier=notifier,
            finalizer_kwargs={},
        )
        await shipping_v2_module.finalize_v2_and_notify(
            context,
            finalizer=lambda **kwargs: {
                "ID": "SP-1",
                "_V2_FINALIZATION_STATUS": "ALREADY_COHERENT",
            },
            notifier=notifier,
            finalizer_kwargs={},
        )
        self.assertEqual(len(notifications), 1)

    async def test_cancel_command_releases_prebooked_draft(self):
        user = SimpleNamespace(id=100, username="alice")
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_message=message,
            effective_user=user,
        )
        context = SimpleNamespace(
            user_data={"shipping_v2_draft_uuid": "draft-1"}
        )
        with patch.object(
            shipping_v2_module,
            "is_shipping_v2_active",
            return_value=True,
        ), patch.object(
            shipping_v2_module,
            "_release_user_draft",
            new=AsyncMock(return_value=True),
        ) as release:
            result = (
                await shipping_v2_module.cancel_v2_shipping_receipt_command(
                    update,
                    context,
                    receipt_state=1,
                )
            )
        self.assertEqual(result, -1)
        release.assert_awaited_once()

    async def test_change_items_releases_before_returning_to_selection(self):
        user = SimpleNamespace(id=100, username="alice")
        query = SimpleNamespace(
            data="shipping_v2_change_items",
            from_user=user,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(
            user_data={"shipping_v2_draft_uuid": "draft-1"}
        )
        state = {"active_draft": None, "available_items": []}
        with patch.object(
            shipping_v2_module,
            "is_shipping_v2_active",
            return_value=True,
        ), patch.object(
            shipping_v2_module,
            "_release_user_draft",
            new=AsyncMock(return_value=True),
        ) as release, patch.object(
            shipping_v2_module,
            "prepare_v2_opening_state",
            return_value=state,
        ), patch.object(
            shipping_v2_module,
            "_render_available",
            new=AsyncMock(),
        ) as render:
            await shipping_v2_module.cancel_v2_shipping(update, context)
        release.assert_awaited_once()
        render.assert_awaited_once()
        query.answer.assert_awaited_once()


class ShippingV2AdminNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_notification_happens_after_coherent_completion(self):
        order = []
        user = SimpleNamespace(id=900, username="admin")
        message = SimpleNamespace(
            text="TRACK123",
            reply_text=AsyncMock(),
        )
        update = SimpleNamespace(
            effective_user=user,
            effective_message=message,
            callback_query=None,
        )
        bot = SimpleNamespace(send_message=AsyncMock())
        context = SimpleNamespace(
            user_data={"admin_tracking_shipping_id": "SP-1"},
            bot=bot,
        )

        def complete(*args, **kwargs):
            order.append("complete")
            return {
                "ID": "SP-1",
                "TELEGRAM_ID": "100",
                "USERNAME": "@alice",
                "CORRIERE": "BRT",
                "VERSIONE_SCHEMA": "V2",
            }

        async def send_message(*args, **kwargs):
            order.append("notify")

        bot.send_message.side_effect = send_message
        with patch.object(
            admin_module,
            "check_admin",
            new=AsyncMock(return_value=True),
        ), patch.object(
            admin_module,
            "get_admin",
            return_value={"USERNAME": "@admin"},
        ), patch.object(
            admin_module,
            "get_shipping_request",
            return_value={
                "ID": "SP-1",
                "VERSIONE_SCHEMA": "V2",
            },
        ), patch.object(
            admin_module,
            "complete_shipping_request_by_version",
            side_effect=complete,
        ), patch.object(
            admin_module,
            "get_config_values",
            return_value={},
        ):
            result = await admin_module.receive_tracking(update, context)
        self.assertEqual(result, -1)
        self.assertEqual(order[:2], ["complete", "notify"])

    async def test_user_is_not_notified_when_completion_fails(self):
        user = SimpleNamespace(id=900, username="admin")
        message = SimpleNamespace(
            text="TRACK123",
            reply_text=AsyncMock(),
        )
        update = SimpleNamespace(
            effective_user=user,
            effective_message=message,
            callback_query=None,
        )
        bot = SimpleNamespace(send_message=AsyncMock())
        context = SimpleNamespace(
            user_data={"admin_tracking_shipping_id": "SP-1"},
            bot=bot,
        )
        with patch.object(
            admin_module,
            "check_admin",
            new=AsyncMock(return_value=True),
        ), patch.object(
            admin_module,
            "get_admin",
            return_value={"USERNAME": "@admin"},
        ), patch.object(
            admin_module,
            "get_shipping_request",
            return_value={
                "ID": "SP-1",
                "VERSIONE_SCHEMA": "V2",
            },
        ), patch.object(
            admin_module,
            "complete_shipping_request_by_version",
            side_effect=RuntimeError("partial"),
        ):
            with self.assertLogs(admin_module.logger, level="ERROR"):
                result = await admin_module.receive_tracking(
                    update,
                    context,
                )
        self.assertEqual(result, admin_module.ADMIN_TRACKING)
        bot.send_message.assert_not_awaited()

    async def test_resume_rebuilds_empty_telegram_context(self):
        user = SimpleNamespace(id=100, username="alice")
        query = SimpleNamespace(
            data="shipping_v2_resume",
            from_user=user,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(user_data={})
        item = {
            "ID_ARTICOLO": item_id(1),
            "OGGETTO_SNAPSHOT": "Oggetto",
            "QUANTITA_SNAPSHOT": "1",
            "STATO_PRENOTAZIONE": "PRENOTATO",
            "PRENOTATO_FINO_AL": (
                NOW + timedelta(hours=1)
            ).isoformat(),
        }
        draft = {
            "uuid_bozza": "draft-1",
            "idempotency_key": "key-1",
            "items": [item],
        }
        with patch.object(
            shipping_v2_module,
            "is_shipping_v2_active",
            return_value=True,
        ), patch.object(
            shipping_v2_module,
            "prepare_v2_opening_state",
            return_value={
                "active_draft": draft,
                "available_items": [],
            },
        ), patch.object(
            shipping_v2_module,
            "validate_v2_draft_for_holder",
            return_value=draft,
        ), patch.object(
            shipping_v2_module,
            "get_profile",
            return_value=PROFILE,
        ), patch.object(
            shipping_v2_module,
            "get_active_shipping_methods",
            return_value=[{"name": "BRT", "price": 10.0}],
        ):
            await shipping_v2_module.resume_v2_shipping(update, context)
        self.assertEqual(
            context.user_data["shipping_v2_draft_uuid"],
            "draft-1",
        )
        self.assertEqual(
            context.user_data["shipping_v2_idempotency_key"],
            "key-1",
        )
        query.answer.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
