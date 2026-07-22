# Rapporto di revisione

## Esito

Il progetto compila correttamente e la struttura generale è coerente con `python-telegram-bot 22.8`.
Le modifiche applicate sono conservative: non cambiano i fogli Google né i flussi principali del bot.

## Problema critico rilevato

Lo ZIP originale conteneva `credentials.json` con una chiave privata Google reale. Inoltre erano presenti `.git` e `.venv`, quindi la chiave potrebbe essere conservata anche nella cronologia Git.

Azioni necessarie:
1. eliminare/revocare la chiave del service account dalla Google Cloud Console;
2. creare una nuova chiave;
3. aggiornare `GOOGLE_CREDENTIALS_JSON` su Railway e l'eventuale `credentials.json` locale;
4. non riutilizzare la vecchia chiave;
5. se il repository è remoto, rimuovere la chiave anche dalla cronologia Git.

## Modifiche applicate

- pacchetto ripulito da credenziali, `.git`, `.venv`, cache e file compilati;
- configurazione obbligatoria verificata prima dell'avvio;
- `WEBHOOK_SECRET` senza valore predefinito debole e minimo di 24 caratteri su Railway;
- gestore globale degli errori Telegram;
- correzione del doppio `query.answer()` nell'elenco admin;
- protezione della lunghezza dei pulsanti con username lunghi;
- logging al posto delle stampe diagnostiche del foglio ordini;
- documentazione per installazione locale e Railway;
- file `.env.example` senza credenziali.

## Controlli eseguiti

- compilazione di tutti i file Python con `compileall`;
- scansione del pacchetto finale per chiavi private e token rimossi;
- verifica della struttura e delle dipendenze dichiarate.

## Limite del test

Non è stato eseguito un collegamento reale a Telegram o Google Sheets perché il pacchetto revisionato non contiene credenziali. Il test completo avverrà al primo avvio con le nuove variabili Railway.
