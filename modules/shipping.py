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

from services.bot_db import (
    create_shipping_request,
    get_admins,
    is_sorting_active,
)


logger = logging.getLogger(__name__)

SHIPPING_PAYMENT_RECEIPT = 1


def shipping_receipt_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(
                "❌ Annulla richiesta",
                callback_data="shipping_receipt_cancel",
            )
        ]]
    )


def shipping_completed_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📦 Torna ai miei ordini",
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

    for admin in get_admins():
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
    query = update.callback_query
    await query.answer()

    if is_sorting_active():
        await query.edit_message_text(
            text=(
                "📦 <b>Smistamento in corso</b>\n\n"
                "Le richieste di spedizione sono temporaneamente sospese. "
                "Riprova quando lo smistamento sarà completato."
            ),
            reply_markup=shipping_completed_keyboard(),
            parse_mode="HTML",
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
        await query.edit_message_text(
            text=(
                "⚠️ <b>Richiesta non valida</b>\n\n"
                "I dati della spedizione non sono più "
                "disponibili. Ripeti la procedura."
            ),
            reply_markup=shipping_completed_keyboard(),
            parse_mode="HTML",
        )
        clear_shipping_data(context)
        return ConversationHandler.END

    context.user_data[
        "waiting_shipping_receipt"
    ] = True

    await query.edit_message_text(
        text=(
            "📎 <b>Invia la ricevuta del pagamento</b>\n\n"
            "Invia una foto oppure un documento/PDF.\n\n"
            f"🚚 Corriere: <b>{escape(selected_carrier['name'])}</b>\n"
            f"💶 Importo: <b>€ {selected_carrier['price']:.2f}</b>"
        ),
        reply_markup=shipping_receipt_cancel_keyboard(),
        parse_mode="HTML",
    )

    return SHIPPING_PAYMENT_RECEIPT


async def receive_shipping_receipt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
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
        await message.reply_text(
            "⚠️ Invia una foto oppure un documento/PDF.",
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
    shipping_profile = context.user_data.get(
        "shipping_profile"
    )

    if (
        not selected_orders
        or not selected_carrier
        or not shipping_profile
    ):
        await message.reply_text(
            "⚠️ Sessione scaduta. Ripeti la procedura.",
            reply_markup=shipping_completed_keyboard(),
        )
        clear_shipping_data(context)
        return ConversationHandler.END

    products = build_products_text(
        selected_orders
    )

    try:
        shipping_request = create_shipping_request(
            telegram_id=user.id,
            username=user.username,
            products=products,
            carrier=selected_carrier["name"],
            shipping_cost=selected_carrier["price"],
            payment_file_id=payment_file_id,
            profile=shipping_profile,
            notes=(
                "Ricevuta inviata tramite bot. "
                f"Tipo allegato: {payment_type}."
            ),
        )

    except Exception:
        logger.exception(
            "Errore creazione richiesta spedizione"
        )
        await message.reply_text(
            "⚠️ Errore durante il salvataggio. Riprova."
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

    await message.reply_text(
        text=(
            "✅ <b>Richiesta di spedizione inviata!</b>\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"🆔 Richiesta: <code>{escape(shipping_id)}</code>\n"
            f"🚚 Corriere: <b>{escape(carrier)}</b>\n"
            f"💶 Costo: <b>€ {shipping_cost:.2f}</b>\n"
            "📋 Stato: <b>IN ATTESA</b>\n\n"
            "Riceverai il tracking quando la spedizione "
            "verrà preparata."
        ),
        reply_markup=shipping_completed_keyboard(),
        parse_mode="HTML",
    )

    return ConversationHandler.END


async def invalid_shipping_receipt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    await update.effective_message.reply_text(
        "⚠️ Sto aspettando una foto o un documento/PDF.",
        reply_markup=shipping_receipt_cancel_keyboard(),
    )
    return SHIPPING_PAYMENT_RECEIPT


async def cancel_shipping_receipt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    query = update.callback_query
    await query.answer()
    clear_shipping_data(context)

    await query.edit_message_text(
        text="❌ Richiesta di spedizione annullata.",
        reply_markup=shipping_completed_keyboard(),
    )

    return ConversationHandler.END
