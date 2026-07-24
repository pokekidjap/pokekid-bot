# Hotfix v2.3.2 — Conflitti disponibilità e sicurezza log

Data: 24/07/2026  
Base: `POKEKID_BOT_performance_hotfix_v2_3_1.zip`  
Checkpoint: `POKEKID_BOT_checkpoint_pre_hotfix_v2_3_2.zip`

## Esito

La regressione è corretta senza migrazioni, modifiche di schema o accessi a
Google reale. `Gestione vendite gruppo`, inclusa `ORDINI`, resta in sola
lettura. Il modello di esecuzione rimane quello previsto per Railway con una
sola replica.

## Causa

L'apertura di “Articoli disponibili” può usare per 10 secondi lo snapshot
single-flight introdotto dalla v2.3.1. Il callback Continua, correttamente,
non considera quello snapshot autorevole: sincronizza il registro e
rivalida la prenotabilità prima di scrivere la bozza.

Se nel frattempo cambia anche uno dei predicati di disponibilità, oppure
compare una prenotazione occupante, `reserve_v2_items()` genera
`ReservationConflictError`. Il ramo di recupero richiamava però
`prepare_v2_opening_state()` senza `force_refresh=True`, quindi poteva
riottenere lo stesso snapshot obsoleto e riproporre selezione e pulsante.

Il confronto statico dei due percorsi non ha rilevato predicati divergenti:
apertura autorevole e prenotazione richiedono entrambi:

1. `IS_ACTIVE=TRUE`;
2. `SYNC_STATUS` uguale a `OK` o `MODIFICATO`;
3. `STATO_ORIGINE=IN MAGAZZINO`;
4. `TELEGRAM_ID_PROPRIETARIO` uguale al titolare;
5. nessuna prenotazione attiva occupante in `SPEDIZIONI_ARTICOLI`.

La causa riproducibile è quindi la diversa freschezza dei dati, non una
scrittura sul foglio sorgente. Senza interrogare l'ambiente reale — azione
deliberatamente non eseguita — non è possibile stabilire quale predicato
fosse falso nei record di produzione. La nuova diagnostica lo registra per
ogni ID al prossimo conflitto, senza includere il Telegram ID proprietario.

## Correzioni

### Recupero dopo conflitto

- `continue_v2_shipping()` usa
  `prepare_v2_opening_state(user.id, force_refresh=True)`;
- `set_available_items(..., preserve_selection=True)` interseca la selezione
  precedente con gli ID del nuovo stato;
- gli ID esclusi spariscono dalla selezione e invalidano i dati downstream
  locali;
- la tastiera aggiunge `shipping_v2_continue` soltanto se la selezione è
  non vuota;
- il testo mostra:
  “La disponibilità è cambiata. Seleziona nuovamente gli articoli
  disponibili.”

Se la sincronizzazione rileva invece una bozza attiva già creata, il render
esistente cancella la sessione di selezione e mostra la bozza autorevole.

### Callback ed edit Telegram

- tutte le callback del modulo Shipping v2 usano un unico helper di
  conferma;
- ogni percorso attivo conferma la query una sola volta prima delle
  operazioni remote;
- viene assorbito esclusivamente il `BadRequest` contenente
  `Query is too old and response timeout expired or query id is invalid`;
- gli altri `BadRequest` non vengono ignorati;
- gli edit ignorano esclusivamente `Message is not modified`, senza
  timestamp artificiali.

### Diagnostica prenotabilità

`inspect_v2_item_availability()` legge soltanto il registro e le
prenotazioni del DATABASE BOT e produce, per ogni ID richiesto, gli esiti dei
cinque predicati. In caso di conflitto il log include:

- ID articolo;
- valore `IS_ACTIVE`;
- valore `SYNC_STATUS`;
- valore `STATO_ORIGINE`;
- sola corrispondenza booleana del proprietario;
- presenza booleana di una prenotazione attiva;
- motivi normalizzati del rifiuto.

Non viene scritto o registrato il Telegram ID del proprietario.

### Sicurezza log HTTP

`services/logging_security.py`:

- imposta `httpx` e `httpcore` a `WARNING`;
- redige il segmento token degli URL `api.telegram.org/bot...`;
- applica la redazione sia ai logger HTTP sia agli handler root già
  configurati.

La protezione rimane efficace anche per record warning o superiori.

## File modificati

- `modules/shipping_v2.py`
- `keyboards/orders.py`
- `services/shipping_v2.py`
- `services/logging_security.py` — nuovo
- `services/bot_version.py`
- `main.py`
- `tests/test_hotfix_v2_3_2.py` — nuovo
- `tests/test_shipping_v2_callbacks.py`
- `README.md`
- `ARCHITECTURE.md`
- `ROADMAP.md`
- `CHANGELOG.md`
- `HOTFIX_V2_3_2_REPORT.md` — nuovo

## Test automatici

- baseline pre-modifica: 175 test superati;
- suite finale: 187 test superati, inclusi tutti i 175 precedenti;
- compilazione ricorsiva: 60 file Python;
- test cache obsoleta più conflitto in Continua;
- verifica di `force_refresh=True`;
- selezione mista: eliminato soltanto l'ID non più disponibile;
- nessun articolo valido: pulsante Continua assente;
- contenuto Telegram modificato: edit eseguito normalmente;
- contenuto identico: `Message is not modified` assorbito;
- callback scaduta su Continua e apertura elenco: flusso non interrotto;
- `BadRequest` differenti su answer ed edit: non assorbiti;
- diagnostica di tutti i cinque predicati e ID assente;
- nessuna scrittura sui fogli finti durante la diagnostica;
- redazione del token e livello `httpx >= WARNING`;
- audit AST delle callback Shipping v2: una conferma per handler nel ramo
  attivo e registrazioni raggiungibili prima del router generico;
- scansione credenziali e confronto con il checkpoint.

Tutti i test usano stub o fogli finti in memoria. Non è stata eseguita
alcuna chiamata a Google, Telegram o Railway.

## Test manuali consigliati su Telegram

1. Con Shipping v2 attiva, aprire gli articoli, selezionarne almeno uno e
   causare su un foglio di prova una variazione di disponibilità prima di
   premere Continua.
2. Verificare che appaia il messaggio richiesto e che gli articoli non più
   validi non risultino selezionati.
3. Se nessun articolo resta valido, verificare l'assenza di Continua; se uno
   resta valido, verificare che soltanto quello rimanga selezionato.
4. Premere rapidamente Aggiorna due volte e verificare che un edit identico
   non mostri errori.
5. Lasciare aperta una tastiera oltre la validità della callback, poi
   premere un pulsante Shipping v2 e verificare che il bot non termini il
   flusso con un'eccezione.
6. Controllare i log Railway: gli URL Telegram devono contenere
   `bot<redacted>` e mai il token reale.

## Rischi residui

- i lock restano locali al processo; la configurazione supportata continua
  a essere una sola replica Railway;
- tra la diagnostica preliminare e il lock interno del repository esiste
  una finestra di concorrenza inevitabile su Google Sheets. Il repository
  rivalida sotto lock e mantiene il comportamento tutto-o-niente;
- l'identificazione del predicato reale nei dati di produzione richiede
  osservare il nuovo warning al prossimo conflitto o svolgere una verifica
  esplicitamente autorizzata su un ambiente di prova;
- `VERSIONE_BOT` in `CONFIG`, se valorizzata, prevale sul fallback `2.3.2` e
  deve essere aggiornata operativamente al deploy.

## Artefatti

Gli hash SHA-256 degli ZIP sono riportati nel riepilogo finale di consegna.
