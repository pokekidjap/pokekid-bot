from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes


async def show_grading(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "⬅️ Torna al menu",
                    callback_data="menu_home",
                )
            ]
        ]
    )

    await query.edit_message_text(
        text=(
            "🎴 <b>Le mie SUB Grading</b>\n\n"
            "Questa sezione sarà disponibile a breve."
        ),
        reply_markup=keyboard,
        parse_mode="HTML",
    )