# Pokekid Bot 2.0.0

Bot Telegram per consultazione ordini, stato SUB grading, profili, richieste di spedizione e gestione amministrativa tramite Google Sheets.

## Funzioni utente

- consultazione ordini e articoli disponibili;
- selezione articoli e richiesta di spedizione;
- storico spedizioni e tracking;
- stato delle SUB grading;
- profilo e dati di spedizione;
- sincronizzazione automatica di Telegram ID e username;
- notifiche per nuovi articoli entrati in magazzino.

## Funzioni amministratore

- dashboard e statistiche;
- ordini raggruppati per utente;
- apertura e completamento smistamento con snapshot anti-duplicato;
- gestione richieste, ricevute, tracking e storico spedizioni;
- broadcast con anteprima e conferma;
- messaggi configurabili dal foglio CONFIG;
- centro notifiche e LOG operativo;
- stato del bot e dei collegamenti.

## Prestazioni e robustezza

- cache TTL differenziata per ORDINI, PROFILI, CONFIG, ADMIN, LOG, SPEDIZIONI e GRADING;
- invalidazione mirata dopo le scritture;
- retry con backoff per le letture Google più sensibili;
- controlli di sola lettura all'avvio;
- gestione centralizzata degli errori Telegram;
- interfaccia e footer condivisi.

## Avvio locale

1. Crea l'ambiente virtuale e installa `requirements.txt`.
2. Copia `.env.example` in `.env` e compila le variabili.
3. Inserisci `credentials.json` nella cartella `BOT` oppure configura `GOOGLE_CREDENTIALS_JSON`.
4. Avvia con `python main.py`.

I test con il database reale devono essere eseguiti dopo aver configurato token, credenziali e fogli.
