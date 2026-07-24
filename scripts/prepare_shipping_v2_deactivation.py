#!/usr/bin/env python3
"""Prepara in modo esplicito e verificabile la disattivazione Shipping v2.

La modalità predefinita è di sola lettura. Il rilascio richiede insieme
``--release-prebooked`` e ``--confirm-production`` e non viene mai eseguito
automaticamente dallo startup.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import BOT_DB_SHEET_ID
from services.common import clean_value
from services.google_runtime import worksheet_session
from services.reservations import ReservationsRepository
from services.shipping_v2_schema import (
    SHIPPING_ITEMS_HEADERS,
    SHIPPING_ITEMS_WORKSHEET_NAME,
    normalized_headers,
    rows_as_dicts,
)

ITALY_TIMEZONE = ZoneInfo("Europe/Rome")
RELEASE_REASON = "DISATTIVAZIONE_V2_OPERATORE"


class DeactivationError(RuntimeError):
    pass


def _aware_now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(ITALY_TIMEZONE)
    if current.tzinfo is None or current.utcoffset() is None:
        raise DeactivationError("Il clock deve essere timezone-aware.")
    return current


def _parse_time(value: str) -> datetime | None:
    text = clean_value(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise DeactivationError("Timestamp prenotazione non valido.") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DeactivationError("Timestamp prenotazione privo di timezone.")
    return parsed


def inspect_shipping_v2_deactivation(
    *,
    bot_db_spreadsheet_id: str | None = None,
    session_factory=worksheet_session,
    now: datetime | None = None,
) -> dict[str, Any]:
    spreadsheet_id = clean_value(
        bot_db_spreadsheet_id or BOT_DB_SHEET_ID
    )
    if not spreadsheet_id:
        raise DeactivationError("BOT_DB_SHEET_ID non configurato.")
    current = _aware_now(now)
    with session_factory(
        spreadsheet_id,
        SHIPPING_ITEMS_WORKSHEET_NAME,
    ) as session:
        values = session.call(
            lambda worksheet: worksheet.get_all_values(),
            operation_name="ispezione disattivazione Shipping v2",
        )
    if tuple(normalized_headers(values)) != SHIPPING_ITEMS_HEADERS:
        raise DeactivationError(
            "SPEDIZIONI_ARTICOLI non rispetta lo schema previsto."
        )
    records = rows_as_dicts(values, SHIPPING_ITEMS_HEADERS)
    drafts: dict[str, list[dict[str, str]]] = defaultdict(list)
    for record in records:
        if record.get("UUID_BOZZA"):
            drafts[record["UUID_BOZZA"]].append(record)

    active = []
    expired = []
    confirmed = []
    shipped = []
    for draft_uuid, rows in sorted(drafts.items()):
        states = {row["STATO_PRENOTAZIONE"] for row in rows}
        detail = {
            "draft_uuid": draft_uuid,
            "item_count": len(rows),
            "states": sorted(states),
        }
        if states == {"PRENOTATO"}:
            expiries = [_parse_time(row["PRENOTATO_FINO_AL"]) for row in rows]
            expiry = min(
                (value for value in expiries if value is not None),
                default=None,
            )
            detail["expires_at"] = expiry.isoformat() if expiry else ""
            if expiry is not None and expiry <= current:
                expired.append(detail)
            else:
                active.append(detail)
        elif "CONFERMATO" in states:
            confirmed.append(detail)
        elif "SPEDITO" in states:
            shipped.append(detail)

    return {
        "generated_at": current.isoformat(),
        "mode": "READ_ONLY",
        "safe_to_disable": not active,
        "counts": {
            "prebooked_active": len(active),
            "prebooked_expired": len(expired),
            "confirmed": len(confirmed),
            "shipped": len(shipped),
        },
        "prebooked_active": active,
        "prebooked_expired": expired,
        "confirmed": confirmed,
        "shipped": shipped,
    }


def prepare_shipping_v2_deactivation(
    *,
    release_prebooked: bool = False,
    confirm_production: bool = False,
    bot_db_spreadsheet_id: str | None = None,
    session_factory=worksheet_session,
    repository_factory: Callable[..., ReservationsRepository] = (
        ReservationsRepository
    ),
    now: datetime | None = None,
) -> dict[str, Any]:
    if release_prebooked and not confirm_production:
        raise DeactivationError(
            "--release-prebooked richiede --confirm-production."
        )
    before = inspect_shipping_v2_deactivation(
        bot_db_spreadsheet_id=bot_db_spreadsheet_id,
        session_factory=session_factory,
        now=now,
    )
    if not release_prebooked:
        return before

    spreadsheet_id = clean_value(
        bot_db_spreadsheet_id or BOT_DB_SHEET_ID
    )
    repository = repository_factory(
        bot_db_spreadsheet_id=spreadsheet_id,
        session_factory=session_factory,
    )
    released = []
    for detail in (
        before["prebooked_active"] + before["prebooked_expired"]
    ):
        repository.release_draft(
            detail["draft_uuid"],
            reason=RELEASE_REASON,
            now=_aware_now(now),
        )
        released.append(detail["draft_uuid"])

    after = inspect_shipping_v2_deactivation(
        bot_db_spreadsheet_id=spreadsheet_id,
        session_factory=session_factory,
        now=now,
    )
    return {
        **after,
        "mode": "RELEASE_PREBOOKED",
        "before": before,
        "released_drafts": released,
    }


def format_text_report(report: dict[str, Any]) -> str:
    counts = report["counts"]
    lines = [
        "Shipping v2 - preparazione disattivazione",
        f"mode={report['mode']}",
        f"safe_to_disable={str(report['safe_to_disable']).lower()}",
        f"prebooked_active={counts['prebooked_active']}",
        f"prebooked_expired={counts['prebooked_expired']}",
        f"confirmed={counts['confirmed']}",
        f"shipped={counts['shipped']}",
    ]
    for label in ("prebooked_active", "prebooked_expired", "confirmed"):
        for detail in report[label]:
            lines.append(
                f"{label}: draft={detail['draft_uuid']} "
                f"items={detail['item_count']} "
                f"expires_at={detail.get('expires_at', '')}"
            )
    if report.get("released_drafts"):
        lines.append(
            "released_drafts=" + ",".join(report["released_drafts"])
        )
    return "\n".join(lines) + "\n"


def write_reports(
    report: dict[str, Any],
    report_dir: Path,
) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "shipping_v2_deactivation_report.json"
    text_path = report_dir / "shipping_v2_deactivation_report.txt"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    text_path.write_text(format_text_report(report), encoding="utf-8")
    return json_path, text_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-prebooked", action="store_true")
    parser.add_argument("--confirm-production", action="store_true")
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path("."),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = prepare_shipping_v2_deactivation(
            release_prebooked=args.release_prebooked,
            confirm_production=args.confirm_production,
        )
    except Exception as error:
        print(f"ERRORE: {error}", file=sys.stderr)
        return 2
    print(format_text_report(report), end="")
    if args.release_prebooked:
        json_path, text_path = write_reports(report, args.report_dir)
        print(f"report_json={json_path.resolve()}")
        print(f"report_text={text_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
