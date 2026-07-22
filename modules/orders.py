import asyncio
import logging
import time
from html import escape

from services.perf import start_flow
from telegram import Update
from telegram.ext import ContextTypes

from keyboards.orders import (
    available_orders_keyboard,
    orders_back_keyboard,
    orders_keyboard,
    orders_pagination_keyboard,
    shipping_carriers_keyboard,
    shipping_summary_keyboard,
)
from services.bot_db import (
    get_active_shipping_methods,
    get_current_datetime,
    get_paypal_email,
    get_profile,
)
from services.sheets import get_user_orders
from services.ui import compact_error, page_title, with_footer


logger = logging.getLogger(__name__)
ORDERS_PER_PAGE = 6


def _orders_response(text: str) -> str:
    return with_footer(text)


def _orders_error(
    query,
    message: str,
    reply_markup=None,
) -> None:
    return query.edit_message_text(
        compact_error(message),
        reply_markup=reply_markup,
        parse_mode="HTML",
    )


def _clear_shipping_session_data(
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    shipping_keys = [
        "available_orders",
        "selected_order_rows",
        "selected_orders",
        "shipping_profile",
        "shipping_methods",
        "selected_carrier",
        "waiting_shipping_receipt",
    ]

    for key in shipping_keys:
        context.user_data.pop(key, None)


def _order_icon(status: str) -> str:
    return "🟢" if status == "IN MAGAZZINO" else "🟡"


def _get_orders_page(
    orders: list[dict],
    page: int,
) -> list[dict]:
    start = (page - 1) * ORDERS_PER_PAGE
    end = start + ORDERS_PER_PAGE
    return orders[start:end]


def _parse_orders_page(query) -> int:
    data = query.data or ""
    if ":" in data:
        try:
            page = int(data.split(":", 1)[1])
            return max(page, 1)
        except ValueError:
            return 1
    return 1


def get_available_orders(
    username: str | None,
) -> list[dict]:
    """
    Restituisce solamente gli ordini
    con stato IN MAGAZZINO.
    """
    orders = get_user_orders(
        username
    )

    return [
        order
        for order in orders
        if order["status"] == "IN MAGAZZINO"
    ]


def build_available_orders_text(
    available_orders: list[dict],
    selected_rows: set[int],
) -> str:
    """
    Crea il testo della schermata
    di selezione degli articoli.
    """
    if not available_orders:
        return (
            "🟢 <b>Ordini disponibili</b>\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Al momento non hai articoli disponibili "
            "per la spedizione.\n\n"
            "Non appena uno o più ordini saranno pronti, "
            "compariranno in questa sezione.\n\n"
            "━━━━━━━━━━━━━━━━━━"
        )

    total_available = sum(
        order["quantity"]
        for order in available_orders
    )

    total_selected = sum(
        order["quantity"]
        for order in available_orders
        if order["row_number"] in selected_rows
    )

    return with_footer(
        "🟢 <b>Ordini disponibili</b>\n\n"
        "Seleziona gli articoli che vuoi ricevere.\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"📦 Articoli disponibili: "
        f"<b>{total_available}</b>\n"
        f"✅ Articoli selezionati: "
        f"<b>{total_selected}</b>\n\n"
        "Premi sui pulsanti qui sotto per "
        "selezionare o deselezionare gli articoli.\n\n"
        "━━━━━━━━━━━━━━━━━━"
    )


async def show_orders_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    with start_flow("orders_menu"):
        await query.answer()
        await query.edit_message_text(
            with_footer(
                page_title(
                    "📦",
                    "I miei ordini",
                    "Scegli cosa vuoi consultare",
                )
            ),
            reply_markup=orders_keyboard(),
            parse_mode="HTML",
        )


async def show_available_orders(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    username = query.from_user.username

    with start_flow("orders_available"):
        await query.answer()

        if not username:
            await query.edit_message_text(
                text=_orders_response(
                    "⚠️ <b>Username Telegram non disponibile</b>\n\n"
                    "Per consultare i tuoi ordini devi impostare uno username nelle impostazioni di Telegram."
                ),
                reply_markup=orders_back_keyboard(),
                parse_mode="HTML",
            )
            return

        try:
            available_orders = get_available_orders(
                username
            )

        except Exception:
            logger.exception(
                "Errore durante la lettura degli ordini disponibili"
            )

            await _orders_error(
                query,
                "Riprova tra qualche minuto.",
                reply_markup=orders_back_keyboard(),
            )
            return

        context.user_data[
            "available_orders"
        ] = available_orders

        context.user_data[
            "selected_order_rows"
        ] = set()

        text = build_available_orders_text(
            available_orders,
            set(),
        )

        await query.edit_message_text(
            text=_orders_response(text),
            reply_markup=available_orders_keyboard(
                available_orders,
                set(),
            ),
            parse_mode="HTML",
        )


async def toggle_available_order(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    await query.answer()

    try:
        row_number = int(
            query.data.split(
                ":",
                1,
            )[1]
        )

    except (
        IndexError,
        ValueError,
    ):
        await query.answer(
            "Articolo non valido.",
            show_alert=True,
        )
        return

    available_orders = context.user_data.get(
        "available_orders",
        [],
    )

    valid_rows = {
        order["row_number"]
        for order in available_orders
    }

    if row_number not in valid_rows:
        await query.answer(
            "Questo articolo non è più disponibile.",
            show_alert=True,
        )
        return

    selected_rows = context.user_data.get(
        "selected_order_rows",
        set(),
    )

    if not isinstance(
        selected_rows,
        set,
    ):
        selected_rows = set(
            selected_rows
        )

    if row_number in selected_rows:
        selected_rows.remove(
            row_number
        )

    else:
        selected_rows.add(
            row_number
        )

    context.user_data[
        "selected_order_rows"
    ] = selected_rows

    text = build_available_orders_text(
        available_orders,
        selected_rows,
    )

    await query.edit_message_text(
        text=text,
        reply_markup=available_orders_keyboard(
            available_orders,
            selected_rows,
        ),
        parse_mode="HTML",
    )


async def continue_shipping_request(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    user = query.from_user

    with start_flow("shipping_start"):
        await query.answer()

        available_orders = context.user_data.get(
            "available_orders",
            [],
        )

    selected_rows = context.user_data.get(
        "selected_order_rows",
        set(),
    )

    selected_orders = [
        order
        for order in available_orders
        if order["row_number"] in selected_rows
    ]

    if not selected_orders:
        await query.answer(
            "Seleziona almeno un articolo.",
            show_alert=True,
        )
        return

    try:
        profile = await asyncio.to_thread(
            get_profile,
            user.id,
        )

    except Exception:
        logger.exception(
            "Errore durante la lettura del profilo"
        )

        await _orders_error(
            query,
            "Impossibile leggere i dati di spedizione.",
            reply_markup=orders_back_keyboard(),
        )
        return

    if not profile:
        await query.edit_message_text(
            text=_orders_response(
                "⚠️ <b>Dati di spedizione mancanti</b>\n\n"
                "Prima di richiedere una spedizione devi inserire i tuoi dati nella sezione Profilo."
            ),
            reply_markup=orders_back_keyboard(),
            parse_mode="HTML",
        )
        return

    try:
        shipping_methods = (
            get_active_shipping_methods()
        )

    except Exception:
        logger.exception(
            "Errore durante la lettura dei corrieri"
        )

        await _orders_error(
            query,
            "Riprova tra qualche minuto.",
            reply_markup=orders_back_keyboard(),
        )
        return

    if not shipping_methods:
        await query.edit_message_text(
            text=_orders_response(
                "⚠️ <b>Nessun corriere disponibile</b>\n\n"
                "Al momento non risultano metodi di spedizione attivi."
            ),
            reply_markup=orders_back_keyboard(),
            parse_mode="HTML",
        )
        return

    context.user_data[
        "selected_orders"
    ] = selected_orders

    context.user_data[
        "shipping_profile"
    ] = profile

    context.user_data[
        "shipping_methods"
    ] = shipping_methods

    context.user_data[
        "shipping_selection_timestamp"
    ] = get_current_datetime()

    product_lines = []

    for order in selected_orders:
        product_lines.append(
            "🎴 "
            f"<b>{escape(order['name'])}</b> "
            f"×{order['quantity']}"
        )

    text = (
        "🚚 <b>Scegli il corriere</b>\n\n"
        "Articoli selezionati:\n\n"
        + "\n".join(product_lines)
        + "\n\n━━━━━━━━━━━━━━━━━━\n\n"
        "Seleziona il metodo di spedizione:"
    )

    await query.edit_message_text(
        text=_orders_response(text),
        reply_markup=shipping_carriers_keyboard(
            shipping_methods
        ),
        parse_mode="HTML",
    )


async def select_shipping_carrier(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    await query.answer()

    try:
        carrier_index = int(
            query.data.split(
                ":",
                1,
            )[1]
        )

    except (
        IndexError,
        ValueError,
    ):
        await query.answer(
            "Corriere non valido.",
            show_alert=True,
        )
        return

    shipping_methods = context.user_data.get(
        "shipping_methods",
        [],
    )

    if (
        carrier_index < 0
        or carrier_index >= len(
            shipping_methods
        )
    ):
        await query.answer(
            "Corriere non disponibile.",
            show_alert=True,
        )
        return

    selected_carrier = shipping_methods[
        carrier_index
    ]

    context.user_data[
        "selected_carrier"
    ] = selected_carrier

    selected_orders = context.user_data.get(
        "selected_orders",
        [],
    )

    profile = context.user_data.get(
        "shipping_profile",
        {},
    )

    paypal_email = get_paypal_email()

    product_lines = []

    for order in selected_orders:
        product_lines.append(
            "🎴 "
            f"<b>{escape(order['name'])}</b> "
            f"×{order['quantity']}"
        )

    carrier_name = escape(
        selected_carrier["name"]
    )

    carrier_price = selected_carrier[
        "price"
    ]

    text = (
        "📦 <b>Riepilogo spedizione</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        + "\n".join(product_lines)
        + "\n\n━━━━━━━━━━━━━━━━━━\n\n"
        f"🚚 Corriere: <b>{carrier_name}</b>\n"
        f"💶 Costo: <b>€ {carrier_price:.2f}</b>\n\n"
        "📍 <b>Indirizzo di spedizione</b>\n"
        f"{escape(profile.get('NOME', ''))}\n"
        f"{escape(profile.get('INDIRIZZO', ''))}\n"
        f"{escape(profile.get('CAP', ''))} "
        f"{escape(profile.get('CITTA', ''))} "
        f"({escape(profile.get('PROVINCIA', ''))})\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Effettua il pagamento tramite PayPal a:\n\n"
        f"💳 <code>{escape(paypal_email)}</code>\n\n"
        "Dopo il pagamento premi il pulsante "
        "qui sotto e invia la ricevuta."
    )

    await query.edit_message_text(
        text=with_footer(text),
        reply_markup=shipping_summary_keyboard(),
        parse_mode="HTML",
    )


async def cancel_shipping_request(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    await query.answer()

    _clear_shipping_session_data(context)

    await query.edit_message_text(
        text=with_footer(
            "❌ <b>Richiesta di spedizione annullata</b>\n\n"
            "Nessuna richiesta è stata salvata."
        ),
        reply_markup=orders_back_keyboard(),
        parse_mode="HTML",
    )


async def show_all_orders(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    username = query.from_user.username

    with start_flow("orders_all"):
        await query.answer()

        if not username:
            await query.edit_message_text(
                text=(
                    "⚠️ <b>Username Telegram "
                    "non disponibile</b>\n\n"
                    "Per consultare i tuoi ordini devi "
                    "impostare uno username nelle "
                    "impostazioni di Telegram."
                ),
                reply_markup=orders_back_keyboard(),
                parse_mode="HTML",
            )
            return

    try:
        orders = get_user_orders(
            username
        )

    except Exception:
        logger.exception(
            "Errore durante la lettura degli ordini"
        )

        await query.edit_message_text(
            text=(
                "⚠️ <b>Servizio momentaneamente "
                "non disponibile</b>\n\n"
                "Riprova tra qualche minuto."
            ),
            reply_markup=orders_back_keyboard(),
            parse_mode="HTML",
        )
        return

    total_items = sum(
        order["quantity"]
        for order in orders
    )

    available_items = sum(
        order["quantity"]
        for order in orders
        if order["status"] == "IN MAGAZZINO"
    )

    pending_items = (
        total_items
        - available_items
    )

    display_username = (
        f"@{username}"
    )

    if not orders:
        text = (
            "📦 <b>I miei ordini</b>\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 <b>{escape(display_username)}</b>\n\n"
            "Non risultano ordini associati al tuo account.\n\n"
            "━━━━━━━━━━━━━━━━━━"
        )
        keyboard = orders_back_keyboard()
    else:
        total_pages = max(
            1,
            (len(orders) + ORDERS_PER_PAGE - 1) // ORDERS_PER_PAGE,
        )
        page = _parse_orders_page(query)
        page = min(page, total_pages)
        page_orders = _get_orders_page(orders, page)

        articles = []
        for order in page_orders:
            icon = _order_icon(order["status"])
            quantity_text = (
                f" ×{order['quantity']}"
                if order["quantity"] > 1
                else ""
            )
            articles.append(
                f"{icon} "
                f"<b>{escape(order['name'])}</b>"
                f"{quantity_text}"
                f" · {escape(order['status'])}"
            )

        text = (
            "📦 <b>I miei ordini</b>\n\n"
            f"👤 <b>{escape(display_username)}</b>\n"
            f"📦 Totale articoli: <b>{total_items}</b> · "
            f"🟢 Disponibili: <b>{available_items}</b> · "
            f"🟡 In attesa: <b>{pending_items}</b>\n\n"
            f"Pagina <b>{page}</b> di <b>{total_pages}</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            + "\n\n".join(articles)
        )
        keyboard = orders_pagination_keyboard(page, total_pages)

    await query.edit_message_text(
        text=_orders_response(text),
        reply_markup=keyboard,
        parse_mode="HTML",
    )