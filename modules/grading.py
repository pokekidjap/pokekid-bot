import asyncio
import html
import logging

from services.perf import start_flow
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.error import BadRequest
from telegram.ext import (
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from keyboards.grading import grading_keyboard
from services.grading import get_grading_records
from services.status import get_sub_status_info
from services.ui import (
    DIVIDER,
    operation_unavailable,
    page_indicator,
    page_title,
    readable_status,
    section_title,
    summary_row,
    with_footer,
)


logger = logging.getLogger(__name__)
GRADING_PER_PAGE = 6
GRADING_SEARCH = 1


def _grading_response(text: str) -> str:
    return with_footer(text)


def _is_message_not_modified(error: BadRequest) -> bool:
    return "message is not modified" in str(error).lower()


def _grading_error(
    query,
    message: str,
    reply_markup=None,
) -> None:
    return query.edit_message_text(
        operation_unavailable(message),
        reply_markup=reply_markup,
        parse_mode="HTML",
    )


def _normalize_search(search_query: str | None) -> str:
    return str(search_query or "").strip().lower()


def _filter_grading_records(
    grading_records: list[dict],
    search_query: str | None,
) -> list[dict]:
    normalized_query = _normalize_search(search_query)
    if not normalized_query:
        return grading_records

    filtered: list[dict] = []
    for record in grading_records:
        grading = str(record.get("grading", "")).lower()
        sub = str(record.get("sub", "")).lower()
        service = str(record.get("service", "")).lower()

        if (
            normalized_query in grading
            or normalized_query in sub
            or normalized_query in service
        ):
            filtered.append(record)

    return filtered


def _sort_grading_records(
    grading_records: list[dict],
) -> list[dict]:
    def sort_key(record: dict) -> tuple[int, str, str]:
        status_info = get_sub_status_info(record.get("status", ""))
        grading = str(record.get("grading", "")).upper()
        sub = str(record.get("sub", "")).upper()
        return (
            status_info["step"],
            grading,
            sub,
        )

    return sorted(
        grading_records,
        key=sort_key,
    )


def _get_grading_page(
    grading_records: list[dict],
    page: int,
) -> list[dict]:
    start = (page - 1) * GRADING_PER_PAGE
    end = start + GRADING_PER_PAGE
    return grading_records[start:end]


def _build_status_summary(
    grading_records: list[dict],
) -> str:
    grouped: dict[str, int] = {}

    for record in grading_records:
        status = get_sub_status_info(record.get("status", ""))["name"]
        grouped[status] = grouped.get(status, 0) + 1

    ordered_status = sorted(
        grouped.items(),
        key=lambda item: get_sub_status_info(item[0])["step"],
    )

    summary = [
        f"{get_sub_status_info(status)['emoji']} "
        f"<b>{html.escape(readable_status(status))}</b>: <b>{count}</b>"
        for status, count in ordered_status
    ]

    return "\n".join(summary)


def _group_page_records(
    page_records: list[dict],
) -> list[tuple[str, list[dict]]]:
    grouped: dict[str, list[dict]] = {}

    for record in page_records:
        status = get_sub_status_info(record.get("status", ""))["name"]
        grouped.setdefault(status, []).append(record)

    return sorted(
        grouped.items(),
        key=lambda item: get_sub_status_info(item[0])["step"],
    )


def build_grading_text(
    page_records: list[dict],
    all_filtered_records: list[dict],
    search_query: str | None,
    page: int,
    total_pages: int,
) -> str:
    if not all_filtered_records:
        sections = [
            page_title("🎴", "Stato SUB Grading"),
            "",
            summary_row("📄", "Totale risultati", 0),
            page_indicator(1, 1),
        ]
        if search_query:
            sections.extend(
                [
                    f"🔎 Ricerca: <code>{html.escape(search_query)}</code>",
                    "",
                    "Nessuna SUB trovata per il termine indicato.",
                    "",
                    "Prova un altro termine o cancella la ricerca.",
                ]
            )
        else:
            sections.extend(
                [
                    "",
                    "Al momento non ci sono SUB pubblicate.",
                ]
            )
        return _grading_response("\n".join(sections))

    sections: list[str] = [
        page_title("🎴", "Stato SUB Grading"),
        "",
        summary_row("📄", "Totale risultati", len(all_filtered_records)),
        page_indicator(page, total_pages),
    ]

    if search_query:
        sections.extend(
            [
                f"🔎 Ricerca: <code>{html.escape(search_query)}</code>",
            ]
        )

    sections.extend(
        [
            "",
            section_title("📊", "Riepilogo stati"),
            _build_status_summary(all_filtered_records),
            "",
            DIVIDER,
            "",
        ]
    )

    grouped_page_records = _group_page_records(page_records)

    for status, records in grouped_page_records:
        sections.append(
            f"🔹 <b>{html.escape(readable_status(status))}</b>"
        )
        sections.append("")

        for record in records:
            grading = html.escape(record.get("grading", ""))
            sub = html.escape(record.get("sub", ""))
            service = html.escape(record.get("service", ""))
            status_info = get_sub_status_info(record.get("status", ""))

            sections.extend(
                [
                    f"🏢 Grading: <b>{grading}</b>",
                    f"📦 SUB: <code>{sub}</code>",
                ]
            )

            if service:
                sections.append(f"💎 Servizio: <b>{service}</b>")

            sections.extend(
                [
                    f"<code>{status_info['progress']}</code> "
                    f"<b>{status_info['step']}/{status_info['total_steps']}</b> "
                    f"{status_info['emoji']}",
                    "",
                ]
            )

    return _grading_response("\n".join(sections))


async def _load_grading_records(
    context: ContextTypes.DEFAULT_TYPE,
    force_refresh: bool = False,
) -> list[dict]:
    if force_refresh:
        context.user_data.pop("grading_records", None)

    grading_records = context.user_data.get("grading_records")
    if grading_records is None:
        grading_records = await asyncio.to_thread(
            get_grading_records,
            force_refresh=force_refresh,
        )
        context.user_data["grading_records"] = grading_records

    return grading_records


def _parse_grading_page(query) -> int:
    data = query.data or ""
    if ":" in data:
        try:
            page = int(data.split(":", 1)[1])
            return max(page, 1)
        except ValueError:
            return 1
    return 1


async def _render_grading_page(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    force_refresh: bool = False,
    search_query: str | None = None,
    page: int = 1,
    chat_id: int | None = None,
    message_id: int | None = None,
) -> None:
    if force_refresh:
        context.user_data.pop("grading_records", None)

    grading_records = await _load_grading_records(
        context,
        force_refresh=force_refresh,
    )
    grading_records = _sort_grading_records(grading_records)
    filtered_records = _filter_grading_records(
        grading_records,
        search_query,
    )

    total_pages = max(
        1,
        (len(filtered_records) + GRADING_PER_PAGE - 1) // GRADING_PER_PAGE,
    )
    page = min(max(page, 1), total_pages)
    page_records = _get_grading_page(filtered_records, page)

    if search_query is not None:
        context.user_data["grading_search_query"] = search_query

    context.user_data["grading_page"] = page

    text = build_grading_text(
        page_records,
        filtered_records,
        search_query,
        page,
        total_pages,
    )

    keyboard = grading_keyboard(page, total_pages, search_query)

    if query is not None:
        try:
            await query.edit_message_text(
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        except BadRequest as error:
            if not _is_message_not_modified(error):
                raise
        return

    if chat_id is None or message_id is None:
        raise RuntimeError(
            "Informazioni messaggio mancanti per il rendering della ricerca SUB."
        )

    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )


async def show_grading(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    with start_flow("grading"):
        await _show_grading(update, context)


async def _show_grading(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    force_refresh = data == "grading_refresh"
    page = _parse_grading_page(query)
    search_query = context.user_data.get("grading_search_query")

    await _render_grading_page(
        query,
        context,
        force_refresh=force_refresh,
        search_query=search_query,
        page=page,
    )


async def start_grading_search(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    query = update.callback_query
    await query.answer()

    if query.message is None:
        await _grading_error(
            query,
            "Non è stato possibile iniziare la ricerca.",
            reply_markup=grading_keyboard(page=1, total_pages=1),
        )
        return ConversationHandler.END

    context.user_data["grading_search_request"] = {
        "chat_id": query.message.chat_id,
        "message_id": query.message.message_id,
    }

    await query.edit_message_text(
        text=_grading_response(
            page_title("🎴", "Stato SUB Grading")
            + "\n\n"
            + section_title("🔎", "Cerca SUB")
            + "\n\n"
            "Invia il testo con cui cercare una SUB, un GRADING o un servizio."
        ),
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "❌ Annulla",
                        callback_data="grading_search_cancel",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Indietro",
                        callback_data="menu_grading",
                    )
                ],
            ]
        ),
        parse_mode="HTML",
    )

    return GRADING_SEARCH


async def receive_grading_search(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    if update.message is None:
        return GRADING_SEARCH

    search_query = str(update.message.text or "").strip()
    if not search_query:
        await update.message.reply_text(
            with_footer(
                "⚠️ <b>Termine non valido</b>\n\n"
                "Inserisci un termine per la ricerca."
            ),
            parse_mode="HTML",
        )
        return GRADING_SEARCH

    request_data = context.user_data.pop("grading_search_request", None)
    if not request_data:
        await update.message.reply_text(
            operation_unavailable(
                "La ricerca non è più attiva."
            ),
            parse_mode="HTML",
        )
        return ConversationHandler.END

    await _render_grading_page(
        None,
        context,
        search_query=search_query,
        page=1,
        chat_id=request_data["chat_id"],
        message_id=request_data["message_id"],
    )

    return ConversationHandler.END


async def cancel_grading_search(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data.pop("grading_search_query", None)
    context.user_data.pop("grading_search_request", None)

    await _render_grading_page(
        query,
        context,
        search_query=None,
        page=1,
    )

    return ConversationHandler.END


def build_grading_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                start_grading_search,
                pattern=r"^grading_search$",
            )
        ],
        states={
            GRADING_SEARCH: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_grading_search,
                )
            ]
        },
        fallbacks=[
            CallbackQueryHandler(
                cancel_grading_search,
                pattern=r"^grading_search_cancel$",
            ),
            CallbackQueryHandler(
                show_grading,
                pattern=r"^menu_grading$",
            ),
            CallbackQueryHandler(
                show_grading,
                pattern=r"^grading_refresh$",
            ),
        ],
        allow_reentry=True,
    )
