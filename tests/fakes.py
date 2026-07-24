from __future__ import annotations

import copy
import re
import sys
import threading
import types
from contextlib import contextmanager


def install_dependency_stubs() -> None:
    """Installa stub locali: i test non possono raggiungere Google reale."""
    if "dotenv" not in sys.modules:
        dotenv = types.ModuleType("dotenv")
        dotenv.load_dotenv = lambda: None
        sys.modules["dotenv"] = dotenv

    if "gspread" not in sys.modules:
        gspread = types.ModuleType("gspread")

        class APIError(Exception):
            pass

        class WorksheetNotFound(Exception):
            pass

        gspread.exceptions = types.SimpleNamespace(
            APIError=APIError,
            WorksheetNotFound=WorksheetNotFound,
        )
        gspread.Client = object
        gspread.Spreadsheet = object
        gspread.Worksheet = object
        gspread.authorize = lambda credentials: None
        sys.modules["gspread"] = gspread

    if "google.oauth2.service_account" not in sys.modules:
        google = types.ModuleType("google")
        oauth2 = types.ModuleType("google.oauth2")
        service_account = types.ModuleType(
            "google.oauth2.service_account"
        )

        class Credentials:
            @classmethod
            def from_service_account_info(cls, *args, **kwargs):
                return cls()

            @classmethod
            def from_service_account_file(cls, *args, **kwargs):
                return cls()

        service_account.Credentials = Credentials
        sys.modules["google"] = google
        sys.modules["google.oauth2"] = oauth2
        sys.modules["google.oauth2.service_account"] = service_account

    if "telegram" not in sys.modules:
        telegram = types.ModuleType("telegram")

        class InlineKeyboardButton:
            def __init__(self, text, callback_data=None, **kwargs):
                self.text = text
                self.callback_data = callback_data

        class InlineKeyboardMarkup:
            def __init__(self, inline_keyboard):
                self.inline_keyboard = inline_keyboard

        class Update:
            pass

        telegram.InlineKeyboardButton = InlineKeyboardButton
        telegram.InlineKeyboardMarkup = InlineKeyboardMarkup
        telegram.Update = Update
        sys.modules["telegram"] = telegram

        telegram_error = types.ModuleType("telegram.error")

        class BadRequest(Exception):
            pass

        telegram_error.BadRequest = BadRequest
        sys.modules["telegram.error"] = telegram_error

        telegram_ext = types.ModuleType("telegram.ext")
        telegram_ext.ContextTypes = types.SimpleNamespace(
            DEFAULT_TYPE=object,
        )
        telegram_ext.ConversationHandler = types.SimpleNamespace(END=-1)
        sys.modules["telegram.ext"] = telegram_ext


def _column_number(cell: str) -> int:
    letters = re.match(r"[A-Z]+", cell.upper()).group(0)
    result = 0
    for letter in letters:
        result = result * 26 + ord(letter) - 64
    return result


def _row_number(cell: str) -> int:
    return int(re.search(r"\d+", cell).group(0))


class FakeSheet:
    def __init__(self, values=None, *, readonly=False):
        self.values = copy.deepcopy(values or [])
        self.readonly = readonly
        self.write_calls = 0
        self.timeout_after_append_once = False
        self._timeout_raised = False

    def get_all_values(self):
        return copy.deepcopy(self.values)

    def _guard_write(self):
        self.write_calls += 1
        if self.readonly:
            raise AssertionError("Scrittura vietata sul gestionale ORDINI")

    def _ensure(self, row: int, column: int):
        while len(self.values) < row:
            self.values.append([])
        while len(self.values[row - 1]) < column:
            self.values[row - 1].append("")

    def update(self, *, range_name, values, **kwargs):
        self._guard_write()
        start = range_name.split(":")[0]
        start_row = _row_number(start)
        start_column = _column_number(start)
        for row_offset, row in enumerate(values):
            for column_offset, value in enumerate(row):
                target_row = start_row + row_offset
                target_column = start_column + column_offset
                self._ensure(target_row, target_column)
                self.values[target_row - 1][target_column - 1] = str(value)

    def batch_update(self, updates, **kwargs):
        self._guard_write()
        for update in updates:
            self.update(
                range_name=update["range"],
                values=update["values"],
            )
        # update() incrementa il contatore; il valore preciso non è usato.

    def append_rows(self, rows, **kwargs):
        self._guard_write()
        self.values.extend(copy.deepcopy(rows))
        if (
            self.timeout_after_append_once
            and not self._timeout_raised
        ):
            self._timeout_raised = True
            raise TimeoutError("timeout simulato dopo append")


class FakeSession:
    def __init__(self, sheet: FakeSheet):
        self.worksheet = sheet

    def call(self, operation, *, operation_name):
        for attempt in range(2):
            try:
                return operation(self.worksheet)
            except TimeoutError:
                if attempt == 1:
                    raise


class FakeRuntime:
    def __init__(self):
        self.sheets = {}
        self.locks = {}

    def add(self, spreadsheet_id: str, name: str, sheet: FakeSheet):
        key = (spreadsheet_id, name)
        self.sheets[key] = sheet
        self.locks.setdefault(key, threading.RLock())

    @contextmanager
    def session(self, spreadsheet_id: str, name: str):
        key = (spreadsheet_id, name)
        if key not in self.sheets:
            raise sys.modules[
                "gspread"
            ].exceptions.WorksheetNotFound(name)
        with self.locks[key]:
            yield FakeSession(self.sheets[key])

    def operation(
        self,
        spreadsheet_id,
        name,
        operation,
        *,
        operation_name,
    ):
        with self.session(spreadsheet_id, name) as session:
            return session.call(
                operation,
                operation_name=operation_name,
            )


def item_id(number: int) -> str:
    return f"ART-00000000-0000-4000-8000-{number:012d}"


def valid_registry_values(schema, owners: list[tuple[str, str]]):
    rows = [list(schema.ORDER_REGISTRY_HEADERS)]
    for index, (owner_id, username) in enumerate(owners, start=1):
        record = {
            "ID_ARTICOLO": item_id(index),
            "SOURCE_SPREADSHEET_ID": "SOURCE",
            "SOURCE_SHEET": "ORDINI",
            "SOURCE_ROW": str(index + 1),
            "IDENTITY_FINGERPRINT": f"identity-{index}",
            "ROW_FINGERPRINT": f"row-{index}",
            "DUPLICATE_INDEX": "1",
            "DATA": "oggi",
            "OGGETTO": f"Oggetto {index}",
            "QUANTITA": "1",
            "USERNAME": username,
            "TELEGRAM_ID_PROPRIETARIO": owner_id,
            "STATO_ORIGINE": "IN MAGAZZINO",
            "FIRST_SEEN_AT": "2026-07-23T10:00:00+02:00",
            "LAST_SEEN_AT": "2026-07-23T10:00:00+02:00",
            "SYNC_STATUS": "OK",
            "IS_ACTIVE": "TRUE",
            "VERSIONE": "V1",
        }
        rows.append(
            [
                record.get(header, "")
                for header in schema.ORDER_REGISTRY_HEADERS
            ]
        )
    return rows


def valid_reservation_record(
    schema,
    *,
    detail_uuid="detail-1",
    draft_uuid="draft-1",
    item=None,
    owner_id="100",
    role="TITOLARE",
    state="PRENOTATO",
    idempotency_key="key-1",
):
    item = item or item_id(1)
    record = {
        "UUID_DETTAGLIO": detail_uuid,
        "UUID_BOZZA": draft_uuid,
        "UUID_SPEDIZIONE": "",
        "ID_SPEDIZIONE": "",
        "ID_ARTICOLO": item,
        "TELEGRAM_ID_PROPRIETARIO": owner_id,
        "USERNAME_PROPRIETARIO": "@user",
        "RUOLO": role,
        "OGGETTO_SNAPSHOT": "Oggetto",
        "QUANTITA_SNAPSHOT": "1",
        "RIGA_ORDINE_SNAPSHOT": "2",
        "STATO_PRENOTAZIONE": state,
        "PRENOTATO_IL": "2026-07-23T10:00:00+02:00",
        "PRENOTATO_FINO_AL": "2026-07-23T11:00:00+02:00",
        "CONFERMATO_IL": "",
        "SPEDITO_IL": "",
        "RILASCIATO_IL": "",
        "MOTIVO_RILASCIO": "",
        "IDEMPOTENCY_KEY": idempotency_key,
        "ULTIMO_AGGIORNAMENTO": "2026-07-23T10:00:00+02:00",
        "VERSIONE": "V1",
    }
    if state == "CONFERMATO":
        record["UUID_SPEDIZIONE"] = "shipping-1"
        record["PRENOTATO_FINO_AL"] = ""
        record["CONFERMATO_IL"] = "2026-07-23T10:30:00+02:00"
    elif state == "SPEDITO":
        record["UUID_SPEDIZIONE"] = "shipping-1"
        record["PRENOTATO_FINO_AL"] = ""
        record["SPEDITO_IL"] = "2026-07-23T10:30:00+02:00"
    elif state == "RILASCIATO":
        record["PRENOTATO_FINO_AL"] = ""
        record["RILASCIATO_IL"] = "2026-07-23T10:30:00+02:00"
    return record
