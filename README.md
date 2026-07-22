# Pokekid Bot

Bot Telegram collegato a Google Sheets per ordini, profili, grading e spedizioni.

## Installazione locale

1. Crea l'ambiente virtuale:
   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
2. Installa le dipendenze:
   ```powershell
   pip install -r requirements.txt
   ```
3. Copia `.env.example` in `.env` e inserisci i valori reali.
4. Per Google puoi usare in locale `credentials.json` nella cartella principale. Il file è ignorato da Git.
5. Avvia:
   ```powershell
   python main.py
   ```

## Railway

Imposta come variabili: `BOT_TOKEN`, `SPREADSHEET_ID`, `WORKSHEET_NAME`, `BOT_DB_SHEET_ID`, `GOOGLE_CREDENTIALS_JSON` e `WEBHOOK_SECRET`.

## Sicurezza

Non caricare mai su Git o negli ZIP pubblici:
- `.env`
- `credentials.json`
- token Telegram
- chiavi private Google
- cartelle `.git`, `.venv` e `__pycache__`

Se una chiave è stata condivisa, eliminala dalla console del servizio e creane una nuova: rimuoverla soltanto dal file non la rende nuovamente sicura.
