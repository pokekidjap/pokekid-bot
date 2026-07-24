# Spedizioni v2.1 — Foundation report

## Risultato

Sono state predisposte le fondamenta dati per ID articolo stabili,
sincronizzazione conservativa e prenotazioni idempotenti. Il sistema resta
disattivato per default. La fase v2.2 collega queste API a callback, handler,
ConversationHandler e interfaccia Telegram esclusivamente quando entrambi i
feature flag sono attivi.

L'hardening v2.1.1 aggiunge validate-only pre/post migrazione, bozza viva
unica per titolare, ruoli non falsificabili, contributor esplicitamente
autorizzati, idempotenza legata al titolare e validazione completa dei dati.

La fase v2.3 usa queste fondamenta per aggiungere direttamente gli articoli
propri a una richiesta V2 esistente. Il flusso dedicato non passa dalla bozza
normale del titolare: scrive righe `CONTRIBUENTE` già `CONFERMATO`, dopo
validazione completa e con idempotenza legata al payload.

## Confine read-only

“Gestione vendite gruppo”, incluse `ORDINI` e `GRADING`, non riceve alcuna
scrittura dal nuovo codice. L'unica operazione del registro sulla sorgente è
`get_all_values()`. Tutte le nuove strutture e scritture appartengono al
DATABASE BOT.

## Strutture

### `ORDINI_ARTICOLI` A:W

Registro append/update senza cancellazioni automatiche. Contiene
`ID_ARTICOLO`, origine, due fingerprint, indice duplicato, snapshot A:K
normalizzato, proprietario, stato di sincronizzazione, attività e versione.

Gli ID sono `ART-UUIDv4`, univoci e persistono nel registro. Una riga
scomparsa diventa `NON_PRESENTE`/`FALSE`. Username non risolti diventano
`NON_ASSOCIATO` con Telegram ID vuoto.

### `SPEDIZIONI` V:X

- V `UUID_SPEDIZIONE`
- W `IDEMPOTENCY_KEY`
- X `VERSIONE_SCHEMA`

A:U resta inalterato e le richieste legacy non sono riscritte.

### `SPEDIZIONI_ARTICOLI` A:U

Conserva dettaglio/bozza/spedizione, articolo e proprietario, ruolo,
snapshot, stato, scadenze, timestamp, motivo di rilascio, idempotency key e
versione. Non è stata creata `SPEDIZIONI_PARTECIPANTI`.

## API

`services/order_registry.py`:

- `read_source_orders_read_only()`
- `parse_source_orders()`
- `build_sync_plan()`
- `OrderRegistryRepository.synchronize()`
- `synchronize_order_registry()`

`services/reservations.py`:

- `get_active_reservations()`
- `is_item_reservable()`
- `create_or_get_draft()`
- `reserve_items()`
- `get_active_draft_for_user()`
- `confirm_reservations()`
- `release_draft()`
- `mark_items_shipped()`
- `release_expired_reservations()`
- `get_items_grouped_by_owner()`

La fase v2.2 aggiunge:

- `services/shipping_engine.py` per la scelta centralizzata del motore;
- `services/shipping_v2_session.py` per lo stato Telegram separato;
- `services/shipping_v2.py` con
  `create_or_get_v2_shipping_request()`,
  `get_v2_shipping_request_by_draft()`,
  `validate_v2_draft_for_holder()` e
  `complete_v2_shipping_request()`;
- `modules/shipping_v2.py` per il flusso Telegram del singolo titolare.

La fase v2.2.1 aggiunge:

- `services/shipping_v2_text.py` per elenchi HTML compatti e budget Telegram;
- `validate_v2_draft_against_registry()` per la rivalidazione sincronizzata;
- esiti tecnici `CREATED_NOW`, `RECONCILED_NOW` e `ALREADY_COHERENT`;
- marker notifiche admin nel `LOG`, senza nuove colonne o worksheet;
- `scripts/prepare_shipping_v2_deactivation.py` per l'ispezione e
  l'eventuale rilascio esplicito delle sole bozze `PRENOTATO`.

La fase v2.3 aggiunge:

- `services/shipping_v2_join.py` per ricerca target, selezione, unione,
  partecipanti, raggruppamento e annullamento admin;
- `services/shipping_v2_join_session.py` per lo stato Telegram separato;
- `modules/shipping_v2_join.py` per username e callback `join_v2_*`.

Non è stata aggiunta `SPEDIZIONI_PARTECIPANTI`: titolare e contributor sono
ricavati dalle righe già presenti in `SPEDIZIONI_ARTICOLI`.

## Riconciliazione

La strategia usa, nell'ordine, riga+row fingerprint, row fingerprint univoco
spostato, riga+identity fingerprint con modifica controllata, quindi blocco
ambiguo. I duplicati mantengono un `DUPLICATE_INDEX`. Se un gruppo duplicato
cambia mentre un suo ID è prenotato o confermato, non viene eseguita una
riassegnazione automatica.

## Prenotabilità

Un articolo è prenotabile soltanto con:

- `IS_ACTIVE=TRUE`;
- `SYNC_STATUS=OK` o `MODIFICATO`;
- `STATO_ORIGINE=IN MAGAZZINO`;
- `TELEGRAM_ID_PROPRIETARIO` presente;
- nessuna prenotazione occupante.

Il gruppo è tutto-o-niente. Lo stesso contenuto con la stessa idempotency key
restituisce lo stesso risultato; un contenuto diverso genera un errore. Prima
di ripetere un append dopo timeout vengono ricontrollati UUID e key.

## Test simulati

- dry-run, validate-only, apply protetto e seconda applicazione idempotente;
- backup JSON/CSV e creazione delle due schede;
- preservazione A:U di SPEDIZIONI;
- zero scritture sul gestionale e digest ORDINI invariato;
- ID univoci e conservati, modifica controllata, spostamento univoco,
  scomparsa, username non associato e duplicati;
- gruppo duplicato modificato con prenotazione: `AMBIGUO`, nessuna
  riassegnazione;
- prenotazioni singole/multiple e rifiuto atomico;
- 20 thread sullo stesso articolo: 1 successo, 19 conflitti, 1 record attivo;
- articoli differenti concorrenti: tutti completati;
- idempotenza uguale/differente e timeout post-append senza duplicati;
- rilascio, scadenza, conferma, spedizione, terminalità e raggruppamento.
- compilazione e parsing AST di tutti i 43 file Python;
- audit byte-per-byte di 17 file protetti (`main.py`, `modules/`,
  `keyboards/`, cache, runtime Google e requirements): nessuna differenza;
- scansione credenziali e pattern di segreti: nessun risultato.

La suite è permanente in `tests/` e usa soltanto `unittest`. I 31 test delle
fondamenta restano invariati; la fase v2.2 aggiunge test di feature flag,
selezione, bozza, finalizzazione, concorrenza, callback e completamento
admin; la v2.2.1 aggiunge paginazione, budget testi, rivalidazione, retry con
allegato differente, marker admin e disattivazione, sempre senza Google
reale. La v2.3 porta la suite a 163 test con copertura di username, unione,
stati parziali, più contributor, notifiche, tracking e annullamento admin.

## Limite single-instance

I lock di `google_runtime` proteggono soltanto thread dello stesso processo.
Non esistono vincoli transazionali o lock distribuiti su Google Sheets.
Railway deve mantenere una sola istanza. Cache e lock Python non sono
un'autorità distribuita.

## Rischi residui

- una modifica esterna simultanea ai fogli del DATABASE BOT non è una
  transazione multi-worksheet;
- più repliche possono ancora creare race non risolvibili con RLock locali;
- i casi ambigui richiedono revisione operativa;
- l'integrazione Telegram valida flag e schema senza fallback legacy, ma
  richiede ancora collaudo manuale su Telegram e fogli di prova;
- il restore dai backup è intenzionalmente manuale;
- nessun test ha scritto su Google Sheets reali.
- l'accordo tra titolare e contributor resta esterno al bot, come decisione
  funzionale esplicita della v2.3.
