from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def home_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(
                "📦 I miei ordini",
                callback_data="menu_orders",
            )
        ],
        [
            InlineKeyboardButton(
                "🎴 Le mie SUB Grading",
                callback_data="menu_grading",
            )
        ],
    ]

    return InlineKeyboardMarkup(keyboard)