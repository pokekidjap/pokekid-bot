"""Funzioni helper comuni per il progetto."""
from __future__ import annotations


def clean_value(value) -> str:
    """Converte un valore in testo e rimuove spazi iniziali e finali."""
    if value is None:
        return ""
    return str(value).strip()


def normalize_username(username: str | None) -> str:
    """Uniforma lo username Telegram con @ e in minuscolo."""
    username = clean_value(username).lower()
    if not username:
        return ""
    if not username.startswith("@"):
        username = f"@{username}"
    return username


def normalize_telegram_id(telegram_id: int | str) -> str:
    """Uniforma il Telegram ID come stringa."""
    return clean_value(telegram_id)


def normalize_header(header: str) -> str:
    """Uniforma le intestazioni delle colonne dei fogli."""
    return clean_value(header).upper()


def parse_quantity(value) -> int:
    """Converte una quantità dal foglio in intero, gestendo numeri decimali."""
    if value is None or value == "":
        return 0
    try:
        text = str(value).strip().replace(",", ".")
        return int(float(text))
    except (TypeError, ValueError):
        return 0


def is_truthy(value) -> bool:
    """Restituisce True per valori testuali che rappresentano un vero."""
    return str(value or "").strip().upper() in {
        "TRUE",
        "VERO",
        "SI",
        "SÌ",
        "1",
        "YES",
    }
