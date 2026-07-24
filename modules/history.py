import asyncio
from html import escape
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from services.bot_db import get_user_shipping_requests
from services.perf import start_flow
from services.ui import page_title, readable_status, with_footer


async def show_shipping_history_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with start_flow("shipping_history"):
        query = update.callback_query
        if query:
            await query.answer()
        user = update.effective_user
        requests = (
            await asyncio.to_thread(get_user_shipping_requests, user.id)
            if user
            else []
        )
        if not requests:
            text = with_footer(
                page_title("🚚", "Le mie spedizioni")
                + "\n\n"
                "Non risultano ancora spedizioni associate al tuo profilo."
            )
        else:
            lines = []
            for item in requests[:20]:
                status = str(item.get("STATO", ""))
                icon = (
                    "✅"
                    if status == "SPEDITO"
                    else "❌"
                    if status == "ANNULLATO"
                    else "🟡"
                )
                tracking = item.get("TRACKING", "")
                details = [
                    f"{icon} <b>Spedizione</b>",
                    f"🆔 ID: <code>{escape(item.get('ID', ''))}</code>",
                    f"📋 Stato: <b>{escape(readable_status(status))}</b>",
                    f"🚚 Corriere: {escape(item.get('CORRIERE', ''))}",
                ]
                if tracking:
                    details.append(
                        f"🔎 Tracking: <code>{escape(tracking)}</code>"
                    )
                lines.append("\n".join(details))
            text = with_footer(
                page_title("🚚", "Le mie spedizioni")
                + "\n\n"
                + "\n\n".join(lines)
            )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("⬅️ Indietro", callback_data="menu_orders"),
                InlineKeyboardButton("🏠 Menu principale", callback_data="menu_home"),
            ],
        ])
        if query:
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")
        else:
            await update.effective_message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")
