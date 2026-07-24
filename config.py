import os

from dotenv import load_dotenv


# Carica le variabili presenti nel file .env quando lavori dal PC
load_dotenv()


# ==========================
# Telegram
# ==========================
BOT_TOKEN = os.getenv("BOT_TOKEN")


# ==========================
# Google Sheets - Gestionale
# ==========================
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
WORKSHEET_NAME = os.getenv("WORKSHEET_NAME", "ORDINI")


# ==========================
# Google Sheets - BOT DB
# ==========================
BOT_DB_SHEET_ID = os.getenv("BOT_DB_SHEET_ID")


# ==========================
# Configurazione server
# ==========================
PORT = int(os.getenv("PORT", "8000"))
KOYEB_PUBLIC_DOMAIN = os.getenv("KOYEB_PUBLIC_DOMAIN")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")


# ==========================
# Credenziali Google
# ==========================
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
STARTUP_CHECKS = os.getenv("STARTUP_CHECKS", "true").strip().lower() in {"1", "true", "yes", "on"}


def _env_bool(name: str, default: bool = False) -> bool:
    fallback = "true" if default else "false"
    return os.getenv(name, fallback).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _positive_int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as error:
        raise RuntimeError(
            f"{name} deve essere un numero intero positivo."
        ) from error
    if value <= 0:
        raise RuntimeError(
            f"{name} deve essere un numero intero positivo."
        )
    return value


# Shipping v2.2: disattivata per default e attivabile solo con doppio consenso.
SHIPPING_V2_ENABLED = _env_bool("SHIPPING_V2_ENABLED", False)
SHIPPING_V2_SINGLE_INSTANCE_ACK = _env_bool(
    "SHIPPING_V2_SINGLE_INSTANCE_ACK",
    False,
)
SHIPPING_RESERVATION_TTL_MINUTES = _positive_int_env(
    "SHIPPING_RESERVATION_TTL_MINUTES",
    60,
)


def is_shipping_v2_activation_allowed() -> bool:
    """Richiede feature flag e conferma esplicita della singola istanza."""
    return (
        SHIPPING_V2_ENABLED
        and SHIPPING_V2_SINGLE_INSTANCE_ACK
    )


def validate_config() -> None:
    """Controlla le variabili indispensabili prima di avviare il bot."""
    missing = []

    for name, value in (
        ("BOT_TOKEN", BOT_TOKEN),
        ("SPREADSHEET_ID", SPREADSHEET_ID),
        ("BOT_DB_SHEET_ID", BOT_DB_SHEET_ID),
    ):
        if not str(value or "").strip():
            missing.append(name)

    if missing:
        raise RuntimeError(
            "Configurazione incompleta. Variabili mancanti: "
            + ", ".join(missing)
        )

    railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
    if railway_domain and len(str(WEBHOOK_SECRET or "").strip()) < 24:
        raise RuntimeError(
            "Su Railway imposta WEBHOOK_SECRET con almeno 24 caratteri casuali."
        )
