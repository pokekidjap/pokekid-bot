from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def grading_keyboard(
    page: int,
    total_pages: int,
    search_query: str | None = None,
) -> InlineKeyboardMarkup:
    keyboard: list[list[InlineKeyboardButton]] = []

    if total_pages > 1:
        pagination_row: list[InlineKeyboardButton] = []

        if page > 1:
            pagination_row.append(
                InlineKeyboardButton(
                    "⬅️ Pagina precedente",
                    callback_data=f"grading_page:{page - 1}",
                )
            )

        if page < total_pages:
            pagination_row.append(
                InlineKeyboardButton(
                    "➡️ Pagina successiva",
                    callback_data=f"grading_page:{page + 1}",
                )
            )

        keyboard.append(pagination_row)

    keyboard.append(
        [
            InlineKeyboardButton(
                "🔄 Aggiorna",
                callback_data="grading_refresh",
            ),
            InlineKeyboardButton(
                "🔎 Cerca SUB",
                callback_data="grading_search",
            ),
        ]
    )

    if search_query:
        keyboard.append(
            [
                InlineKeyboardButton(
                    "❌ Cancella ricerca",
                    callback_data="menu_grading",
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                "⬅️ Torna al menu",
                callback_data="menu_home",
            )
        ]
    )

    return InlineKeyboardMarkup(keyboard)
