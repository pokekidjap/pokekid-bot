"""Flusso Telegram per l'unione diretta a una spedizione Shipping v2."""
from __future__ import annotations

import asyncio
import logging
from html import escape

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes, ConversationHandler

from keyboards.orders import (
    orders_back_keyboard,
    v2_join_completed_keyboard,
    v2_join_selection_keyboard,
    v2_join_username_keyboard,
)
from services.bot_db import get_admins, write_log
from services.common import clean_value, normalize_username, parse_quantity
from services.perf import track_async_flow
from services.shipping_engine import is_shipping_v2_active
from services.shipping_v2_join import (
    ShippingV2JoinConflictError,
    ShippingV2JoinInvalidProfileError,
    ShippingV2JoinMultipleTargetsError,
    ShippingV2JoinNotFoundError,
    ShippingV2JoinProfileNotFoundError,
    ShippingV2JoinSelfError,
    add_contributor_items_to_v2_shipping,
    find_joinable_v2_shipping_by_username,
    get_joinable_items_for_contributor,
)
from services.shipping_v2_join_session import (
    JOIN_AVAILABLE_ITEMS,
    JOIN_IDEMPOTENCY_KEY,
    JOIN_PAGE,
    JOIN_SELECTED_ITEM_IDS,
    JOIN_SHIPPING_ID,
    JOIN_SHIPPING_UUID,
    JOIN_TARGET_ID,
    JOIN_TARGET_USERNAME,
    clear_shipping_v2_join_session,
    current_join_page,
    ensure_join_idempotency_key,
    initialize_shipping_v2_join_session,
    join_page_count,
    join_selected_item_ids,
    set_join_available_items,
    set_join_page,
    toggle_join_item,
)
from services.shipping_v2_text import compact_item_message, ensure_v2_text_budget
from services.ui import compact_error, page_title, summary_row, with_footer

logger = logging.getLogger(__name__)
SHIPPING_V2_JOIN_USERNAME = 30


def _is_message_not_modified(error: BadRequest) -> bool:
    return "message is not modified" in str(error).lower()


async def _edit_query(query, text: str, reply_markup=None) -> None:
    try:
        await query.edit_message_text(
            with_footer(ensure_v2_text_budget(text)),
            reply_markup=reply_markup,
            parse_mode="HTML",
        )
    except BadRequest as error:
        if _is_message_not_modified(error):
            return
        raise


async def _reply_message(message, text: str, reply_markup=None) -> None:
    await message.reply_text(
        with_footer(ensure_v2_text_budget(text)),
        reply_markup=reply_markup,
        parse_mode="HTML",
    )


async def _record_join_error(user, action: str, error: BaseException) -> None:
    logger.error(
        "Shipping v2 join %s fallito per telegram_id=%s",
        action,
        getattr(user, "id", ""),
        exc_info=error,
    )
    try:
        await asyncio.to_thread(
            write_log,
            telegram_id=getattr(user, "id", ""),
            username=getattr(user, "username", ""),
            action=f"SHIPPING_V2_JOIN_ERRORE_{action.upper()}",
            details=f"{type(error).__name__}: {error}"[:500],
        )
    except Exception:
        logger.exception("Dettaglio errore unione v2 non scritto nel LOG")


def _selection_text(
    *,
    target_username: str,
    shipping_id: str,
    items: list[dict],
    selected: set[str],
    page: int,
) -> str:
    total_units = sum(
        max(0, parse_quantity(item.get("QUANTITA", "")))
        for item in items
    )
    selected_units = sum(
        max(0, parse_quantity(item.get("QUANTITA", "")))
        for item in items
        if clean_value(item.get("ID_ARTICOLO", "")).upper() in selected
    )
    if not items:
        return (
            page_title("📦", "Unisci a una spedizione")
            + "\n\n"
            + f"Titolare: <b>{escape(target_username)}</b>\n"
            + f"Richiesta: <code>{escape(shipping_id)}</code>\n\n"
            + "Non hai articoli disponibili da aggiungere."
        )
    return (
        page_title(
            "📦",
            "Unisci a una spedizione",
            "Seleziona soltanto i tuoi articoli da aggiungere.",
        )
        + "\n\n"
        + f"Titolare: <b>{escape(target_username)}</b>\n"
        + f"Richiesta: <code>{escape(shipping_id)}</code>\n\n"
        + summary_row("📦", "Disponibili", total_units)
        + "\n"
        + summary_row("✅", "Selezionati", selected_units)
        + "\n\n"
        + "Tocca un articolo per selezionarlo o deselezionarlo.\n\n"
        + f"Pagina {page} di {join_page_count(items)}"
    )


def _join_target(user_data) -> tuple[str, str, str, str]:
    target_id = clean_value(user_data.get(JOIN_TARGET_ID, ""))
    target_username = normalize_username(
        user_data.get(JOIN_TARGET_USERNAME, "")
    )
    shipping_id = clean_value(
        user_data.get(JOIN_SHIPPING_ID, "")
    ).upper()
    shipping_uuid = clean_value(
        user_data.get(JOIN_SHIPPING_UUID, "")
    )
    if not all((target_id, target_username, shipping_id, shipping_uuid)):
        raise ShippingV2JoinConflictError(
            "Sessione di unione scaduta o incompleta."
        )
    return target_id, target_username, shipping_id, shipping_uuid


async def _render_selection(query, context) -> None:
    target_id, target_username, shipping_id, shipping_uuid = _join_target(
        context.user_data
    )
    del target_id, shipping_uuid
    items = context.user_data.get(JOIN_AVAILABLE_ITEMS, [])
    if not isinstance(items, list):
        items = []
    selected = join_selected_item_ids(context.user_data)
    page = current_join_page(context.user_data, items)
    await _edit_query(
        query,
        _selection_text(
            target_username=target_username,
            shipping_id=shipping_id,
            items=items,
            selected=selected,
            page=page,
        ),
        reply_markup=v2_join_selection_keyboard(items, selected, page),
    )


async def start_shipping_v2_join(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    query = update.callback_query
    if query is None:
        return ConversationHandler.END
    if not is_shipping_v2_active():
        await query.answer(
            "Questa funzione non è disponibile.",
            show_alert=True,
        )
        return ConversationHandler.END
    await query.answer()
    initialize_shipping_v2_join_session(context.user_data)
    await _edit_query(
        query,
        page_title("📦", "Unisci a una spedizione")
        + "\n\n"
        + "Invia lo username Telegram del titolare della spedizione.\n\n"
        + "Esempio: <code>@TizioB</code> oppure <code>TizioB</code>",
        reply_markup=v2_join_username_keyboard(),
    )
    return SHIPPING_V2_JOIN_USERNAME


@track_async_flow("shipping_v2_join_open")
async def receive_shipping_v2_join_username(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return SHIPPING_V2_JOIN_USERNAME
    if not is_shipping_v2_active():
        clear_shipping_v2_join_session(context.user_data)
        await _reply_message(
            message,
            compact_error("Questa funzione non è più disponibile."),
            reply_markup=orders_back_keyboard(),
        )
        return ConversationHandler.END
    username = normalize_username(message.text if message.text else "")
    try:
        target = await asyncio.to_thread(
            find_joinable_v2_shipping_by_username,
            username,
            user.id,
        )
        items = await asyncio.to_thread(
            get_joinable_items_for_contributor,
            contributor_id=user.id,
            target_id=target["TARGET_TELEGRAM_ID"],
            shipping_id=target["ID"],
            shipping_uuid=target["UUID_SPEDIZIONE"],
        )
    except (
        ShippingV2JoinProfileNotFoundError,
        ShippingV2JoinInvalidProfileError,
    ):
        await _reply_message(
            message,
            "⚠️ <b>Username non trovato</b>\n\n"
            "Controlla lo username e invialo nuovamente.",
            reply_markup=v2_join_username_keyboard(),
        )
        return SHIPPING_V2_JOIN_USERNAME
    except ShippingV2JoinSelfError:
        await _reply_message(
            message,
            "⚠️ <b>Questa è la tua spedizione</b>\n\n"
            "Per aggiungere i tuoi articoli usa la normale richiesta "
            "di spedizione.",
            reply_markup=v2_join_username_keyboard(),
        )
        return SHIPPING_V2_JOIN_USERNAME
    except ShippingV2JoinNotFoundError:
        await _reply_message(
            message,
            "⚠️ <b>Nessuna spedizione disponibile</b>\n\n"
            "L'utente indicato non possiede una spedizione V2 in attesa.",
            reply_markup=v2_join_username_keyboard(),
        )
        return SHIPPING_V2_JOIN_USERNAME
    except ShippingV2JoinMultipleTargetsError:
        clear_shipping_v2_join_session(context.user_data)
        await _reply_message(
            message,
            "⚠️ <b>Operazione da verificare</b>\n\n"
            "Sono presenti più spedizioni disponibili per questo utente. "
            "Contatta lo staff.",
            reply_markup=orders_back_keyboard(),
        )
        return ConversationHandler.END
    except Exception as error:
        await _record_join_error(user, "ricerca", error)
        await _reply_message(
            message,
            compact_error(
                "Non è stato possibile cercare la spedizione. Riprova."
            ),
            reply_markup=v2_join_username_keyboard(),
        )
        return SHIPPING_V2_JOIN_USERNAME

    initialize_shipping_v2_join_session(context.user_data)
    context.user_data[JOIN_TARGET_ID] = target["TARGET_TELEGRAM_ID"]
    context.user_data[JOIN_TARGET_USERNAME] = target["TARGET_USERNAME"]
    context.user_data[JOIN_SHIPPING_ID] = target["ID"]
    context.user_data[JOIN_SHIPPING_UUID] = target["UUID_SPEDIZIONE"]
    selected = set_join_available_items(
        context.user_data,
        items,
        preserve_selection=False,
    )
    page = current_join_page(context.user_data, items)
    await _reply_message(
        message,
        _selection_text(
            target_username=target["TARGET_USERNAME"],
            shipping_id=target["ID"],
            items=items,
            selected=selected,
            page=page,
        ),
        reply_markup=v2_join_selection_keyboard(items, selected, page),
    )
    return ConversationHandler.END


@track_async_flow("shipping_v2_join_toggle")
async def toggle_shipping_v2_join_item(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if not is_shipping_v2_active():
        await query.answer(
            "Questa funzione non è più disponibile.",
            show_alert=True,
        )
        return
    await query.answer()
    item_id = (query.data or "").split(":", 1)[-1].strip().upper()
    try:
        toggle_join_item(context.user_data, item_id)
        await _render_selection(query, context)
    except (ValueError, ShippingV2JoinConflictError):
        clear_shipping_v2_join_session(context.user_data)
        await _edit_query(
            query,
            "⚠️ <b>Selezione scaduta</b>\n\n"
            "Avvia nuovamente l'unione dal menu ordini.",
            reply_markup=orders_back_keyboard(),
        )


async def change_shipping_v2_join_page(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if not is_shipping_v2_active():
        await query.answer(
            "Questa funzione non è più disponibile.",
            show_alert=True,
        )
        return
    await query.answer()
    requested = (query.data or "").split(":", 1)[-1]
    set_join_page(context.user_data, requested)
    try:
        await _render_selection(query, context)
    except ShippingV2JoinConflictError:
        clear_shipping_v2_join_session(context.user_data)
        await _edit_query(
            query,
            "⚠️ <b>Selezione scaduta</b>\n\n"
            "Avvia nuovamente l'unione dal menu ordini.",
            reply_markup=orders_back_keyboard(),
        )


@track_async_flow("shipping_v2_join_refresh")
async def refresh_shipping_v2_join(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if not is_shipping_v2_active():
        await query.answer(
            "Questa funzione non è più disponibile.",
            show_alert=True,
        )
        return
    await query.answer()
    user = query.from_user
    try:
        target_id, _, shipping_id, shipping_uuid = _join_target(
            context.user_data
        )
        items = await asyncio.to_thread(
            get_joinable_items_for_contributor,
            contributor_id=user.id,
            target_id=target_id,
            shipping_id=shipping_id,
            shipping_uuid=shipping_uuid,
        )
        set_join_available_items(
            context.user_data,
            items,
            preserve_selection=True,
        )
        await _render_selection(query, context)
    except ShippingV2JoinConflictError:
        clear_shipping_v2_join_session(context.user_data)
        await _edit_query(
            query,
            "⚠️ <b>Spedizione non più disponibile</b>\n\n"
            "La richiesta è cambiata oppure è stata completata.",
            reply_markup=orders_back_keyboard(),
        )
    except Exception as error:
        await _record_join_error(user, "aggiornamento", error)
        await _edit_query(
            query,
            compact_error("Non è stato possibile aggiornare gli articoli."),
            reply_markup=v2_join_selection_keyboard(
                context.user_data.get(JOIN_AVAILABLE_ITEMS, []),
                join_selected_item_ids(context.user_data),
                current_join_page(context.user_data),
            ),
        )


async def _notify_join_completed(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    result: dict,
    contributor,
    target_id: str,
    target_username: str,
) -> None:
    request = result["shipping"]
    items = result["added_items"]
    count = len(items)
    contributor_username = normalize_username(contributor.username)
    owner_text = with_footer(compact_item_message(
        prefix=(
            "📦 <b>Spedizione aggiornata</b>\n\n"
            f"{escape(contributor_username)} ha aggiunto "
            f"<b>{count}</b> articoli alla tua richiesta di spedizione."
        ),
        items=items,
        source="draft",
    ))
    try:
        await context.bot.send_message(
            chat_id=int(target_id),
            text=owner_text,
            parse_mode="HTML",
        )
    except Exception:
        logger.exception(
            "Notifica unione non inviata al titolare %s",
            target_id,
        )

    admin_text = with_footer(compact_item_message(
        prefix=(
            "📦 <b>Spedizione aggiornata</b>\n\n"
            f"Richiesta: <code>{escape(request.get('ID', ''))}</code>\n"
            f"Titolare: {escape(target_username)}\n"
            f"Contribuente: {escape(contributor_username)}\n"
            f"Articoli aggiunti: <b>{count}</b>"
        ),
        items=items,
        source="draft",
    ))
    try:
        admins = await asyncio.to_thread(get_admins)
    except Exception:
        logger.exception("Impossibile leggere gli admin per l'unione v2")
        return
    for admin in admins:
        telegram_id = clean_value(admin.get("TELEGRAM_ID", ""))
        if not telegram_id:
            continue
        try:
            await context.bot.send_message(
                chat_id=int(telegram_id),
                text=admin_text,
                parse_mode="HTML",
            )
        except Exception:
            logger.exception(
                "Notifica unione non inviata all'admin %s",
                telegram_id,
            )


async def confirm_shipping_v2_join(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if not is_shipping_v2_active():
        await query.answer(
            "Questa funzione non è più disponibile.",
            show_alert=True,
        )
        return
    selected = join_selected_item_ids(context.user_data)
    if not selected:
        await query.answer(
            "Seleziona almeno un articolo.",
            show_alert=True,
        )
        return
    await query.answer()
    user = query.from_user
    try:
        target_id, target_username, shipping_id, shipping_uuid = _join_target(
            context.user_data
        )
        key = ensure_join_idempotency_key(context.user_data)
        result = await asyncio.to_thread(
            add_contributor_items_to_v2_shipping,
            contributor_id=user.id,
            contributor_username=user.username,
            target_id=target_id,
            target_username=target_username,
            shipping_id=shipping_id,
            shipping_uuid=shipping_uuid,
            item_ids=sorted(selected),
            idempotency_key=key,
        )
    except ShippingV2JoinConflictError as error:
        await _record_join_error(user, "conflitto", error)
        try:
            target_id, _, shipping_id, shipping_uuid = _join_target(
                context.user_data
            )
            items = await asyncio.to_thread(
                get_joinable_items_for_contributor,
                contributor_id=user.id,
                target_id=target_id,
                shipping_id=shipping_id,
                shipping_uuid=shipping_uuid,
            )
            context.user_data.pop(JOIN_IDEMPOTENCY_KEY, None)
            set_join_available_items(
                context.user_data,
                items,
                preserve_selection=True,
            )
            await _edit_query(
                query,
                "⚠️ <b>Disponibilità cambiata</b>\n\n"
                "Controlla la selezione aggiornata e conferma nuovamente.",
                reply_markup=v2_join_selection_keyboard(
                    items,
                    join_selected_item_ids(context.user_data),
                    current_join_page(context.user_data, items),
                ),
            )
        except Exception:
            clear_shipping_v2_join_session(context.user_data)
            await _edit_query(
                query,
                "⚠️ <b>Spedizione non più disponibile</b>\n\n"
                "La richiesta è cambiata oppure è stata completata.",
                reply_markup=orders_back_keyboard(),
            )
        return
    except Exception as error:
        await _record_join_error(user, "conferma", error)
        await _edit_query(
            query,
            compact_error(
                "Gli articoli non sono stati confermati. Riprova."
            ),
            reply_markup=v2_join_selection_keyboard(
                context.user_data.get(JOIN_AVAILABLE_ITEMS, []),
                selected,
                current_join_page(context.user_data),
            ),
        )
        return

    await _notify_join_completed(
        context,
        result=result,
        contributor=user,
        target_id=target_id,
        target_username=target_username,
    )
    added = result["added_items"]
    clear_shipping_v2_join_session(context.user_data)
    await _edit_query(
        query,
        compact_item_message(
            prefix=(
                "✅ <b>Articoli aggiunti</b>\n\n"
                f"I tuoi <b>{len(added)}</b> articoli sono stati aggiunti "
                f"alla spedizione di {escape(target_username)}."
            ),
            items=added,
            source="draft",
        ),
        reply_markup=v2_join_completed_keyboard(),
    )


async def cancel_shipping_v2_join(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    query = update.callback_query
    clear_shipping_v2_join_session(context.user_data)
    text = (
        "❌ <b>Unione annullata</b>\n\n"
        "Nessun articolo è stato modificato."
    )
    if query is not None:
        await query.answer()
        await _edit_query(
            query,
            text,
            reply_markup=orders_back_keyboard(),
        )
    elif update.effective_message is not None:
        await _reply_message(
            update.effective_message,
            text,
            reply_markup=orders_back_keyboard(),
        )
    return ConversationHandler.END
