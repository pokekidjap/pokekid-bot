# Fluidità v2 — Connessioni Google e richieste concorrenti

Data: 23/07/2026

## Ambito

La fase introduce esclusivamente il riuso delle risorse Google, la
sincronizzazione delle chiamate gspread e la protezione della cache dai
caricamenti duplicati. Interfaccia Telegram, callback, handler, tastiere,
schema dei fogli, TTL, chiavi cache e logica funzionale restano invariati.

Il checkpoint precedente è
`POKEKID_BOT_checkpoint_pre_fluidita_v2.zip`, copia completa e verificata
della build Fluidità v1.1 usata come base.

## Architettura introdotta

`services/google_runtime.py` possiede le sole cache di risorse:

```text
Credentials (1 per processo)
        |
Client gspread (1 per processo)
        |
Spreadsheet (1 per spreadsheet_id)
        |
Worksheet (1 per spreadsheet_id + worksheet_name)
        |
RLock di accesso della singola Worksheet
```

Credenziali e risorse sono inizializzate in modo lazy. Lock distinti
proteggono Credentials, client e ogni risorsa durante l'inizializzazione.
Un errore non inserisce oggetti parziali nelle cache e resta visibile al
chiamante.

Le chiamate remote passano da:

- `worksheet_operation()` per una singola operazione;
- `worksheet_session()` per una sequenza read-modify-write;
- `spreadsheet_operation()` per operazioni sullo Spreadsheet.

Gli helper applicano `call_with_retry()`, misurano la durata tramite
`PerfContext` e rilasciano sempre il lock. Il runtime non memorizza valori
delle celle. `reset_google_resources()` è disponibile soltanto come reset
esplicito per test e recovery.

## Cache single-flight

`services/cache.py` mantiene un solo loader in corso per chiave. I waiter:

- attendono senza mantenere il lock globale;
- ricevono una copia profonda dello stesso risultato nuovo;
- ricevono l'eccezione del loader se il caricamento fallisce.

Il caricamento viene sempre ripulito, quindi una richiesta successiva può
riprovare. Una generazione globale e una per chiave impediscono a un risultato
iniziato prima di `invalidate()` di ripopolare la cache. Le chiavi differenti
restano indipendenti e possono essere caricate in parallelo.

`cache_info()` conserva `entries` e aggiunge:

- `keys`;
- `loads_in_progress`;
- `coalesced_waits`.

## Migrazione accessi Google

Sono state migrate tutte le operazioni remote presenti in:

- `services/sheets.py`;
- `services/grading.py`;
- `services/bot_db.py`;
- `services/profiles.py`.

L'audit AST ha censito 23 chiamate remote (`get_all_values`, `append_row`,
`update`, `delete_rows`, `col_values` e `worksheets`): tutte risultano
contenute in un helper protetto o in una sessione. L'unico altro metodo
chiamato `update` è `existing_request.update()`, operazione locale su
dizionario.

Non restano accessi Google non migrati nei quattro servizi. L'import
`gspread` in `services/retry.py` rimane intenzionalmente perché serve soltanto
a classificare `APIError`; non apre connessioni e non accede a fogli.

## Risultati dei test

### Risorse e concorrenza

Test simulato con 50 richieste concorrenti alla stessa worksheet:

| Operazione | Prima | Dopo |
|---|---:|---:|
| Creazione Credentials | 50 | 1 |
| `gspread.authorize()` | 50 | 1 |
| `open_by_key()` | 50 | 1 |
| `spreadsheet.worksheet()` | 50 | 1 |

- concorrenza massima sulla stessa worksheet: `1`;
- concorrenza osservata su due worksheet differenti: `2`;
- reset esplicito: superato;
- errore di inizializzazione seguito da retry: superato;
- propagazione di `SpreadsheetNotFound` e `WorksheetNotFound`: superata;
- retry centralizzato: successo al terzo tentativo simulato;
- misurazione prestazionale: aperture e operazione registrate.

### Cache

- 20 richieste concorrenti sulla stessa chiave: `1` loader;
- 20 force refresh concorrenti: `1` nuovo loader;
- richiesta normale durante refresh: attende e riceve il nuovo valore;
- due chiavi differenti: concorrenza osservata `2`;
- eccezione con 20 waiter: tutti sbloccati, `1` loader, retry successivo
  riuscito;
- invalidazione durante il caricamento: risultato precedente non reinserito;
- invalidazione per prefisso: completata senza deadlock;
- copie profonde in entrata e uscita: verificate;
- `cache_info()`: campi precedenti e nuovi verificati.

### Servizi e reattività

- cache ordini normale: una lettura per due richieste;
- refresh ordini: una nuova lettura e sostituzione della cache;
- cache grading normale: una lettura per due richieste;
- refresh grading: una nuova lettura e sostituzione della cache;
- profili: lettura, creazione, modifica, sincronizzazione e cancellazione;
- configurazione: lettura e read-modify-write;
- admin: riconoscimento autorizzato e non autorizzato;
- spedizioni: lettura, creazione e completamento;
- log e lista worksheet BOT DB;
- heartbeat asincrono durante un'operazione Google simulata di 300 ms:
  `10` tick, event loop reattivo.

### Audit e compatibilità

- compilazione: tutti i 34 file Python;
- parsing AST: tutti i 34 file Python;
- `gspread.authorize()`, `open_by_key()` e `spreadsheet.worksheet()` presenti
  soltanto in `services/google_runtime.py`;
- 14 file Telegram (`main.py`, `modules/`, `keyboards/`) identici byte per
  byte al checkpoint;
- 74 callback data letterali, 34 registrazioni handler/conversazioni e la
  mappa delle 30 funzioni con `query.answer()` identiche al checkpoint;
- `check_admin()` conserva una sola `query.answer()` diretta;
- TTL e sei chiavi cache letterali invariati;
- nessun file credenziali, token Telegram, chiave API Google o chiave privata
  rilevato.

## Rischi residui

- `generate_shipping_id()` legge gli ID e la successiva append avviene in una
  seconda operazione: resta non atomico, intenzionalmente fuori ambito.
- Le cache di risorse e i lock sono per processo. Più processi o istanze del
  bot non condividono lo stesso coordinamento.
- Se una worksheet viene eliminata e ricreata mentre il processo è attivo, la
  risorsa memorizzata richiede `reset_google_resources()` o il riavvio.
- Un reset durante un'operazione già avviata non la annulla; impedisce però
  che la vecchia risorsa venga nuovamente memorizzata.
- I test usano worksheet simulate. Il collaudo con quote, permessi e latenze
  reali richiede un foglio Google dedicato.

## Test manuali consigliati

1. Aprire due sessioni Telegram e richiedere contemporaneamente ordini e SUB
   Grading, verificando testi e tastiere invariati.
2. Premere contemporaneamente “Aggiorna elenco” e refresh grading da più
   utenti, verificando una risposta coerente e nessun errore tecnico.
3. Creare e modificare un profilo, poi riaprirlo per verificare
   l'invalidazione della cache.
4. Eseguire da admin apertura/chiusura smistamento, lettura richieste,
   inserimento tracking e consultazione log.
5. Creare una richiesta di spedizione completa e verificare riga, log,
   ricevuta e notifica admin.
6. Osservare i log prestazionali e le quote Google in un ambiente di prova
   durante richieste simultanee.
