from html import escape
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from services.bot_db import get_user_shipping_requests


async def show_shipping_history_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()
    user = update.effective_user
    requests = get_user_shipping_requests(user.id) if user else []
    if not requests:
        text = "🗂 <b>Storico spedizioni</b>\n\nNon risultano spedizioni associate al tuo profilo."
    else:
        lines = []
        for item in requests[:20]:
            icon = "✅" if item.get("STATO") == "SPEDITO" else "🟡"
            tracking = item.get("TRACKING", "")
            line = f"{icon} <code>{escape(item.get('ID', ''))}</code>\n🚚 {escape(item.get('CORRIERE', ''))} · {escape(item.get('STATO', ''))}"
            if tracking:
                line += f"\n🔎 <code>{escape(tracking)}</code>"
            lines.append(line)
        text = "🗂 <b>Storico spedizioni</b>\n\n" + "\n\n".join(lines)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 I miei ordini", callback_data="menu_orders")],
        [InlineKeyboardButton("🏠 Menu principale", callback_data="menu_home")],
    ])
    if query:
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await update.effective_message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")
