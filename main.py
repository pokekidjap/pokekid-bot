import logging
import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import BOT_TOKEN, PORT, WEBHOOK_SECRET, validate_config
from keyboards.home import home_keyboard
from modules.admin import (
    ADMIN_TRACKING,
    admin_command,
    cancel_tracking_input,
    complete_sorting,
    open_shipping_request,
    receive_tracking,
    show_admin_home,
    show_bot_status,
    show_orders_by_user,
    show_pending_shipping_requests,
    show_shipping_history,
    show_shipping_receipt,
    show_user_orders_detail,
    start_sorting,
    start_tracking_input,
)
from modules.grading import show_grading
from modules.history import show_shipping_history_user
from modules.orders import (
    cancel_shipping_request,
    continue_shipping_request,
    select_shipping_carrier,
    show_all_orders,
    show_available_orders,
    show_orders_menu,
    toggle_available_order,
)
from modules.profile import (
    PROFILE_ADDRESS,
    PROFILE_CITY,
    PROFILE_EMAIL,
    PROFILE_NAME,
    PROFILE_PHONE,
    PROFILE_POSTAL_CODE,
    PROFILE_PROVINCE,
    PROFILE_REVIEW,
    ask_profile_delete_confirmation,
    cancel_profile_form,
    receive_profile_address,
    receive_profile_city,
    receive_profile_email,
    receive_profile_name,
    receive_profile_phone,
    receive_profile_postal_code,
    receive_profile_province,
    remove_profile,
    restart_profile_form,
    save_profile_form,
    show_profile,
    show_profile_shipments,
    show_profile_shipping_data,
    start_profile_form,
)
from modules.shipping import (
    SHIPPING_PAYMENT_RECEIPT,
    cancel_shipping_receipt,
    invalid_shipping_receipt,
    receive_shipping_receipt,
    start_shipping_payment,
)
from services.bot_db import is_admin

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

HOME_TEXT = (
    "🏠 <b>Pokekid Bot</b>\n\n"
    "Benvenuto! 👋\n\n"
    "Scegli una sezione:"
)


def get_home_keyboard(telegram_id: int | str) -> InlineKeyboardMarkup:
    rows = [
        list(row)
        for row in home_keyboard().inline_keyboard
    ]

    if is_admin(telegram_id):
        rows.append(
            [
                InlineKeyboardButton(
                    "🛠️ Pannello Admin",
                    callback_data="admin_home",
                )
            ]
        )

    return InlineKeyboardMarkup(rows)


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    context.user_data.pop("profile_form", None)

    if update.message is None:
        return

    await update.message.reply_text(
        HOME_TEXT,
        reply_markup=get_home_keyboard(
            update.effective_user.id
        ),
        parse_mode="HTML",
    )


async def show_home(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    if query is None:
        return

    await query.answer()
    await query.edit_message_text(
        HOME_TEXT,
        reply_markup=get_home_keyboard(
            query.from_user.id
        ),
        parse_mode="HTML",
    )


async def handle_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    if query is None:
        return

    routes = {
        "menu_home": show_home,
        "menu_orders": show_orders_menu,
        "orders_available": show_available_orders,
        "orders_all": show_all_orders,
        "shipping_history_user": show_shipping_history_user,
        "menu_grading": show_grading,
        "menu_profile": show_profile,
        "profile_shipping_data": show_profile_shipping_data,
        "profile_delete_confirm": ask_profile_delete_confirmation,
        "profile_delete": remove_profile,
        "profile_shipments": show_profile_shipments,
        "admin_home": show_admin_home,
        "admin_orders_users": show_orders_by_user,
        "admin_sorting_start": start_sorting,
        "admin_sorting_complete": complete_sorting,
        "admin_shipping_list": show_pending_shipping_requests,
        "admin_shipping_history": show_shipping_history,
        "admin_bot_status": show_bot_status,
    }

    handler = routes.get(query.data)

    if handler is None:
        await query.answer(
            "Funzione non riconosciuta.",
            show_alert=True,
        )
        return

    await handler(update, context)


def build_profile_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                start_profile_form,
                pattern=r"^(profile_add_data|profile_edit_data)$",
            )
        ],
        states={
            PROFILE_NAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_profile_name,
                )
            ],
            PROFILE_EMAIL: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_profile_email,
                )
            ],
            PROFILE_PHONE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_profile_phone,
                )
            ],
            PROFILE_ADDRESS: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_profile_address,
                )
            ],
            PROFILE_POSTAL_CODE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_profile_postal_code,
                )
            ],
            PROFILE_CITY: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_profile_city,
                )
            ],
            PROFILE_PROVINCE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_profile_province,
                )
            ],
            PROFILE_REVIEW: [
                CallbackQueryHandler(
                    save_profile_form,
                    pattern=r"^profile_form_save$",
                ),
                CallbackQueryHandler(
                    restart_profile_form,
                    pattern=r"^profile_form_restart$",
                ),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(
                cancel_profile_form,
                pattern=r"^profile_form_cancel$",
            ),
            CommandHandler(
                "cancel",
                cancel_profile_form,
            ),
        ],
        allow_reentry=True,
    )


def build_shipping_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                start_shipping_payment,
                pattern=r"^shipping_payment$",
            )
        ],
        states={
            SHIPPING_PAYMENT_RECEIPT: [
                MessageHandler(
                    filters.PHOTO | filters.Document.ALL,
                    receive_shipping_receipt,
                ),
                MessageHandler(
                    ~filters.COMMAND,
                    invalid_shipping_receipt,
                ),
            ]
        },
        fallbacks=[
            CallbackQueryHandler(
                cancel_shipping_receipt,
                pattern=r"^shipping_receipt_cancel$",
            )
        ],
        allow_reentry=True,
    )


def build_admin_tracking_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                start_tracking_input,
                pattern=r"^admin_shipping_tracking:.+$",
            )
        ],
        states={
            ADMIN_TRACKING: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_tracking,
                )
            ]
        },
        fallbacks=[
            CallbackQueryHandler(
                cancel_tracking_input,
                pattern=r"^admin_tracking_cancel$",
            )
        ],
        allow_reentry=True,
    )



async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Registra gli errori non gestiti senza interrompere il bot."""
    logger.error(
        "Eccezione non gestita durante un aggiornamento Telegram",
        exc_info=context.error,
    )


def register_handlers(application: Application) -> None:
    application.add_handler(
        CommandHandler("start", start)
    )
    application.add_handler(
        CommandHandler("admin", admin_command)
    )
    application.add_handler(
        CommandHandler(
            "spedizioni",
            show_shipping_history_user,
        )
    )

    application.add_handler(
        build_profile_conversation_handler()
    )
    application.add_handler(
        build_shipping_conversation_handler()
    )
    application.add_handler(
        build_admin_tracking_handler()
    )

    application.add_handler(
        CallbackQueryHandler(
            toggle_available_order,
            pattern=r"^order_toggle:\d+$",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            continue_shipping_request,
            pattern=r"^shipping_continue$",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            select_shipping_carrier,
            pattern=r"^shipping_carrier:\d+$",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            cancel_shipping_request,
            pattern=r"^shipping_cancel$",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            open_shipping_request,
            pattern=r"^admin_shipping_open:.+$",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            show_shipping_receipt,
            pattern=r"^admin_shipping_receipt:.+$",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            show_user_orders_detail,
            pattern=r"^admin_user_orders:\d+$",
        )
    )

    # Deve restare per ultimo perché gestisce
    # tutti i callback generici non intercettati prima.
    application.add_handler(
        CallbackQueryHandler(handle_button)
    )


def get_railway_public_domain() -> str:
    """
    Restituisce il dominio pubblico Railway.

    Railway valorizza automaticamente RAILWAY_PUBLIC_DOMAIN
    quando al servizio è associato un dominio pubblico.
    """
    return os.getenv(
        "RAILWAY_PUBLIC_DOMAIN",
        "",
    ).strip()


def get_webhook_secret() -> str:
    secret = str(WEBHOOK_SECRET or "").strip()

    if not secret:
        raise RuntimeError(
            "WEBHOOK_SECRET non configurato."
        )

    return secret


def run_application(application: Application) -> None:
    railway_domain = get_railway_public_domain()

    if railway_domain:
        webhook_secret = get_webhook_secret()
        webhook_url = (
            f"https://{railway_domain}/"
            f"{webhook_secret}"
        )

        logger.info(
            "Avvio Pokekid Bot su Railway tramite webhook: %s",
            webhook_url,
        )

        application.run_webhook(
            listen="0.0.0.0",
            port=int(PORT),
            url_path=webhook_secret,
            webhook_url=webhook_url,
            secret_token=webhook_secret,
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )
        return

    logger.info(
        "Avvio Pokekid Bot in locale tramite polling"
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


def main() -> None:
    validate_config()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    register_handlers(application)
    application.add_error_handler(error_handler)
    run_application(application)


if __name__ == "__main__":
    main()