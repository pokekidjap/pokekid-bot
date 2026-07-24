# Report Fluidità v1

Data: 23/07/2026

## Obiettivo

La fase “Fluidità v1” impedisce alle operazioni sincrone verso Google Sheets
di bloccare l'event loop di Telegram, rende univoca la conferma delle
`CallbackQuery` e completa la misurazione dei flussi principali.

Il checkpoint precedente alle modifiche è
`POKEKID_BOT_checkpoint_pre_fluidita_v1.zip`, con SHA-256:

`E83B2227A02002A9EBFC0DB5C40F037BAAAF7B9566DE1AD1E2C3E2CE34AC92FD`

## File Python modificati

- `main.py`
- `modules/admin.py`
- `modules/grading.py`
- `modules/history.py`
- `modules/orders.py`
- `modules/profile.py`
- `modules/shipping.py`
- `services/notifications.py`
- `services/perf.py`

Sono stati aggiornati anche `CHANGELOG.md`, `ROADMAP.md` e questo report.

## Operazioni spostate fuori dall'event loop

Sono stati aggiunti 47 punti `asyncio.to_thread()` rispetto al checkpoint,
portando a 55 il totale dei punti già presenti o introdotti nei flussi
analizzati.

- `main.py`: `sync_basic_profile`, `get_config_values`, `get_admins`,
  `is_admin`;
- `modules/grading.py`: `get_grading_records`;
- `modules/history.py`: `get_user_shipping_requests`;
- `modules/orders.py`: `get_active_shipping_methods`, `get_paypal_email`,
  `get_user_orders`; le letture di ordini disponibili e profilo erano già
  eseguite in thread;
- `modules/shipping.py`: `is_sorting_active`, `create_shipping_request`,
  `get_admins`;
- `modules/admin.py`: tutte le chiamate usate dagli async verso `bot_db`,
  `admin_orders`, `sorting` e `stats`;
- `services/notifications.py`: `get_admins`, `get_config_values`,
  `get_all_profiles`.

Le chiamate rimangono sequenziali. Non è stato introdotto `asyncio.gather()`.
Nei thread entrano solo funzioni sincrone e valori semplici già estratti
nell'event loop; gli oggetti Telegram e `context.user_data` restano
nell'event loop.

## CallbackQuery

Sono stati corretti i percorsi con potenziale doppia conferma:

- selezione articolo non valido o non più disponibile;
- prosecuzione spedizione senza articoli selezionati;
- selezione corriere non valido o non disponibile;
- marcatura notifiche admin, che richiamava un renderer capace di rispondere
  nuovamente alla callback;
- apertura richiesta, ricevuta e inserimento tracking quando il dato remoto
  non è presente;
- errore durante l'invio della ricevuta.

Per avvio e chiusura smistamento, la callback valida viene ora confermata prima
della verifica remota. Gli errori successivi alla conferma vengono mostrati
nel messaggio e non producono una seconda `query.answer()`.

La gestione selettiva di `Message is not modified` per ordini e grading è
rimasta invariata.

## Misurazione delle prestazioni

I contesti coprono ora l'intero flusso, inclusi attese Google, rendering e
invio o modifica del messaggio.

Flussi coperti:

- `home`
- `profile`
- `orders_all`
- `orders_available`
- `grading`
- `shipping_history`
- `admin_dashboard`
- `shipping_start`
- `shipping_payment`
- `shipping_receipt`

Restano inoltre attivi i contesti già esistenti `start`, `orders_menu` e
`admin_orders_by_user`.

Il riepilogo di `services/perf.py` è invariato nei campi. Viene scritto a
livello warning se il tempo totale supera 1500 ms, altrimenti a livello info.
I nomi dei flussi non contengono dati personali.

## Verifiche

- 33 file Python compilati correttamente;
- 89 funzioni async sottoposte ad audit AST;
- zero chiamate Google bloccanti dirette trovate nell'event loop;
- una chiamata sincrona mantenuta e motivata:
  `get_current_datetime()`, che usa solamente `datetime.now()` locale e non
  accede a Google;
- 31 funzioni con `query.answer()` analizzate con enumerazione dei rami:
  zero percorsi oltre una risposta;
- 85 callback prodotte confrontate con 27 pattern specifici e 24 route
  generiche: zero callback prive di instradamento;
- `orders_refresh` e `grading_refresh` confermati prima del router generico;
- chiamata Google simulata di 1,05 secondi: 17 heartbeat async completati
  durante l'attesa;
- eccezione generata nel thread propagata correttamente al chiamante async;
- `contextvars` e contesto `start_flow` conservati attraverso
  `asyncio.to_thread()`;
- livelli info/warning verificati ai due lati della soglia di 1500 ms;
- confronto finale limitato ai file elencati in questo report;
- scansione segreti: nessuna credenziale inclusa nel pacchetto.

## Invarianti confermate

- nessuna modifica a callback data o tastiere;
- nessuna modifica alla struttura Google Sheets;
- nessuna modifica a TTL, chiavi cache o retry;
- nessuna cache persistente di client, spreadsheet o worksheet gspread;
- nessuna modifica alla logica di ordini, profili, grading, spedizioni o
  amministrazione;
- nessuna implementazione di “Unisci a un'altra spedizione”;
- compatibilità mantenuta con python-telegram-bot 22.x.

## Rischi residui

- le operazioni Google non bloccano più l'event loop, ma possono ancora
  richiedere tempo o fallire per quota, rete o configurazione;
- il pool thread predefinito di `asyncio` è condiviso: picchi elevati di
  richieste simultanee richiedono osservazione in produzione;
- i flussi di scrittura Google restano sincroni e non transazionali
  all'interno del thread, come prima dell'intervento;
- non è stato eseguito un test reale contro Telegram e Google Sheets di
  produzione.

## Test manuali consigliati

1. Aprire home, profilo, ordini completi, ordini disponibili, grading e
   storico, verificando che altri utenti possano usare il bot durante le
   letture.
2. Provare paginazione e refresh ordini/grading, incluso un refresh senza
   cambiamenti.
3. Selezionare e deselezionare articoli, poi proseguire senza selezione e con
   una selezione valida.
4. Completare una richiesta di spedizione fino all'invio della ricevuta.
5. Dal pannello admin aprire dashboard, statistiche, notifiche, richieste e
   ricevute.
6. Avviare e completare uno smistamento e inviare un tracking.
7. Controllare i log `perf` verificando info sotto soglia e warning per flussi
   oltre 1500 ms, senza dati personali.
