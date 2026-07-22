"""Testi, componenti e costanti condivise dell'interfaccia Telegram."""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

BOT_VERSION = "2.0.0"
LAST_UPDATE = "22/07/2026"
FOOTER = f"🤖 Pokekid Bot • v{BOT_VERSION}"
DIVIDER = "━━━━━━━━━━━━━━━━━━"


def with_footer(text: str) -> str:
    clean = text.rstrip()
    if clean.endswith(FOOTER):
        return clean
    return f"{clean}\n\n{FOOTER}"


def page_title(icon: str, title: str, subtitle: str = "") -> str:
    text = f"{icon} <b>{title}</b>"
    if subtitle:
        text += f"\n\n{subtitle.strip()}"
    return text


def back_keyboard(callback_data: str, label: str = "⬅️ Indietro") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=callback_data)]])


def compact_error(message: str = "Riprova tra qualche minuto.") -> str:
    return with_footer(f"⚠️ <b>Servizio momentaneamente non disponibile</b>\n\n{message}")
