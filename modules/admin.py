import logging
from html import escape

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from keyboards.admin import (
    admin_home_keyboard,
    admin_shipping_detail_keyboard,
    admin_shipping_list_keyboard,
    admin_tracking_cancel_keyboard,
    admin_users_keyboard,
)
from services.admin_orders import get_orders_grouped_by_user
from services.bot_db import (
    complete_shipping_request,
    get_admin,
    get_all_shipping_requests,
    get_bot_status,
    get_shipping_request,
    is_admin,
    set_sorting_status,
)

logger = logging.getLogger(__name__)
ADMIN_TRACKING = 20


async def deny_admin_access(update: Update) -> None:
    if update.callback_query:
        await update.callback_query.answer("Accesso non autorizzato.", show_alert=True)
    elif update.effective_message:
        await update.effective_message.reply_text("⛔ Accesso non autorizzato.")


async def check_admin(update: Update) -> bool:
    user = update.effective_user
    if not user or not is_admin(user.id):
        await deny_admin_access(update)
        return False
    return True


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update):
        return
    await update.effective_message.reply_text(
        "🛠️ <b>Pannello Admin · Versione 1.3</b>\n\nSeleziona una funzione:",
        reply_markup=admin_home_keyboard(), parse_mode="HTML",
    )


async def show_admin_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update):
        return
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🛠️ <b>Pannello Admin · Versione 1.3</b>\n\nSeleziona una funzione:",
        reply_markup=admin_home_keyboard(), parse_mode="HTML",
    )


async def show_orders_by_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update):
        return
    query = update.callback_query
    await query.answer()
    try:
        users = get_orders_grouped_by_user()
    except Exception:
        logger.exception("Errore lettura ordini per utente")
        await query.edit_message_text("⚠️ Impossibile leggere gli ordini.", reply_markup=admin_home_keyboard())
        return
    context.user_data["admin_order_users"] = users
    await query.edit_message_text(
        f"👥 <b>Ordini per utente</b>\n\nUtenti trovati: <b>{len(users)}</b>\n🟢 = prodotti in magazzino / da gestire",
        reply_markup=admin_users_keyboard(users), parse_mode="HTML",
    )


async def show_user_orders_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update):
        return

    query = update.callback_query
    await query.answer()

    try:
        index = int(query.data.split(":", 1)[1])
        user = context.user_data.get("admin_order_users", [])[index]
    except (ValueError, IndexError):
        await query.answer("Elenco scaduto: aggiorna.", show_alert=True)
        return

    status_icons = {
        "IN MAGAZZINO": "🟢",
        "GRADING": "🔵",
        "RESTAURO": "🟣",
        "ORDINATO": "🟡",
    }

    sections = []
    current_status = None

    for order in user["rows"]:
        status = order["status"] or "SENZA STATO"

        if status != current_status:
            if sections:
                sections.append("━━━━━━━━━━━━━━")
            current_status = status

        icon = status_icons.get(status, "⚪")
        sections.append(
            f"{icon} <b>{escape(order['name'])}</b> ×{order['quantity']}\n"
            f"   {escape(status)}"
        )

    summary_lines = [
        f"📦 Da gestire: <b>{user['total_quantity']}</b>",
        "",
        f"🟢 In magazzino: <b>{user['ready_quantity']}</b>",
        f"🔵 Grading: <b>{user['grading_quantity']}</b>",
        f"🟣 Restauro: <b>{user['restoration_quantity']}</b>",
        f"🟡 Ordinati: <b>{user['ordered_quantity']}</b>",
    ]

    if user.get("other_quantity", 0):
        summary_lines.append(f"⚪ Altri stati: <b>{user['other_quantity']}</b>")

    header = (
        f"👤 <b>{escape(user['username'])}</b>\n\n"
        + "\n".join(summary_lines)
        + "\n\n"
    )

    # Telegram accetta messaggi fino a 4096 caratteri. Lasciamo margine
    # per evitare errori con utenti che hanno moltissimi articoli.
    max_text_length = 3900
    body_parts = []
    current_length = len(header)
    hidden_items = 0

    for position, section in enumerate(sections):
        separator = "\n\n" if body_parts else ""
        addition = separator + section

        if current_length + len(addition) > max_text_length:
            hidden_items = sum(
                1
                for remaining in sections[position:]
                if remaining != "━━━━━━━━━━━━━━"
            )
            break

        body_parts.append(section)
        current_length += len(addition)

    text = header + "\n\n".join(body_parts)

    if hidden_items:
        text += f"\n\n… e altri <b>{hidden_items}</b> articoli non mostrati."

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("⬅️ Utenti", callback_data="admin_orders_users")]]
        ),
        parse_mode="HTML",
    )


async def start_sorting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update):
        return
    query = update.callback_query
    await query.answer()
    admin = get_admin(query.from_user.id) or {}
    set_sorting_status(True, admin.get("USERNAME", str(query.from_user.id)))
    await query.edit_message_text(
        "📦 <b>Smistamento avviato</b>\n\nLe nuove richieste di spedizione sono temporaneamente bloccate fino al completamento.",
        reply_markup=admin_home_keyboard(), parse_mode="HTML",
    )


async def complete_sorting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update):
        return
    query = update.callback_query
    await query.answer()
    admin = get_admin(query.from_user.id) or {}
    set_sorting_status(False, admin.get("USERNAME", str(query.from_user.id)))
    await query.edit_message_text(
        "✅ <b>Smistamento completato</b>\n\nGli utenti possono nuovamente richiedere le spedizioni.",
        reply_markup=admin_home_keyboard(), parse_mode="HTML",
    )


async def show_bot_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update):
        return
    query = update.callback_query
    await query.answer()
    try:
        status = get_bot_status()
        users = get_orders_grouped_by_user()
        total_orders = sum(u["total_quantity"] for u in users)
        ready = sum(u["ready_quantity"] for u in users)
    except Exception:
        logger.exception("Errore stato bot")
        await query.edit_message_text("⚠️ Impossibile calcolare lo stato del bot.", reply_markup=admin_home_keyboard())
        return
    await query.edit_message_text(
        "📊 <b>Stato bot</b>\n\n"
        f"🤖 Versione: <b>{escape(status['version'])}</b>\n"
        f"👤 Profili: <b>{status['profiles']}</b>\n"
        f"🛠 Admin attivi: <b>{status['admins']}</b>\n"
        f"📦 Articoli totali: <b>{total_orders}</b>\n"
        f"🟢 Articoli pronti: <b>{ready}</b>\n"
        f"🟡 Spedizioni in attesa: <b>{status['shipping_pending']}</b>\n"
        f"✅ Spedizioni inviate: <b>{status['shipping_sent']}</b>\n"
        f"🔄 Smistamento: <b>{'ATTIVO' if status['sorting'] else 'NON ATTIVO'}</b>",
        reply_markup=admin_home_keyboard(), parse_mode="HTML",
    )


async def show_pending_shipping_requests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _show_shipping_requests(update, {"IN_ATTESA"}, False)


async def show_shipping_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _show_shipping_requests(update, None, True)


async def _show_shipping_requests(update: Update, statuses: set[str] | None, history: bool) -> None:
    if not await check_admin(update):
        return
    query = update.callback_query
    await query.answer()
    try:
        requests = get_all_shipping_requests(statuses=statuses)
    except Exception:
        logger.exception("Errore lettura richieste")
        await query.edit_message_text("⚠️ Impossibile leggere le richieste.", reply_markup=admin_home_keyboard())
        return
    title = "🗂 <b>Storico spedizioni</b>" if history else "🚚 <b>Richieste in attesa</b>"
    await query.edit_message_text(
        f"{title}\n\nTotale: <b>{len(requests)}</b>" if requests else f"{title}\n\nNessuna richiesta presente.",
        reply_markup=admin_shipping_list_keyboard(requests, history), parse_mode="HTML",
    )


async def open_shipping_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update):
        return
    query = update.callback_query
    await query.answer()
    shipping_id = query.data.split(":", 1)[1]
    request = get_shipping_request(shipping_id)
    if not request:
        await query.answer("Richiesta non trovata.", show_alert=True)
        return
    tracking = request.get("TRACKING", "") or "—"
    text = (
        "📦 <b>Dettaglio richiesta</b>\n\n"
        f"🆔 <code>{escape(request.get('ID', ''))}</code>\n"
        f"📋 Stato: <b>{escape(request.get('STATO', ''))}</b>\n"
        f"👤 {escape(request.get('USERNAME', ''))} · <code>{escape(request.get('TELEGRAM_ID', ''))}</code>\n\n"
        f"🎴 {escape(request.get('PRODOTTI', ''))}\n\n"
        f"🚚 {escape(request.get('CORRIERE', ''))} · € {escape(request.get('COSTO_SPEDIZIONE', ''))}\n"
        f"🔎 Tracking: <code>{escape(tracking)}</code>\n\n"
        f"📍 <b>{escape(request.get('NOME', ''))}</b>\n{escape(request.get('INDIRIZZO', ''))}\n"
        f"{escape(request.get('CAP', ''))} {escape(request.get('CITTA', ''))} ({escape(request.get('PROVINCIA', ''))})\n"
        f"📞 {escape(request.get('TELEFONO', ''))}\n✉️ {escape(request.get('EMAIL', ''))}"
    )
    await query.edit_message_text(text, reply_markup=admin_shipping_detail_keyboard(shipping_id), parse_mode="HTML")


async def show_shipping_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update):
        return
    query = update.callback_query
    await query.answer()
    shipping_id = query.data.split(":", 1)[1]
    request = get_shipping_request(shipping_id)
    file_id = request.get("PAYMENT_FILE_ID", "") if request else ""
    if not file_id:
        await query.answer("Ricevuta non presente.", show_alert=True)
        return
    try:
        if "TIPO ALLEGATO: FOTO" in request.get("NOTE", "").upper():
            await context.bot.send_photo(query.message.chat_id, file_id, caption=f"📎 Ricevuta {shipping_id}")
        else:
            await context.bot.send_document(query.message.chat_id, file_id, caption=f"📎 Ricevuta {shipping_id}")
    except Exception:
        logger.exception("Errore invio allegato")
        await query.answer("Impossibile aprire la ricevuta.", show_alert=True)


async def start_tracking_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    shipping_id = query.data.split(":", 1)[1]
    if not get_shipping_request(shipping_id):
        await query.answer("Richiesta non trovata.", show_alert=True)
        return ConversationHandler.END
    context.user_data["admin_tracking_shipping_id"] = shipping_id
    await query.edit_message_text(
        f"🚚 <b>Inserimento tracking</b>\n\nRichiesta: <code>{escape(shipping_id)}</code>\n\nInvia il codice tracking.",
        reply_markup=admin_tracking_cancel_keyboard(), parse_mode="HTML",
    )
    return ADMIN_TRACKING


async def receive_tracking(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_admin(update):
        return ConversationHandler.END
    message, user = update.effective_message, update.effective_user
    tracking = message.text.strip() if message and message.text else ""
    if len(tracking) < 4:
        await message.reply_text("⚠️ Tracking non valido. Riprova.")
        return ADMIN_TRACKING
    shipping_id = context.user_data.get("admin_tracking_shipping_id", "")
    admin_data = get_admin(user.id) or {}
    try:
        request = complete_shipping_request(shipping_id, tracking, admin_data.get("USERNAME") or str(user.id))
    except Exception:
        logger.exception("Errore completamento spedizione")
        await message.reply_text("⚠️ Errore durante il salvataggio. Riprova.")
        return ADMIN_TRACKING
    context.user_data.pop("admin_tracking_shipping_id", None)
    try:
        await context.bot.send_message(
            int(request["TELEGRAM_ID"]),
            "📦 <b>La tua spedizione è partita!</b>\n\n"
            f"🆔 <code>{escape(shipping_id)}</code>\n🚚 <b>{escape(request.get('CORRIERE', ''))}</b>\n"
            f"🔎 Tracking: <code>{escape(tracking)}</code>\n\nTrovi tutto nello storico spedizioni.",
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("Notifica tracking non inviata")
    await message.reply_text(
        f"✅ Spedizione <code>{escape(shipping_id)}</code> aggiornata.\nTracking: <code>{escape(tracking)}</code>",
        reply_markup=admin_home_keyboard(), parse_mode="HTML",
    )
    return ConversationHandler.END


async def cancel_tracking_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    context.user_data.pop("admin_tracking_shipping_id", None)
    await query.edit_message_text("❌ Inserimento tracking annullato.", reply_markup=admin_home_keyboard())
    return ConversationHandler.END
