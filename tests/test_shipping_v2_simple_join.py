from __future__ import annotations

import asyncio
import copy
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from tests.fakes import (
    FakeRuntime,
    FakeSheet,
    install_dependency_stubs,
    item_id,
    valid_registry_values,
)

install_dependency_stubs()

from keyboards.admin import admin_shipping_detail_keyboard
from keyboards.orders import orders_keyboard
from modules import admin as admin_module
from modules import shipping_v2_join as join_module
from services import reservations
from services import shipping_v2
from services import shipping_v2_join as join_service
from services import shipping_v2_schema as schema
from services.shipping_v2_join_session import (
    JOIN_AVAILABLE_ITEMS,
    JOIN_IDEMPOTENCY_KEY,
    JOIN_PAGE,
    JOIN_SELECTED_ITEM_IDS,
    JOIN_SHIPPING_ID,
    JOIN_SHIPPING_UUID,
    JOIN_TARGET_ID,
    JOIN_TARGET_USERNAME,
    clear_shipping_v2_join_session,
    current_join_page,
    ensure_join_idempotency_key,
    initialize_shipping_v2_join_session,
    join_item_callback_data,
    join_page_count,
    join_selected_item_ids,
    set_join_available_items,
    set_join_page,
    toggle_join_item,
)

NOW = datetime(2026, 7, 24, 10, 0, tzinfo=ZoneInfo("Europe/Rome"))
PROFILE = {
    "NOME": "Bob",
    "EMAIL": "bob@example.test",
    "TELEFONO": "0000000000",
    "INDIRIZZO": "Via Test 1",
    "CAP": "00100",
    "CITTA": "Roma",
    "PROVINCIA": "RM",
}


def schema_check():
    return SimpleNamespace(valid=True, errors=[])


def join_key(item_ids, suffix="00000000-0000-4000-8000-000000000001"):
    user_data = {
        JOIN_SELECTED_ITEM_IDS: {
            str(value).strip().upper()
            for value in item_ids
        }
    }
    return ensure_join_idempotency_key(
        user_data,
        uuid_factory=lambda: suffix,
    )


class TimeoutAfterBatchUpdateOnce(FakeSheet):
    def __init__(self, values=None):
        super().__init__(values)
        self.timeout_after_batch_update_once = False
        self._batch_timeout_raised = False

    def batch_update(self, updates, **kwargs):
        super().batch_update(updates, **kwargs)
        if (
            self.timeout_after_batch_update_once
            and not self._batch_timeout_raised
        ):
            self._batch_timeout_raised = True
            raise TimeoutError("timeout simulato dopo batch_update")


class ShippingV2SimpleJoinServiceTests(unittest.TestCase):
    def setUp(self):
        self.runtime = FakeRuntime()
        owners = [
            ("200", "@bob"),
            ("100", "@alice"),
            ("100", "@alice"),
            ("100", "@alice"),
            ("300", "@carol"),
            ("200", "@bob"),
        ]
        self.runtime.add(
            "BOT",
            schema.ORDER_REGISTRY_WORKSHEET_NAME,
            FakeSheet(valid_registry_values(schema, owners)),
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
        draft = self.repo.reserve_items(
            telegram_id="200",
            username="@bob",
            item_ids=[item_id(1)],
            idempotency_key="holder-key",
            now=NOW,
        )
        self.shipping_coordinator = shipping_v2.ShippingV2Coordinator(
            bot_db_spreadsheet_id="BOT",
            session_factory=self.runtime.session,
            now_factory=lambda: NOW + timedelta(minutes=1),
            cache_invalidator=lambda key: None,
            log_writer=lambda **kwargs: None,
        )
        self.target = self.shipping_coordinator.create_or_get(
            draft_uuid=draft["uuid_bozza"],
            holder_id="200",
            username="@bob",
            payment_file_id="receipt-bob",
            payment_type="FOTO",
            profile=PROFILE,
            carrier="BRT",
            shipping_cost=10.0,
            idempotency_key=draft["idempotency_key"],
        )
        self.logs = []
        self.joiner = join_service.ShippingV2JoinCoordinator(
            bot_db_spreadsheet_id="BOT",
            session_factory=self.runtime.session,
            now_factory=lambda: NOW + timedelta(minutes=2),
            cache_invalidator=lambda key: None,
            log_writer=lambda **kwargs: self.logs.append(kwargs),
        )

    def registry_records(self):
        return schema.rows_as_dicts(
            self.runtime.sheets[
                ("BOT", schema.ORDER_REGISTRY_WORKSHEET_NAME)
            ].get_all_values(),
            schema.ORDER_REGISTRY_HEADERS,
        )

    def item_records(self):
        return schema.rows_as_dicts(
            self.runtime.sheets[
                ("BOT", schema.SHIPPING_ITEMS_WORKSHEET_NAME)
            ].get_all_values(),
            schema.SHIPPING_ITEMS_HEADERS,
        )

    def shipping_records(self):
        return shipping_v2._shipping_records(
            self.runtime.sheets[("BOT", "SPEDIZIONI")].get_all_values()
        )

    def set_shipping(self, **fields):
        sheet = self.runtime.sheets[("BOT", "SPEDIZIONI")]
        headers = sheet.values[0]
        for row in sheet.values[1:]:
            if row[headers.index("ID")] != self.target["ID"]:
                continue
            for field, value in fields.items():
                row[headers.index(field)] = value

    def join(
        self,
        item_ids=(None,),
        *,
        contributor="100",
        username="@alice",
        key=None,
        coordinator=None,
    ):
        selected = [
            item_id(2) if value is None else value
            for value in item_ids
        ]
        return (coordinator or self.joiner).add_contributor_items(
            contributor_id=contributor,
            contributor_username=username,
            target_id="200",
            target_username="@bob",
            shipping_id=self.target["ID"],
            shipping_uuid=self.target["UUID_SPEDIZIONE"],
            item_ids=selected,
            idempotency_key=key or join_key(selected),
        )

    def test_username_is_normalized_with_or_without_at_and_case_insensitive(self):
        seen = []
        for raw in ("Bob", "@BOB"):
            result = join_service.find_joinable_v2_shipping_by_username(
                raw,
                "100",
                profile_getter=lambda value: (
                    seen.append(value) or {"TELEGRAM_ID": "200"}
                ),
                coordinator=self.joiner,
            )
            self.assertEqual(result["TARGET_USERNAME"], "@bob")
            self.assertEqual(result["ID"], self.target["ID"])
        self.assertEqual(seen, ["@bob", "@bob"])

    def test_username_missing_invalid_id_and_self_join_are_blocked(self):
        with self.assertRaises(
            join_service.ShippingV2JoinProfileNotFoundError
        ):
            join_service.find_joinable_v2_shipping_by_username(
                "unknown",
                "100",
                profile_getter=lambda value: None,
                coordinator=self.joiner,
            )
        with self.assertRaises(
            join_service.ShippingV2JoinInvalidProfileError
        ):
            join_service.find_joinable_v2_shipping_by_username(
                "bob",
                "100",
                profile_getter=lambda value: {"TELEGRAM_ID": "abc"},
                coordinator=self.joiner,
            )
        with self.assertRaises(join_service.ShippingV2JoinSelfError):
            join_service.find_joinable_v2_shipping_by_username(
                "alice",
                "100",
                profile_getter=lambda value: {"TELEGRAM_ID": "100"},
                coordinator=self.joiner,
            )

    def test_legacy_shipped_tracking_and_no_active_target_are_not_selectable(self):
        field_values = (
            ("VERSIONE_SCHEMA", ""),
            ("STATO", "SPEDITO"),
            ("TRACKING", "TRACK-1"),
            ("STATO", "ANNULLATO"),
        )
        for field, value in field_values:
            with self.subTest(field=field, value=value):
                original = self.target[field]
                self.set_shipping(**{field: value})
                with self.assertRaises(join_service.ShippingV2JoinNotFoundError):
                    self.joiner.find_joinable_by_owner("200")
                self.set_shipping(**{field: original})

    def test_multiple_active_targets_are_blocked(self):
        shipping_sheet = self.runtime.sheets[("BOT", "SPEDIZIONI")]
        shipping_headers = shipping_sheet.values[0]
        second_request = copy.deepcopy(shipping_sheet.values[1])
        second_request[shipping_headers.index("ID")] = "SP-20260724-999"
        second_request[shipping_headers.index("UUID_SPEDIZIONE")] = "ship-2"
        second_request[shipping_headers.index("IDEMPOTENCY_KEY")] = "holder-2"
        shipping_sheet.values.append(second_request)

        items_sheet = self.runtime.sheets[
            ("BOT", schema.SHIPPING_ITEMS_WORKSHEET_NAME)
        ]
        item_headers = items_sheet.values[0]
        second_item = copy.deepcopy(items_sheet.values[1])
        second_item[item_headers.index("UUID_DETTAGLIO")] = "detail-2"
        second_item[item_headers.index("UUID_BOZZA")] = "draft-2"
        second_item[item_headers.index("UUID_SPEDIZIONE")] = "ship-2"
        second_item[item_headers.index("ID_SPEDIZIONE")] = "SP-20260724-999"
        second_item[item_headers.index("ID_ARTICOLO")] = item_id(6)
        second_item[item_headers.index("IDEMPOTENCY_KEY")] = "holder-2"
        items_sheet.values.append(second_item)
        with self.assertRaises(
            join_service.ShippingV2JoinMultipleTargetsError
        ):
            self.joiner.find_joinable_by_owner("200")

    def test_selection_contains_only_contributor_available_items(self):
        available = self.joiner.get_joinable_items(
            contributor_id="100",
            target_id="200",
            shipping_id=self.target["ID"],
            shipping_uuid=self.target["UUID_SPEDIZIONE"],
        )
        self.assertEqual(
            {record["ID_ARTICOLO"] for record in available},
            {item_id(2), item_id(3), item_id(4)},
        )
        self.assertNotIn(item_id(1), {row["ID_ARTICOLO"] for row in available})
        self.assertNotIn(item_id(5), {row["ID_ARTICOLO"] for row in available})

    def test_occupied_item_is_excluded(self):
        self.repo.reserve_items(
            telegram_id="100",
            username="@alice",
            item_ids=[item_id(2)],
            idempotency_key="alice-draft",
            now=NOW + timedelta(minutes=2),
        )
        available = self.joiner.get_joinable_items(
            contributor_id="100",
            target_id="200",
            shipping_id=self.target["ID"],
            shipping_uuid=self.target["UUID_SPEDIZIONE"],
        )
        self.assertNotIn(
            item_id(2),
            {record["ID_ARTICOLO"] for record in available},
        )

    def test_union_adds_confirmed_contributor_without_new_shipping_row(self):
        before = len(self.shipping_records())
        result = self.join()
        self.assertEqual(len(self.shipping_records()), before)
        row = result["added_items"][0]
        holder = self.item_records()[0]
        self.assertEqual(row["RUOLO"], "CONTRIBUENTE")
        self.assertEqual(row["STATO_PRENOTAZIONE"], "CONFERMATO")
        self.assertEqual(row["UUID_BOZZA"], holder["UUID_BOZZA"])
        self.assertEqual(row["UUID_SPEDIZIONE"], self.target["UUID_SPEDIZIONE"])
        self.assertEqual(row["ID_SPEDIZIONE"], self.target["ID"])
        self.assertEqual(row["TELEGRAM_ID_PROPRIETARIO"], "100")
        self.assertEqual(row["OGGETTO_SNAPSHOT"], "Oggetto 2")
        self.assertEqual(row["RIGA_ORDINE_SNAPSHOT"], "3")
        self.assertTrue(row["CONFERMATO_IL"].endswith("+02:00"))
        self.assertEqual(row["PRENOTATO_IL"], "")
        self.assertIn("Oggetto 1", result["shipping"]["PRODOTTI"])
        self.assertIn("Oggetto 2", result["shipping"]["PRODOTTI"])
        self.assertEqual(
            self.logs[-1]["action"],
            "SHIPPING_V2_CONTRIBUTOR_ADDED",
        )

    def test_union_is_all_or_nothing_for_invalid_or_foreign_items(self):
        before_items = len(self.item_records())
        before_products = self.shipping_records()[0]["PRODOTTI"]
        with self.assertRaises(join_service.ShippingV2JoinConflictError):
            self.join(
                item_ids=(item_id(2), item_id(5)),
                key=join_key((item_id(2), item_id(5))),
            )
        self.assertEqual(len(self.item_records()), before_items)
        self.assertEqual(
            self.shipping_records()[0]["PRODOTTI"],
            before_products,
        )

    def test_same_key_same_payload_is_idempotent_and_changed_payload_is_not(self):
        key = join_key((item_id(2),))
        first = self.join(key=key)
        second = self.join(key=key)
        self.assertEqual(
            first["added_items"][0]["UUID_DETTAGLIO"],
            second["added_items"][0]["UUID_DETTAGLIO"],
        )
        self.assertEqual(len(self.shipping_records()), 1)
        with self.assertRaises(
            join_service.ShippingV2JoinIdempotencyError
        ):
            self.join(
                item_ids=(item_id(2), item_id(3)),
                key=key,
            )

    def test_same_key_different_contributor_is_rejected(self):
        key = join_key((item_id(2),))
        self.join(key=key)
        with self.assertRaises(
            join_service.ShippingV2JoinConflictError
        ):
            self.join(
                item_ids=(item_id(2),),
                contributor="300",
                username="@carol",
                key=key,
            )

    def test_timeout_after_append_is_reconciled_without_duplicates(self):
        sheet = self.runtime.sheets[
            ("BOT", schema.SHIPPING_ITEMS_WORKSHEET_NAME)
        ]
        sheet.timeout_after_append_once = True
        result = self.join()
        self.assertEqual(len(result["added_items"]), 1)
        self.assertEqual(
            len([
                row
                for row in self.item_records()
                if row["ID_ARTICOLO"] == item_id(2)
            ]),
            1,
        )

    def test_timeout_after_products_update_is_reconciled(self):
        current = self.runtime.sheets[("BOT", "SPEDIZIONI")]
        replacement = TimeoutAfterBatchUpdateOnce(current.values)
        replacement.timeout_after_batch_update_once = True
        self.runtime.add("BOT", "SPEDIZIONI", replacement)
        result = self.join()
        self.assertIn("Oggetto 2", result["shipping"]["PRODOTTI"])
        self.assertEqual(len(self.shipping_records()), 1)

    def test_partial_contributor_rows_are_recovered(self):
        selected = (item_id(2), item_id(3))
        key = join_key(selected)
        self.join(item_ids=selected, key=key)
        sheet = self.runtime.sheets[
            ("BOT", schema.SHIPPING_ITEMS_WORKSHEET_NAME)
        ]
        headers = sheet.values[0]
        sheet.values = [
            row
            for row in sheet.values
            if (
                row is headers
                or row[headers.index("ID_ARTICOLO")] != item_id(3)
            )
        ]
        result = self.join(item_ids=selected, key=key)
        self.assertEqual(
            {row["ID_ARTICOLO"] for row in result["added_items"]},
            set(selected),
        )
        self.assertEqual(len(result["added_items"]), 2)

    def test_two_simultaneous_confirmations_return_one_coherent_result(self):
        key = join_key((item_id(2),))

        def attempt(_):
            return self.join(key=key)["added_items"][0]["UUID_DETTAGLIO"]

        with ThreadPoolExecutor(max_workers=8) as pool:
            detail_ids = list(pool.map(attempt, range(8)))
        self.assertEqual(len(set(detail_ids)), 1)
        self.assertEqual(
            len([
                row
                for row in self.item_records()
                if row["ID_ARTICOLO"] == item_id(2)
            ]),
            1,
        )

    def test_later_additions_by_same_and_different_contributors_are_supported(self):
        self.join()
        self.join(
            item_ids=(item_id(3),),
            key=join_key(
                (item_id(3),),
                "00000000-0000-4000-8000-000000000002",
            ),
        )
        self.join(
            item_ids=(item_id(5),),
            contributor="300",
            username="@carol",
            key=join_key(
                (item_id(5),),
                "00000000-0000-4000-8000-000000000003",
            ),
        )
        groups = join_service.get_v2_shipping_items_grouped_by_owner(
            shipping_id=self.target["ID"],
            shipping_uuid=self.target["UUID_SPEDIZIONE"],
            coordinator=self.joiner,
        )
        self.assertEqual(
            {
                (group["TELEGRAM_ID"], group["RUOLO"])
                for group in groups
            },
            {
                ("200", "TITOLARE"),
                ("100", "CONTRIBUENTE"),
                ("300", "CONTRIBUENTE"),
            },
        )
        alice_group = next(
            group for group in groups if group["TELEGRAM_ID"] == "100"
        )
        self.assertEqual(alice_group["NUMERO_ARTICOLI"], 2)

    def test_already_added_article_is_not_available_again(self):
        self.join()
        available = self.joiner.get_joinable_items(
            contributor_id="100",
            target_id="200",
            shipping_id=self.target["ID"],
            shipping_uuid=self.target["UUID_SPEDIZIONE"],
        )
        self.assertNotIn(
            item_id(2),
            {row["ID_ARTICOLO"] for row in available},
        )

    def test_products_limit_blocks_before_contributor_append(self):
        sheet = self.runtime.sheets[
            ("BOT", schema.ORDER_REGISTRY_WORKSHEET_NAME)
        ]
        sheet.values[2][
            sheet.values[0].index("OGGETTO")
        ] = "X" * 45001
        before = len(self.item_records())
        with self.assertRaises(join_service.ShippingV2JoinConflictError):
            self.join()
        self.assertEqual(len(self.item_records()), before)

    def test_duplicate_detail_uuid_generation_is_blocked(self):
        existing_uuid = self.item_records()[0]["UUID_DETTAGLIO"]
        coordinator = join_service.ShippingV2JoinCoordinator(
            bot_db_spreadsheet_id="BOT",
            session_factory=self.runtime.session,
            now_factory=lambda: NOW + timedelta(minutes=2),
            uuid_factory=lambda: existing_uuid,
            cache_invalidator=lambda key: None,
            log_writer=lambda **kwargs: None,
        )
        with self.assertRaises(join_service.ShippingV2JoinConflictError):
            self.join(coordinator=coordinator)
        self.assertEqual(len(self.item_records()), 1)

    def test_tracking_or_cancellation_race_modifies_nothing(self):
        for field, value in (("TRACKING", "TRACK"), ("STATO", "ANNULLATO")):
            with self.subTest(field=field):
                original = self.target[field]
                self.set_shipping(**{field: value})
                before = len(self.item_records())
                with self.assertRaises(
                    join_service.ShippingV2JoinConflictError
                ):
                    self.join()
                self.assertEqual(len(self.item_records()), before)
                self.set_shipping(**{field: original})

    def test_participants_are_unique_and_grouped_from_current_items(self):
        self.join()
        self.join(
            item_ids=(item_id(3),),
            key=join_key(
                (item_id(3),),
                "00000000-0000-4000-8000-000000000004",
            ),
        )
        participants = join_service.get_v2_shipping_participants(
            shipping_id=self.target["ID"],
            shipping_uuid=self.target["UUID_SPEDIZIONE"],
            coordinator=self.joiner,
        )
        self.assertEqual(
            [row["TELEGRAM_ID"] for row in participants],
            ["200", "100"],
        )

    def test_completion_returns_all_unique_tracking_participants(self):
        self.join()
        coordinator = shipping_v2.ShippingV2Coordinator(
            bot_db_spreadsheet_id="BOT",
            session_factory=self.runtime.session,
            now_factory=lambda: NOW + timedelta(minutes=3),
            cache_invalidator=lambda key: None,
            log_writer=lambda **kwargs: None,
        )
        completed = coordinator.complete(
            self.target["ID"],
            "TRACK-123",
            "@admin",
        )
        self.assertEqual(
            {
                row["TELEGRAM_ID"]
                for row in completed["_V2_PARTICIPANTS"]
            },
            {"100", "200"},
        )

    def test_admin_cancellation_releases_all_and_is_idempotent(self):
        self.join()
        result = self.joiner.cancel_by_admin(
            shipping_id=self.target["ID"],
            admin="999",
        )
        self.assertEqual(result["shipping"]["STATO"], "ANNULLATO")
        self.assertEqual(
            {row["STATO_PRENOTAZIONE"] for row in result["items"]},
            {"RILASCIATO"},
        )
        self.assertTrue(
            all(
                row["MOTIVO_RILASCIO"] == "ANNULLATA_ADMIN:999"
                for row in result["items"]
            )
        )
        second = self.joiner.cancel_by_admin(
            shipping_id=self.target["ID"],
            admin="999",
        )
        self.assertTrue(second["already_coherent"])
        self.assertEqual(len(self.shipping_records()), 1)
        self.assertEqual(
            self.logs[-1]["action"],
            "SHIPPING_V2_ANNULLATA_ADMIN",
        )

    def test_admin_cancellation_preserves_commercial_and_personal_history(self):
        self.join()
        before = self.shipping_records()[0]
        after = self.joiner.cancel_by_admin(
            shipping_id=self.target["ID"],
            admin="999",
        )["shipping"]
        for field in (
            "CORRIERE",
            "COSTO_SPEDIZIONE",
            "PAYMENT_FILE_ID",
            "NOME",
            "EMAIL",
            "TELEFONO",
            "INDIRIZZO",
            "CAP",
            "CITTA",
            "PROVINCIA",
        ):
            self.assertEqual(after[field], before[field])

    def test_admin_cancellation_repairs_both_partial_directions(self):
        self.join()
        first = self.joiner.cancel_by_admin(
            shipping_id=self.target["ID"],
            admin="999",
        )
        self.set_shipping(STATO="IN_ATTESA")
        repaired_main = self.joiner.cancel_by_admin(
            shipping_id=self.target["ID"],
            admin="999",
        )
        self.assertEqual(repaired_main["shipping"]["STATO"], "ANNULLATO")

        self.set_shipping(STATO="ANNULLATO")
        items_sheet = self.runtime.sheets[
            ("BOT", schema.SHIPPING_ITEMS_WORKSHEET_NAME)
        ]
        headers = items_sheet.values[0]
        for row in items_sheet.values[1:]:
            row[headers.index("STATO_PRENOTAZIONE")] = "CONFERMATO"
            row[headers.index("RILASCIATO_IL")] = ""
            row[headers.index("MOTIVO_RILASCIO")] = ""
        repaired_items = self.joiner.cancel_by_admin(
            shipping_id=self.target["ID"],
            admin="999",
        )
        self.assertEqual(
            {row["STATO_PRENOTAZIONE"] for row in repaired_items["items"]},
            {"RILASCIATO"},
        )
        self.assertEqual(first["shipping"]["ID"], self.target["ID"])

    def test_admin_cancellation_blocks_tracking_shipped_and_legacy(self):
        cases = (
            ("TRACKING", "TRACK"),
            ("STATO", "SPEDITO"),
            ("VERSIONE_SCHEMA", ""),
        )
        for field, value in cases:
            with self.subTest(field=field):
                original = self.target[field]
                self.set_shipping(**{field: value})
                with self.assertRaises(join_service.ShippingV2AdminCancelError):
                    self.joiner.cancel_by_admin(
                        shipping_id=self.target["ID"],
                        admin="999",
                    )
                self.set_shipping(**{field: original})

    def test_admin_cancellation_blocks_when_one_linked_item_is_shipped(self):
        self.join()
        sheet = self.runtime.sheets[
            ("BOT", schema.SHIPPING_ITEMS_WORKSHEET_NAME)
        ]
        headers = sheet.values[0]
        sheet.values[1][
            headers.index("STATO_PRENOTAZIONE")
        ] = "SPEDITO"
        sheet.values[1][headers.index("SPEDITO_IL")] = (
            NOW + timedelta(minutes=3)
        ).isoformat()
        with self.assertRaises(join_service.ShippingV2AdminCancelError):
            self.joiner.cancel_by_admin(
                shipping_id=self.target["ID"],
                admin="999",
            )


class ShippingV2JoinSessionAndKeyboardTests(unittest.TestCase):
    def test_pagination_boundaries_0_1_8_9_50(self):
        for count, expected in ((0, 1), (1, 1), (8, 1), (9, 2), (50, 7)):
            with self.subTest(count=count):
                items = [
                    {"ID_ARTICOLO": item_id(index + 1)}
                    for index in range(count)
                ]
                self.assertEqual(join_page_count(items), expected)

    def test_selection_is_preserved_and_key_invalidated_on_change(self):
        items = [
            {"ID_ARTICOLO": item_id(1)},
            {"ID_ARTICOLO": item_id(2)},
        ]
        user_data = {}
        initialize_shipping_v2_join_session(user_data)
        set_join_available_items(
            user_data,
            items,
            preserve_selection=False,
        )
        toggle_join_item(user_data, item_id(1))
        key = ensure_join_idempotency_key(user_data)
        self.assertEqual(ensure_join_idempotency_key(user_data), key)
        toggle_join_item(user_data, item_id(2))
        self.assertNotIn(JOIN_IDEMPOTENCY_KEY, user_data)
        selected = set_join_available_items(
            user_data,
            [items[1]],
            preserve_selection=True,
        )
        self.assertEqual(selected, {item_id(2)})

    def test_toggle_and_page_change_are_local_only(self):
        user_data = {
            JOIN_AVAILABLE_ITEMS: [
                {"ID_ARTICOLO": item_id(index + 1)}
                for index in range(9)
            ],
            JOIN_SELECTED_ITEM_IDS: set(),
            JOIN_PAGE: 1,
        }
        toggle_join_item(user_data, item_id(9))
        set_join_page(user_data, 2)
        self.assertEqual(join_selected_item_ids(user_data), {item_id(9)})
        self.assertEqual(current_join_page(user_data), 2)
        self.assertEqual(
            set(user_data),
            {
                JOIN_AVAILABLE_ITEMS,
                JOIN_SELECTED_ITEM_IDS,
                JOIN_PAGE,
            },
        )

    def test_callbacks_fit_telegram_limit(self):
        self.assertLessEqual(
            len(join_item_callback_data(item_id(1)).encode("utf-8")),
            64,
        )

    def test_clear_removes_only_join_keys(self):
        user_data = {
            JOIN_TARGET_ID: "200",
            JOIN_TARGET_USERNAME: "@bob",
            JOIN_SHIPPING_ID: "SP-1",
            JOIN_SHIPPING_UUID: "uuid",
            JOIN_AVAILABLE_ITEMS: [],
            JOIN_SELECTED_ITEM_IDS: set(),
            JOIN_PAGE: 1,
            JOIN_IDEMPOTENCY_KEY: "key",
            "shipping_v2_selected_item_ids": {"regular"},
        }
        clear_shipping_v2_join_session(user_data)
        self.assertEqual(
            user_data,
            {"shipping_v2_selected_item_ids": {"regular"}},
        )

    def test_join_button_exists_only_when_v2_is_active(self):
        off = [
            button.callback_data
            for row in orders_keyboard(False).inline_keyboard
            for button in row
        ]
        on = [
            button.callback_data
            for row in orders_keyboard(True).inline_keyboard
            for button in row
        ]
        self.assertNotIn("shipping_v2_join", off)
        self.assertIn("shipping_v2_join", on)

    def test_admin_cancel_button_only_when_explicitly_allowed(self):
        off = [
            button.callback_data
            for row in admin_shipping_detail_keyboard("SP-1").inline_keyboard
            for button in row
        ]
        on = [
            button.callback_data
            for row in admin_shipping_detail_keyboard(
                "SP-1",
                allow_v2_cancel=True,
            ).inline_keyboard
            for button in row
        ]
        self.assertFalse(any("admin_shipping_cancel:" in value for value in off))
        self.assertIn("admin_shipping_cancel:SP-1", on)


class ShippingV2JoinTelegramTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_command_clears_session_without_writes(self):
        message = AsyncMock()
        update = SimpleNamespace(
            callback_query=None,
            effective_message=message,
        )
        context = SimpleNamespace(user_data={
            JOIN_TARGET_ID: "200",
            JOIN_SELECTED_ITEM_IDS: {item_id(2)},
        })
        result = await join_module.cancel_shipping_v2_join(update, context)
        self.assertEqual(result, -1)
        self.assertEqual(context.user_data, {})
        message.reply_text.assert_awaited_once()

    async def test_join_notifications_reach_owner_and_admin_without_buttons(self):
        bot = SimpleNamespace(send_message=AsyncMock())
        context = SimpleNamespace(bot=bot)
        result = {
            "shipping": {"ID": "SP-1"},
            "added_items": [{
                "OGGETTO_SNAPSHOT": "Carta",
                "QUANTITA_SNAPSHOT": "1",
            }],
        }
        contributor = SimpleNamespace(id=100, username="alice")
        with patch.object(
            join_module,
            "get_admins",
            return_value=[{"TELEGRAM_ID": "999"}],
        ):
            await join_module._notify_join_completed(
                context,
                result=result,
                contributor=contributor,
                target_id="200",
                target_username="@bob",
            )
        self.assertEqual(bot.send_message.await_count, 2)
        for call in bot.send_message.await_args_list:
            self.assertNotIn("reply_markup", call.kwargs)

    async def test_notification_failure_does_not_raise_or_undo_union(self):
        bot = SimpleNamespace(
            send_message=AsyncMock(side_effect=RuntimeError("telegram"))
        )
        context = SimpleNamespace(bot=bot)
        result = {
            "shipping": {"ID": "SP-1"},
            "added_items": [{
                "OGGETTO_SNAPSHOT": "Carta",
                "QUANTITA_SNAPSHOT": "1",
            }],
        }
        with patch.object(
            join_module,
            "get_admins",
            return_value=[],
        ), patch.object(join_module.logger, "exception"):
            await join_module._notify_join_completed(
                context,
                result=result,
                contributor=SimpleNamespace(id=100, username="alice"),
                target_id="200",
                target_username="@bob",
            )
        bot.send_message.assert_awaited_once()

    async def test_admin_cancellation_notifies_each_participant_once(self):
        bot = SimpleNamespace(send_message=AsyncMock())
        context = SimpleNamespace(bot=bot)
        result = {
            "shipping": {"ID": "SP-1"},
            "participants": [
                {"TELEGRAM_ID": "200"},
                {"TELEGRAM_ID": "100"},
                {"TELEGRAM_ID": "100"},
            ],
        }
        await admin_module._notify_v2_shipping_cancelled(context, result)
        self.assertEqual(bot.send_message.await_count, 2)
        self.assertEqual(
            {call.kwargs["chat_id"] for call in bot.send_message.await_args_list},
            {100, 200},
        )

    async def test_admin_grouped_detail_contains_roles_ids_and_counts(self):
        text = admin_module._v2_grouped_items_text([
            {
                "TELEGRAM_ID": "200",
                "USERNAME": "@bob",
                "RUOLO": "TITOLARE",
                "NUMERO_ARTICOLI": 1,
                "QUANTITA_TOTALE": 2,
                "ITEMS": [{
                    "OGGETTO_SNAPSHOT": "Carta B",
                    "QUANTITA_SNAPSHOT": "2",
                }],
            },
            {
                "TELEGRAM_ID": "100",
                "USERNAME": "@alice",
                "RUOLO": "CONTRIBUENTE",
                "NUMERO_ARTICOLI": 1,
                "QUANTITA_TOTALE": 1,
                "ITEMS": [{
                    "OGGETTO_SNAPSHOT": "Carta A",
                    "QUANTITA_SNAPSHOT": "1",
                }],
            },
        ])
        for value in (
            "TITOLARE",
            "CONTRIBUENTE",
            "@bob",
            "@alice",
            "200",
            "100",
            "Carta B",
            "Carta A",
        ):
            self.assertIn(value, text)

    async def test_confirm_shows_contributor_success(self):
        query = SimpleNamespace(
            data="join_v2_confirm",
            from_user=SimpleNamespace(id=100, username="alice"),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query)
        user_data = {
            JOIN_TARGET_ID: "200",
            JOIN_TARGET_USERNAME: "@bob",
            JOIN_SHIPPING_ID: "SP-1",
            JOIN_SHIPPING_UUID: "ship-1",
            JOIN_AVAILABLE_ITEMS: [{
                "ID_ARTICOLO": item_id(2),
                "OGGETTO": "Carta",
                "QUANTITA": "1",
            }],
            JOIN_SELECTED_ITEM_IDS: {item_id(2)},
            JOIN_PAGE: 1,
        }
        context = SimpleNamespace(
            user_data=user_data,
            bot=SimpleNamespace(send_message=AsyncMock()),
        )
        result = {
            "shipping": {"ID": "SP-1"},
            "added_items": [{
                "ID_ARTICOLO": item_id(2),
                "OGGETTO_SNAPSHOT": "Carta",
                "QUANTITA_SNAPSHOT": "1",
            }],
        }
        with patch.object(
            join_module,
            "is_shipping_v2_active",
            return_value=True,
        ), patch.object(
            join_module,
            "add_contributor_items_to_v2_shipping",
            return_value=result,
        ), patch.object(
            join_module,
            "_notify_join_completed",
            new=AsyncMock(),
        ):
            await join_module.confirm_shipping_v2_join(update, context)
        query.answer.assert_awaited_once()
        query.edit_message_text.assert_awaited_once()
        self.assertIn(
            "Articoli aggiunti",
            query.edit_message_text.await_args.args[0],
        )
        self.assertFalse(
            any(key.startswith("shipping_v2_join") for key in user_data)
        )

    async def test_tracking_v2_is_sent_once_to_each_unique_participant(self):
        message = SimpleNamespace(text="TRACK-123", reply_text=AsyncMock())
        user = SimpleNamespace(id=999)
        update = SimpleNamespace(
            effective_message=message,
            effective_user=user,
        )
        bot = SimpleNamespace(send_message=AsyncMock())
        context = SimpleNamespace(
            user_data={"admin_tracking_shipping_id": "SP-1"},
            bot=bot,
        )
        completed = {
            "ID": "SP-1",
            "VERSIONE_SCHEMA": "V2",
            "CORRIERE": "BRT",
            "TRACKING": "TRACK-123",
            "_V2_PARTICIPANTS": [
                {"TELEGRAM_ID": "200", "USERNAME": "@bob"},
                {"TELEGRAM_ID": "100", "USERNAME": "@alice"},
                {"TELEGRAM_ID": "100", "USERNAME": "@alice"},
            ],
        }
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
            return_value={"ID": "SP-1", "VERSIONE_SCHEMA": "V2"},
        ), patch.object(
            admin_module,
            "complete_shipping_request_by_version",
            return_value=completed,
        ), patch.object(
            admin_module,
            "get_config_values",
            return_value={},
        ):
            result = await admin_module.receive_tracking(update, context)
        self.assertEqual(result, -1)
        self.assertEqual(bot.send_message.await_count, 2)
        self.assertEqual(
            {
                call.args[0]
                for call in bot.send_message.await_args_list
            },
            {100, 200},
        )

    async def test_tracking_legacy_still_notifies_only_holder(self):
        message = SimpleNamespace(text="TRACK-123", reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_message=message,
            effective_user=SimpleNamespace(id=999),
        )
        bot = SimpleNamespace(send_message=AsyncMock())
        context = SimpleNamespace(
            user_data={"admin_tracking_shipping_id": "SP-1"},
            bot=bot,
        )
        completed = {
            "ID": "SP-1",
            "VERSIONE_SCHEMA": "",
            "TELEGRAM_ID": "200",
            "USERNAME": "@bob",
            "CORRIERE": "BRT",
        }
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
            return_value={"ID": "SP-1"},
        ), patch.object(
            admin_module,
            "complete_shipping_request_by_version",
            return_value=completed,
        ), patch.object(
            admin_module,
            "get_config_values",
            return_value={},
        ):
            await admin_module.receive_tracking(update, context)
        bot.send_message.assert_awaited_once()
        self.assertEqual(
            bot.send_message.await_args.args[0],
            200,
        )


if __name__ == "__main__":
    unittest.main()
