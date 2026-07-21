import logging

from telegram import Update
from telegram.ext import ContextTypes

from keyboards.orders import orders_back_keyboard, orders_keyboard
from services.sheets import get_user_orders


logger = logging.getLogger(__name__)


async def show_orders_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    await query.edit_message_text(
        text=(
            "📦 <b>I miei ordini</b>\n\n"
            "Scegli cosa vuoi consultare:"
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

    if not username:
        await query.edit_message_text(
            text=(
                "⚠️ <b>Username Telegram non disponibile</b>\n\n"
                "Per consultare i tuoi ordini devi impostare "
                "uno username nelle impostazioni di Telegram."
            ),
            reply_markup=orders_back_keyboard(),
            parse_mode="HTML",
        )
        return

    try:
        orders = get_user_orders(username)

    except Exception:
        logger.exception("Errore durante la lettura degli ordini")

        await query.edit_message_text(
            text=(
                "⚠️ <b>Servizio momentaneamente non disponibile</b>\n\n"
                "Riprova tra qualche minuto."
            ),
            reply_markup=orders_back_keyboard(),
            parse_mode="HTML",
        )
        return

    available_orders = [
        order
        for order in orders
        if order["status"] == "IN MAGAZZINO"
    ]

    if not available_orders:
        text = (
            "🟢 <b>Ordini disponibili</b>\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Al momento non hai articoli disponibili "
            "per la spedizione.\n\n"
            "Non appena uno o più ordini saranno pronti, "
            "compariranno in questa sezione.\n\n"
            "━━━━━━━━━━━━━━━━━━"
        )

    else:
        articles = []

        for order in available_orders:
            articles.append(
                f"🎴 <b>{order['name']}</b>"
            )

        total_available = sum(
            order["quantity"]
            for order in available_orders
        )

        text = (
            "🟢 <b>Ordini disponibili</b>\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            + "\n\n".join(articles)
            + "\n\n━━━━━━━━━━━━━━━━━━\n\n"
            f"Totale disponibili: <b>{total_available}</b>"
        )

    await query.edit_message_text(
        text=text,
        reply_markup=orders_back_keyboard(),
        parse_mode="HTML",
    )


async def show_all_orders(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    username = query.from_user.username

    if not username:
        await query.edit_message_text(
            text=(
                "⚠️ <b>Username Telegram non disponibile</b>\n\n"
                "Per consultare i tuoi ordini devi impostare "
                "uno username nelle impostazioni di Telegram."
            ),
            reply_markup=orders_back_keyboard(),
            parse_mode="HTML",
        )
        return

    try:
        orders = get_user_orders(username)

    except Exception:
        logger.exception("Errore durante la lettura degli ordini")

        await query.edit_message_text(
            text=(
                "⚠️ <b>Servizio momentaneamente non disponibile</b>\n\n"
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

    pending_items = total_items - available_items
    display_username = f"@{username}"

    if not orders:
        text = (
            "📦 <b>I miei ordini</b>\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 <b>{display_username}</b>\n\n"
            "Non risultano ordini associati al tuo account.\n\n"
            "━━━━━━━━━━━━━━━━━━"
        )

    else:
        articles = []

        for order in orders:
            if order["status"] == "IN MAGAZZINO":
                icon = "🟢"
            else:
                icon = "🟡"

            articles.append(
                f"{icon} <b>{order['name']}</b>"
            )

        text = (
            "📦 <b>I miei ordini</b>\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 <b>{display_username}</b>\n\n"
            f"📦 Totale articoli: <b>{total_items}</b>\n"
            f"🟢 Disponibili: <b>{available_items}</b>\n"
            f"🟡 In attesa: <b>{pending_items}</b>\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            + "\n\n".join(articles)
            + "\n\n━━━━━━━━━━━━━━━━━━"
        )

    await query.edit_message_text(
        text=text,
        reply_markup=orders_back_keyboard(),
        parse_mode="HTML",
    )