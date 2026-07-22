from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def admin_home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Ordini per utente", callback_data="admin_orders_users")],
        [InlineKeyboardButton("📦 Avvia smistamento", callback_data="admin_sorting_start")],
        [InlineKeyboardButton("✅ Completa smistamento", callback_data="admin_sorting_complete")],
        [InlineKeyboardButton("🚚 Richieste spedizione", callback_data="admin_shipping_list")],
        [InlineKeyboardButton("🗂 Storico spedizioni", callback_data="admin_shipping_history")],
        [InlineKeyboardButton("📊 Stato bot", callback_data="admin_bot_status")],
        [InlineKeyboardButton("🏠 Menu principale", callback_data="menu_home")],
    ])


def admin_users_keyboard(users: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for index, user in enumerate(users[:50]):
        rows.append([InlineKeyboardButton(
            f"👤 {user['username']} · 🟢 {user['ready_quantity']}/{user['total_quantity']}",
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
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Annulla inserimento", callback_data="admin_tracking_cancel")]])
