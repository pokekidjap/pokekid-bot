from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from services.ui import shorten_button_text
from services.shipping_v2_session import (
    item_callback_data,
    page_callback_data,
    page_count,
    paginated_items,
)
from services.shipping_v2_join_session import (
    join_item_callback_data,
    join_page_callback_data,
    join_page_count,
    paginated_join_items,
)


ORDER_BUTTON_MAX_LENGTH = 42


def orders_keyboard(
    shipping_v2_enabled: bool = False,
) -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(
                "🟢 Articoli disponibili",
                callback_data="orders_available",
            )
        ],
    ]
    if shipping_v2_enabled:
        keyboard.append([
            InlineKeyboardButton(
                "📦 Unisci a una spedizione",
                callback_data="shipping_v2_join",
            )
        ])
    keyboard.extend([
        [
            InlineKeyboardButton(
                "📋 Tutti gli ordini",
                callback_data="orders_all",
            )
        ],
        [
            InlineKeyboardButton(
                "🏠 Menu principale",
                callback_data="menu_home",
            )
        ],
    ])

    return InlineKeyboardMarkup(keyboard)


def orders_back_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(
                "⬅️ Indietro",
                callback_data="menu_orders",
            ),
            InlineKeyboardButton(
                "🏠 Menu principale",
                callback_data="menu_home",
            ),
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


def shipping_profile_incomplete_keyboard(
    has_profile: bool,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📝 Completa profilo",
                    callback_data=(
                        "profile_edit_data"
                        if has_profile
                        else "profile_add_data"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Indietro",
                    callback_data="orders_available",
                ),
                InlineKeyboardButton(
                    "🏠 Menu principale",
                    callback_data="menu_home",
                ),
            ],
        ]
    )


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
                    "◀️ Precedente",
                    callback_data=f"orders_all:{page - 1}",
                )
            )
        if page < total_pages:
            row.append(
                InlineKeyboardButton(
                    "Successiva ▶️",
                    callback_data=f"orders_all:{page + 1}",
                )
            )
        keyboard.append(row)

    keyboard.extend(
        [
            [
                InlineKeyboardButton(
                    "⬅️ Indietro",
                    callback_data="menu_orders",
                ),
                InlineKeyboardButton(
                    "🏠 Menu principale",
                    callback_data="menu_home",
                ),
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

        prefix = f"{icon} "
        suffix = f" ×{quantity}"
        name_length = ORDER_BUTTON_MAX_LENGTH - len(prefix) - len(suffix)
        short_name = shorten_button_text(name, max(2, name_length))
        button_text = f"{prefix}{short_name}{suffix}"

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
                    "🔄 Aggiorna",
                    callback_data="orders_refresh",
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Indietro",
                    callback_data="menu_orders",
                ),
                InlineKeyboardButton(
                    "🏠 Menu principale",
                    callback_data="menu_home",
                ),
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
                    "⬅️ Indietro",
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
                "⬅️ Indietro",
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


def v2_available_orders_keyboard(
    available_items: list[dict],
    selected_item_ids: set[str],
    page: int = 1,
) -> InlineKeyboardMarkup:
    keyboard = []
    total_pages = page_count(available_items)
    current = min(max(1, int(page)), total_pages)
    for item in paginated_items(available_items, current):
        raw_item_id = str(item.get("ID_ARTICOLO", "")).strip()
        item_id = raw_item_id.upper()
        callback_data = item_callback_data(raw_item_id)
        quantity = str(item.get("QUANTITA", "")).strip() or "1"
        icon = "✅" if item_id in selected_item_ids else "⬜"
        prefix = f"{icon} "
        suffix = f" ×{quantity}"
        name_length = ORDER_BUTTON_MAX_LENGTH - len(prefix) - len(suffix)
        name = shorten_button_text(
            str(item.get("OGGETTO", "")).strip(),
            max(2, name_length),
        )
        keyboard.append([
            InlineKeyboardButton(
                f"{prefix}{name}{suffix}",
                callback_data=callback_data,
            )
        ])

    navigation = []
    if current > 1:
        navigation.append(
            InlineKeyboardButton(
                "◀️ Precedente",
                callback_data=page_callback_data(current - 1),
            )
        )
    if current < total_pages:
        navigation.append(
            InlineKeyboardButton(
                "Successiva ▶️",
                callback_data=page_callback_data(current + 1),
            )
        )
    if navigation:
        keyboard.append(navigation)

    if selected_item_ids:
        keyboard.append([
            InlineKeyboardButton(
                "📦 Continua con la spedizione",
                callback_data="shipping_v2_continue",
            )
        ])
    keyboard.extend([
        [
            InlineKeyboardButton(
                "🔄 Aggiorna",
                callback_data="orders_refresh",
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Indietro",
                callback_data="menu_orders",
            ),
            InlineKeyboardButton(
                "🏠 Menu principale",
                callback_data="menu_home",
            ),
        ],
    ])
    return InlineKeyboardMarkup(keyboard)


def v2_availability_changed_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🟢 Torna agli articoli",
                callback_data="orders_available",
            )
        ],
        [
            InlineKeyboardButton(
                "🏠 Menu principale",
                callback_data="menu_home",
            )
        ],
    ])


def v2_active_draft_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "▶️ Riprendi",
                callback_data="shipping_v2_resume",
            )
        ],
        [
            InlineKeyboardButton(
                "❌ Annulla bozza",
                callback_data="shipping_v2_cancel_draft",
            )
        ],
        [
            InlineKeyboardButton(
                "🏠 Menu principale",
                callback_data="menu_home",
            )
        ],
    ])


def v2_confirmed_shipping_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🚚 Le mie spedizioni",
                callback_data="shipping_history_user",
            )
        ],
        [
            InlineKeyboardButton(
                "🏠 Menu principale",
                callback_data="menu_home",
            )
        ],
    ])


def v2_shipping_carriers_keyboard(
    shipping_methods: list[dict],
) -> InlineKeyboardMarkup:
    keyboard = []
    for index, method in enumerate(shipping_methods):
        keyboard.append([
            InlineKeyboardButton(
                f"🚚 {method['name']} – € {method['price']:.2f}",
                callback_data=f"shipping_v2_carrier:{index}",
            )
        ])
    keyboard.extend([
        [
            InlineKeyboardButton(
                "⬅️ Cambia articoli",
                callback_data="shipping_v2_change_items",
            )
        ],
        [
            InlineKeyboardButton(
                "❌ Annulla",
                callback_data="shipping_v2_cancel",
            )
        ],
    ])
    return InlineKeyboardMarkup(keyboard)


def v2_shipping_summary_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📎 Invia ricevuta pagamento",
                callback_data="shipping_payment",
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Cambia articoli",
                callback_data="shipping_v2_change_items",
            )
        ],
        [
            InlineKeyboardButton(
                "❌ Annulla",
                callback_data="shipping_v2_cancel",
            )
        ],
    ])


def v2_shipping_receipt_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "❌ Annulla",
                callback_data="shipping_receipt_cancel",
            )
        ]
    ])


def v2_retry_cancel_keyboard(callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔄 Riprova",
                callback_data=callback_data,
            )
        ],
        [
            InlineKeyboardButton(
                "🏠 Menu principale",
                callback_data="menu_home",
            )
        ],
    ])


def v2_join_username_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "❌ Annulla",
                callback_data="join_v2_cancel",
            )
        ]
    ])


def v2_join_selection_keyboard(
    available_items: list[dict],
    selected_item_ids: set[str],
    page: int = 1,
) -> InlineKeyboardMarkup:
    keyboard = []
    total_pages = join_page_count(available_items)
    current = min(max(1, int(page)), total_pages)
    for item in paginated_join_items(available_items, current):
        raw_item_id = str(item.get("ID_ARTICOLO", "")).strip()
        item_id = raw_item_id.upper()
        quantity = str(item.get("QUANTITA", "")).strip() or "1"
        icon = "✅" if item_id in selected_item_ids else "⬜"
        prefix = f"{icon} "
        suffix = f" ×{quantity}"
        name_length = ORDER_BUTTON_MAX_LENGTH - len(prefix) - len(suffix)
        name = shorten_button_text(
            str(item.get("OGGETTO", "")).strip(),
            max(2, name_length),
        )
        keyboard.append([
            InlineKeyboardButton(
                f"{prefix}{name}{suffix}",
                callback_data=join_item_callback_data(raw_item_id),
            )
        ])

    navigation = []
    if current > 1:
        navigation.append(
            InlineKeyboardButton(
                "◀️ Precedente",
                callback_data=join_page_callback_data(current - 1),
            )
        )
    if current < total_pages:
        navigation.append(
            InlineKeyboardButton(
                "Successiva ▶️",
                callback_data=join_page_callback_data(current + 1),
            )
        )
    if navigation:
        keyboard.append(navigation)

    if selected_item_ids:
        keyboard.append([
            InlineKeyboardButton(
                "✅ Conferma aggiunta",
                callback_data="join_v2_confirm",
            )
        ])
    keyboard.extend([
        [
            InlineKeyboardButton(
                "🔄 Aggiorna",
                callback_data="join_v2_refresh",
            )
        ],
        [
            InlineKeyboardButton(
                "❌ Annulla",
                callback_data="join_v2_cancel",
            )
        ],
    ])
    return InlineKeyboardMarkup(keyboard)


def v2_join_completed_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📦 I miei ordini",
                callback_data="menu_orders",
            )
        ],
        [
            InlineKeyboardButton(
                "🏠 Menu principale",
                callback_data="menu_home",
            )
        ],
    ])
