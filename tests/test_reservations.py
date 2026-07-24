from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tests.fakes import (
    FakeRuntime,
    FakeSheet,
    install_dependency_stubs,
    item_id,
    valid_registry_values,
)

install_dependency_stubs()

from services import reservations
from services import shipping_v2_schema as schema

NOW = datetime(2026, 7, 23, 12, 0, tzinfo=ZoneInfo("Europe/Rome"))


class ReservationHardeningTests(unittest.TestCase):
    def setUp(self):
        self.runtime = FakeRuntime()
        owners = (
            [("100", "@alice")] * 30
            + [("200", "@bob")] * 10
        )
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
        self.repo = reservations.ReservationsRepository(
            bot_db_spreadsheet_id="BOT",
            session_factory=self.runtime.session,
        )

    def reserve(self, number, key, **kwargs):
        return self.repo.reserve_items(
            telegram_id=kwargs.pop("telegram_id", "100"),
            username=kwargs.pop("username", "@alice"),
            item_ids=[item_id(number)],
            idempotency_key=key,
            now=kwargs.pop("now", NOW),
            **kwargs,
        )

    def test_only_one_active_draft_per_holder(self):
        self.reserve(1, "first")
        with self.assertRaises(reservations.ReservationConflictError):
            self.reserve(2, "second")

    def test_new_draft_after_release(self):
        draft = self.reserve(1, "release")
        self.repo.release_draft(
            draft["uuid_bozza"],
            reason="ANNULLATA",
            now=NOW + timedelta(minutes=1),
        )
        second = self.reserve(
            1,
            "release-new",
            now=NOW + timedelta(minutes=2),
        )
        self.assertTrue(second["created"])

    def test_new_draft_after_shipping(self):
        draft = self.reserve(1, "shipped")
        self.repo.confirm_reservations(
            draft["uuid_bozza"],
            now=NOW + timedelta(minutes=1),
        )
        self.repo.mark_items_shipped(
            draft["uuid_bozza"],
            now=NOW + timedelta(minutes=2),
        )
        second = self.reserve(
            2,
            "after-shipped",
            now=NOW + timedelta(minutes=3),
        )
        self.assertTrue(second["created"])

    def test_same_idempotency_key_returns_same_draft(self):
        first = self.reserve(1, "retry")
        second = self.reserve(1, "retry")
        self.assertFalse(second["created"])
        self.assertEqual(first["uuid_bozza"], second["uuid_bozza"])

    def test_same_key_different_holder_is_idempotency_conflict(self):
        self.reserve(1, "holder-key")
        with self.assertRaises(reservations.IdempotencyConflictError):
            self.repo.reserve_items(
                telegram_id="200",
                username="@bob",
                item_ids=[item_id(1)],
                idempotency_key="holder-key",
                authorized_contributor_item_ids={item_id(1)},
                now=NOW,
            )

    def test_cannot_reserve_only_other_users_items(self):
        with self.assertRaises(reservations.ReservationConflictError):
            self.repo.reserve_items(
                telegram_id="100",
                username="@alice",
                item_ids=[item_id(31)],
                idempotency_key="only-other",
                authorized_contributor_item_ids={item_id(31)},
                now=NOW,
            )

    def test_cannot_forge_titular_role(self):
        with self.assertRaises(reservations.ReservationConflictError):
            self.repo.reserve_items(
                telegram_id="100",
                username="@alice",
                item_ids=[item_id(1), item_id(31)],
                idempotency_key="fake-titular",
                roles={item_id(31): "TITOLARE"},
                authorized_contributor_item_ids={item_id(31)},
                now=NOW,
            )

    def test_cannot_forge_contributor_role(self):
        with self.assertRaises(reservations.ReservationConflictError):
            self.repo.reserve_items(
                telegram_id="100",
                username="@alice",
                item_ids=[item_id(1)],
                idempotency_key="fake-contributor",
                roles={item_id(1): "CONTRIBUENTE"},
                now=NOW,
            )

    def test_authorized_contributor_is_accepted(self):
        result = self.repo.reserve_items(
            telegram_id="100",
            username="@alice",
            item_ids=[item_id(1), item_id(31)],
            idempotency_key="authorized",
            authorized_contributor_item_ids={item_id(31)},
            now=NOW,
        )
        roles = {
            row["ID_ARTICOLO"]: row["RUOLO"]
            for row in result["items"]
        }
        self.assertEqual(roles[item_id(1)], "TITOLARE")
        self.assertEqual(roles[item_id(31)], "CONTRIBUENTE")

    def test_unauthorized_contributor_is_rejected(self):
        with self.assertRaises(reservations.ReservationConflictError):
            self.repo.reserve_items(
                telegram_id="100",
                username="@alice",
                item_ids=[item_id(1), item_id(31)],
                idempotency_key="unauthorized",
                now=NOW,
            )

    def test_at_least_one_titular_item_is_required(self):
        with self.assertRaises(reservations.ReservationConflictError):
            self.repo.reserve_items(
                telegram_id="100",
                username="@alice",
                item_ids=[item_id(31), item_id(32)],
                idempotency_key="no-titular",
                authorized_contributor_item_ids={
                    item_id(31),
                    item_id(32),
                },
                now=NOW,
            )

    def test_twenty_concurrent_requests_same_item(self):
        def attempt(index):
            try:
                self.reserve(1, f"same-{index}")
                return "success"
            except reservations.ReservationConflictError:
                return "conflict"

        with ThreadPoolExecutor(max_workers=20) as pool:
            outcomes = list(pool.map(attempt, range(20)))
        self.assertEqual(outcomes.count("success"), 1)
        self.assertEqual(outcomes.count("conflict"), 19)
        self.assertEqual(
            len(self.repo.get_active_reservations(now=NOW)),
            1,
        )

    def test_twenty_concurrent_items_same_holder_one_draft(self):
        def attempt(index):
            try:
                self.reserve(index + 1, f"holder-{index}")
                return "success"
            except reservations.ReservationConflictError:
                return "conflict"

        with ThreadPoolExecutor(max_workers=20) as pool:
            outcomes = list(pool.map(attempt, range(20)))
        self.assertEqual(outcomes.count("success"), 1)
        self.assertEqual(outcomes.count("conflict"), 19)
        active_draft = self.repo.get_active_draft_for_user(
            "100",
            now=NOW,
        )
        self.assertIsNotNone(active_draft)

    def test_expired_draft_is_released_before_new_one(self):
        first = self.reserve(1, "expires", ttl_minutes=1)
        second = self.reserve(
            2,
            "after-expiry",
            now=NOW + timedelta(minutes=2),
        )
        self.assertTrue(second["created"])
        rows = schema.rows_as_dicts(
            self.runtime.sheets[
                ("BOT", schema.SHIPPING_ITEMS_WORKSHEET_NAME)
            ].get_all_values(),
            schema.SHIPPING_ITEMS_HEADERS,
        )
        expired_rows = [
            row
            for row in rows
            if row["UUID_BOZZA"] == first["uuid_bozza"]
        ]
        self.assertEqual(
            {row["STATO_PRENOTAZIONE"] for row in expired_rows},
            {"RILASCIATO"},
        )

    def test_timeout_after_append_does_not_duplicate(self):
        sheet = self.runtime.sheets[
            ("BOT", schema.SHIPPING_ITEMS_WORKSHEET_NAME)
        ]
        sheet.timeout_after_append_once = True
        self.reserve(1, "timeout")
        records = schema.rows_as_dicts(
            sheet.get_all_values(),
            schema.SHIPPING_ITEMS_HEADERS,
        )
        self.assertEqual(len(records), 1)


if __name__ == "__main__":
    unittest.main()

