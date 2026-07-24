from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def profile_keyboard(
    has_profile: bool,
    is_complete: bool | None = None,
) -> InlineKeyboardMarkup:
    """
    Tastiera principale della sezione Profilo.
    """
    keyboard = []

    if has_profile and is_complete is not False:
        keyboard.append(
            [
                InlineKeyboardButton(
                    "📍 Dati di spedizione",
                    callback_data="profile_shipping_data",
                )
            ]
        )

        keyboard.append(
            [
                InlineKeyboardButton(
                    "✏️ Modifica",
                    callback_data="profile_edit_data",
                )
            ]
        )

        keyboard.append(
            [
                InlineKeyboardButton(
                    "🗑 Elimina",
                    callback_data="profile_delete_confirm",
                )
            ]
        )

    elif has_profile:
        keyboard.append(
            [
                InlineKeyboardButton(
                    "📝 Completa profilo",
                    callback_data="profile_edit_data",
                )
            ]
        )

    else:
        keyboard.append(
            [
                InlineKeyboardButton(
                    "➕ Inserisci dati",
                    callback_data="profile_add_data",
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                "🚚 Le mie spedizioni",
                callback_data="profile_shipments",
            )
        ]
    )

    keyboard.append(
        [
            InlineKeyboardButton(
                "🏠 Menu principale",
                callback_data="menu_home",
            )
        ]
    )

    return InlineKeyboardMarkup(keyboard)


def profile_data_keyboard() -> InlineKeyboardMarkup:
    """
    Tastiera visualizzata sotto i dati di spedizione.
    """
    keyboard = [
        [
            InlineKeyboardButton(
                "✏️ Modifica",
                callback_data="profile_edit_data",
            ),
            InlineKeyboardButton(
                "🗑 Elimina",
                callback_data="profile_delete_confirm",
            ),
        ],
        [
            InlineKeyboardButton(
                "⬅️ Indietro",
                callback_data="menu_profile",
            )
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


def profile_delete_confirmation_keyboard() -> InlineKeyboardMarkup:
    """
    Tastiera per confermare l'eliminazione dei dati.
    """
    keyboard = [
        [
            InlineKeyboardButton(
                "✅ Elimina",
                callback_data="profile_delete",
            )
        ],
        [
            InlineKeyboardButton(
                "❌ Annulla",
                callback_data="menu_profile",
            )
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


def profile_back_keyboard() -> InlineKeyboardMarkup:
    """
    Tastiera semplice per tornare al Profilo.
    """
    keyboard = [
        [
            InlineKeyboardButton(
                "⬅️ Indietro",
                callback_data="menu_profile",
            )
        ]
    ]

    return InlineKeyboardMarkup(keyboard)


def profile_form_cancel_keyboard() -> InlineKeyboardMarkup:
    """
    Tastiera mostrata durante l'inserimento dei dati.
    """
    keyboard = [
        [
            InlineKeyboardButton(
                "❌ Annulla",
                callback_data="profile_form_cancel",
            )
        ]
    ]

    return InlineKeyboardMarkup(keyboard)


def profile_form_review_keyboard() -> InlineKeyboardMarkup:
    """
    Tastiera del riepilogo finale.
    """
    keyboard = [
        [
            InlineKeyboardButton(
                "✅ Salva dati",
                callback_data="profile_form_save",
            )
        ],
        [
            InlineKeyboardButton(
                "✏️ Ricomincia",
                callback_data="profile_form_restart",
            )
        ],
        [
            InlineKeyboardButton(
                "❌ Annulla",
                callback_data="profile_form_cancel",
            )
        ],
    ]

    return InlineKeyboardMarkup(keyboard)
