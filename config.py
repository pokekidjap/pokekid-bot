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
