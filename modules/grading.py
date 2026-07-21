import html
import logging

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import ContextTypes

from services.grading import get_grading_records
from services.status import get_sub_status_info


logger = logging.getLogger(__name__)


def grading_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔄 Aggiorna",
                    callback_data="menu_grading",
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Torna al menu",
                    callback_data="menu_home",
                )
            ],
        ]
    )


def build_grading_text(
    grading_records: list[dict],
) -> str:
    """
    Crea il messaggio Telegram con tutte le SUB.
    """
    if not grading_records:
        return (
            "🎴 <b>STATO SUB GRADING</b>\n\n"
            "Al momento non ci sono SUB pubblicate."
        )

    sections = [
        "🎴 <b>STATO SUB GRADING</b>",
        "",
    ]

    for index, record in enumerate(
        grading_records
    ):
        grading = html.escape(
            record.get("grading", "")
        )

        sub = html.escape(
            record.get("sub", "")
        )

        service = html.escape(
            record.get("service", "")
        )

        status_info = get_sub_status_info(
            record.get("status", "")
        )

        status_name = html.escape(
            status_info["name"]
        )

        emoji = status_info["emoji"]
        progress = status_info["progress"]
        step = status_info["step"]
        total_steps = status_info["total_steps"]

        if index > 0:
            sections.extend(
                [
                    "",
                    "━━━━━━━━━━━━━━━━━━",
                    "",
                ]
            )

        sections.extend(
            [
                f"🏢 <b>{grading}</b>",
                f"📦 {sub}",
            ]
        )

        if service:
            sections.append(
                f"💎 Servizio: <b>{service}</b>"
            )

        sections.extend(
            [
                "",
                f"<code>{progress}</code> "
                f"<b>{step}/{total_steps}</b>",
                "",
                f"{emoji} <b>{status_name}</b>",
            ]
        )

        if status_info["name"] == "CHIUSA":
            sections.extend(
                [
                    "",
                    "📦 La SUB è rientrata ed è stata chiusa.",
                ]
            )

    return "\n".join(sections)


async def show_grading(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    try:
        grading_records = get_grading_records()

        text = build_grading_text(
            grading_records
        )

    except Exception:
        logger.exception(
            "Errore durante la lettura delle SUB."
        )

        text = (
            "⚠️ <b>Errore temporaneo</b>\n\n"
            "Non è stato possibile recuperare "
            "lo stato delle SUB.\n"
            "Riprova tra qualche minuto."
        )

    await query.edit_message_text(
        text=text,
        reply_markup=grading_keyboard(),
        parse_mode="HTML",
    )