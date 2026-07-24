"""Risorse Google Sheets condivise, lazy e thread-safe per processo."""
from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import TypeVar

import gspread
from google.oauth2.service_account import Credentials

from config import GOOGLE_CREDENTIALS_JSON
from services.perf import get_perf_context
from services.retry import call_with_retry

T = TypeVar("T")

BASE_DIR = Path(__file__).resolve().parent.parent
CREDENTIALS_FILE = BASE_DIR / "credentials.json"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

_STATE_LOCK = RLock()
_CREDENTIALS_INIT_LOCK = RLock()
_CLIENT_INIT_LOCK = RLock()

_credentials: Credentials | None = None
_client: gspread.Client | None = None
_spreadsheets: dict[str, gspread.Spreadsheet] = {}
_worksheets: dict[tuple[str, str], gspread.Worksheet] = {}

_spreadsheet_init_locks: dict[str, RLock] = {}
_spreadsheet_access_locks: dict[str, RLock] = {}
_worksheet_init_locks: dict[tuple[str, str], RLock] = {}
_worksheet_access_locks: dict[tuple[str, str], RLock] = {}

_generation = 0


def _resource_lock(lock_map: dict, key) -> RLock:
    with _STATE_LOCK:
        lock = lock_map.get(key)
        if lock is None:
            lock = RLock()
            lock_map[key] = lock
        return lock


def _timed_retry(
    operation: Callable[[], T],
    *,
    operation_name: str,
) -> T:
    perf = get_perf_context()
    start = time.perf_counter()
    try:
        return call_with_retry(
            operation,
            operation_name=operation_name,
        )
    finally:
        if perf is not None:
            perf.sheet_call(
                (time.perf_counter() - start) * 1000.0
            )


def _build_credentials() -> Credentials:
    if GOOGLE_CREDENTIALS_JSON:
        try:
            credentials_info = json.loads(
                GOOGLE_CREDENTIALS_JSON
            )
        except json.JSONDecodeError as error:
            raise RuntimeError(
                "La variabile GOOGLE_CREDENTIALS_JSON "
                "non contiene un JSON valido."
            ) from error

        private_key = credentials_info.get("private_key")
        if private_key:
            credentials_info["private_key"] = private_key.replace(
                "\\n",
                "\n",
            )

        return Credentials.from_service_account_info(
            credentials_info,
            scopes=SCOPES,
        )

    if CREDENTIALS_FILE.exists():
        return Credentials.from_service_account_file(
            CREDENTIALS_FILE,
            scopes=SCOPES,
        )

    raise FileNotFoundError(
        "Credenziali Google non trovate.\n"
        f"In locale inserisci credentials.json in: "
        f"{CREDENTIALS_FILE}\n"
        "Su Railway configura la variabile "
        "GOOGLE_CREDENTIALS_JSON."
    )


def get_credentials() -> Credentials:
    """Restituisce l'unica istanza Credentials del processo."""
    global _credentials

    with _CREDENTIALS_INIT_LOCK:
        with _STATE_LOCK:
            if _credentials is not None:
                return _credentials
            generation = _generation

        created = _build_credentials()

        with _STATE_LOCK:
            if generation == _generation:
                if _credentials is None:
                    _credentials = created
                return _credentials

        return created


def get_client() -> gspread.Client:
    """Restituisce l'unico client gspread autorizzato del processo."""
    global _client

    with _CLIENT_INIT_LOCK:
        with _STATE_LOCK:
            if _client is not None:
                return _client
            generation = _generation

        created = gspread.authorize(
            get_credentials()
        )

        with _STATE_LOCK:
            if generation == _generation:
                if _client is None:
                    _client = created
                return _client

        return created


def get_spreadsheet(
    spreadsheet_id: str,
) -> gspread.Spreadsheet:
    """Apre una sola volta ciascun file Google Sheets."""
    spreadsheet_id = str(spreadsheet_id or "").strip()
    if not spreadsheet_id:
        raise RuntimeError("Spreadsheet ID non configurato.")

    init_lock = _resource_lock(
        _spreadsheet_init_locks,
        spreadsheet_id,
    )

    with init_lock:
        with _STATE_LOCK:
            cached = _spreadsheets.get(spreadsheet_id)
            if cached is not None:
                return cached
            generation = _generation

        client = get_client()
        opened = _timed_retry(
            lambda: client.open_by_key(spreadsheet_id),
            operation_name="apertura Google Sheets",
        )

        with _STATE_LOCK:
            if generation == _generation:
                existing = _spreadsheets.get(spreadsheet_id)
                if existing is None:
                    _spreadsheets[spreadsheet_id] = opened
                    return opened
                return existing

        return opened


def get_worksheet(
    spreadsheet_id: str,
    worksheet_name: str,
) -> gspread.Worksheet:
    """Apre una sola volta ciascuna coppia file/scheda."""
    spreadsheet_id = str(spreadsheet_id or "").strip()
    worksheet_name = str(worksheet_name or "").strip()
    if not spreadsheet_id:
        raise RuntimeError("Spreadsheet ID non configurato.")
    if not worksheet_name:
        raise RuntimeError("Worksheet name non configurato.")

    key = (spreadsheet_id, worksheet_name)
    init_lock = _resource_lock(
        _worksheet_init_locks,
        key,
    )

    with init_lock:
        with _STATE_LOCK:
            cached = _worksheets.get(key)
            if cached is not None:
                return cached
            generation = _generation

        spreadsheet = get_spreadsheet(spreadsheet_id)
        opened = _timed_retry(
            lambda: spreadsheet.worksheet(worksheet_name),
            operation_name="apertura scheda Google Sheets",
        )

        with _STATE_LOCK:
            if generation == _generation:
                existing = _worksheets.get(key)
                if existing is None:
                    _worksheets[key] = opened
                    return opened
                return existing

        return opened


@dataclass(frozen=True)
class WorksheetSession:
    """Accesso a una worksheet mentre il relativo RLock è acquisito."""

    worksheet: gspread.Worksheet

    def call(
        self,
        operation: Callable[[gspread.Worksheet], T],
        *,
        operation_name: str,
    ) -> T:
        return _timed_retry(
            lambda: operation(self.worksheet),
            operation_name=operation_name,
        )


@contextmanager
def worksheet_session(
    spreadsheet_id: str,
    worksheet_name: str,
) -> Iterator[WorksheetSession]:
    """Mantiene il lock della singola worksheet per una sequenza remota."""
    spreadsheet_id = str(spreadsheet_id or "").strip()
    worksheet_name = str(worksheet_name or "").strip()
    key = (spreadsheet_id, worksheet_name)
    worksheet = get_worksheet(
        spreadsheet_id,
        worksheet_name,
    )
    access_lock = _resource_lock(
        _worksheet_access_locks,
        key,
    )
    with access_lock:
        yield WorksheetSession(worksheet)


def worksheet_operation(
    spreadsheet_id: str,
    worksheet_name: str,
    operation: Callable[[gspread.Worksheet], T],
    *,
    operation_name: str,
) -> T:
    """Esegue con retry e lock un'operazione sulla singola worksheet."""
    with worksheet_session(
        spreadsheet_id,
        worksheet_name,
    ) as session:
        return session.call(
            operation,
            operation_name=operation_name,
        )


def spreadsheet_operation(
    spreadsheet_id: str,
    operation: Callable[[gspread.Spreadsheet], T],
    *,
    operation_name: str,
) -> T:
    """Esegue con retry e lock un'operazione sul file Google Sheets."""
    spreadsheet_id = str(spreadsheet_id or "").strip()
    spreadsheet = get_spreadsheet(spreadsheet_id)
    access_lock = _resource_lock(
        _spreadsheet_access_locks,
        spreadsheet_id,
    )
    with access_lock:
        return _timed_retry(
            lambda: operation(spreadsheet),
            operation_name=operation_name,
        )


def reset_google_resources() -> None:
    """Azzera esplicitamente le sole risorse Google memorizzate."""
    global _credentials, _client, _generation

    with _STATE_LOCK:
        _generation += 1
        _credentials = None
        _client = None
        _spreadsheets.clear()
        _worksheets.clear()


def google_runtime_info() -> dict[str, int]:
    """Espone soltanto conteggi diagnostici, mai credenziali o dati."""
    with _STATE_LOCK:
        return {
            "credentials": int(_credentials is not None),
            "clients": int(_client is not None),
            "spreadsheets": len(_spreadsheets),
            "worksheets": len(_worksheets),
        }
