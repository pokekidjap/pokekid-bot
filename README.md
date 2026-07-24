# POKEKID BOT 2.0.0

Bot Telegram per la gestione autonoma di ordini, profili, spedizioni, tracking e SUB Grading della community POKEKID.

## Funzioni utente
- visualizzazione degli ordini operativi associati allo username Telegram;
- visualizzazione degli articoli disponibili in magazzino;
- selezione degli articoli da spedire;
- scelta del corriere e invio ricevuta;
- unione diretta dei propri articoli a una spedizione V2 in attesa tramite
  username del titolare;
- consultazione storico spedizioni e tracking;
- creazione e modifica profilo;
- consultazione stato SUB Grading;
- notifiche per nuovi prodotti disponibili.

La vista utente degli ordini esclude le righe con stato `EVASO`, `RESTAURO`
o `GRADING`. Il pannello amministratore esclude invece solamente `EVASO`.

## Funzioni amministratore
- dashboard e statistiche;
- ordini raggruppati per utente;
- apertura e completamento smistamento;
- richieste di spedizione pendenti e storico;
- visualizzazione ricevute e inserimento tracking;
- dettaglio V2 raggruppato per proprietario e annullamento amministrativo
  delle richieste V2 in attesa;
- broadcast con anteprima e conferma;
- messaggi configurabili da Google Sheets;
- centro notifiche e log operativo;
- controllo stato collegamenti.

## Requisiti
- Python 3.11 o superiore consigliato;
- bot Telegram e relativo token;
- service account Google con accesso ai fogli;
- due Google Spreadsheet configurati;
- dipendenze di `requirements.txt`.

## Installazione locale

```bash
python -m venv .venv
```

Windows:

```powershell
.venv\Scripts\activate
pip install -r requirements.txt
```

macOS/Linux:

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

## Configurazione
Creare un file `.env` locale, senza inserirlo in Git:

```env
BOT_TOKEN=
SPREADSHEET_ID=
WORKSHEET_NAME=ORDINI
BOT_DB_SHEET_ID=
PORT=8000
WEBHOOK_SECRET=
STARTUP_CHECKS=true
SHIPPING_V2_ENABLED=false
SHIPPING_V2_SINGLE_INSTANCE_ACK=false
```

Per le credenziali Google usare uno dei due metodi:
- file locale `credentials.json` nella root del progetto;
- variabile `GOOGLE_CREDENTIALS_JSON` con il JSON completo.

Non usare entrambi se non necessario e non condividere mai le credenziali.

## Fogli richiesti

### Gestionale (`SPREADSHEET_ID`)
- `ORDINI` o il nome indicato in `WORKSHEET_NAME`;
- `GRADING`.

### Database bot (`BOT_DB_SHEET_ID`)
- `PROFILI`;
- `ADMIN`;
- `SPEDIZIONI`;
- `CONFIG`;
- `LOG`.
- con Shipping v2 migrato: `ORDINI_ARTICOLI` e
  `SPEDIZIONI_ARTICOLI`, oltre alle colonne V:X di `SPEDIZIONI`.

## Avvio

```bash
python main.py
```

Senza `RAILWAY_PUBLIC_DOMAIN` il bot usa il polling. Su Railway usa un webhook e richiede `WEBHOOK_SECRET` di almeno 24 caratteri.

## Comandi Telegram
- `/start` — menu principale;
- `/spedizioni` — storico spedizioni utente;
- `/admin` — pannello amministratore;
- `/cancel` — annulla il modulo profilo, l'inserimento username dell'unione
  V2 e, con Shipping v2 attivo, rilascia la bozza durante l'attesa della
  ricevuta.

Negli altri flussi l'annullamento avviene tramite i pulsanti inline dedicati.

## Stato della build 2.0.0 analizzata

La revisione statica del 23/07/2026 ha rilevato che il motore legacy non
riserva né aggiorna le righe del foglio ordini e quindi, da solo, non
impedisce richieste duplicate sugli stessi articoli. La completezza del
profilo è ora validata in modo condiviso.

L'import di `start_flow`, i refresh grading/ordini, l'invalidazione della
cache profili e la pulizia della sessione sono stati corretti il 23/07/2026.
I limiti residui sono documentati in `ARCHITECTURE.md` e pianificati in
`ROADMAP.md`.

La fase Shipping v2.2 aggiunge un motore opzionale per la spedizione degli
articoli del singolo titolare. È disattivato per default e richiede entrambi
i flag Shipping v2. Con i flag disattivati il flusso descritto sopra resta
legacy; con entrambi attivi la selezione usa ID articolo stabili, crea una
prenotazione al pulsante Continua e finalizza `SPEDIZIONI` insieme a
`SPEDIZIONI_ARTICOLI`. Il gestionale `ORDINI` resta sempre in sola lettura.

La fase Shipping v2.3 aggiunge, dietro gli stessi flag, un percorso separato
per collegare direttamente i propri articoli all'unica richiesta V2
`IN_ATTESA` di un altro utente individuato tramite username. Non esistono
inviti o consensi nel bot: gli utenti si accordano privatamente. La richiesta
resta intestata al titolare, conserva destinazione, corriere, costo e
ricevuta, mentre gli articoli aggiunti sono tracciati come
`CONTRIBUENTE`. Tracking e annullamento amministrativo coinvolgono tutti i
Telegram ID unici ricavati da `SPEDIZIONI_ARTICOLI`.

La hotfix prestazionale v2.3.1 evita la riscrittura dell'intero registro
quando i dati non cambiano, riduce le letture duplicate durante apertura e
refresh e rende toggle/paginazione completamente locali. La versione mostrata
dal bot viene caricata da `CONFIG -> VERSIONE_BOT` durante lo startup e poi
letta dalla memoria.

La hotfix v2.3.2 forza una nuova sincronizzazione quando `Continua con la
spedizione` incontra un conflitto, elimina dalla selezione gli articoli non
più disponibili e mostra il pulsante Continua solo se la selezione non è
vuota. Le callback scadute e gli edit senza modifiche sono gestiti nei soli
casi Telegram previsti. I log HTTP non espongono il token del bot. Il
fallback locale della versione è `2.3.2`.

## Struttura

```text
BOT/
├── main.py
├── config.py
├── requirements.txt
├── modules/
├── keyboards/
├── services/
└── utils/
```

Per una descrizione tecnica dettagliata vedere `ARCHITECTURE.md`. Per le regole destinate a Codex vedere `AGENTS.md`.

## Verifiche consigliate

```bash
python -m compileall .
```

I test permanenti senza Google reale si eseguono con:

```bash
python -m unittest discover -s tests -v
```

Il collaudo completo richiede inoltre un ambiente di prova configurato e
l'accesso a Telegram e a fogli Google non di produzione.

## Sicurezza
- `.env`, `credentials.json`, `.venv`, `.git` e `__pycache__` non devono essere distribuiti negli ZIP di lavoro;
- revocare immediatamente qualsiasi chiave condivisa per errore;
- utilizzare variabili Railway per token e credenziali di produzione.
