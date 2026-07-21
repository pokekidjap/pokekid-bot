import os

from dotenv import load_dotenv


# Carica le variabili presenti nel file .env quando lavori dal PC
load_dotenv()


# Telegram
BOT_TOKEN = os.getenv("BOT_TOKEN")


# Google Sheets
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
WORKSHEET_NAME = os.getenv("WORKSHEET_NAME", "ORDINI")


# Configurazione server Koyeb
PORT = int(os.getenv("PORT", "8000"))
KOYEB_PUBLIC_DOMAIN = os.getenv("KOYEB_PUBLIC_DOMAIN")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "pokekid-webhook")


# Credenziali Google in formato JSON
# Verranno usate online su Koyeb al posto del file credentials.json
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")