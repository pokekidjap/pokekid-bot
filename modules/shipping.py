import asyncio
import logging
from html import escape

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
)

from keyboards.orders import shipping_profile_incomplete_keyboard
from services.bot_db import (
    create_shipping_request,
    get_admins,
    get_profile,
    is_sorting_active,
)
from services.perf import start_flow
from services.profiles import is_shipping_profile_complete
from services.shipping_engine import is_shipping_v2_active
from services.sheets import get_user_orders
from services.ui import (
    operation_unavailable,
    page_title,
    section_title,
    with_footer,
)
from modules.shipping_v2 import (
    cancel_v2_shipping_receipt,
    cancel_v2_shipping_receipt_command,
    receive_v2_shipping_receipt,
    start_v2_shipping_payment,
)


logger = logging.getLogger(__name__)

SHIPPING_PAYMENT_RECEIPT = 1


def _shipping_response(text: str) -> str:
    return with_footer(text)


async def _edit_shipping_query(
    query,
    text: str,
    reply_markup=None,
    parse_mode: str = "HTML",
) -> None:
    await query.edit_message_text(
        _shipping_response(text),
        reply_markup=reply_markup,
        parse_mode=parse_mode,
    )


async def _reply_shipping_message(
    message,
    text: str,
    reply_markup=None,
    parse_mode: str = "HTML",
) -> None:
    await message.reply_text(
        _shipping_response(text),
        reply_markup=reply_markup,
        parse_mode=parse_mode,
    )


def shipping_receipt_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(
                "❌ Annulla",
                callback_data="shipping_receipt_cancel",
            )
        ]]
    )


def shipping_completed_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
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


def clear_shipping_data(
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    shipping_keys = [
        "available_orders",
        "selected_order_rows",
        "selected_orders",
        "shipping_profile",
        "shipping_methods",
        "selected_carrier",
        "shipping_selection_timestamp",
        "waiting_shipping_receipt",
    ]

    for key in shipping_keys:
        context.user_data.pop(
            key,
            None,
        )


def build_products_text(
    selected_orders: list[dict],
) -> str:
    products = []

    for order in selected_orders:
        name = str(
            order.get("name", "")
        ).strip()

        quantity = order.get(
            "quantity",
            0,
        )

        row_number = order.get(
            "row_number",
            "",
        )

        product_text = f"{name} ×{quantity}"

        if row_number:
            product_text += f" [RIGA {row_number}]"

        products.append(product_text)

    return " | ".join(products)


def selected_orders_are_still_available(
    selected_orders: list[dict],
    current_orders: list[dict],
) -> bool:
    """Verifica riga, nome, quantità e disponibilità della selezione."""
    current_by_row = {
        order.get("row_number"): order
        for order in current_orders
    }

    for selected_order in selected_orders:
        current_order = current_by_row.get(
            selected_order.get("row_number")
        )

        if current_order is None:
            return False

        if (
            str(current_order.get("name", "")).strip()
            != str(selected_order.get("name", "")).strip()
        ):
            return False

        if (
            current_order.get("quantity")
            != selected_order.get("quantity")
        ):
            return False

        if (
            str(current_order.get("status", "")).strip().upper()
            != "IN MAGAZZINO"
        ):
            return False

    return True


async def notify_admins(
    context: ContextTypes.DEFAULT_TYPE,
    shipping_request: dict,
    payment_type: str,
) -> None:
    shipping_id = shipping_request.get(
        "ID",
        "",
    )

    text = (
        "📦 <b>Nuova richiesta di spedizione</b>\n\n"
        f"🆔 <code>{escape(shipping_id)}</code>\n"
        f"👤 {escape(shipping_request.get('USERNAME', ''))}\n"
        f"🚚 {escape(shipping_request.get('CORRIERE', ''))}\n"
        f"💶 € {escape(str(shipping_request.get('COSTO_SPEDIZIONE', '')))}\n\n"
        f"🎴 {escape(shipping_request.get('PRODOTTI', ''))}"
    )

    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(
                "👁 Apri richiesta",
                callback_data=(
                    f"admin_shipping_open:{shipping_id}"
                ),
            )
        ]]
    )

    admins = await asyncio.to_thread(get_admins)
    for admin in admins:
        telegram_id = admin.get(
            "TELEGRAM_ID",
            "",
        )

        if not telegram_id:
            continue

        try:
            await context.bot.send_message(
                chat_id=int(telegram_id),
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )

        except Exception:
            logger.exception(
                "Notifica admin non inviata a %s",
                telegram_id,
            )


async def start_shipping_payment(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    with start_flow("shipping_payment"):
        return await _start_shipping_payment(update, context)


async def _start_shipping_payment(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    if is_shipping_v2_active():
        return await start_v2_shipping_payment(
            update,
            context,
            receipt_state=SHIPPING_PAYMENT_RECEIPT,
        )

    query = update.callback_query

    await query.answer()

    if await asyncio.to_thread(is_sorting_active):
        await _edit_shipping_query(
            query,
            page_title("📦", "Smistamento in corso")
            + "\n\n"
            "Le richieste di spedizione sono temporaneamente sospese. "
            "Riprova quando lo smistamento sarà completato.",
            reply_markup=shipping_completed_keyboard(),
        )
        clear_shipping_data(context)
        return ConversationHandler.END

    selected_orders = context.user_data.get(
        "selected_orders",
        [],
    )
    selected_carrier = context.user_data.get(
        "selected_carrier"
    )
    shipping_profile = context.user_data.get(
        "shipping_profile"
    )

    if (
        not selected_orders
        or not selected_carrier
        or not shipping_profile
    ):
        await _edit_shipping_query(
            query,
            operation_unavailable(
                "I dati della spedizione non sono più disponibili."
            ),
            reply_markup=shipping_completed_keyboard(),
        )
        clear_shipping_data(context)
        return ConversationHandler.END

    context.user_data[
        "waiting_shipping_receipt"
    ] = True

    await _edit_shipping_query(
        query,
        page_title("📎", "Invia la ricevuta")
        + "\n\n"
        "Invia una foto oppure un documento/PDF.\n\n"
        + section_title("🚚", "Spedizione")
        + "\n"
        + f"Corriere: <b>{escape(selected_carrier['name'])}</b>\n"
        + f"Importo: <b>€ {selected_carrier['price']:.2f}</b>",
        reply_markup=shipping_receipt_cancel_keyboard(),
    )

    return SHIPPING_PAYMENT_RECEIPT


async def receive_shipping_receipt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    with start_flow("shipping_receipt"):
        return await _receive_shipping_receipt(update, context)


async def _receive_shipping_receipt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    if is_shipping_v2_active():
        return await receive_v2_shipping_receipt(
            update,
            context,
            receipt_state=SHIPPING_PAYMENT_RECEIPT,
        )

    message = update.effective_message
    user = update.effective_user

    if not message or not user:
        return SHIPPING_PAYMENT_RECEIPT

    payment_file_id = ""
    payment_type = ""

    if message.photo:
        payment_file_id = message.photo[-1].file_id
        payment_type = "FOTO"
    elif message.document:
        payment_file_id = message.document.file_id
        payment_type = "DOCUMENTO"

    if not payment_file_id:
        await _reply_shipping_message(
            message,
            "⚠️ <b>Allegato non valido</b>\n\n"
            "Invia una foto oppure un documento/PDF.",
            reply_markup=shipping_receipt_cancel_keyboard(),
        )
        return SHIPPING_PAYMENT_RECEIPT

    selected_orders = context.user_data.get(
        "selected_orders",
        [],
    )
    selected_carrier = context.user_data.get(
        "selected_carrier"
    )

    if (
        not selected_orders
        or not selected_carrier
    ):
        await _reply_shipping_message(
            message,
            operation_unavailable(
                "La sessione di spedizione è scaduta."
            ),
            reply_markup=shipping_completed_keyboard(),
        )
        clear_shipping_data(context)
        return ConversationHandler.END

    try:
        sorting_active = await asyncio.to_thread(
            is_sorting_active
        )
    except Exception:
        logger.exception(
            "Errore verifica smistamento prima della spedizione"
        )
        await _reply_shipping_message(
            message,
            operation_unavailable(
                "Non è stato possibile verificare lo stato delle spedizioni."
            ),
            reply_markup=shipping_receipt_cancel_keyboard(),
        )
        return SHIPPING_PAYMENT_RECEIPT

    if sorting_active:
        clear_shipping_data(context)
        await _reply_shipping_message(
            message,
            page_title("📦", "Smistamento in corso")
            + "\n\n"
            "Lo smistamento è iniziato durante la procedura. "
            "Riprova quando sarà completato.",
            reply_markup=shipping_completed_keyboard(),
        )
        return ConversationHandler.END

    try:
        fresh_profile = await asyncio.to_thread(
            get_profile,
            user.id,
            force_refresh=True,
        )
    except Exception:
        logger.exception(
            "Errore rilettura profilo prima della spedizione"
        )
        await _reply_shipping_message(
            message,
            operation_unavailable(
                "Non è stato possibile verificare il profilo."
            ),
            reply_markup=shipping_receipt_cancel_keyboard(),
        )
        return SHIPPING_PAYMENT_RECEIPT

    if not is_shipping_profile_complete(
        fresh_profile
    ):
        clear_shipping_data(context)
        await _reply_shipping_message(
            message,
            "⚠️ <b>Profilo modificato</b>\n\n"
            "Il profilo è stato eliminato o modificato. "
            "Completa i dati e ripeti la procedura.",
            reply_markup=shipping_profile_incomplete_keyboard(
                has_profile=fresh_profile is not None,
            ),
        )
        return ConversationHandler.END

    try:
        current_orders = await asyncio.to_thread(
            get_user_orders,
            user.username,
            force_refresh=True,
        )
    except Exception:
        logger.exception(
            "Errore rilettura ordini prima della spedizione"
        )
        await _reply_shipping_message(
            message,
            operation_unavailable(
                "Non è stato possibile verificare gli articoli."
            ),
            reply_markup=shipping_receipt_cancel_keyboard(),
        )
        return SHIPPING_PAYMENT_RECEIPT

    if not selected_orders_are_still_available(
        selected_orders,
        current_orders,
    ):
        clear_shipping_data(context)
        await _reply_shipping_message(
            message,
            "⚠️ <b>Articoli modificati</b>\n\n"
            "La disponibilità dei tuoi articoli è cambiata. "
            "Riapri gli ordini e ripeti la selezione.",
            reply_markup=shipping_completed_keyboard(),
        )
        return ConversationHandler.END

    products = build_products_text(
        selected_orders
    )

    try:
        shipping_request = await asyncio.to_thread(
            create_shipping_request,
            telegram_id=user.id,
            username=user.username,
            products=products,
            carrier=selected_carrier["name"],
            shipping_cost=selected_carrier["price"],
            payment_file_id=payment_file_id,
            profile=fresh_profile,
            notes=(
                "Ricevuta inviata tramite bot. "
                f"Tipo allegato: {payment_type}."
            ),
        )

    except Exception:
        logger.exception(
            "Errore creazione richiesta spedizione"
        )
        await _reply_shipping_message(
            message,
            operation_unavailable(
                "Non è stato possibile salvare la richiesta."
            ),
            reply_markup=shipping_receipt_cancel_keyboard(),
        )
        return SHIPPING_PAYMENT_RECEIPT

    try:
        await notify_admins(
            context,
            shipping_request,
            payment_type,
        )
    except Exception:
        logger.exception(
            "Errore generale notifica admin"
        )

    shipping_id = shipping_request["ID"]
    carrier = shipping_request["CORRIERE"]
    shipping_cost = float(
        shipping_request["COSTO_SPEDIZIONE"]
    )

    clear_shipping_data(context)

    await _reply_shipping_message(
        message,
        page_title("✅", "Richiesta completata")
        + "\n\n"
        f"🆔 Richiesta: <code>{escape(shipping_id)}</code>\n"
        f"🚚 Corriere: <b>{escape(carrier)}</b>\n"
        f"💶 Costo: <b>€ {shipping_cost:.2f}</b>\n"
        "📋 Stato: <b>In attesa</b>\n\n"
        "Riceverai il tracking quando la spedizione "
        "verrà preparata.",
        reply_markup=shipping_completed_keyboard(),
    )

    return ConversationHandler.END


async def invalid_shipping_receipt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.effective_message
    if message:
        await _reply_shipping_message(
            message,
            "⚠️ <b>Allegato non valido</b>\n\n"
            "Invia una foto oppure un documento/PDF.",
            reply_markup=shipping_receipt_cancel_keyboard(),
        )
    return SHIPPING_PAYMENT_RECEIPT


async def cancel_shipping_receipt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    if is_shipping_v2_active():
        return await cancel_v2_shipping_receipt(
            update,
            context,
            receipt_state=SHIPPING_PAYMENT_RECEIPT,
        )

    query = update.callback_query
    await query.answer()
    clear_shipping_data(context)

    await _edit_shipping_query(
        query,
        "❌ <b>Richiesta annullata</b>\n\n"
        "Nessuna richiesta è stata salvata.",
        reply_markup=shipping_completed_keyboard(),
    )

    return ConversationHandler.END


async def cancel_shipping_receipt_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int | None:
    return await cancel_v2_shipping_receipt_command(
        update,
        context,
        receipt_state=SHIPPING_PAYMENT_RECEIPT,
    )
