"""Interfaccia Telegram Shipping v2 per spedizioni del solo titolare."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from html import escape
from typing import Any, Callable
from weakref import WeakKeyDictionary

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes, ConversationHandler

from keyboards.orders import (
    orders_back_keyboard,
    shipping_profile_incomplete_keyboard,
    v2_active_draft_keyboard,
    v2_availability_changed_keyboard,
    v2_available_orders_keyboard,
    v2_confirmed_shipping_keyboard,
    v2_retry_cancel_keyboard,
    v2_shipping_carriers_keyboard,
    v2_shipping_receipt_cancel_keyboard,
    v2_shipping_summary_keyboard,
)
from services.bot_db import (
    get_active_shipping_methods,
    get_admins,
    get_paypal_email,
    get_profile,
    is_sorting_active,
    write_log,
)
from services.common import clean_value, parse_quantity
from services.profiles import is_shipping_profile_complete
from services.perf import track_async_flow
from services.reservations import (
    ReservationConflictError,
    ReservationStateError,
    get_active_draft_for_user,
    release_draft,
)
from services.shipping_engine import is_shipping_v2_active
from services.shipping_v2 import (
    ShippingV2ConflictError,
    ShippingV2DraftInvalidError,
    ShippingV2Error,
    ShippingV2ExpiredError,
    create_or_get_v2_shipping_request,
    get_v2_shipping_request_by_draft,
    is_v2_admin_notified,
    prepare_v2_opening_state,
    record_v2_admin_notification,
    reserve_v2_items,
    validate_v2_draft_against_registry,
    validate_v2_draft_for_holder,
)
from services.shipping_v2_session import (
    AVAILABLE_ITEMS,
    DRAFT_UUID,
    IDEMPOTENCY_KEY,
    METHODS,
    PROFILE,
    SELECTED_CARRIER,
    SELECTED_ITEM_IDS,
    WAITING_RECEIPT,
    clear_shipping_v2_session,
    current_page,
    ensure_idempotency_key,
    page_count,
    selected_item_ids,
    set_available_items,
    set_page,
    toggle_item,
)
from services.shipping_v2_text import (
    compact_item_message,
    ensure_v2_text_budget,
)
from services.ui import (
    DIVIDER,
    compact_error,
    page_title,
    section_title,
    summary_row,
    with_footer,
)

logger = logging.getLogger(__name__)
_V2_NOTIFICATION_LOCKS: WeakKeyDictionary = WeakKeyDictionary()


def _notification_lock(shipping_id: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    locks = _V2_NOTIFICATION_LOCKS.setdefault(loop, {})
    return locks.setdefault(shipping_id, asyncio.Lock())


def _is_message_not_modified(error: BadRequest) -> bool:
    return "message is not modified" in str(error).lower()


def _is_query_too_old(error: BadRequest) -> bool:
    return (
        "query is too old and response timeout expired "
        "or query id is invalid"
    ) in str(error).lower()


async def _answer_query(query, *args, **kwargs) -> bool:
    """Conferma una callback ignorando soltanto la scadenza Telegram."""
    try:
        await query.answer(*args, **kwargs)
        return True
    except BadRequest as error:
        if _is_query_too_old(error):
            return False
        raise


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


async def _record_v2_error(
    *,
    user,
    action: str,
    error: BaseException,
) -> None:
    logger.error(
        "Shipping v2 %s fallito per telegram_id=%s",
        action,
        getattr(user, "id", ""),
        exc_info=error,
    )
    try:
        await asyncio.to_thread(
            write_log,
            telegram_id=getattr(user, "id", ""),
            username=getattr(user, "username", ""),
            action=f"SHIPPING_V2_ERRORE_{action.upper()}",
            details=f"{type(error).__name__}: {error}"[:500],
        )
    except Exception:
        logger.exception("Dettaglio errore Shipping v2 non scritto nel LOG")


async def _record_v2_event(
    *,
    user,
    action: str,
    details: str,
) -> None:
    logger.info(
        "Shipping v2 %s per telegram_id=%s",
        action,
        getattr(user, "id", ""),
    )
    try:
        await asyncio.to_thread(
            write_log,
            telegram_id=getattr(user, "id", ""),
            username=getattr(user, "username", ""),
            action=f"SHIPPING_V2_{action.upper()}",
            details=details[:500],
        )
    except Exception:
        logger.exception("Evento Shipping v2 non scritto nel LOG")


async def _require_v2(query) -> bool:
    if is_shipping_v2_active():
        return True
    await _answer_query(
        query,
        "Questa procedura non è più attiva. Riapri gli ordini.",
        show_alert=True,
    )
    return False


def _draft_state(draft: dict) -> str:
    states = {
        clean_value(item.get("STATO_PRENOTAZIONE", "")).upper()
        for item in draft.get("items", [])
    }
    if len(states) != 1:
        raise ShippingV2ConflictError("Stati bozza non uniformi.")
    return next(iter(states))


def _draft_expiry(draft: dict) -> str:
    values = {
        clean_value(item.get("PRENOTATO_FINO_AL", ""))
        for item in draft.get("items", [])
        if clean_value(item.get("PRENOTATO_FINO_AL", ""))
    }
    if not values:
        return ""
    if len(values) != 1:
        raise ShippingV2ConflictError("Scadenze bozza non uniformi.")
    raw = next(iter(values))
    try:
        return datetime.fromisoformat(raw).strftime("%d/%m/%Y alle %H:%M")
    except ValueError:
        raise ShippingV2ConflictError("Scadenza bozza non valida.")


def _draft_items_for_context(draft: dict) -> list[dict]:
    return [
        {
            "ID_ARTICOLO": item.get("ID_ARTICOLO", ""),
            "OGGETTO": item.get("OGGETTO_SNAPSHOT", ""),
            "QUANTITA": item.get("QUANTITA_SNAPSHOT", ""),
        }
        for item in draft.get("items", [])
    ]


def _available_text(
    items: list[dict],
    selected: set[str],
    page: int,
) -> str:
    total_pages = page_count(items)
    if not items:
        return (
            page_title("🟢", "Ordini disponibili")
            + "\n\n"
            "Al momento non hai articoli disponibili per la spedizione.\n\n"
            "Ti avviseremo quando saranno disponibili nuovi prodotti.\n\n"
            f"Pagina {page} di {total_pages}"
        )
    total = sum(max(0, parse_quantity(item.get("QUANTITA"))) for item in items)
    selected_total = sum(
        max(0, parse_quantity(item.get("QUANTITA")))
        for item in items
        if clean_value(item.get("ID_ARTICOLO", "")).upper() in selected
    )
    return (
        page_title(
            "🟢",
            "Ordini disponibili",
            "Seleziona gli articoli che vuoi ricevere.",
        )
        + "\n\n"
        + summary_row("📦", "Disponibili", total)
        + "\n"
        + summary_row("✅", "Selezionati", selected_total)
        + "\n\n"
        "Tocca un articolo per selezionarlo o deselezionarlo.\n\n"
        f"Pagina {page} di {total_pages}"
    )


async def _render_available(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    state: dict[str, Any] | None = None,
    force_refresh: bool = False,
) -> None:
    if state is None:
        state = await asyncio.to_thread(
            prepare_v2_opening_state,
            query.from_user.id,
            force_refresh=force_refresh,
        )
    active = state.get("active_draft")
    if active:
        clear_shipping_v2_session(context.user_data)
        context.user_data[DRAFT_UUID] = active.get("uuid_bozza", "")
        context.user_data[IDEMPOTENCY_KEY] = active.get(
            "idempotency_key",
            "",
        )
        current_state = _draft_state(active)
        if current_state == "PRENOTATO":
            expiry = _draft_expiry(active)
            await _edit_query(
                query,
                compact_item_message(
                    prefix=(
                        page_title("📦", "Spedizione in preparazione")
                        + "\n\n"
                        + section_title("🎴", "Articoli prenotati")
                    ),
                    items=active.get("items", []),
                    source="draft",
                    suffix=(
                        f"⏳ Scadenza: <b>{escape(expiry)}</b>\n\n"
                        "Riprendi la procedura oppure annulla la bozza."
                    ),
                ),
                reply_markup=v2_active_draft_keyboard(),
            )
            return
        if current_state == "CONFERMATO":
            request = await asyncio.to_thread(
                get_v2_shipping_request_by_draft,
                active.get("uuid_bozza", ""),
            )
            if not request:
                raise ShippingV2ConflictError(
                    "Bozza confermata senza richiesta recuperabile."
                )
            await _edit_query(
                query,
                compact_item_message(
                    prefix=(
                        page_title("✅", "Richiesta già confermata")
                        + "\n\n"
                        + f"🆔 Richiesta: <code>{escape(request.get('ID', ''))}</code>\n"
                        + "📋 Stato: <b>In attesa</b>\n\n"
                        + section_title("🎴", "Articoli")
                    ),
                    items=active.get("items", []),
                    source="draft",
                ),
                reply_markup=v2_confirmed_shipping_keyboard(),
            )
            return
        raise ShippingV2ConflictError("Bozza attiva in stato non previsto.")

    items = state.get("available_items", [])
    selected = set_available_items(
        context.user_data,
        items,
        preserve_selection=True,
    )
    page = current_page(context.user_data, items)
    await _edit_query(
        query,
        _available_text(items, selected, page),
        reply_markup=v2_available_orders_keyboard(items, selected, page),
    )


def _available_flow_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> str:
    del context
    query = update.callback_query
    return (
        "shipping_v2_refresh_available"
        if query is not None and query.data == "orders_refresh"
        else "shipping_v2_open_available"
    )


@track_async_flow(_available_flow_name)
async def show_v2_available_orders(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if not await _require_v2(query):
        return
    await _answer_query(query)
    try:
        await _render_available(
            query,
            context,
            force_refresh=query.data == "orders_refresh",
        )
    except Exception as error:
        await _record_v2_error(
            user=query.from_user,
            action="apertura",
            error=error,
        )
        await _edit_query(
            query,
            compact_error(
                "Le spedizioni sono temporaneamente indisponibili. "
                "Riprova più tardi."
            ),
            reply_markup=orders_back_keyboard(),
        )


@track_async_flow("shipping_v2_toggle_item")
async def toggle_v2_available_item(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if not await _require_v2(query):
        return
    await _answer_query(query)
    item_id = (query.data or "").split(":", 1)[-1].strip().upper()
    try:
        selected = toggle_item(context.user_data, item_id)
    except ValueError:
        await _edit_query(
            query,
            "⚠️ <b>Articolo non disponibile</b>\n\n"
            "Aggiorna l’elenco e ripeti la selezione.",
            reply_markup=orders_back_keyboard(),
        )
        return
    items = context.user_data.get(AVAILABLE_ITEMS, [])
    page = current_page(context.user_data, items)
    await _edit_query(
        query,
        _available_text(items, selected, page),
        reply_markup=v2_available_orders_keyboard(items, selected, page),
    )


@track_async_flow("shipping_v2_change_page")
async def change_v2_items_page(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if not await _require_v2(query):
        return
    await _answer_query(query)
    requested = (query.data or "").split(":", 1)[-1]
    page = set_page(context.user_data, requested)
    items = context.user_data.get(AVAILABLE_ITEMS, [])
    selected = selected_item_ids(context.user_data)
    await _edit_query(
        query,
        _available_text(items, selected, page),
        reply_markup=v2_available_orders_keyboard(items, selected, page),
    )


def _carrier_screen_text(draft: dict) -> str:
    expiry = _draft_expiry(draft)
    return compact_item_message(
        prefix=(
            page_title("📦", "Prepara la spedizione")
            + "\n\n"
            + section_title("🎴", "Articoli prenotati")
        ),
        items=draft.get("items", []),
        source="draft",
        suffix=(
            f"{DIVIDER}\n\n"
            f"⏳ Prenotazione valida fino al <b>{escape(expiry)}</b>\n\n"
            + section_title("🚚", "Scegli il corriere")
            + "\n\nSeleziona il metodo di spedizione."
        ),
    )


def _confirmed_request_text(request: dict, items: list[dict]) -> str:
    return compact_item_message(
        prefix=(
            page_title("✅", "Richiesta già confermata")
            + "\n\n"
            + f"🆔 Richiesta: <code>{escape(request.get('ID', ''))}</code>\n"
            + "📋 Stato: <b>In attesa</b>\n\n"
            + section_title("🎴", "Articoli")
        ),
        items=items,
        source="draft",
    )


async def _recover_confirmed_request(
    *,
    target,
    context: ContextTypes.DEFAULT_TYPE,
    draft: dict,
    edit: bool,
) -> None:
    request = await asyncio.to_thread(
        get_v2_shipping_request_by_draft,
        draft.get("uuid_bozza", ""),
    )
    if not request:
        raise ShippingV2ConflictError(
            "Bozza confermata senza richiesta recuperabile."
        )
    request = dict(request)
    request["_V2_ITEM_SNAPSHOTS"] = list(draft.get("items", []))
    await _notify_v2_admins(context, request)
    clear_shipping_v2_session(context.user_data)
    text = _confirmed_request_text(request, draft.get("items", []))
    if edit:
        await _edit_query(
            target,
            text,
            reply_markup=v2_confirmed_shipping_keyboard(),
        )
    else:
        await _reply_message(
            target,
            text,
            reply_markup=v2_confirmed_shipping_keyboard(),
        )


async def _release_draft_after_registry_change(
    user,
    context: ContextTypes.DEFAULT_TYPE,
    draft_uuid: str,
) -> dict | None:
    try:
        await asyncio.to_thread(
            release_draft,
            draft_uuid,
            reason="DISPONIBILITA_CAMBIATA",
        )
    except ReservationStateError:
        current = await asyncio.to_thread(
            validate_v2_draft_for_holder,
            draft_uuid,
            user.id,
            allowed_states=("PRENOTATO", "CONFERMATO", "RILASCIATO"),
            allow_expired=True,
        )
        if "CONFERMATO" in current["states"]:
            return current
        if current["states"] != {"RILASCIATO"}:
            raise
    clear_shipping_v2_session(context.user_data)
    await _record_v2_event(
        user=user,
        action="DISPONIBILITA_CAMBIATA",
        details=(
            "Bozza PRENOTATO rilasciata dopo rivalidazione del registro."
        ),
    )
    return None


async def _handle_invalid_draft(
    *,
    target,
    user,
    context: ContextTypes.DEFAULT_TYPE,
    draft_uuid: str,
    edit: bool,
) -> None:
    confirmed = await _release_draft_after_registry_change(
        user,
        context,
        draft_uuid,
    )
    if confirmed is not None:
        await _recover_confirmed_request(
            target=target,
            context=context,
            draft=confirmed,
            edit=edit,
        )
        return
    text = (
        page_title("⚠️", "Disponibilità cambiata")
        + "\n\n"
        "Uno o più articoli non sono più disponibili. "
        "La bozza è stata rilasciata: seleziona nuovamente gli articoli."
    )
    if edit:
        await _edit_query(
            target,
            text,
            reply_markup=v2_availability_changed_keyboard(),
        )
    else:
        await _reply_message(
            target,
            text,
            reply_markup=v2_availability_changed_keyboard(),
        )


async def continue_v2_shipping(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if not await _require_v2(query):
        return
    selected = selected_item_ids(context.user_data)
    answer_args = (
        ("Seleziona almeno un articolo.",)
        if not selected
        else ()
    )
    answer_kwargs = {"show_alert": True} if not selected else {}
    await _answer_query(query, *answer_args, **answer_kwargs)
    if not selected:
        return
    user = query.from_user
    previous_items = context.user_data.get(AVAILABLE_ITEMS, [])
    try:
        if await asyncio.to_thread(is_sorting_active):
            await _edit_query(
                query,
                page_title("📦", "Smistamento in corso")
                + "\n\n"
                "Le richieste di spedizione sono temporaneamente sospese.",
                reply_markup=orders_back_keyboard(),
            )
            return
        profile = await asyncio.to_thread(
            get_profile,
            user.id,
            force_refresh=True,
        )
        if not is_shipping_profile_complete(profile):
            await _edit_query(
                query,
                "👤 <b>Il mio profilo</b>\n\n"
                "⚠️ <b>Profilo di spedizione da completare</b>\n\n"
                "Prima di richiedere una spedizione completa tutti i dati "
                "nella sezione Profilo.",
                reply_markup=shipping_profile_incomplete_keyboard(
                    has_profile=profile is not None,
                ),
            )
            return
        methods = await asyncio.to_thread(get_active_shipping_methods)
        if not methods:
            await _edit_query(
                query,
                compact_error("Al momento non risultano corrieri attivi."),
                reply_markup=orders_back_keyboard(),
            )
            return
        key = ensure_idempotency_key(context.user_data)
        draft = await asyncio.to_thread(
            reserve_v2_items,
            holder_id=user.id,
            username=user.username,
            item_ids=sorted(selected),
            idempotency_key=key,
        )
    except ReservationConflictError as error:
        context.user_data.pop(IDEMPOTENCY_KEY, None)
        await _record_v2_error(
            user=user,
            action="conflitto_prenotazione",
            error=error,
        )
        try:
            state = await asyncio.to_thread(
                prepare_v2_opening_state,
                user.id,
                force_refresh=True,
            )
            if state.get("active_draft"):
                await _render_available(query, context, state=state)
                return
            items = state.get("available_items", [])
            current_ids = {
                clean_value(item.get("ID_ARTICOLO", "")).upper()
                for item in items
            }
            unavailable_ids = selected.difference(current_ids)
            previous_by_id = {
                clean_value(item.get("ID_ARTICOLO", "")).upper(): clean_value(
                    item.get("OGGETTO", "")
                )
                for item in previous_items
            }
            unavailable_names = [
                previous_by_id[item_id]
                for item_id in sorted(unavailable_ids)
                if previous_by_id.get(item_id)
            ]
            selected_now = set_available_items(
                context.user_data,
                items,
                preserve_selection=True,
            )
            unavailable_text = (
                "\n\nNon più disponibili:\n"
                + "\n".join(
                    f"• <b>{escape(name)}</b>"
                    for name in unavailable_names
                )
                if unavailable_names
                else ""
            )
            await _edit_query(
                query,
                page_title("⚠️", "Disponibilità cambiata")
                + "\n\n"
                "La disponibilità è cambiata. Seleziona nuovamente "
                "gli articoli disponibili."
                + unavailable_text,
                reply_markup=v2_available_orders_keyboard(
                    items,
                    selected_now,
                    current_page(context.user_data, items),
                ),
            )
        except Exception as refresh_error:
            await _record_v2_error(
                user=user,
                action="refresh_conflitto",
                error=refresh_error,
            )
            await _edit_query(
                query,
                compact_error("Non è stato possibile aggiornare gli articoli."),
                reply_markup=orders_back_keyboard(),
            )
        return
    except Exception as error:
        await _record_v2_error(
            user=user,
            action="prenotazione",
            error=error,
        )
        await _edit_query(
            query,
            compact_error(
                "Non è stato possibile prenotare gli articoli. Riprova."
            ),
            reply_markup=orders_back_keyboard(),
        )
        return

    context.user_data[DRAFT_UUID] = draft["uuid_bozza"]
    context.user_data[IDEMPOTENCY_KEY] = draft["idempotency_key"]
    context.user_data[PROFILE] = profile
    context.user_data[METHODS] = methods
    context.user_data[AVAILABLE_ITEMS] = _draft_items_for_context(draft)
    context.user_data[SELECTED_ITEM_IDS] = {
        item["ID_ARTICOLO"]
        for item in draft["items"]
    }
    await _edit_query(
        query,
        _carrier_screen_text(draft),
        reply_markup=v2_shipping_carriers_keyboard(methods),
    )


async def resume_v2_shipping(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if not await _require_v2(query):
        return
    await _answer_query(query)
    user = query.from_user
    draft_uuid = clean_value(context.user_data.get(DRAFT_UUID, ""))
    try:
        state = await asyncio.to_thread(
            prepare_v2_opening_state,
            user.id,
        )
        draft = state.get("active_draft")
        if not draft:
            clear_shipping_v2_session(context.user_data)
            await _render_available(query, context, state=state)
            return
        if _draft_state(draft) == "CONFERMATO":
            await _render_available(query, context, state=state)
            return
        validated = await asyncio.to_thread(
            validate_v2_draft_for_holder,
            draft["uuid_bozza"],
            user.id,
        )
        profile = await asyncio.to_thread(
            get_profile,
            user.id,
            force_refresh=True,
        )
        methods = await asyncio.to_thread(get_active_shipping_methods)
        if not is_shipping_profile_complete(profile):
            await _edit_query(
                query,
                "⚠️ <b>Profilo di spedizione da completare</b>\n\n"
                "Completa il profilo prima di riprendere la spedizione.",
                reply_markup=shipping_profile_incomplete_keyboard(
                    has_profile=profile is not None,
                ),
            )
            return
        if not methods:
            raise ShippingV2Error("Nessun corriere attivo.")
    except Exception as error:
        await _record_v2_error(
            user=user,
            action="ripresa",
            error=error,
        )
        await _edit_query(
            query,
            compact_error("Non è stato possibile riprendere la spedizione."),
            reply_markup=v2_active_draft_keyboard(),
        )
        return

    clear_shipping_v2_session(context.user_data)
    context.user_data[DRAFT_UUID] = validated["uuid_bozza"]
    context.user_data[IDEMPOTENCY_KEY] = validated["idempotency_key"]
    context.user_data[PROFILE] = profile
    context.user_data[METHODS] = methods
    context.user_data[AVAILABLE_ITEMS] = _draft_items_for_context(validated)
    context.user_data[SELECTED_ITEM_IDS] = {
        item["ID_ARTICOLO"]
        for item in validated["items"]
    }
    await _edit_query(
        query,
        _carrier_screen_text(validated),
        reply_markup=v2_shipping_carriers_keyboard(methods),
    )


async def select_v2_shipping_carrier(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if not await _require_v2(query):
        return
    await _answer_query(query)
    user = query.from_user
    draft_uuid = clean_value(context.user_data.get(DRAFT_UUID, ""))
    try:
        index = int((query.data or "").split(":", 1)[1])
        methods = context.user_data.get(METHODS)
        if not isinstance(methods, list):
            methods = await asyncio.to_thread(get_active_shipping_methods)
        if index < 0 or index >= len(methods):
            raise ShippingV2ConflictError("Corriere non disponibile.")
        if not draft_uuid:
            active = await asyncio.to_thread(
                get_active_draft_for_user,
                user.id,
            )
            draft_uuid = active.get("uuid_bozza", "") if active else ""
        draft = await asyncio.to_thread(
            validate_v2_draft_against_registry,
            draft_uuid,
            user.id,
        )
        if "CONFERMATO" in draft["states"]:
            await _recover_confirmed_request(
                target=query,
                context=context,
                draft=draft,
                edit=True,
            )
            return
        profile = await asyncio.to_thread(
            get_profile,
            user.id,
            force_refresh=True,
        )
        if not is_shipping_profile_complete(profile):
            raise ShippingV2ConflictError("Profilo incompleto.")
        selected_carrier = methods[index]
        paypal_email = await asyncio.to_thread(get_paypal_email)
    except ShippingV2DraftInvalidError:
        try:
            await _handle_invalid_draft(
                target=query,
                user=user,
                context=context,
                draft_uuid=draft_uuid,
                edit=True,
            )
        except Exception as error:
            await _record_v2_error(
                user=user,
                action="rilascio_disponibilita",
                error=error,
            )
            await _edit_query(
                query,
                compact_error(
                    "Non è stato possibile aggiornare la disponibilità."
                ),
                reply_markup=v2_active_draft_keyboard(),
            )
        return
    except Exception as error:
        await _record_v2_error(
            user=user,
            action="corriere",
            error=error,
        )
        await _edit_query(
            query,
            compact_error("I dati della spedizione non sono più disponibili."),
            reply_markup=v2_active_draft_keyboard(),
        )
        return

    context.user_data[DRAFT_UUID] = draft["uuid_bozza"]
    context.user_data[IDEMPOTENCY_KEY] = draft["idempotency_key"]
    context.user_data[METHODS] = methods
    context.user_data[PROFILE] = profile
    context.user_data[SELECTED_CARRIER] = selected_carrier
    expiry = _draft_expiry(draft)
    text = compact_item_message(
        prefix=(
            page_title("📦", "Riepilogo spedizione")
            + "\n\n"
            + section_title("🎴", "Articoli")
        ),
        items=draft["items"],
        source="draft",
        suffix=(
            section_title("🚚", "Spedizione")
            + "\n"
            + f"Corriere: <b>{escape(selected_carrier['name'])}</b>\n"
            + f"Costo: <b>€ {selected_carrier['price']:.2f}</b>\n\n"
            + section_title("📍", "Destinazione")
            + "\n"
            + f"{escape(profile.get('NOME', ''))}\n"
            + f"{escape(profile.get('INDIRIZZO', ''))}\n"
            + f"{escape(profile.get('CAP', ''))} "
            + f"{escape(profile.get('CITTA', ''))} "
            + f"({escape(profile.get('PROVINCIA', ''))})\n\n"
            + section_title("💳", "Pagamento")
            + "\n"
            + f"PayPal: <code>{escape(paypal_email)}</code>\n\n"
            + f"⏳ Prenotazione valida fino al <b>{escape(expiry)}</b>.\n"
            + "Superata la scadenza dovrai ripetere la procedura.\n\n"
            + "Dopo il pagamento premi il pulsante e invia la ricevuta."
        ),
    )
    await _edit_query(
        query,
        text,
        reply_markup=v2_shipping_summary_keyboard(),
    )


async def _release_user_draft(
    user,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    reason: str,
) -> bool:
    draft_uuid = clean_value(context.user_data.get(DRAFT_UUID, ""))
    if not draft_uuid:
        active = await asyncio.to_thread(
            get_active_draft_for_user,
            user.id,
        )
        if active:
            draft_uuid = active.get("uuid_bozza", "")
    if not draft_uuid:
        clear_shipping_v2_session(context.user_data)
        return True
    await asyncio.to_thread(
        validate_v2_draft_for_holder,
        draft_uuid,
        user.id,
        allowed_states=("PRENOTATO", "RILASCIATO"),
        allow_expired=True,
    )
    await asyncio.to_thread(
        release_draft,
        draft_uuid,
        reason=reason,
    )
    clear_shipping_v2_session(context.user_data)
    return True


async def cancel_v2_shipping(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if not await _require_v2(query):
        return
    await _answer_query(query)
    callback = query.data or ""
    reason = (
        "CAMBIO_ARTICOLI"
        if callback == "shipping_v2_change_items"
        else "ANNULLATA_UTENTE"
    )
    try:
        await _release_user_draft(
            query.from_user,
            context,
            reason=reason,
        )
    except Exception as error:
        await _record_v2_error(
            user=query.from_user,
            action="rilascio",
            error=error,
        )
        await _edit_query(
            query,
            compact_error(
                "La bozza non è stata annullata. Riprova per completare "
                "l’operazione."
            ),
            reply_markup=v2_retry_cancel_keyboard(callback),
        )
        return
    if callback == "shipping_v2_change_items":
        try:
            state = await asyncio.to_thread(
                prepare_v2_opening_state,
                query.from_user.id,
            )
            await _render_available(query, context, state=state)
        except Exception as error:
            await _record_v2_error(
                user=query.from_user,
                action="ritorno_selezione",
                error=error,
            )
            await _edit_query(
                query,
                compact_error("Bozza annullata. Riapri gli ordini."),
                reply_markup=orders_back_keyboard(),
            )
        return
    await _edit_query(
        query,
        "❌ <b>Bozza annullata</b>\n\n"
        "Gli articoli sono stati rilasciati. "
        "Nessuna richiesta è stata salvata.",
        reply_markup=orders_back_keyboard(),
    )


async def start_v2_shipping_payment(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    receipt_state: int,
) -> int:
    query = update.callback_query
    if not await _require_v2(query):
        return ConversationHandler.END
    await _answer_query(query)
    user = query.from_user
    draft_uuid = clean_value(context.user_data.get(DRAFT_UUID, ""))
    try:
        if await asyncio.to_thread(is_sorting_active):
            await _edit_query(
                query,
                page_title("📦", "Smistamento in corso")
                + "\n\n"
                "Le richieste di spedizione sono temporaneamente sospese.",
                reply_markup=orders_back_keyboard(),
            )
            return ConversationHandler.END
        draft = await asyncio.to_thread(
            validate_v2_draft_against_registry,
            draft_uuid,
            user.id,
        )
        if "CONFERMATO" in draft["states"]:
            await _recover_confirmed_request(
                target=query,
                context=context,
                draft=draft,
                edit=True,
            )
            return ConversationHandler.END
        carrier = context.user_data.get(SELECTED_CARRIER)
        profile = context.user_data.get(PROFILE)
        if not carrier or not is_shipping_profile_complete(profile):
            raise ShippingV2ConflictError(
                "Sessione v2 incompleta prima della ricevuta."
            )
    except ShippingV2DraftInvalidError:
        try:
            await _handle_invalid_draft(
                target=query,
                user=user,
                context=context,
                draft_uuid=draft_uuid,
                edit=True,
            )
        except Exception as error:
            await _record_v2_error(
                user=user,
                action="rilascio_disponibilita",
                error=error,
            )
            await _edit_query(
                query,
                compact_error(
                    "Non è stato possibile aggiornare la disponibilità."
                ),
                reply_markup=v2_active_draft_keyboard(),
            )
        return ConversationHandler.END
    except ShippingV2ExpiredError as error:
        try:
            await _release_user_draft(
                user,
                context,
                reason="TTL_SCADUTO",
            )
        except Exception as release_error:
            await _record_v2_error(
                user=user,
                action="rilascio_scaduta",
                error=release_error,
            )
            await _edit_query(
                query,
                compact_error(
                    "Non è stato possibile rilasciare la prenotazione. "
                    "Riprova."
                ),
                reply_markup=v2_retry_cancel_keyboard(
                    "shipping_v2_cancel",
                ),
            )
            return ConversationHandler.END
        await _record_v2_error(
            user=user,
            action="prenotazione_scaduta",
            error=error,
        )
        await _edit_query(
            query,
            page_title("⌛", "Prenotazione scaduta")
            + "\n\n"
            "La prenotazione è scaduta. Seleziona nuovamente gli articoli.",
            reply_markup=orders_back_keyboard(),
        )
        return ConversationHandler.END
    except Exception as error:
        await _record_v2_error(
            user=user,
            action="avvio_ricevuta",
            error=error,
        )
        await _edit_query(
            query,
            compact_error("Non è stato possibile aprire l’invio ricevuta."),
            reply_markup=v2_active_draft_keyboard(),
        )
        return ConversationHandler.END

    context.user_data[DRAFT_UUID] = draft["uuid_bozza"]
    context.user_data[IDEMPOTENCY_KEY] = draft["idempotency_key"]
    context.user_data[WAITING_RECEIPT] = True
    await _edit_query(
        query,
        page_title("📎", "Invia la ricevuta")
        + "\n\n"
        "Invia una foto oppure un documento/PDF.\n\n"
        + section_title("🚚", "Spedizione")
        + "\n"
        + f"Corriere: <b>{escape(carrier['name'])}</b>\n"
        + f"Importo: <b>€ {carrier['price']:.2f}</b>",
        reply_markup=v2_shipping_receipt_cancel_keyboard(),
    )
    return receipt_state


async def _notify_v2_admins(
    context: ContextTypes.DEFAULT_TYPE,
    request: dict,
) -> None:
    shipping_id = clean_value(request.get("ID", "")).upper()
    items = list(request.get("_V2_ITEM_SNAPSHOTS", []))
    text = with_footer(compact_item_message(
        prefix=(
            "📦 <b>Nuova richiesta di spedizione</b>\n\n"
            f"🆔 <code>{escape(shipping_id)}</code>\n"
            f"👤 {escape(request.get('USERNAME', ''))}\n"
            f"🚚 {escape(request.get('CORRIERE', ''))}\n"
            f"💶 € {escape(str(request.get('COSTO_SPEDIZIONE', '')))}"
        ),
        items=items,
        source="draft",
    ))
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "👁 Apri richiesta",
            callback_data=f"admin_shipping_open:{shipping_id}",
        )
    ]])
    async with _notification_lock(shipping_id):
        admins = await asyncio.to_thread(get_admins)
        for admin in admins:
            telegram_id = clean_value(admin.get("TELEGRAM_ID", ""))
            if not telegram_id:
                continue
            try:
                already_notified = await asyncio.to_thread(
                    is_v2_admin_notified,
                    shipping_id,
                    telegram_id,
                )
            except Exception:
                # Se il marker non è leggibile si preferisce un possibile
                # duplicato a una perdita silenziosa della notifica.
                logger.exception(
                    "Marker notifica v2 non leggibile per admin %s",
                    telegram_id,
                )
                already_notified = False
            if already_notified:
                continue
            try:
                await context.bot.send_message(
                    chat_id=int(telegram_id),
                    text=text,
                    reply_markup=keyboard,
                    parse_mode="HTML",
                )
            except Exception:
                logger.exception(
                    "Notifica richiesta v2 non inviata all'admin %s",
                    telegram_id,
                )
                continue
            try:
                await asyncio.to_thread(
                    record_v2_admin_notification,
                    shipping_id,
                    telegram_id,
                )
            except Exception:
                logger.exception(
                    "Marker notifica v2 non scritto per admin %s",
                    telegram_id,
                )


async def finalize_v2_and_notify(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    finalizer: Callable[..., dict] = create_or_get_v2_shipping_request,
    notifier: Callable[..., Any] = _notify_v2_admins,
    finalizer_kwargs: dict[str, Any],
) -> dict:
    request = await asyncio.to_thread(finalizer, **finalizer_kwargs)
    await notifier(context, request)
    return request


async def receive_v2_shipping_receipt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    receipt_state: int,
) -> int:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return receipt_state
    payment_file_id = ""
    payment_type = ""
    if message.photo:
        payment_file_id = message.photo[-1].file_id
        payment_type = "FOTO"
    elif message.document:
        payment_file_id = message.document.file_id
        payment_type = "DOCUMENTO"
    if not payment_file_id:
        await _reply_message(
            message,
            "⚠️ <b>Allegato non valido</b>\n\n"
            "Invia una foto oppure un documento/PDF.",
            reply_markup=v2_shipping_receipt_cancel_keyboard(),
        )
        return receipt_state

    draft_uuid = clean_value(context.user_data.get(DRAFT_UUID, ""))
    try:
        if await asyncio.to_thread(is_sorting_active):
            await _release_user_draft(
                user,
                context,
                reason="SMISTAMENTO_AVVIATO",
            )
            await _reply_message(
                message,
                page_title("📦", "Smistamento in corso")
                + "\n\n"
                "Lo smistamento è iniziato durante la procedura. "
                "Gli articoli sono stati rilasciati.",
                reply_markup=orders_back_keyboard(),
            )
            return ConversationHandler.END
        profile = await asyncio.to_thread(
            get_profile,
            user.id,
            force_refresh=True,
        )
        if not is_shipping_profile_complete(profile):
            raise ShippingV2ConflictError("Profilo modificato o incompleto.")
        methods = await asyncio.to_thread(get_active_shipping_methods)
        carrier = context.user_data.get(SELECTED_CARRIER)
        if not carrier or not any(
            method.get("name") == carrier.get("name")
            and abs(float(method.get("price")) - float(carrier.get("price")))
            < 0.000001
            for method in methods
        ):
            raise ShippingV2ConflictError("Corriere non più disponibile.")
        if not draft_uuid:
            active = await asyncio.to_thread(
                get_active_draft_for_user,
                user.id,
            )
            draft_uuid = active.get("uuid_bozza", "") if active else ""
        draft = await asyncio.to_thread(
            validate_v2_draft_against_registry,
            draft_uuid,
            user.id,
        )
        key = draft["idempotency_key"]
        context.user_data[DRAFT_UUID] = draft["uuid_bozza"]
        context.user_data[IDEMPOTENCY_KEY] = key
        request = await finalize_v2_and_notify(
            context,
            finalizer_kwargs={
                "draft_uuid": draft["uuid_bozza"],
                "holder_id": user.id,
                "username": user.username,
                "payment_file_id": payment_file_id,
                "payment_type": payment_type,
                "profile": profile,
                "carrier": carrier["name"],
                "shipping_cost": carrier["price"],
                "idempotency_key": key,
            },
        )
    except ShippingV2DraftInvalidError:
        try:
            await _handle_invalid_draft(
                target=message,
                user=user,
                context=context,
                draft_uuid=draft_uuid,
                edit=False,
            )
        except Exception as error:
            await _record_v2_error(
                user=user,
                action="rilascio_disponibilita",
                error=error,
            )
            await _reply_message(
                message,
                compact_error(
                    "Non è stato possibile aggiornare la disponibilità."
                ),
                reply_markup=v2_active_draft_keyboard(),
            )
        return ConversationHandler.END
    except ShippingV2ExpiredError as error:
        await _record_v2_error(
            user=user,
            action="ricevuta_scaduta",
            error=error,
        )
        try:
            await _release_user_draft(
                user,
                context,
                reason="TTL_SCADUTO",
            )
        except Exception as release_error:
            await _record_v2_error(
                user=user,
                action="rilascio_scaduta",
                error=release_error,
            )
        await _reply_message(
            message,
            page_title("⌛", "Prenotazione scaduta")
            + "\n\n"
            "Non è stata creata alcuna spedizione. "
            "Seleziona nuovamente gli articoli.",
            reply_markup=orders_back_keyboard(),
        )
        return ConversationHandler.END
    except ShippingV2ConflictError as error:
        await _record_v2_error(
            user=user,
            action="conflitto_permanente",
            error=error,
        )
        await _reply_message(
            message,
            page_title("⚠️", "Richiesta da verificare")
            + "\n\n"
            "I dati della richiesta non sono più coerenti. "
            "Non reinviare la ricevuta: contatta l’assistenza oppure "
            "annulla la bozza.",
            reply_markup=v2_shipping_receipt_cancel_keyboard(),
        )
        return receipt_state
    except Exception as error:
        await _record_v2_error(
            user=user,
            action="finalizzazione",
            error=error,
        )
        await _reply_message(
            message,
            compact_error(
                "La richiesta non è stata confermata. "
                "La bozza è stata mantenuta: puoi inviare nuovamente "
                "la ricevuta."
            ),
            reply_markup=v2_shipping_receipt_cancel_keyboard(),
        )
        return receipt_state

    clear_shipping_v2_session(context.user_data)
    try:
        cost = float(request.get("COSTO_SPEDIZIONE", 0))
    except (TypeError, ValueError):
        cost = 0.0
    await _reply_message(
        message,
        compact_item_message(
            prefix=(
                page_title("✅", "Richiesta completata")
                + "\n\n"
                f"🆔 Richiesta: <code>{escape(request.get('ID', ''))}</code>\n"
                f"🚚 Corriere: <b>{escape(request.get('CORRIERE', ''))}</b>\n"
                f"💶 Costo: <b>€ {cost:.2f}</b>\n"
                "📋 Stato: <b>In attesa</b>"
            ),
            items=request.get("_V2_ITEM_SNAPSHOTS", []),
            source="draft",
            suffix=(
                "Riceverai il tracking quando la spedizione verrà preparata."
            ),
        ),
        reply_markup=v2_confirmed_shipping_keyboard(),
    )
    return ConversationHandler.END


async def cancel_v2_shipping_receipt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    receipt_state: int,
) -> int:
    query = update.callback_query
    await _answer_query(query)
    try:
        await _release_user_draft(
            query.from_user,
            context,
            reason="ANNULLATA_RICEVUTA",
        )
    except Exception as error:
        await _record_v2_error(
            user=query.from_user,
            action="annulla_ricevuta",
            error=error,
        )
        await _edit_query(
            query,
            compact_error(
                "La bozza non è stata annullata. Premi nuovamente Annulla."
            ),
            reply_markup=v2_shipping_receipt_cancel_keyboard(),
        )
        return receipt_state
    await _edit_query(
        query,
        "❌ <b>Richiesta annullata</b>\n\n"
        "Gli articoli sono stati rilasciati. "
        "Nessuna richiesta è stata salvata.",
        reply_markup=orders_back_keyboard(),
    )
    return ConversationHandler.END


async def cancel_v2_shipping_receipt_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    receipt_state: int,
) -> int | None:
    # Con motore legacy il fallback resta trasparente come nella v2.1.1.
    if not is_shipping_v2_active():
        return None
    message = update.effective_message
    user = update.effective_user
    try:
        await _release_user_draft(
            user,
            context,
            reason="ANNULLATA_COMANDO",
        )
    except Exception as error:
        await _record_v2_error(
            user=user,
            action="cancel_comando",
            error=error,
        )
        await _reply_message(
            message,
            compact_error(
                "La bozza non è stata annullata. Riprova con /cancel."
            ),
            reply_markup=v2_shipping_receipt_cancel_keyboard(),
        )
        return receipt_state
    await _reply_message(
        message,
        "❌ <b>Richiesta annullata</b>\n\n"
        "Gli articoli sono stati rilasciati. "
        "Nessuna richiesta è stata salvata.",
        reply_markup=orders_back_keyboard(),
    )
    return ConversationHandler.END
