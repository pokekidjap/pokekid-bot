from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📦 I miei ordini", callback_data="menu_orders"),
            InlineKeyboardButton("🎴 Le mie SUB", callback_data="menu_grading"),
        ],
        [InlineKeyboardButton("👤 Profilo", callback_data="menu_profile")],
    ])
