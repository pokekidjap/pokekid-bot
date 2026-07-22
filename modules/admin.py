import logging
from html import escape

from services.perf import start_flow
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from keyboards.admin import (
    admin_broadcast_confirm_keyboard,
    admin_back_keyboard,
    admin_home_keyboard,
    admin_orders_back_keyboard,
    admin_shipping_detail_keyboard,
    admin_shipping_list_keyboard,
    admin_tracking_cancel_keyboard,
    admin_users_keyboard,
    admin_cancel_keyboard,
    admin_messages_keyboard,
)
from services.admin_orders import get_orders_grouped_by_user
from services.bot_db import (
    complete_shipping_request,
    get_admin,
    get_admins,
    get_all_shipping_requests,
    get_bot_status,
    get_config_values,
    get_recent_logs,
    set_config_value,
    write_log,
    get_shipping_request,
    is_admin,
    is_sorting_active,
    set_sorting_status,
)
from services.notifications import notify_warehouse_users, send_broadcast
from services.stats import get_admin_statistics
from services.sorting import clear_sorting_snapshot, get_users_with_new_ready_items, save_sorting_snapshot
from services.ui import BOT_VERSION, LAST_UPDATE, compact_error, with_footer, page_title

logger = logging.getLogger(__name__)
ADMIN_TRACKING = 20
ADMIN_BROADCAST = 21
ADMIN_MESSAGE_EDIT = 22
ADMIN_BROADCAST_CONFIRM = 23


def _admin_response(text: str) -> str:
    return with_footer(text)


async def _edit_admin_query(
    query,
    text: str,
    reply_markup=None,
    parse_mode: str = "HTML",
) -> None:
    await query.edit_message_text(
        _admin_response(text),
        reply_markup=reply_markup,
        parse_mode=parse_mode,
    )


async def _reply_admin_message(
    message,
    text: str,
    reply_markup=None,
    parse_mode: str = "HTML",
) -> None:
    await message.reply_text(
        _admin_response(text),
        reply_markup=reply_markup,
        parse_mode=parse_mode,
    )


async def _admin_error_edit(query, message: str, reply_markup=None) -> None:
    await _edit_admin_query(query, compact_error(message), reply_markup=reply_markup)


async def _admin_error_reply(message_obj, message: str) -> None:
    await _reply_admin_message(message_obj, compact_error(message))


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


def _admin_home_text() -> str:
    return with_footer(
        page_title(
            "👑",
            "ADMIN PANEL",
            "Gestisci ordini, spedizioni e comunicazioni in un unico pannello.",
        )
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update):
        return
    with start_flow("admin_dashboard"):
        await _reply_admin_message(
            update.effective_message,
            _admin_home_text(),
            reply_markup=admin_home_keyboard(),
        )


async def show_admin_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update):
        return
    query = update.callback_query
    await query.answer()
    with start_flow("admin_dashboard"):
        await _edit_admin_query(
            query,
            _admin_home_text(),
            reply_markup=admin_home_keyboard(),
        )


async def show_orders_by_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update):
        return
    query = update.callback_query

    with start_flow("admin_orders_by_user"):
        await query.answer()
        try:
            users = get_orders_grouped_by_user()
        except Exception:
            logger.exception("Errore lettura ordini per utente")
            await _admin_error_edit(query, "Impossibile leggere gli ordini.", reply_markup=admin_home_keyboard())
            return
        context.user_data["admin_order_users"] = users
    await _edit_admin_query(
        query,
        "👥 <b>Ordini per utente</b>\n\n"
        f"Utenti trovati: <b>{len(users)}</b>\n"
        "🟢 = prodotti in magazzino / da gestire",
        reply_markup=admin_users_keyboard(users),
    )


async def show_user_orders_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update):
        return

    query = update.callback_query

    try:
        index = int(query.data.split(":", 1)[1])
        user = context.user_data.get("admin_order_users", [])[index]
    except (ValueError, IndexError, AttributeError):
        await query.answer("Elenco scaduto: aggiorna.", show_alert=True)
        return

    await query.answer()

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

    await _edit_admin_query(
        query,
        text,
        reply_markup=admin_orders_back_keyboard(),
    )


async def start_sorting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update):
        return
    query = update.callback_query
    if is_sorting_active():
        await query.answer("Uno smistamento è già attivo.", show_alert=True)
        return
    await query.answer()
    admin = get_admin(query.from_user.id) or {}
    admin_name = admin.get("USERNAME", str(query.from_user.id))
    try:
        ready_before = save_sorting_snapshot()
        set_sorting_status(True, admin_name)
    except Exception:
        logger.exception("Errore avvio smistamento")
        await _admin_error_edit(query, "Impossibile avviare lo smistamento.", reply_markup=admin_home_keyboard())
        return
    await _edit_admin_query(
        query,
        "📦 <b>Smistamento avviato</b>\n\n"
        f"Snapshot salvato: <b>{ready_before}</b> articoli già in magazzino.\n"
        "Le nuove richieste di spedizione sono temporaneamente bloccate.",
        reply_markup=admin_home_keyboard(),
    )


async def complete_sorting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update):
        return
    query = update.callback_query
    if not is_sorting_active():
        await query.answer("Non risulta alcuno smistamento attivo.", show_alert=True)
        return
    await query.answer()
    admin = get_admin(query.from_user.id) or {}
    admin_name = admin.get("USERNAME", str(query.from_user.id))

    try:
        changed_users = get_users_with_new_ready_items()
    except Exception:
        logger.exception("Errore confronto smistamento")
        await _admin_error_edit(
            query,
            "Impossibile confrontare gli ordini. Lo smistamento non è stato chiuso.",
            reply_markup=admin_home_keyboard(),
        )
        return

    notification_result = await notify_warehouse_users(context.bot, changed_users)
    sent = notification_result["sent"]
    failed = notification_result["failed"]
    missing_profiles = notification_result["missing"]

    set_sorting_status(False, admin_name)
    clear_sorting_snapshot()
    write_log(
        action="NOTIFICHE_SMISTAMENTO",
        details=f"Inviate={sent}; senza profilo={len(missing_profiles)}; errori={failed}",
        admin=admin_name,
    )

    missing_text = ""
    if missing_profiles:
        preview = ", ".join(missing_profiles[:10])
        if len(missing_profiles) > 10:
            preview += f" e altri {len(missing_profiles) - 10}"
        missing_text = (
            f"\n⚠️ Senza profilo: <b>{len(missing_profiles)}</b>\n"
            f"{escape(preview)}"
        )

    await _edit_admin_query(
        query,
        "✅ <b>Smistamento completato</b>\n\n"
        f"🔔 Notifiche inviate: <b>{sent}</b>\n"
        f"❌ Errori di invio: <b>{failed}</b>"
        f"{missing_text}\n\n"
        "Gli utenti possono nuovamente richiedere le spedizioni.",
        reply_markup=admin_home_keyboard(),
    )


async def show_bot_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update):
        return
    query = update.callback_query
    await query.answer()
    try:
        status = get_bot_status()
        database_line = "🟢 Database"
        sheets_line = "🟢 Google Sheets"
    except Exception:
        logger.exception("Errore stato bot")
        status = {
            "version": BOT_VERSION,
            "profiles": 0,
            "admins": len(get_admins()),
            "sorting": False,
        }
        database_line = "🔴 Database"
        sheets_line = "🔴 Google Sheets"

    sorting_line = "🟢 Smistamento ON" if status.get("sorting") else "🔴 Smistamento OFF"
    text = with_footer(
        "🤖 <b>Pokekid Bot</b>\n\n"
        f"Versione: {escape(status.get('version') or BOT_VERSION)}\n\n"
        f"{database_line}\n"
        f"{sheets_line}\n"
        "🟢 Bot Online\n"
        f"{sorting_line}\n\n"
        f"👥 Utenti registrati: {status.get('profiles', 0)}\n"
        f"👑 Admin: {status.get('admins', 0)}\n\n"
        f"Ultimo aggiornamento:\n{LAST_UPDATE}"
    )
    await _edit_admin_query(
        query,
        text,
        reply_markup=admin_home_keyboard(),
    )


async def show_admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update):
        return
    query = update.callback_query
    await query.answer()
    try:
        stats = get_admin_statistics()
    except Exception:
        logger.exception("Errore statistiche admin")
        await _admin_error_edit(query, "Impossibile calcolare le statistiche. Riprova più tardi.", reply_markup=admin_home_keyboard())
        return

    await _edit_admin_query(
        query,
        "📊 <b>Statistiche</b>\n\n"
        f"👥 Utenti registrati: <b>{stats['profiles']}</b>\n"
        f"📦 Articoli attivi: <b>{stats['active_items']}</b>\n"
        f"🟢 In magazzino: <b>{stats['ready_items']}</b>\n"
        f"🔵 Grading: <b>{stats['grading_items']}</b>\n"
        f"🟣 Restauro: <b>{stats['restoration_items']}</b>\n"
        f"🟡 Ordinati: <b>{stats['ordered_items']}</b>\n"
        f"🚚 Spedizioni in attesa: <b>{stats['shipping_pending']}</b>\n"
        f"✅ Spedizioni inviate: <b>{stats['shipping_sent']}</b>\n"
        f"👤 Utenti con ordini: <b>{stats['users_with_orders']}</b>",
        reply_markup=admin_home_keyboard(),
    )


async def show_admin_notifications(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update):
        return
    query = update.callback_query
    await query.answer()
    try:
        logs = get_recent_logs(50)
    except Exception:
        logger.exception("Errore lettura centro notifiche")
        await _admin_error_edit(query, "Impossibile leggere le notifiche.", reply_markup=admin_home_keyboard())
        return

    interesting = {
        "USERNAME_AGGIORNATO": "🔄",
        "NOTIFICHE_SMISTAMENTO": "📦",
        "BROADCAST": "📣",
        "SPEDIZIONE_COMPLETATA": "🚚",
        "UTENTE_REGISTRATO": "👤",
    }
    rows = []
    for item in logs:
        action = item.get("AZIONE", "")
        if action not in interesting:
            continue
        date = item.get("DATA", "")
        username = item.get("USERNAME", "")
        details = item.get("DETTAGLI", "")
        label = action.replace("_", " ").title()
        extra = f" · {username}" if username else ""
        rows.append(
            f"{interesting[action]} <b>{escape(label)}</b>{escape(extra)}\n"
            f"<i>{escape(date)}</i>\n{escape(details[:220])}"
        )

    config = get_config_values()
    seen_raw = config.get(f"ADMIN_NOTIFICHE_LETTE_{query.from_user.id}", {}).get("value", "0")
    try:
        seen = int(seen_raw or 0)
    except ValueError:
        seen = 0
    total_interesting = len(rows)
    unread = max(total_interesting - seen, 0)
    body = "\n\n".join(rows[:8]) if rows else "Nessuna notifica recente."
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Segna tutte come lette", callback_data="admin_notifications_read")],
        [InlineKeyboardButton("⬅️ Pannello Admin", callback_data="admin_home")],
    ])
    await _edit_admin_query(
        query,
        f"🔔 <b>Centro notifiche</b> · Non lette: <b>{unread}</b>\n\n" + body,
        reply_markup=keyboard,
    )


async def mark_admin_notifications_read(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update):
        return
    query = update.callback_query
    await query.answer()
    logs = get_recent_logs(50)
    interesting_actions = {"USERNAME_AGGIORNATO", "NOTIFICHE_SMISTAMENTO", "BROADCAST", "SPEDIZIONE_COMPLETATA", "UTENTE_REGISTRATO"}
    count = sum(1 for item in logs if item.get("AZIONE", "") in interesting_actions)
    set_config_value(f"ADMIN_NOTIFICHE_LETTE_{query.from_user.id}", str(count), True)
    await show_admin_notifications(update, context)


async def show_admin_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update):
        return
    query = update.callback_query
    await query.answer()
    await _edit_admin_query(
        query,
        "💬 <b>Messaggi configurabili</b>\n\n"
        "Scegli il testo da modificare:",
        reply_markup=admin_messages_keyboard(),
    )


async def start_message_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    key = query.data.split(":", 1)[1]
    current = get_config_values().get(key, {}).get("value", "") or "(testo predefinito)"
    context.user_data["admin_message_key"] = key
    await _edit_admin_query(
        query,
        "💬 <b>Modifica messaggio</b>\n\n"
        f"Chiave: <code>{escape(key)}</code>\n\n"
        f"Testo attuale:\n{escape(current)}\n\n"
        "Invia il nuovo testo. Per la notifica magazzino puoi usare "
        "<code>{USERNAME}</code>.",
        reply_markup=admin_cancel_keyboard("admin_messages"),
    )
    return ADMIN_MESSAGE_EDIT


async def receive_message_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_admin(update):
        return ConversationHandler.END
    message = update.effective_message
    key = context.user_data.pop("admin_message_key", "")
    text = (message.text or "").strip() if message else ""
    if not key or not text:
        if message:
            await _admin_error_reply(message, "Testo non valido.")
        return ConversationHandler.END
    try:
        set_config_value(key, text, True)
    except Exception:
        logger.exception("Errore salvataggio messaggio configurabile")
        if message:
            await _admin_error_reply(message, "Impossibile salvare il messaggio.")
        return ConversationHandler.END
    if message:
        await _reply_admin_message(
            message,
            "✅ Messaggio aggiornato correttamente.",
            reply_markup=admin_home_keyboard(),
        )
    return ConversationHandler.END


async def start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    await _edit_admin_query(
        query,
        "📣 <b>Broadcast</b>\n\n"
        "Invia il messaggio da spedire a tutti gli utenti registrati.\n\n"
        "Prima dell'invio vedrai un'anteprima e dovrai confermare.",
        reply_markup=admin_cancel_keyboard(),
    )
    return ADMIN_BROADCAST


async def receive_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Salva l'anteprima: l'invio parte solo dopo conferma esplicita."""
    if not await check_admin(update):
        return ConversationHandler.END
    message_obj = update.effective_message
    message = (message_obj.text or "").strip() if message_obj else ""
    if not message:
        await _admin_error_reply(message_obj, "Messaggio vuoto. Riprova.")
        return ADMIN_BROADCAST

    context.user_data["pending_broadcast"] = message
    await _reply_admin_message(
        message_obj,
        "📣 <b>Conferma broadcast</b>\n\n"
        "Il seguente messaggio sarà inviato a tutti gli utenti registrati:\n\n"
        f"{escape(message)}",
        reply_markup=admin_broadcast_confirm_keyboard(),
    )
    return ADMIN_BROADCAST_CONFIRM


async def confirm_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    message = context.user_data.pop("pending_broadcast", "")
    if not message:
        await _edit_admin_query(
            query,
            "⚠️ Anteprima scaduta. Avvia nuovamente il broadcast.",
            reply_markup=admin_home_keyboard(),
        )
        return ConversationHandler.END
    result = await send_broadcast(context.bot, message)
    admin = get_admin(update.effective_user.id) or {}
    write_log(
        action="BROADCAST",
        details=f"Inviati={result['sent']}; errori={result['failed']}; destinatari={result['total']}",
        admin=admin.get("USERNAME", ""),
    )
    await _edit_admin_query(
        query,
        "✅ <b>Broadcast completato</b>\n\n"
        f"Inviati: <b>{result['sent']}</b>\n"
        f"Errori: <b>{result['failed']}</b>",
        reply_markup=admin_home_keyboard(),
    )
    return ConversationHandler.END


async def cancel_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    context.user_data.pop("pending_broadcast", None)
    await _edit_admin_query(
        query,
        "❌ Broadcast annullato.",
        reply_markup=admin_home_keyboard(),
    )
    return ConversationHandler.END


async def cancel_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    context.user_data.pop("admin_message_key", None)
    await _edit_admin_query(
        query,
        _admin_home_text(),
        reply_markup=admin_home_keyboard(),
    )
    return ConversationHandler.END


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
        await _admin_error_edit(query, "Impossibile leggere le richieste.", reply_markup=admin_home_keyboard())
        return
    title = "🗂 <b>Storico spedizioni</b>" if history else "🚚 <b>Richieste in attesa</b>"
    text = (
        f"{title}\n\nTotale: <b>{len(requests)}</b>"
        if requests
        else f"{title}\n\nNessuna richiesta presente."
    )
    await _edit_admin_query(
        query,
        text,
        reply_markup=admin_shipping_list_keyboard(requests, history),
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
    text = with_footer(
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
    await _edit_admin_query(
        query,
        text,
        reply_markup=admin_shipping_detail_keyboard(shipping_id),
    )


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
        note_text = request.get("NOTE", "").upper() if request else ""
        if "TIPO ALLEGATO: FOTO" in note_text:
            await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=file_id,
                caption=f"📎 Ricevuta {shipping_id}",
            )
        else:
            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=file_id,
                caption=f"📎 Ricevuta {shipping_id}",
            )
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
    await _edit_admin_query(
        query,
        f"🚚 <b>Inserimento tracking</b>\n\n"
        f"Richiesta: <code>{escape(shipping_id)}</code>\n\n"
        "Invia il codice tracking.",
        reply_markup=admin_tracking_cancel_keyboard(),
    )
    return ADMIN_TRACKING


async def receive_tracking(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_admin(update):
        return ConversationHandler.END
    message, user = update.effective_message, update.effective_user
    tracking = message.text.strip() if message and message.text else ""
    if len(tracking) < 4:
        await _admin_error_reply(message, "Tracking non valido. Riprova.")
        return ADMIN_TRACKING
    shipping_id = context.user_data.get("admin_tracking_shipping_id", "")
    admin_data = get_admin(user.id) or {}
    try:
        request = complete_shipping_request(shipping_id, tracking, admin_data.get("USERNAME") or str(user.id))
    except Exception:
        logger.exception("Errore completamento spedizione")
        await _admin_error_reply(message, "Errore durante il salvataggio. Riprova.")
        return ADMIN_TRACKING
    context.user_data.pop("admin_tracking_shipping_id", None)
    try:
        shipping_template = get_config_values().get("MSG_SPEDIZIONE", {}).get("value", "").strip()
        shipping_text = shipping_template or (
            "📦 <b>La tua spedizione è partita!</b>\n\n"
            "🆔 <code>{ID}</code>\n🚚 <b>{CORRIERE}</b>\n"
            "🔎 Tracking: <code>{TRACKING}</code>\n\n"
            "Trovi tutto nello storico spedizioni."
        )
        shipping_text = (
            shipping_text
            .replace("{ID}", escape(shipping_id))
            .replace("{CORRIERE}", escape(request.get("CORRIERE", "")))
            .replace("{TRACKING}", escape(tracking))
            .replace("{USERNAME}", escape(request.get("USERNAME", "")))
        )
        await context.bot.send_message(
            int(request["TELEGRAM_ID"]),
            with_footer(shipping_text),
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("Notifica tracking non inviata")
    await _reply_admin_message(
        message,
        f"✅ Spedizione <code>{escape(shipping_id)}</code> aggiornata.\n"
        f"Tracking: <code>{escape(tracking)}</code>",
        reply_markup=admin_home_keyboard(),
    )
    return ConversationHandler.END


async def cancel_tracking_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    context.user_data.pop("admin_tracking_shipping_id", None)
    await _edit_admin_query(
        query,
        "❌ Inserimento tracking annullato.",
        reply_markup=admin_home_keyboard(),
    )
    return ConversationHandler.END
