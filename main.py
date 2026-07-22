import logging
import os

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from config import (
    BOT_TOKEN,
    KOYEB_PUBLIC_DOMAIN,
    PORT,
    WEBHOOK_SECRET,
)
from keyboards.home import home_keyboard
from modules.grading import show_grading
from modules.orders import (
    show_all_orders,
    show_available_orders,
    show_orders_menu,
)
from modules.profile import (
    ask_profile_delete_confirmation,
    remove_profile,
    show_profile,
    show_profile_data_form_placeholder,
    show_profile_shipments,
    show_profile_shipping_data,
)


logging.basicConfig(
    format=(
        "%(asctime)s - %(name)s - "
        "%(levelname)s - %(message)s"
    ),
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


HOME_TEXT = (
    "🏠 <b>Pokekid Bot</b>\n\n"
    "Benvenuto! 👋\n\n"
    "Scegli una sezione:"
)


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await update.message.reply_text(
        text=HOME_TEXT,
        reply_markup=home_keyboard(),
        parse_mode="HTML",
    )


async def show_home(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    await query.edit_message_text(
        text=HOME_TEXT,
        reply_markup=home_keyboard(),
        parse_mode="HTML",
    )


async def handle_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    await query.answer()

    routes = {
        # Home
        "menu_home": show_home,

        # Ordini
        "menu_orders": show_orders_menu,
        "orders_available": show_available_orders,
        "orders_all": show_all_orders,

        # Grading
        "menu_grading": show_grading,

        # Profilo
        "menu_profile": show_profile,
        "profile_shipping_data": show_profile_shipping_data,
        "profile_add_data": show_profile_data_form_placeholder,
        "profile_edit_data": show_profile_data_form_placeholder,
        "profile_delete_confirm": ask_profile_delete_confirmation,
        "profile_delete": remove_profile,
        "profile_shipments": show_profile_shipments,
    }

    handler = routes.get(
        query.data
    )

    if handler is None:
        await query.answer(
            text="Funzione non riconosciuta.",
            show_alert=True,
        )
        return

    await handler(
        update,
        context,
    )


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN non trovato."
        )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            handle_button
        )
    )

    if KOYEB_PUBLIC_DOMAIN:
        webhook_url = (
            f"https://{KOYEB_PUBLIC_DOMAIN}/"
            f"{WEBHOOK_SECRET}"
        )

        logger.info(
            "Avvio Pokekid Bot tramite webhook"
        )

        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=WEBHOOK_SECRET,
            webhook_url=webhook_url,
            secret_token=WEBHOOK_SECRET,
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )

    else:
        logger.info(
            "Avvio Pokekid Bot in locale tramite polling"
        )

        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )


if __name__ == "__main__":
    main()