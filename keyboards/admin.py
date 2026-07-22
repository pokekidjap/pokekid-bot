from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def admin_home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👥 Ordini", callback_data="admin_orders_users"),
            InlineKeyboardButton("🚚 Spedizioni", callback_data="admin_shipping_list"),
        ],
        [
            InlineKeyboardButton("📦 Avvia", callback_data="admin_sorting_start"),
            InlineKeyboardButton("✅ Completa", callback_data="admin_sorting_complete"),
        ],
        [
            InlineKeyboardButton("📊 Statistiche", callback_data="admin_stats"),
            InlineKeyboardButton("ℹ️ Info bot", callback_data="admin_bot_status"),
        ],
        [
            InlineKeyboardButton("📣 Broadcast", callback_data="admin_broadcast_start"),
            InlineKeyboardButton("🔔 Notifiche", callback_data="admin_notifications"),
        ],
        [
            InlineKeyboardButton("💬 Messaggi", callback_data="admin_messages"),
            InlineKeyboardButton("🗂 Storico", callback_data="admin_shipping_history"),
        ],
        [InlineKeyboardButton("🏠 Menu principale", callback_data="menu_home")],
    ])


def admin_users_keyboard(users: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for index, user in enumerate(users[:50]):
        username = str(user.get("username", ""))
        if len(username) > 24:
            username = username[:21] + "..."
        rows.append([InlineKeyboardButton(
            f"👤 {username} · 🟢 {user['ready_quantity']}/{user['total_quantity']}",
            callback_data=f"admin_user_orders:{index}",
        )])
    rows.append([InlineKeyboardButton("⬅️ Pannello Admin", callback_data="admin_home")])
    return InlineKeyboardMarkup(rows)


def admin_shipping_list_keyboard(requests: list[dict], history: bool = False) -> InlineKeyboardMarkup:
    rows = []
    for request in requests[:40]:
        shipping_id = request.get("ID", "")
        username = request.get("USERNAME", "")
        status = request.get("STATO", "")
        icon = "🟡" if status == "IN_ATTESA" else "✅" if status == "SPEDITO" else "📦"
        rows.append([InlineKeyboardButton(
            f"{icon} {shipping_id} · {username}",
            callback_data=f"admin_shipping_open:{shipping_id}",
        )])
    rows.append([InlineKeyboardButton("🔄 Aggiorna", callback_data="admin_shipping_history" if history else "admin_shipping_list")])
    rows.append([InlineKeyboardButton("⬅️ Pannello Admin", callback_data="admin_home")])
    return InlineKeyboardMarkup(rows)


def admin_shipping_detail_keyboard(shipping_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📎 Mostra ricevuta", callback_data=f"admin_shipping_receipt:{shipping_id}")],
        [InlineKeyboardButton("🚚 Inserisci tracking e spedisci", callback_data=f"admin_shipping_tracking:{shipping_id}")],
        [InlineKeyboardButton("⬅️ Richieste", callback_data="admin_shipping_list")],
    ])


def admin_tracking_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Annulla", callback_data="admin_tracking_cancel")]])


def admin_cancel_keyboard(callback_data: str = "admin_home") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Annulla", callback_data=callback_data)]])


def admin_back_keyboard(callback_data: str = "admin_home") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Indietro", callback_data=callback_data)]])


def admin_orders_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Utenti", callback_data="admin_orders_users")]])


def admin_broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Conferma invio", callback_data="admin_broadcast_confirm")],
        [InlineKeyboardButton("❌ Annulla", callback_data="admin_broadcast_cancel")],
    ])


def admin_messages_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Notifica magazzino", callback_data="admin_message_edit:MSG_MAGAZZINO")],
        [InlineKeyboardButton("👋 Messaggio benvenuto", callback_data="admin_message_edit:MSG_BENVENUTO")],
        [InlineKeyboardButton("🚚 Notifica spedizione", callback_data="admin_message_edit:MSG_SPEDIZIONE")],
        [InlineKeyboardButton("📢 Firma broadcast", callback_data="admin_message_edit:MSG_BROADCAST_FOOTER")],
        [InlineKeyboardButton("⬅️ Pannello Admin", callback_data="admin_home")],
    ])
