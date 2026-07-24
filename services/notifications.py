"""Invio centralizzato delle notifiche Telegram."""
from __future__ import annotations

import asyncio
import logging
from html import escape

from services.bot_db import get_admins, get_config_values
from services.common import normalize_username
from services.profiles import get_all_profiles
from services.ui import with_footer

logger = logging.getLogger(__name__)


async def notify_admins(bot, text: str, footer: bool = True) -> tuple[int, int]:
    sent = failed = 0
    payload = with_footer(text) if footer else text
    admins = await asyncio.to_thread(get_admins, active_only=True)
    for admin in admins:
        try:
            await bot.send_message(int(admin["TELEGRAM_ID"]), payload, parse_mode="HTML")
            sent += 1
        except Exception:
            failed += 1
            logger.exception("Notifica admin non inviata a %s", admin.get("TELEGRAM_ID"))
    return sent, failed


async def notify_warehouse_users(bot, usernames) -> dict:
    result = {"sent": 0, "failed": 0, "missing": []}
    if not usernames:
        return result

    config = await asyncio.to_thread(get_config_values)
    template = config.get("MSG_MAGAZZINO", {}).get("value", "").strip()
    profiles = await asyncio.to_thread(get_all_profiles)
    profiles_by_username = {
        profile.get("USERNAME", ""): profile
        for profile in profiles
        if profile.get("USERNAME")
    }

    for username in usernames:
        normalized = normalize_username(username)
        profile = profiles_by_username.get(normalized)
        telegram_id = profile.get("TELEGRAM_ID", "") if profile else ""
        if not telegram_id:
            result["missing"].append(username)
            continue
        display_username = profile.get("USERNAME") or username
        text = template or (
            "Ciao {USERNAME},\n\n"
            "📦 <b>SONO ARRIVATI NUOVI ARTICOLI IN MAGAZZINO!</b>\n\n"
            "Puoi consultarli nella sezione “I miei ordini”."
        )
        text = text.replace("{USERNAME}", display_username)
        try:
            await bot.send_message(int(telegram_id), with_footer(text), parse_mode="HTML")
            result["sent"] += 1
        except Exception:
            result["failed"] += 1
            logger.exception("Notifica magazzino non inviata a %s", username)
    if result["missing"]:
        await notify_admins(
            bot,
            "⚠️ <b>Notifiche smistamento non inviate</b>\n\n"
            "Profili Telegram non collegati:\n" + escape(", ".join(result["missing"][:20])),
        )
    return result


async def send_broadcast(bot, message: str) -> dict:
    config = await asyncio.to_thread(get_config_values)
    custom_footer = config.get("MSG_BROADCAST_FOOTER", {}).get("value", "").strip()
    payload = message + (f"\n\n{custom_footer}" if custom_footer else "")
    payload = with_footer(payload)
    sent = failed = 0
    seen = set()
    profiles = await asyncio.to_thread(get_all_profiles)
    for profile in profiles:
        telegram_id = str(profile.get("TELEGRAM_ID", "")).strip()
        if not telegram_id or telegram_id in seen:
            continue
        seen.add(telegram_id)
        try:
            await bot.send_message(int(telegram_id), payload, parse_mode="HTML")
            sent += 1
        except Exception:
            failed += 1
            logger.exception("Broadcast non inviato a %s", telegram_id)
    return {"sent": sent, "failed": failed, "total": len(seen)}
