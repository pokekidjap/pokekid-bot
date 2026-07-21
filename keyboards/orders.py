from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def orders_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(
                "🟢 Vedi disponibili",
                callback_data="orders_available",
            )
        ],
        [
            InlineKeyboardButton(
                "📋 Vedi tutti gli ordini",
                callback_data="orders_all",
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Torna al menu",
                callback_data="menu_home",
            )
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


def orders_back_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(
                "⬅️ Torna agli ordini",
                callback_data="menu_orders",
            )
        ],
        [
            InlineKeyboardButton(
                "🏠 Menu principale",
                callback_data="menu_home",
            )
        ],
    ]

    return InlineKeyboardMarkup(keyboard)