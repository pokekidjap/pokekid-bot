import asyncio
import logging
import os

from services.perf import start_flow
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
    TypeHandler,
)

from config import BOT_TOKEN, PORT, STARTUP_CHECKS, WEBHOOK_SECRET, validate_config
from keyboards.home import home_keyboard
from modules.admin import (
    ADMIN_BROADCAST,
    ADMIN_MESSAGE_EDIT,
    ADMIN_BROADCAST_CONFIRM,
    ADMIN_TRACKING,
    admin_command,
    cancel_admin_input,
    cancel_tracking_input,
    complete_sorting,
    open_shipping_request,
    receive_broadcast,
    confirm_broadcast,
    cancel_broadcast,
    receive_message_edit,
    receive_tracking,
    show_admin_home,
    show_admin_messages,
    show_admin_notifications,
    mark_admin_notifications_read,
    show_admin_stats,
    show_bot_status,
    show_orders_by_user,
    show_pending_shipping_requests,
    show_shipping_history,
    show_shipping_receipt,
    show_user_orders_detail,
    start_broadcast,
    start_message_edit,
    start_sorting,
    start_tracking_input,
)
from modules.grading import (
    build_grading_conversation_handler,
    show_grading,
)
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
from services.bot_db import get_admins, get_config_values, is_admin
from services.profiles import sync_basic_profile
from services.startup import run_startup_checks
from services.ui import BOT_VERSION, LAST_UPDATE, compact_error, with_footer

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

HOME_TEXT = with_footer(
    "🏠 <b>Menu principale</b>\n\n"
    "Scegli una sezione:"
)

INFO_TEXT = with_footer(
    "🤖 <b>Pokekid Bot</b>\n\n"
    f"Versione {BOT_VERSION}\n\n"
    f"Ultimo aggiornamento:\n{LAST_UPDATE}"
)


def get_home_text() -> str:
    try:
        custom = get_config_values().get("MSG_BENVENUTO", {}).get("value", "").strip()
    except Exception:
        logger.exception("Impossibile leggere il messaggio di benvenuto")
        custom = ""
    if custom:
        return with_footer(custom)
    return HOME_TEXT


def get_home_keyboard(telegram_id: int | str) -> InlineKeyboardMarkup:
    rows = [list(row) for row in home_keyboard().inline_keyboard]
    if is_admin(telegram_id):
        rows.append([
            InlineKeyboardButton("🛠️ Pannello Admin", callback_data="admin_home")
        ])
    rows.append([InlineKeyboardButton("ℹ️ Info bot", callback_data="bot_info")])
    return InlineKeyboardMarkup(rows)


async def sync_telegram_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Registra ID/username e avvisa gli admin quando lo username cambia."""
    user = update.effective_user
    if user is None:
        return
    try:
        result = sync_basic_profile(user.id, user.username)
    except Exception:
        logger.exception("Impossibile sincronizzare il profilo Telegram")
        return

    if not result.get("username_changed"):
        return

    old_username = result.get("old_username") or "(nessuno)"
    new_username = result.get("new_username") or "(nessuno)"
    name = result.get("name") or user.full_name or "Utente"
    text = (
        "🔄 <b>Username aggiornato</b>\n\n"
        f"👤 {name}\n"
        f"🆔 <code>{user.id}</code>\n\n"
        f"Vecchio username: <b>{old_username}</b>\n"
        f"Nuovo username: <b>{new_username}</b>\n\n"
        "⚠️ Controlla il foglio ORDINI e aggiorna lo username, se presente."
    )
    for admin in get_admins(active_only=True):
        try:
            await context.bot.send_message(
                chat_id=int(admin["TELEGRAM_ID"]),
                text=text,
                parse_mode="HTML",
            )
        except Exception:
            logger.exception(
                "Notifica cambio username non inviata all'admin %s",
                admin.get("TELEGRAM_ID"),
            )


async def sync_every_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sincronizza il profilo su comandi, messaggi e pulsanti senza bloccare gli handler."""
    await sync_telegram_user(update, context)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with start_flow("start"):
        context.user_data.pop("profile_form", None)
        if update.message is None:
            return
        await sync_telegram_user(update, context)
        await update.message.reply_text(
            get_home_text(),
            reply_markup=get_home_keyboard(update.effective_user.id),
            parse_mode="HTML",
        )


async def show_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    await sync_telegram_user(update, context)
    await query.edit_message_text(
        get_home_text(),
        reply_markup=get_home_keyboard(query.from_user.id),
        parse_mode="HTML",
    )


async def show_bot_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    await query.edit_message_text(
        INFO_TEXT,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Menu", callback_data="menu_home")]
        ]),
        parse_mode="HTML",
    )


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return

    routes = {
        "menu_home": show_home,
        "bot_info": show_bot_info,
        "menu_orders": show_orders_menu,
        "orders_available": show_available_orders,
        "orders_all": show_all_orders,
        "shipping_history_user": show_shipping_history_user,
        "menu_grading": show_grading,
        "grading_page": show_grading,
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
        "admin_stats": show_admin_stats,
        "admin_messages": show_admin_messages,
        "admin_notifications": show_admin_notifications,
        "admin_notifications_read": mark_admin_notifications_read,
    }

    callback_data = query.data or ""
    callback_action = callback_data.split(":", 1)[0]
    handler = routes.get(callback_action)
    if handler is None:
        await query.answer("Funzione non riconosciuta.", show_alert=True)
        return
    await handler(update, context)


def build_profile_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(
            start_profile_form,
            pattern=r"^(profile_add_data|profile_edit_data)$",
        )],
        states={
            PROFILE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_profile_name)],
            PROFILE_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_profile_email)],
            PROFILE_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_profile_phone)],
            PROFILE_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_profile_address)],
            PROFILE_POSTAL_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_profile_postal_code)],
            PROFILE_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_profile_city)],
            PROFILE_PROVINCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_profile_province)],
            PROFILE_REVIEW: [
                CallbackQueryHandler(save_profile_form, pattern=r"^profile_form_save$"),
                CallbackQueryHandler(restart_profile_form, pattern=r"^profile_form_restart$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_profile_form, pattern=r"^profile_form_cancel$"),
            CommandHandler("cancel", cancel_profile_form),
        ],
        allow_reentry=True,
    )


def build_shipping_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(start_shipping_payment, pattern=r"^shipping_payment$")],
        states={
            SHIPPING_PAYMENT_RECEIPT: [
                MessageHandler(filters.PHOTO | filters.Document.ALL, receive_shipping_receipt),
                MessageHandler(~filters.COMMAND, invalid_shipping_receipt),
            ]
        },
        fallbacks=[CallbackQueryHandler(cancel_shipping_receipt, pattern=r"^shipping_receipt_cancel$")],
        allow_reentry=True,
    )


def build_admin_tracking_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(start_tracking_input, pattern=r"^admin_shipping_tracking:.+$")],
        states={ADMIN_TRACKING: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_tracking)]},
        fallbacks=[CallbackQueryHandler(cancel_tracking_input, pattern=r"^admin_tracking_cancel$")],
        allow_reentry=True,
    )


def build_admin_broadcast_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(start_broadcast, pattern=r"^admin_broadcast_start$")],
        states={
            ADMIN_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_broadcast)],
            ADMIN_BROADCAST_CONFIRM: [
                CallbackQueryHandler(confirm_broadcast, pattern=r"^admin_broadcast_confirm$"),
                CallbackQueryHandler(cancel_broadcast, pattern=r"^admin_broadcast_cancel$"),
            ],
        },
        fallbacks=[CallbackQueryHandler(cancel_admin_input, pattern=r"^admin_home$")],
        allow_reentry=True,
    )


def build_admin_message_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(start_message_edit, pattern=r"^admin_message_edit:.+$")],
        states={ADMIN_MESSAGE_EDIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_message_edit)]},
        fallbacks=[CallbackQueryHandler(cancel_admin_input, pattern=r"^(admin_home|admin_messages)$")],
        allow_reentry=True,
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Registra l'errore e informa l'utente senza esporre dettagli tecnici."""
    logger.error(
        "Eccezione non gestita durante un aggiornamento Telegram",
        exc_info=context.error,
    )
    if not isinstance(update, Update) or update.effective_message is None:
        return
    try:
        await update.effective_message.reply_text(
            compact_error(),
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("Impossibile inviare il messaggio di errore all'utente")


async def post_init(application: Application) -> None:
    """Esegue controlli di sola lettura e pubblica i comandi del bot."""
    await application.bot.set_my_commands([
        ("start", "Apri il menu principale"),
        ("spedizioni", "Consulta le tue spedizioni"),
        ("admin", "Apri il pannello amministratore"),
        ("cancel", "Annulla l'operazione in corso"),
    ])
    if not STARTUP_CHECKS:
        logger.info("Controlli iniziali disattivati da configurazione")
        return
    try:
        await asyncio.to_thread(run_startup_checks)
    except Exception:
        logger.exception(
            "Controlli iniziali non superati. Il bot resta online per consentire il debug."
        )


def register_handlers(application: Application) -> None:
    application.add_handler(TypeHandler(Update, sync_every_update), group=-1)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("spedizioni", show_shipping_history_user))

    application.add_handler(build_profile_conversation_handler())
    application.add_handler(build_shipping_conversation_handler())
    application.add_handler(build_admin_tracking_handler())
    application.add_handler(build_admin_broadcast_handler())
    application.add_handler(build_admin_message_handler())
    application.add_handler(build_grading_conversation_handler())

    application.add_handler(CallbackQueryHandler(toggle_available_order, pattern=r"^order_toggle:\d+$"))
    application.add_handler(CallbackQueryHandler(continue_shipping_request, pattern=r"^shipping_continue$"))
    application.add_handler(CallbackQueryHandler(select_shipping_carrier, pattern=r"^shipping_carrier:\d+$"))
    application.add_handler(CallbackQueryHandler(cancel_shipping_request, pattern=r"^shipping_cancel$"))
    application.add_handler(CallbackQueryHandler(open_shipping_request, pattern=r"^admin_shipping_open:.+$"))
    application.add_handler(CallbackQueryHandler(show_shipping_receipt, pattern=r"^admin_shipping_receipt:.+$"))
    application.add_handler(CallbackQueryHandler(show_user_orders_detail, pattern=r"^admin_user_orders:\d+$"))

    # Deve restare per ultimo: gestisce i callback generici non intercettati prima.
    application.add_handler(CallbackQueryHandler(handle_button))


def get_railway_public_domain() -> str:
    return os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()


def get_webhook_secret() -> str:
    secret = str(WEBHOOK_SECRET or "").strip()
    if not secret:
        raise RuntimeError("WEBHOOK_SECRET non configurato.")
    return secret


def run_application(application: Application) -> None:
    railway_domain = get_railway_public_domain()
    if railway_domain:
        webhook_secret = get_webhook_secret()
        webhook_url = f"https://{railway_domain}/{webhook_secret}"
        logger.info("Avvio Pokekid Bot su Railway tramite webhook: %s", webhook_url)
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

    logger.info("Avvio Pokekid Bot in locale tramite polling")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


def main() -> None:
    validate_config()
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    register_handlers(application)
    application.add_error_handler(error_handler)
    run_application(application)


if __name__ == "__main__":
    main()
