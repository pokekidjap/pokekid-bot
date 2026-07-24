from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📦 I miei ordini", callback_data="menu_orders"),
            InlineKeyboardButton("🎴 SUB Grading", callback_data="menu_grading"),
        ],
        [InlineKeyboardButton("👤 Il mio profilo", callback_data="menu_profile")],
    ])
