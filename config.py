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
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "pokekid-webhook")


# ==========================
# Credenziali Google
# ==========================
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")