"""Testi, componenti e costanti condivise dell'interfaccia Telegram."""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from services.bot_version import BOT_VERSION_FALLBACK, get_bot_version

BOT_VERSION = BOT_VERSION_FALLBACK
LAST_UPDATE = "24/07/2026"
FOOTER = f"🤖 Pokekid Bot • v{BOT_VERSION}"
DIVIDER = "━━━━━━━━━━━━━━━━━━"


def get_footer() -> str:
    return f"\U0001F916 Pokekid Bot \u2022 v{get_bot_version()}"


def with_footer(text: str) -> str:
    clean = text.rstrip()
    footer = get_footer()
    if clean.endswith(footer):
        return clean
    return f"{clean}\n\n{footer}"


def page_title(icon: str, title: str, subtitle: str = "") -> str:
    text = f"{icon} <b>{title}</b>"
    if subtitle:
        text += f"\n\n{subtitle.strip()}"
    return text


def section_title(icon: str, title: str) -> str:
    return f"{icon} <b>{title}</b>"


def page_indicator(page: int, total_pages: int) -> str:
    return f"Pagina <b>{page}</b> di <b>{total_pages}</b>"


def readable_status(status: str | None) -> str:
    normalized = " ".join(
        str(status or "").strip().replace("_", " ").split()
    ).upper()
    labels = {
        "IN ATTESA": "In attesa",
        "SPEDITO": "Spedita",
        "ANNULLATO": "Annullata",
    }
    if normalized in labels:
        return labels[normalized]
    words = normalized.lower().split()
    return " ".join(
        word.upper()
        if word in {"qa", "sub"}
        else word.capitalize()
        if index == 0
        else word
        for index, word in enumerate(words)
    )


def shorten_button_text(text: str, max_length: int = 40) -> str:
    clean = " ".join(str(text).split())
    if max_length < 2:
        raise ValueError("max_length deve essere almeno 2")
    if len(clean) <= max_length:
        return clean
    return f"{clean[:max_length - 1].rstrip()}…"


def summary_row(icon: str, label: str, value: object) -> str:
    return f"{icon} {label}: <b>{value}</b>"


def back_keyboard(callback_data: str, label: str = "⬅️ Indietro") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=callback_data)]])


def compact_error(message: str = "Riprova tra qualche minuto.") -> str:
    return with_footer(f"⚠️ <b>Servizio momentaneamente non disponibile</b>\n\n{message}")


def operation_unavailable(message: str) -> str:
    return with_footer(
        "⚠️ <b>Operazione non disponibile</b>\n\n"
        f"{message.strip()}\n\n"
        "Riprova oppure torna alla schermata precedente."
    )
