# Changelog

Tutte le modifiche rilevanti del progetto devono essere annotate qui.

## [Hotfix v2.3.2 — Conflitti disponibilità e sicurezza log] — 24/07/2026

### Corretto
- dopo un `ReservationConflictError` nel callback
  `shipping_v2_continue`, lo stato viene ricostruito con
  `force_refresh=True` e non può riutilizzare lo snapshot che aveva mostrato
  gli articoli prima del conflitto;
- la selezione locale conserva soltanto gli ID ancora presenti nel nuovo
  stato autorevole; il pulsante `Continua con la spedizione` compare soltanto
  quando esiste almeno un articolo selezionato;
- il messaggio di conflitto indica esplicitamente:
  `La disponibilità è cambiata. Seleziona nuovamente gli articoli disponibili.`;
- la callback di continuazione viene confermata una sola volta prima delle
  operazioni remote; il solo `BadRequest` per query scaduta viene assorbito,
  mentre gli altri errori continuano a essere propagati o trattati come
  errori reali;
- gli edit Telegram ignorano in sicurezza il solo
  `Message is not modified`.

### Diagnostica e sicurezza
- i conflitti di prenotazione registrano, per ogni ID articolo, gli esiti di
  `IS_ACTIVE`, `SYNC_STATUS`, `STATO_ORIGINE`, corrispondenza del
  `TELEGRAM_ID_PROPRIETARIO` e presenza di prenotazioni attive, senza
  registrare l'ID proprietario;
- i logger `httpx` e `httpcore` sono limitati almeno a `WARNING`; un filtro
  redige inoltre qualsiasi token presente in URL Telegram prima dell'output;
- il fallback locale della versione è `2.3.2`;
- aggiunti test isolati per cache obsoleta, refresh autorevole, selezione,
  tastiera, callback scadute, `BadRequest`, edit identici, diagnostica
  articolo e redazione token.

### Invariato
- nessuna migrazione o modifica agli schemi Google Sheets;
- `Gestione vendite gruppo`, inclusa `ORDINI`, resta completamente in sola
  lettura;
- callback data, flusso legacy, lock e compatibilità a replica singola
  restano invariati.

## [Performance Hotfix v2.3.1 — Articoli disponibili e versione bot] — 24/07/2026

### Corretto
- `LAST_SEEN_AT` non cambia più per il solo trascorrere del tempo: una sync
  identica lascia i record invariati e non invia `batch_update`;
- cambi reali di sorgente, proprietario, associazione, stato o attività
  aggiornano ancora il record e `LAST_SEEN_AT`;
- apertura e refresh degli articoli Shipping v2 riusano gli snapshot della
  sincronizzazione e validano lo schema localmente, evitando riletture
  duplicate di `ORDINI_ARTICOLI` e `SPEDIZIONI_ARTICOLI`;
- l'apertura normale usa una cache single-flight di 10 secondi, mentre
  `orders_refresh` forza sempre una nuova sincronizzazione e una rilettura
  reale;
- toggle e paginazione restano interamente locali e non possono più leggere
  `CONFIG` tramite il footer;
- `VERSIONE_BOT` viene caricata una sola volta nello startup; lettura e footer
  usano poi soltanto il valore in memoria, con fallback locale `2.3.1`;
- Home e Info costruiscono versione e footer al momento della risposta,
  senza congelarli durante l'import.

### Osservabilità e test
- aggiunti i flussi perf
  `shipping_v2_open_available`, `shipping_v2_refresh_available`,
  `shipping_v2_toggle_item`, `shipping_v2_change_page`,
  `shipping_v2_join_open`, `shipping_v2_join_refresh` e
  `shipping_v2_join_toggle`;
- aggiunti test permanenti per versione in memoria, zero accessi Sheets su
  toggle/pagina, cache/refresh, riassociazione e doppia sincronizzazione
  identica di 4.239 record senza scritture;
- nessuna migrazione, deploy o connessione a Google reale è stata eseguita.

### Invariato
- schemi e nomi delle worksheet;
- gestionale `ORDINI` in sola lettura;
- lock, prenotazioni, idempotenza, tracking, annullamento admin, callback,
  feature flag e flusso legacy;
- rivalidazione autorevole ai passaggi Continua, pagamento e finalizzazione.

## [Spedizioni v2.3 — Unione semplificata e gestione finale] — 24/07/2026

### Aggiunto
- pulsante `📦 Unisci a una spedizione`, visibile soltanto con Shipping v2
  attiva, e conversazione dedicata per lo username del titolare;
- selezione paginata dei soli articoli propri con callback
  `join_v2_*`, sessione separata e `/cancel` senza scritture;
- servizio `services/shipping_v2_join.py` per ricerca della richiesta,
  aggiunta diretta come `CONTRIBUENTE`, raggruppamento per proprietario e
  annullamento amministrativo;
- idempotency key legata al digest della selezione, riconciliazione di append
  e aggiornamenti parziali e ricostruzione di `PRODOTTI`;
- notifiche best-effort al contribuente, al titolare e agli admin;
- dettaglio admin V2 costruito da `SPEDIZIONI_ARTICOLI`, tracking a tutti i
  Telegram ID coinvolti e annullamento V2 `IN_ATTESA` con conferma;
- 43 test permanenti senza Google reale e report
  `SHIPPING_V2_3_SIMPLE_JOIN_REPORT.md`.

### Corretto
- inizializzazione mancante di `draft_uuid` all'avvio della ricevuta V2;
- il completamento V2 restituisce i partecipanti deduplicati letti dagli
  articoli collegati, così il tracking viene inviato una sola volta per ID.

### Invariato
- `ORDINI_ARTICOLI` A:W, `SPEDIZIONI` A:X e
  `SPEDIZIONI_ARTICOLI` A:U;
- gestionale `ORDINI` e `GRADING` in sola lettura;
- flusso legacy con Shipping v2 disattivata;
- destinatario, indirizzo, ricevuta, corriere e costo della richiesta
  titolare;
- assenza di inviti, consensi, codici, chat, pagamenti condivisi, nuove
  worksheet, migrazioni reali e deploy.

## [Spedizioni v2.2.1 — Hardening operativo, retry e paginazione] — 24/07/2026

### Aggiunto
- paginazione Telegram v2 a 8 articoli con sessione
  `shipping_v2_page` e callback `shipping_v2_page:<numero>`;
- formattatore centralizzato degli elenchi con budget di 3.800 caratteri,
  conteggi globali e indicazione degli articoli omessi;
- rivalidazione sincronizzata della bozza prima del riepilogo, dell'avvio
  ricevuta e della finalizzazione;
- stati tecnici `CREATED_NOW`, `RECONCILED_NOW` e `ALREADY_COHERENT`;
- registro best-effort delle notifiche admin in `LOG`, con marker
  `SHIPPING_V2_ADMIN_NOTIFIED`;
- script operativo `scripts/prepare_shipping_v2_deactivation.py`, in sola
  lettura per default e con rilascio protetto da doppia conferma;
- test permanenti per paginazione, testi, rivalidazione, retry, notifiche e
  disattivazione;
- report `SHIPPING_V2_2_1_OPERATIONAL_HARDENING_REPORT.md`.

### Corretto
- il retry con allegato Telegram differente conserva il primo
  `PAYMENT_FILE_ID` e il primo tipo già salvati, senza duplicare la richiesta;
- gli admin mancanti vengono notificati anche dopo un recupero idempotente;
- una bozza `PRENOTATO` non più valida viene rilasciata e rimandata alla
  selezione prima di mostrare i dati di pagamento;
- una bozza già `CONFERMATO` viene recuperata e non rilasciata;
- conflitti permanenti e guasti transitori producono percorsi utente
  distinti;
- `PRODOTTI` oltre 45.000 caratteri blocca la finalizzazione prima delle
  scritture di spedizione.

### Invariato
- schemi `ORDINI_ARTICOLI` A:W, `SPEDIZIONI` A:X e
  `SPEDIZIONI_ARTICOLI` A:U;
- gestionale e `ORDINI` in sola lettura;
- flusso legacy con i flag v2 disattivati;
- dipendenze, runtime Google, cache, contributor e unione tra utenti.

## [Spedizioni v2.2 — Integrazione Telegram singolo titolare] — 23/07/2026

### Aggiunto
- selettore centralizzato `LEGACY`/`V2` basato sul doppio feature flag;
- selezione Telegram v2 tramite `ID_ARTICOLO` e callback stabili entro 64
  byte;
- sessione `shipping_v2_*` separata dal flusso basato sulle righe legacy;
- schermate per selezione, bozza attiva, ripresa, corriere, riepilogo,
  conflitto, scadenza, conferma e annullamento;
- coordinatore v2 per finalizzazione A:X, idempotenza, progressivo sotto lock
  e recupero da operazioni cross-worksheet parziali;
- completamento admin v2 con aggiornamento coerente di `SPEDIZIONI` e
  `SPEDIZIONI_ARTICOLI`;
- fallback `/cancel` durante l'attesa della ricevuta v2;
- test permanenti per feature flag, selezione, bozze, concorrenza,
  finalizzazione, callback e completamento admin;
- report `SHIPPING_V2_2_TELEGRAM_INTEGRATION_REPORT.md`.

### Sicurezza e compatibilità
- con uno o entrambi i flag mancanti il comportamento resta legacy e le
  schede v2 non vengono richieste;
- con v2 attiva gli errori di schema o servizio sono bloccanti, registrati e
  non causano fallback legacy;
- `ORDINI` e l'intero gestionale restano in sola lettura;
- nessuna migrazione reale, connessione Google reale, dipendenza o modifica
  al runtime/cache;
- nessun contributor, invito o flusso di unione tra utenti.

### Corretto e verificato
- rilascio idempotente delle bozze `PRENOTATO`, incluse quelle scadute;
- ripresa dopo perdita del contesto Telegram;
- riconciliazione di timeout post-append, conferme miste e doppio invio
  simultaneo;
- rifiuto di UUID/key duplicati, payload differenti e tracking conflittuali;
- notifica admin/utente soltanto dopo la coerenza finale;
- handler specifici v2 registrati prima del router generico.

## [Spedizioni v2.1.1 — Hardening fondamenta] — 23/07/2026

### Corretto
- `--validate-only` pre-migrazione valida ora il piano senza richiedere che
  le nuove schede esistano già;
- distinzione esplicita tra schema installato, piano valido e schema finale;
- una sola bozza viva per Telegram ID titolare, verificata sotto lock;
- ruoli derivati dal proprietario e contributor ammessi soltanto tramite
  autorizzazione esplicita;
- idempotenza estesa all'identità del titolare;
- gestione controllata degli errori operativi e dello stato potenzialmente
  parziale.

### Rafforzato
- validazione di ID `ART-UUIDv4`, fingerprint, source row e versione;
- integrità di UUID dettaglio, bozza, articolo, proprietario, ruolo, stato,
  idempotency key, timestamp timezone-aware e titolare delle bozze vive;
- controllo delle prenotazioni vive rispetto allo stato reale del registro,
  senza trattare come errore storico gli articoli già spediti.

### Test
- aggiunta suite permanente di 31 test `unittest` con fake thread-safe;
- nessun collegamento a Google reale e nessuna nuova dipendenza.

## [Spedizioni v2.1 — Registro esterno e prenotazioni] — 23/07/2026

### Aggiunto
- feature flag Shipping v2 disattivate per default e TTL configurabile;
- schema e validatori per `ORDINI_ARTICOLI`, estensione V:X di `SPEDIZIONI`
  e `SPEDIZIONI_ARTICOLI`;
- registro esterno con ID `ART-UUIDv4`, fingerprint, riconciliazione
  conservativa, gestione duplicati e risoluzione proprietari;
- repository prenotazioni tutto-o-niente, TTL, transizioni, idempotenza e
  riconciliazione dei timeout successivi all'append;
- migrazione dry-run/validate/apply con doppia conferma, backup JSON/CSV e
  report pre-migrazione;
- guide `MIGRATION_SHIPPING_V2.md` e `SHIPPING_V2_FOUNDATION_REPORT.md`.

### Sicurezza e compatibilità
- `ORDINI` e l'intero gestionale restano esclusivamente in lettura;
- tutte le nuove scritture sono limitate al DATABASE BOT;
- nessuna modifica a handler, callback, interfaccia, cache, runtime Google o
  dipendenze;
- nessun collegamento del nuovo sistema ai flussi Telegram;
- supportata esclusivamente una singola istanza Railway.

## [Interfaccia utente v1 — Coerenza, leggibilità e utilizzo mobile] — 23/07/2026

### Aggiunto
- helper UI semplici per intestazioni di sezione, pagina corrente, stati
  leggibili, righe di riepilogo e abbreviazione sicura dei pulsanti;
- report `USER_INTERFACE_V1_REPORT.md`.

### Modificato
- uniformati titoli, spaziature, sezioni, footer ed errori delle schermate
  utente di home, ordini, profilo, SUB Grading, storico e spedizione;
- resi più compatti i pulsanti di navigazione e paginazione;
- limitate a 42 caratteri le etichette dei soli articoli selezionabili,
  conservando icona, quantità, dati e callback originali;
- convertiti in etichette leggibili i soli stati mostrati nello storico;
- aggiornato `LAST_UPDATE` al 23/07/2026, lasciando `BOT_VERSION` a `2.0.0`.

### Verificato
- compilazione e parsing AST di tutti i 34 file Python;
- callback data, handler, `ConversationHandler` e chiamate `query.answer()`
  invariati rispetto al checkpoint;
- rendering simulato di home, ordini, profili, grading, storico, riepilogo e
  conferma spedizione;
- escaping HTML, footer singolo, massimo due divisori e lunghezza dei
  pulsanti;
- byte-identicità del pannello admin e dei servizi dati/Google;
- scansione credenziali e confronto completo con il checkpoint.

### Invariato
- pannello amministratore, callback data, handler e conversazioni;
- logica funzionale, cache, TTL, Google Sheets, validazioni e dati salvati;
- `BOT_VERSION` e contenuto configurato di `MSG_BENVENUTO`;
- funzione futura “Unisci a un'altra spedizione”.

## [Stabilità spedizioni v1 — Profilo e validazione finale] — 23/07/2026

### Aggiunto
- validazione condivisa del profilo di spedizione in `services/profiles.py`
  con elenco unico dei sette campi obbligatori;
- `get_missing_shipping_profile_fields()` e
  `is_shipping_profile_complete()`;
- parametro opzionale `force_refresh` in `get_profile()`;
- ricontrollo finale di smistamento, profilo e articoli alla ricezione della
  ricevuta;
- report `SHIPPING_STABILITY_V1_REPORT.md`.

### Corretto
- distinzione tra profilo assente, riga Telegram minima, profilo parziale e
  profilo di spedizione completo;
- schermata Profilo incompleto senza dichiarazioni errate o campi vuoti;
- interruzione del flusso prima della lettura dei corrieri quando il profilo è
  incompleto;
- uso del profilo appena riletto, anziché del solo snapshot di sessione;
- blocco della richiesta se riga, nome, quantità o stato dell'articolo sono
  cambiati.

### Verificato
- profili assente, minimo, parziale e completo;
- quattro varianti della schermata Profilo;
- profilo eliminato o modificato, smistamento iniziato e ordini cambiati tra
  selezione e ricevuta;
- nessuna chiamata a `create_shipping_request()` e nessuna scrittura
  `SPEDIZIONI`/`LOG` nei percorsi bloccati;
- percorso valido con riletture forzate e profilo aggiornato;
- audit AST/import, callback, `query.answer()`, Fluidità v2, checkpoint e
  credenziali.

### Invariato
- schema Google Sheets, TTL, cache single-flight e runtime Google;
- callback data e registrazione handler;
- generazione ID spedizione;
- assenza di prenotazione, deduplicazione e unione spedizioni.

## [Fluidità v2 — Connessioni Google e richieste concorrenti] — 23/07/2026

### Aggiunto
- `services/google_runtime.py` con inizializzazione lazy e thread-safe di una
  sola istanza `Credentials`, un client `gspread`, Spreadsheet per ID e
  Worksheet per coppia ID/nome;
- lock separati di inizializzazione e accesso, con `RLock` per worksheet,
  retry e misurazione prestazionale centralizzati;
- reset esplicito delle sole risorse Google per test e recovery;
- single-flight per chiave nella cache e contatori diagnostici
  `keys`, `loads_in_progress` e `coalesced_waits`;
- report tecnico `FLUIDITY_V2_REPORT.md`.

### Modificato
- migrati al runtime condiviso tutti gli accessi Google di `services/sheets.py`,
  `services/grading.py`, `services/bot_db.py` e `services/profiles.py`;
- protette per l'intera sequenza le operazioni read-modify-write su profili,
  configurazione e completamento spedizioni;
- accorpati loader e refresh forzati concorrenti della stessa chiave, senza
  bloccare caricamenti di chiavi differenti;
- impedito che un caricamento precedente ripopoli una chiave invalidata
  durante la lettura.

### Verificato
- 50 richieste concorrenti: una sola Credentials, autorizzazione, apertura
  Spreadsheet e risoluzione Worksheet;
- serializzazione sulla stessa worksheet e parallelismo tra worksheet
  differenti;
- 20 richieste cache normali e 20 refresh forzati: un solo loader per gruppo;
- propagazione errori, retry successivo, invalidazione durante caricamento e
  invalidazione per prefisso senza deadlock;
- cache ordini/grading, profili, configurazione, admin, spedizioni e heartbeat
  asincrono con worksheet simulate;
- audit AST/import, callback e `query.answer()`, confronto con checkpoint e
  scansione credenziali.

### Invariato
- interfaccia, testi, callback data, handler, ConversationHandler e tastiere;
- schema Google Sheets, TTL, chiavi cache e risultati pubblici;
- generazione non atomica dell'ID spedizione e funzione futura “Unisci a
  un'altra spedizione”.

## [Fluidità v1.1 — hotfix amministrazione] — 23/07/2026

### Corretto
- ripristinato il controllo `check_admin(update)` in
  `show_user_orders_detail()` prima di qualsiasi lettura di
  `context.user_data["admin_order_users"]`;
- sostituita la seconda `query.answer()` dell'elenco scaduto con la modifica
  del messaggio “Elenco scaduto: aggiorna.” e la tastiera di ritorno agli
  ordini admin;
- rimossa la seconda chiamata a `check_admin(update)` da
  `complete_sorting()`, mantenendo il solo controllo iniziale;
- aggiunto un audit amministrativo con call graph che include la
  `query.answer()` eseguita indirettamente da `check_admin()`.

### Verificato
- tutti i 21 handler callback amministrativi registrati e i 3 handler
  messaggio delle conversazioni admin attraversano `check_admin()` una sola
  volta;
- nessuna lettura di `context.user_data` amministrativo precede
  l'autorizzazione;
- nessuna `query.answer()` viene eseguita dopo `check_admin()`;
- simulati dettaglio ordini autorizzato, non autorizzato e scaduto, oltre alla
  chiusura dello smistamento.

### Invariato
- callback data, Google Sheets, cache, tastiere e moduli non interessati;
- logica di chiusura smistamento e notifiche;
- fase successiva “Fluidità v2”, non avviata.

## [Fluidità v1] — 23/07/2026

### Modificato
- spostate fuori dall'event loop, tramite `asyncio.to_thread()`, le letture e
  scritture Google Sheets usate dai flussi async di home, profilo, ordini,
  grading, storico, spedizioni, notifiche e pannello admin;
- mantenuti nell'event loop oggetti Telegram, dati di sessione, formattazione
  dei testi e costruzione delle tastiere;
- esteso `start_flow` all'intera durata dei flussi misurati e aggiunta la
  misurazione a home, profilo, grading e storico spedizioni;
- i riepiloghi prestazionali oltre 1500 ms sono ora registrati come warning;
- corretti i percorsi callback che potevano confermare la stessa query più di
  una volta o confermarla nuovamente dopo un errore remoto.

### Verificato
- compilazione di tutti i file Python;
- audit AST degli handler async, delle chiamate Google e di `query.answer()`;
- audit completo delle callback prodotte e registrate;
- reattività dell'event loop durante una chiamata simulata di 1,05 secondi;
- propagazione delle eccezioni e conservazione di `contextvars` nei thread;
- confronto con il checkpoint e scansione di segreti e credenziali.

### Invariato
- testi, tastiere, callback data, schema Google Sheets, TTL e chiavi di cache;
- retry, logica funzionale e compatibilità con python-telegram-bot 22.x;
- funzione futura “Unisci a un'altra spedizione”, non implementata.

## [Formattazione riepilogo ordini] — 23/07/2026

### Modificato
- conteggi totale, disponibili e in attesa mostrati su righe separate nella
  schermata “📦 I miei ordini”;
- aggiunta una riga vuota dopo lo username e prima dell'indicazione della
  pagina.

### Invariato
- calcoli, escaping HTML, paginazione, callback, tastiere e logica ordini.

## [Seconda correzione refresh build 2.0.0] — 23/07/2026

### Corretto
- callback dedicata `orders_refresh` esclusivamente per il pulsante
  “Aggiorna elenco”;
- `orders_available` torna a usare la cache per apertura e ritorno dagli step
  della spedizione;
- rilettura forzata degli ordini solo quando
  `query.data == "orders_refresh"`;
- gestione selettiva di `BadRequest("Message is not modified")` nei refresh
  ordini e grading, senza errore utente o log applicativo;
- propagazione invariata degli altri errori `BadRequest`;
- una sola conferma `query.answer()` per ciascun callback di refresh.

### Invariato
- callback diversi da quello del pulsante “Aggiorna elenco”;
- testi mostrati, senza timestamp artificiali;
- struttura dei fogli e flusso spedizioni.

## [Correzioni bug build 2.0.0] — 23/07/2026

### Corretto
- import di `start_flow` nel flusso spedizione;
- registrazione specifica di `grading_refresh` prima del router generico;
- refresh grading con invalidazione della cache e nuova lettura;
- refresh ordini con invalidazione esplicita di `orders:records`, rilettura
  forzata e conservazione delle sole selezioni ancora coerenti;
- invalidazione della cache `profiles` dopo `save_profile()`;
- rimozione di `shipping_selection_timestamp` durante la pulizia della
  sessione;
- rimozione delle definizioni duplicate in `modules/grading.py`, mantenendo
  la versione semanticamente equivalente già attiva.

### Invariato
- callback esistenti e struttura dei fogli Google Sheets;
- flusso futuro “Unisci a un'altra spedizione”, non implementato;
- funzionalità non collegate ai bug elencati.

## [2.0.0] — 22/07/2026

### Aggiunto
- nuova interfaccia utente con menu e footer condivisi;
- profilo utente e dati di spedizione;
- consultazione ordini e selezione articoli disponibili;
- richiesta spedizione con scelta corriere e ricevuta;
- storico spedizioni e tracking;
- stato SUB Grading con ricerca e paginazione;
- pannello amministratore;
- smistamento con notifiche di disponibilità;
- broadcast, centro notifiche, statistiche e messaggi configurabili;
- cache TTL, retry e controlli diagnostici iniziali;
- gestione centralizzata degli errori Telegram;
- supporto polling locale e webhook Railway.

### Sicurezza e manutenzione
- configurazione tramite variabili ambiente;
- validazione delle variabili obbligatorie;
- protezione del webhook Railway;
- logging centralizzato;
- esclusione prevista di credenziali, ambienti virtuali e cache tramite `.gitignore`.

## [Documentazione progetto] — 23/07/2026

### Aggiunto
- `AGENTS.md` con regole operative per Codex;
- `ARCHITECTURE.md` con mappa tecnica del progetto;
- `ROADMAP.md` con attività aperte e future;
- aggiornamento del `README.md` sulla base del codice attuale.

### Nota
Questa voce documenta solamente i file guida. Nessun file Python è stato modificato.

## [Revisione di coerenza documentale] — 23/07/2026

### Corretto
- descrizione degli ordini realmente visibili all'utente e all'admin;
- ambito effettivo del comando `/cancel`;
- ordine e comportamento di handler, conversazioni e router callback;
- flusso nominale e limiti correnti di profili, spedizioni e notifiche;
- schema posizionale atteso per le scritture nei fogli del BOT DB;
- TTL, invalidazioni e copertura effettiva del retry;
- stato dei problemi `grading_refresh`, cache profili e integrità delle
  richieste di spedizione;
- riferimenti storici non presenti nel pacchetto consegnato.

### Aggiunto
- mappa dettagliata dei sottosistemi Google Sheets, cache, ordini,
  spedizioni e notifiche;
- attività prioritarie risultanti dalla revisione statica;
- report finale di analisi.

### Nota
Sono stati modificati esclusivamente file di documentazione. Nessun file
Python è stato modificato.
