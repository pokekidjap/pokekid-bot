from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)


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


def orders_pagination_keyboard(
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    keyboard = []

    if page > 1 or page < total_pages:
        row = []
        if page > 1:
            row.append(
                InlineKeyboardButton(
                    "⬅️ Pagina precedente",
                    callback_data=f"orders_all:{page - 1}",
                )
            )
        if page < total_pages:
            row.append(
                InlineKeyboardButton(
                    "➡️ Pagina successiva",
                    callback_data=f"orders_all:{page + 1}",
                )
            )
        keyboard.append(row)

    keyboard.extend(
        [
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
    )

    return InlineKeyboardMarkup(keyboard)


def available_orders_keyboard(
    available_orders: list[dict],
    selected_rows: set[int],
) -> InlineKeyboardMarkup:
    """
    Crea la tastiera per selezionare
    gli articoli disponibili.
    """
    keyboard = []

    for order in available_orders:
        row_number = order["row_number"]
        quantity = order["quantity"]
        name = order["name"]

        if row_number in selected_rows:
            icon = "✅"
        else:
            icon = "⬜"

        button_text = (
            f"{icon} {name} ×{quantity}"
        )

        keyboard.append(
            [
                InlineKeyboardButton(
                    button_text,
                    callback_data=(
                        f"order_toggle:{row_number}"
                    ),
                )
            ]
        )

    if available_orders:
        keyboard.append(
            [
                InlineKeyboardButton(
                    "📦 Continua con la spedizione",
                    callback_data="shipping_continue",
                )
            ]
        )

    keyboard.extend(
        [
            [
                InlineKeyboardButton(
                    "🔄 Aggiorna elenco",
                    callback_data="orders_available",
                )
            ],
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
    )

    return InlineKeyboardMarkup(keyboard)


def shipping_carriers_keyboard(
    shipping_methods: list[dict],
) -> InlineKeyboardMarkup:
    """
    Mostra i corrieri attivi letti
    dal foglio CONFIG.
    """
    keyboard = []

    for index, method in enumerate(
        shipping_methods
    ):
        name = method["name"]
        price = method["price"]

        keyboard.append(
            [
                InlineKeyboardButton(
                    f"🚚 {name} – € {price:.2f}",
                    callback_data=(
                        f"shipping_carrier:{index}"
                    ),
                )
            ]
        )

    keyboard.extend(
        [
            [
                InlineKeyboardButton(
                    "⬅️ Torna agli articoli",
                    callback_data="orders_available",
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ Annulla",
                    callback_data="shipping_cancel",
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(keyboard)


def shipping_summary_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(
                "📎 Invia ricevuta pagamento",
                callback_data="shipping_payment",
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Cambia corriere",
                callback_data="shipping_continue",
            )
        ],
        [
            InlineKeyboardButton(
                "❌ Annulla",
                callback_data="shipping_cancel",
            )
        ],
    ]

    return InlineKeyboardMarkup(keyboard)