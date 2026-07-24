from __future__ import annotations

import copy
import unittest

from tests.fakes import (
    install_dependency_stubs,
    item_id,
    valid_registry_values,
    valid_reservation_record,
)

install_dependency_stubs()

from services import shipping_v2_schema as schema


def as_row(record, headers):
    return [record.get(header, "") for header in headers]


class ShippingSchemaHardeningTests(unittest.TestCase):
    def setUp(self):
        self.registry = valid_registry_values(
            schema,
            [("100", "@alice"), ("200", "@bob")],
        )
        self.shipping = [[
            *schema.SHIPPING_LEGACY_HEADERS,
            *schema.SHIPPING_V2_HEADERS,
        ]]
        self.valid_record = valid_reservation_record(schema)

    def validate(self, records=None, registry=None):
        items = [
            list(schema.SHIPPING_ITEMS_HEADERS),
            *[
                as_row(record, schema.SHIPPING_ITEMS_HEADERS)
                for record in (records or [])
            ],
        ]
        return schema.validate_shipping_v2_values(
            registry or self.registry,
            self.shipping,
            items,
        )

    def test_valid_schema_and_data(self):
        result = self.validate([self.valid_record])
        self.assertTrue(result.valid, result.errors)

    def test_empty_ids_and_uuids_are_rejected(self):
        record = copy.deepcopy(self.valid_record)
        record["UUID_DETTAGLIO"] = ""
        record["UUID_BOZZA"] = ""
        record["ID_ARTICOLO"] = ""
        record["TELEGRAM_ID_PROPRIETARIO"] = ""
        result = self.validate([record])
        self.assertFalse(result.valid)
        self.assertIn(
            "UUID_DETTAGLIO",
            result.details["shipping_items"][
                "missing_required_fields"
            ],
        )

    def test_duplicate_detail_uuid_is_rejected(self):
        first = copy.deepcopy(self.valid_record)
        second = valid_reservation_record(
            schema,
            detail_uuid=first["UUID_DETTAGLIO"],
            draft_uuid="draft-2",
            item=item_id(2),
            owner_id="200",
            idempotency_key="key-2",
        )
        result = self.validate([first, second])
        self.assertFalse(result.valid)
        self.assertTrue(
            result.details["shipping_items"]["duplicate_detail_uuids"]
        )

    def test_invalid_role_is_rejected(self):
        record = copy.deepcopy(self.valid_record)
        record["RUOLO"] = "AMMINISTRATORE"
        result = self.validate([record])
        self.assertFalse(result.valid)
        self.assertTrue(
            result.details["shipping_items"]["invalid_roles"]
        )

    def test_invalid_state_is_rejected(self):
        record = copy.deepcopy(self.valid_record)
        record["STATO_PRENOTAZIONE"] = "SCONOSCIUTO"
        result = self.validate([record])
        self.assertFalse(result.valid)
        self.assertTrue(
            result.details["shipping_items"]["invalid_states"]
        )

    def test_naive_timestamp_is_rejected(self):
        record = copy.deepcopy(self.valid_record)
        record["PRENOTATO_IL"] = "2026-07-23T10:00:00"
        result = self.validate([record])
        self.assertFalse(result.valid)
        self.assertTrue(
            result.details["shipping_items"]["invalid_timestamps"]
        )

    def test_live_reservation_on_non_warehouse_item_is_rejected(self):
        registry = copy.deepcopy(self.registry)
        status_index = schema.ORDER_REGISTRY_HEADERS.index(
            "STATO_ORIGINE"
        )
        registry[1][status_index] = "EVASO"
        result = self.validate([self.valid_record], registry=registry)
        self.assertFalse(result.valid)
        self.assertTrue(
            result.details["invalid_live_reservations"]
        )

    def test_live_draft_with_multiple_titular_ids_is_rejected(self):
        first = copy.deepcopy(self.valid_record)
        second = valid_reservation_record(
            schema,
            detail_uuid="detail-2",
            draft_uuid=first["UUID_BOZZA"],
            item=item_id(2),
            owner_id="200",
            role="TITOLARE",
            idempotency_key=first["IDEMPOTENCY_KEY"],
        )
        result = self.validate([first, second])
        self.assertFalse(result.valid)
        self.assertTrue(
            result.details["shipping_items"][
                "invalid_live_draft_holders"
            ]
        )

    def test_invalid_registry_id_source_row_and_metadata(self):
        registry = copy.deepcopy(self.registry)
        indexes = {
            name: schema.ORDER_REGISTRY_HEADERS.index(name)
            for name in (
                "ID_ARTICOLO",
                "SOURCE_ROW",
                "IDENTITY_FINGERPRINT",
                "VERSIONE",
            )
        }
        registry[1][indexes["ID_ARTICOLO"]] = "ART-NON-UUID"
        registry[1][indexes["SOURCE_ROW"]] = "zero"
        registry[1][indexes["IDENTITY_FINGERPRINT"]] = ""
        registry[1][indexes["VERSIONE"]] = ""
        result = self.validate([], registry=registry)
        self.assertFalse(result.valid)
        details = result.details["order_registry"]
        self.assertTrue(details["invalid_item_id_format"])
        self.assertTrue(details["invalid_active_source_rows"])
        self.assertTrue(details["active_rows_missing_metadata"])

    def test_shipped_item_may_be_inactive_historically(self):
        registry = copy.deepcopy(self.registry)
        active_index = schema.ORDER_REGISTRY_HEADERS.index("IS_ACTIVE")
        sync_index = schema.ORDER_REGISTRY_HEADERS.index("SYNC_STATUS")
        registry[1][active_index] = "FALSE"
        registry[1][sync_index] = "NON_PRESENTE"
        shipped = valid_reservation_record(
            schema,
            state="SPEDITO",
        )
        result = self.validate([shipped], registry=registry)
        self.assertTrue(result.valid, result.errors)


if __name__ == "__main__":
    unittest.main()

