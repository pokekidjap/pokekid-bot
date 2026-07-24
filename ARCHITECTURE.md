# ARCHITECTURE.md — POKEKID BOT

## Vista generale

```text
Telegram Update
      |
      v
main.py
  |-- CommandHandler
  |-- ConversationHandler
  |-- CallbackQueryHandler specifici
  `-- handle_button (router generico, sempre ultimo)
      |
      v
modules/*  <---->  keyboards/*
      |
      v
services/*
      |
      +---- services/cache.py (cache dati TTL e single-flight)
      `---- services/google_runtime.py
              |
              +---- Gestionale Google Sheets: ORDINI, GRADING
              `---- BOT DB Google Sheets: PROFILI, ADMIN, SPEDIZIONI, CONFIG,
                    LOG, ORDINI_ARTICOLI, SPEDIZIONI_ARTICOLI
```

## Avvio applicazione
`main.py` esegue:
1. `validate_config()`;
2. costruzione `Application`;
3. registrazione handler;
4. registrazione error handler;
5. polling locale oppure webhook Railway.

`post_init()` pubblica i comandi Telegram ed esegue controlli di sola lettura quando `STARTUP_CHECKS=true`.

## Routing Telegram

### Comandi
- `/start` → menu principale e sincronizzazione profilo Telegram;
- `/admin` → pannello amministratore;
- `/spedizioni` → storico spedizioni utente.
- `/cancel` → fallback del profilo e, con motore v2 attivo, rilascio della
  bozza durante l'attesa della ricevuta.

`/cancel` viene pubblicato tra i comandi del bot, ma non è registrato come
handler globale. Nel flusso ricevuta legacy il nuovo fallback resta
trasparente e non modifica lo stato della conversazione.

### ConversationHandler
- profilo;
- ricevuta spedizione;
- inserimento tracking admin;
- broadcast admin;
- modifica messaggi admin;
- ricerca grading;
- inserimento username per l'unione Shipping v2.3.

### Callback specifici
Gli handler specifici intercettano:
- `order_toggle:<riga>`;
- `shipping_continue`, `shipping_carrier:<indice>` e `shipping_cancel`;
- `order_v2_toggle:ART-UUID`, `shipping_v2_page:<numero>`,
  `shipping_v2_continue`,
  `shipping_v2_carrier:<indice>`, `shipping_v2_resume`,
  `shipping_v2_cancel`, `shipping_v2_cancel_draft` e
  `shipping_v2_change_items`;
- `shipping_v2_join`, `join_v2_toggle:ART-UUID`,
  `join_v2_page:<numero>`, `join_v2_refresh`, `join_v2_confirm` e
  `join_v2_cancel`;
- `admin_shipping_open:<id>`, `admin_shipping_receipt:<id>` e
  `admin_user_orders:<indice>`;
- `admin_shipping_cancel:<id>`,
  `admin_shipping_cancel_confirm:<id>` e
  `admin_shipping_cancel_back:<id>`;
- gli entry point e gli stati delle conversazioni profilo, ricevuta,
  tracking, broadcast, messaggi e ricerca grading.

Tutti gli handler sono registrati nello stesso gruppo predefinito di
`python-telegram-bot`: per ogni update viene eseguito il primo handler
compatibile. Per questo l'ordine di registrazione è parte del comportamento.

### Router generico
`handle_button()` gestisce callback semplici tramite una mappa. Estrae il prefisso prima di `:`. Se nessuna rotta corrisponde, mostra “Funzione non riconosciuta”. Deve restare l'ultimo handler callback.

`grading_refresh` è registrato come `CallbackQueryHandler` specifico dopo le
conversazioni e prima del router generico. `show_grading()` riconosce la
callback, elimina la copia in `context.user_data` e forza la rilettura della
cache grading.

## Architettura dati

### Servizio ordini
`services/sheets.py` apre il gestionale e normalizza la scheda ordini. I moduli utente e admin consumano dati normalizzati, non celle grezze quando evitabile.

L'associazione utente avviene confrontando la colonna `UTENTI` con lo
username Telegram normalizzato (`@nome`, minuscolo). La vista utente esclude
`EVASO`, `RESTAURO` e `GRADING`; la vista amministrativa esclude solo
`EVASO`. Quantità non valide sono convertite a zero.

### Servizio grading
`services/grading.py` legge la scheda `GRADING`, normalizza i campi e alimenta `modules/grading.py`.

### Runtime Google condiviso
`services/google_runtime.py` è l'unico livello che crea credenziali, autorizza
`gspread`, apre gli Spreadsheet e risolve le Worksheet. Le risorse sono lazy e
condivise per processo:

- una istanza `Credentials`;
- un client `gspread`;
- uno `Spreadsheet` per `spreadsheet_id`;
- una `Worksheet` per `(spreadsheet_id, worksheet_name)`.

I lock di inizializzazione impediscono la creazione duplicata delle risorse.
I lock di accesso sono distinti da quelli di inizializzazione: ogni worksheet
possiede un `RLock`, quindi le operazioni sulla stessa scheda sono serializzate
mentre schede differenti possono procedere in parallelo. Le sequenze
read-modify-write di profili, configurazione e completamento spedizioni
mantengono lo stesso lock dalla lettura alla scrittura.

`worksheet_operation()`, `worksheet_session()` e `spreadsheet_operation()`
applicano il retry esistente e registrano la durata nel contesto
prestazionale. Il lock è rilasciato anche in caso di eccezione e non viene
mantenuto durante parsing o formattazione locale.

Il runtime conserva soltanto oggetti di connessione, mai valori delle celle.
`reset_google_resources()` azzera esplicitamente credenziali, client,
Spreadsheet e Worksheet per test o recovery; non esistono reset automatici.
Errori di configurazione, `SpreadsheetNotFound` e `WorksheetNotFound` restano
visibili al chiamante e un'inizializzazione fallita non viene memorizzata.

### Database bot
`services/bot_db.py` centralizza:
- accesso al database bot tramite il runtime Google condiviso;
- profili;
- amministratori;
- configurazione;
- spedizioni;
- log;
- stato smistamento;
- corrieri;
- aggiornamenti tracking e stato.

Le scritture sono posizionali e presuppongono il seguente ordine delle
colonne:

- `PROFILI` A:J: `TELEGRAM_ID`, `USERNAME`, `NOME`, `EMAIL`, `TELEFONO`,
  `INDIRIZZO`, `CAP`, `CITTA`, `PROVINCIA`, `DATA_AGGIORNAMENTO`;
- `SPEDIZIONI` A:U: `ID`, `DATA`, `TELEGRAM_ID`, `USERNAME`, `PRODOTTI`,
  `STATO`, `CORRIERE`, `TRACKING`, `PAYMENT_FILE_ID`, `NOTE`,
  `DATA_SPEDIZIONE`, `ULTIMO_AGGIORNAMENTO`, `ADMIN`, `NOME`, `EMAIL`,
  `TELEFONO`, `INDIRIZZO`, `CAP`, `CITTA`, `PROVINCIA`,
  `COSTO_SPEDIZIONE`;
- `LOG` A:F: `DATA`, `TELEGRAM_ID`, `USERNAME`, `AZIONE`, `DETTAGLI`,
  `ADMIN`;
- `CONFIG` A:C: `CHIAVE`, `VALORE`, `ATTIVO`;
- `ADMIN`: sono letti almeno `TELEGRAM_ID`, `USERNAME`, `RUOLO` e `ATTIVO`.

Le letture di `PROFILI`, `SPEDIZIONI`, `CONFIG`, `ADMIN` e `LOG` usano le
intestazioni; le scritture non effettuano una migrazione o una verifica
preventiva dell'ordine delle colonne.

### Cache e robustezza
- `services/cache.py`: cache TTL con single-flight per chiave;
- `services/retry.py`: retry con backoff;
- `services/perf.py`: misurazione flussi;
- `services/startup.py`: verifiche iniziali;
- `services/status.py`: stato servizi;
- `services/logger.py`: supporto logging.

TTL effettivi:

| Prefisso | TTL |
|---|---:|
| `orders` | 30 s |
| `profiles` | 60 s |
| `shipping` | 30 s |
| `logs` | 30 s |
| `config` | 300 s |
| `admins` | 600 s |
| `grading` | 60 s |

La cache è protetta da `RLock` e restituisce copie profonde. Per ogni chiave
può esistere un solo loader in corso: le richieste concorrenti, inclusi più
refresh forzati, attendono lo stesso risultato senza trattenere il lock
globale durante la lettura Google. Chiavi differenti possono essere caricate
in parallelo. Un errore viene propagato a tutti i waiter, lo stato di
caricamento viene ripulito e la richiesta successiva può riprovare.

Ogni caricamento registra la generazione della chiave: se `invalidate()` viene
eseguito nel frattempo, il risultato precedente può essere restituito al
chiamante già attivo ma non ripopola la chiave invalidata. L'invalidazione per
prefisso usa lo stesso lock senza attese annidate.

Le invalidazioni sono presenti per cancellazione/sincronizzazione profilo,
spedizioni, configurazione e log. `save_profile()` invalida `profiles`. Il
refresh ordini usa `force=True` su `orders:records`, sostituisce la cache con
la nuova lettura e conserva solo le selezioni che corrispondono ancora alla
stessa riga, allo stesso nome e alla stessa quantità. Aperture e operazioni
remote di `ORDINI`, `GRADING` e BOT DB usano tutte il retry centralizzato.

`cache_info()` mantiene il conteggio `entries` e aggiunge `keys`,
`loads_in_progress` e `coalesced_waits`.

### Versione bot in memoria

`services/bot_version.py` separa il caricamento dalla lettura.
`load_bot_version()` consulta `CONFIG -> VERSIONE_BOT` una sola volta in
`post_init()`, dopo i controlli Google opzionali e tramite
`asyncio.to_thread()`. Un valore assente o un errore usa il fallback `2.3.2`.
`get_bot_version()` restituisce esclusivamente il valore in memoria:
`with_footer()`, toggle e paginazione non eseguono accessi Google. Un cambio
in `CONFIG` diventa visibile al riavvio o dopo un richiamo esplicito del
loader.

## Flusso profilo

```text
/start o menu profilo
       |
       v
show_profile
       |
       +--> visualizza dati
       +--> start_profile_form
       |       |
       |       v
       |  nome -> email -> telefono -> indirizzo -> CAP -> città -> provincia
       |       |
       |       v
       |   revisione e salvataggio
       `--> cancellazione con conferma
```

I dati risiedono in `PROFILI`. La sincronizzazione leggera di Telegram ID e username è in `services/profiles.py`.

La sincronizzazione viene invocata da `/start`, non da ogni update Telegram.
Se il record non esiste crea una riga minima, priva dei dati di spedizione.

`services/profiles.py` definisce una sola lista di campi obbligatori e le
funzioni pubbliche `get_missing_shipping_profile_fields()` e
`is_shipping_profile_complete()`. Il profilo può quindi trovarsi in tre stati:

- assente: viene mostrato il normale invito all'inserimento;
- riga Telegram minima o profilo parziale: viene richiesto di completare i
  dati, senza mostrare campi vuoti;
- profilo di spedizione completo: sono disponibili visualizzazione, modifica
  ed eliminazione.

`get_profile(..., force_refresh=True)` forza una nuova lettura di `PROFILI`
quando serve una validazione aggiornata.

## Flusso ordini

```text
menu_orders
   |
   +--> orders_all
   |
   +--> orders_available
            |
            v
      toggle righe ordine
            |
            v
      shipping_continue
   |
   `--> shipping_v2_join (solo con v2 attiva)
            |
            v
       username -> selezione articoli propri -> conferma
```

Con motore legacy le righe selezionate sono conservate in
`context.user_data` tramite numeri di riga. Con motore v2 la selezione usa
esclusivamente `ID_ARTICOLO` e chiavi `shipping_v2_*` separate. La v2 mostra
8 articoli per pagina; `shipping_v2_page` conserva la pagina corrente e
viene ricondotta all'intervallo valido dopo ogni refresh. Toggle e cambio
pagina non creano prenotazioni.

## Flusso spedizione legacy

```text
articoli selezionati
      |
      v
lettura profilo
      |
      v
scelta corriere
      |
      v
shipping_payment
      |
      v
foto/documento ricevuta
      |
      v
ricontrollo smistamento
      |
      v
profilo aggiornato + completezza
      |
      v
ordini aggiornati + coerenza selezione
      |
      v
create_shipping_request
      |
      +--> riga SPEDIZIONI
      +--> log
      `--> notifica admin
```

Limiti del solo motore legacy:

- le righe del gestionale vengono ricontrollate ma non riservate né
  aggiornate prima di scrivere `SPEDIZIONI`;
- il controllo finale riduce la finestra di incoerenza ma non impedisce una
  modifica successiva alla lettura e precedente alla scrittura;
- non esiste ancora un blocco delle richieste duplicate sugli stessi
  articoli;
- `generate_shipping_id()` calcola il progressivo con lettura e successiva
  scrittura separate, senza protezione da richieste concorrenti.

Alla ricezione dell'allegato, prima di generare l'ID o scrivere:

1. `is_sorting_active()` viene eseguito nuovamente;
2. `get_profile(user.id, force_refresh=True)` rilegge `PROFILI`;
3. `get_user_orders(username, force_refresh=True)` rilegge `ORDINI`;
4. ogni articolo selezionato deve conservare riga, nome, quantità e stato
   `IN MAGAZZINO`.

I percorsi bloccati terminano senza chiamare `create_shipping_request()` e
quindi senza scrivere `SPEDIZIONI` o `LOG`.

Lo stato temporaneo usa chiavi in `context.user_data`, tra cui:
- `available_orders`;
- `selected_order_rows`;
- `selected_orders`;
- `shipping_profile`;
- `shipping_methods`;
- `selected_carrier`;
- `shipping_selection_timestamp`;
- `waiting_shipping_receipt`.

`clear_shipping_data()` deve essere aggiornato quando vengono aggiunte nuove chiavi al flusso.

## Flusso spedizione v2.2

```text
orders_available
      |
      v
valida schema -> sincronizza registro -> rilascia scadenze
      |
      +--> bozza PRENOTATO: riprendi / annulla
      +--> bozza CONFERMATO: storico, nessun annullamento
      `--> selezione tramite ID_ARTICOLO
                    |
                    v
          shipping_v2_continue
                    |
                    v
          reserve_items (tutto-o-niente)
                    |
                    v
          corriere -> ricevuta
                    |
                    v
          rivalidazione registro
                    |
                    v
          create_or_get_v2_shipping_request
                    |
                    +--> SPEDIZIONI A:X
                    `--> SPEDIZIONI_ARTICOLI = CONFERMATO
```

`services/shipping_engine.py` è l'unico punto che seleziona il motore. Se
uno o entrambi i flag non sono attivi viene eseguito il codice legacy senza
richiedere le schede v2. Quando entrambi sono attivi, errori di schema o
servizio interrompono il percorso v2 con un messaggio prudente e non
richiamano `create_shipping_request()`.

La prenotazione nasce esclusivamente al callback
`shipping_v2_continue`. I toggle modificano solo la sessione locale e
invalidano un'eventuale idempotency key non usata. La key viene generata
soltanto al tentativo di continuazione.

`shipping_v2_page:<numero>` modifica soltanto la pagina locale. La tastiera
estrae al massimo 8 elementi, mentre conteggi e
`shipping_v2_selected_item_ids` restano globali.

L'apertura ricostruisce sempre l'eventuale bozza attiva dalle righe correnti
di `SPEDIZIONI_ARTICOLI`. In questo modo una bozza sopravvive alla perdita di
`context.user_data`: `Riprendi` rilegge articoli, profilo e corrieri dal
DATABASE BOT. Una bozza `PRENOTATO` scaduta viene rilasciata; una bozza
`CONFERMATO` blocca nuove richieste e non è annullabile dall'utente.

Con Shipping v2 attiva, l'apertura ottimizzata sincronizza e conserva per
10 secondi uno snapshot single-flight del registro e dello schema. Su miss o
refresh legge una volta `ORDINI`, `PROFILI`, `ORDINI_ARTICOLI`,
`SPEDIZIONI_ARTICOLI` e `SPEDIZIONI`; su hit rilegge soltanto le prenotazioni
per ricostruire bozze e occupazioni correnti. `orders_refresh` usa sempre
`force=True`. Toggle e cambio pagina lavorano soltanto su
`context.user_data`. Continua, pagamento e finalizzazione non usano questo
snapshot come autorità e mantengono la rivalidazione completa sotto i lock
previsti.

La hotfix v2.3.2 rende esplicita anche la riconciliazione dopo un conflitto
in Continua. `continue_v2_shipping()` conferma subito la callback una sola
volta, quindi, se `reserve_v2_items()` segnala
`ReservationConflictError`, richiama
`prepare_v2_opening_state(..., force_refresh=True)`. Il nuovo elenco
interseca gli ID selezionati con quelli ancora disponibili; la tastiera
mostra `shipping_v2_continue` soltanto quando l'intersezione non è vuota.
Il percorso non aggiunge timestamp artificiali: `Message is not modified`
viene ignorato, mentre gli altri `BadRequest` restano errori reali. Per
callback già scadute viene assorbito esclusivamente il testo Telegram
`Query is too old and response timeout expired or query id is invalid`.

Prima della prenotazione, la diagnostica di disponibilità applica per ogni
ID gli stessi predicati autorevoli: `IS_ACTIVE=TRUE`,
`SYNC_STATUS in {OK, MODIFICATO}`, `STATO_ORIGINE=IN MAGAZZINO`,
proprietario uguale al titolare e assenza di una prenotazione occupante in
`SPEDIZIONI_ARTICOLI`. I log riportano gli esiti booleani e gli stati, non
il Telegram ID del proprietario. Il gestionale sorgente resta in sola
lettura: sincronizzazione e diagnostica scrivono esclusivamente nel
DATABASE BOT secondo i confini già definiti.

Annulla, Cambia articoli, annullamento ricevuta e `/cancel` rilasciano
idempotentemente la bozza `PRENOTATO`. Il contesto viene cancellato solo
dopo una scrittura riuscita.

### Budget testi e rivalidazione

`services/shipping_v2_text.py` costruisce gli elenchi senza troncare HTML:
aggiunge righe intere fino al budget di 3.800 caratteri prima del footer e,
se necessario, mostra `… e altri N articoli`. Totale articoli e unità sono
sempre presenti. `PRODOTTI` conserva l'elenco completo e viene rifiutato
prima delle scritture di spedizione oltre 45.000 caratteri.

`validate_v2_draft_against_registry()` sincronizza il registro, acquisisce
`ORDINI_ARTICOLI -> SPEDIZIONI_ARTICOLI`, rilegge la bozza e verifica
titolare, stato, scadenza, attività, associazione, non ambiguità, magazzino,
proprietà e snapshot. È obbligatoria prima del riepilogo con PayPal, quando
l'utente apre l'invio ricevuta e prima della finalizzazione.

Una bozza `PRENOTATO` non più valida viene rilasciata e torna alla selezione.
Una bozza `CONFERMATO` è autorevole, non viene rilasciata e recupera la
richiesta già coerente.

### Finalizzazione cross-worksheet

`services/shipping_v2.py` usa l'ordine globale:

```text
ORDINI_ARTICOLI -> SPEDIZIONI_ARTICOLI -> SPEDIZIONI
```

Il progressivo `SP-AAAAMMGG-NNN` viene calcolato mentre il lock di
`SPEDIZIONI` è acquisito. La riga A:X contiene A:U compatibile col legacy,
UUID, idempotency key e `VERSIONE_SCHEMA=V2`. `PRODOTTI` deriva dagli
snapshot della bozza.

Dopo un timeout post-append, una conferma parziale o un doppio invio, il
coordinatore ricerca key e UUID, recupera l'unica riga principale, completa
le righe articolo e rilegge entrambi i lati. Duplicati, payload differenti e
associazioni a un'altra bozza o titolare sono conflitti bloccanti. Gli admin
vengono notificati solo dopo la coerenza finale.

Il primo `PAYMENT_FILE_ID` e il primo tipo allegato salvati sono autorevoli:
un retry con un altro allegato riconcilia senza sovrascrivere o duplicare.
Differenze commerciali, anagrafiche, di bozza o articolo restano conflitti.
Il risultato tecnico distingue `CREATED_NOW`, `RECONCILED_NOW` e
`ALREADY_COHERENT`.

### Notifiche admin recuperabili

Per ogni coppia richiesta/admin, `LOG` conserva
`SHIPPING_V2_ADMIN_NOTIFIED` con
`shipping_id=<ID>|admin_id=<ID>`. Un `asyncio.Lock` per ID spedizione evita
doppi invii concorrenti nella singola istanza; il marker viene scritto solo
dopo `send_message()` riuscito e un errore su un admin non blocca gli altri.

La semantica è at-least-once: un crash tra invio Telegram e marker può
duplicare la notifica, ma non produrre una perdita silenziosa. Lo stato
idempotente della richiesta non è prova dell'avvenuta consegna.

### Disattivazione sicura

`scripts/prepare_shipping_v2_deactivation.py` è read-only per default e
riporta `PRENOTATO` attive/scadute, `CONFERMATO` e `SPEDITO`.
`safe_to_disable=true` richiede zero bozze `PRENOTATO` attive. Il rilascio
richiede insieme `--release-prebooked --confirm-production`, interessa solo
`PRENOTATO` e produce report JSON e testuale.

### Completamento admin v2

Il dispatcher usa `complete_shipping_request()` per le righe legacy e
`complete_v2_shipping_request()` per `VERSIONE_SCHEMA=V2`. Il percorso v2
riconcilia `SPEDIZIONI` e `SPEDIZIONI_ARTICOLI`, accetta idempotentemente lo
stesso tracking e rifiuta un tracking diverso su una richiesta già spedita.
La notifica parte soltanto dopo la verifica di entrambi i lati. Il risultato
include i partecipanti deduplicati ricavati da `SPEDIZIONI_ARTICOLI`, così
il modulo admin invia `MSG_SPEDIZIONE` al titolare e a tutti i contributor.

## Unione semplificata Shipping v2.3

```text
shipping_v2_join
      |
      v
username -> PROFILI -> TELEGRAM_ID titolare
      |
      v
unica SPEDIZIONI V2 IN_ATTESA senza tracking
      |
      v
sync ORDINI_ARTICOLI -> soli articoli del contribuente
      |
      v
toggle/pagina in context.user_data
      |
      v
join_v2_confirm
      |
      v
ORDINI_ARTICOLI -> SPEDIZIONI_ARTICOLI -> SPEDIZIONI
      |
      +--> righe CONTRIBUENTE direttamente CONFERMATO
      `--> PRODOTTI ricostruito da tutti gli snapshot collegati
```

`modules/shipping_v2_join.py` gestisce la conversazione username e le
callback. `services/shipping_v2_join_session.py` conserva soltanto chiavi
`shipping_v2_join_*`; il riavvio prima della conferma perde la selezione ma
non lascia scritture persistenti.

`services/shipping_v2_join.py`:

- normalizza lo username tramite `get_profile_by_username()`;
- accetta soltanto una richiesta V2 `IN_ATTESA`, senza tracking e coerente
  tra riga principale e articoli;
- filtra `ORDINI_ARTICOLI` per proprietario, attività, sincronizzazione,
  magazzino e assenza di stati occupanti;
- non crea righe `SPEDIZIONI` e non modifica dati anagrafici, corriere,
  costo o ricevuta;
- usa la stessa `UUID_BOZZA`, `UUID_SPEDIZIONE` e `ID_SPEDIZIONE` del
  titolare;
- lega l'idempotency key al digest canonico degli ID selezionati;
- recupera righe contributor parziali e `PRODOTTI` non ancora aggiornato;
- verifica nuovamente entrambi i lati prima del successo.

Più aggiunte dello stesso o di differenti contributor sono ammesse finché la
richiesta resta `IN_ATTESA`. Gli articoli già collegati non vengono più
mostrati. Non esistono inviti, consenso, codici o una scheda partecipanti.

## Flusso smistamento

```text
admin_sorting_start
      |
      v
snapshot disponibilità
      |
      v
richieste spedizione sospese
      |
      v
admin_sorting_complete
      |
      v
confronto snapshot e notifiche utenti
```

L'obiettivo dello snapshot è evitare notifiche duplicate.

## Flusso admin spedizioni

```text
admin_shipping_list
      |
      v
admin_shipping_open:<id>
      |
      +--> mostra ricevuta
      +--> annulla richiesta V2 IN_ATTESA con conferma
      `--> inserisci tracking
                |
                v
        aggiorna SPEDIZIONI
                |
                v
        notifica utente
```

## Flusso grading
`modules/grading.py` implementa elenco, ricerca, paginazione e refresh. Le
tastiere sono in `keyboards/grading.py`. Il modulo contiene una sola
definizione per funzione; il refresh è raggiungibile sia fuori dalla ricerca
tramite handler specifico, sia come fallback della conversazione.

## Configurazione dinamica
La scheda `CONFIG` contiene valori e messaggi usati dal bot, inclusi corrieri e testi amministrabili. Il codice legge configurazioni attive e usa fallback locali quando previsto.

Contiene inoltre lo stato e lo snapshot dello smistamento e i contatori del
centro notifiche admin. Il centro notifiche non possiede una coda autonoma:
filtra gli ultimi 50 record di `LOG` e salva in `CONFIG` il numero di eventi
marcati come letti per ciascun amministratore.

## Sistema di notifiche

- nuova richiesta di spedizione: `modules/shipping.notify_admins()` invia a
  tutti gli admin attivi un messaggio con pulsante di apertura nel legacy;
  la v2 notifica soltanto gli admin senza marker dedicato nel `LOG`;
- articoli entrati in magazzino: alla chiusura dello smistamento lo snapshot
  viene confrontato con `ORDINI`, gli username sono risolti tramite
  `PROFILI` e gli utenti trovati ricevono `MSG_MAGAZZINO` o il fallback;
- cambio username: `/start` aggiorna `PROFILI` e avvisa gli admin di
  allineare manualmente `ORDINI`;
- tracking: il completamento admin aggiorna `SPEDIZIONI`, scrive il log e
  tenta l'invio di `MSG_SPEDIZIONE`; per V2 usa tutti i proprietari unici
  di `SPEDIZIONI_ARTICOLI`, per il legacy soltanto il titolare;
- unione v2.3: contribuente, titolare e admin ricevono notifiche semplici
  dopo la coerenza completa, senza pulsanti di consenso;
- annullamento admin v2.3: tutti gli utenti coinvolti ricevono l'ID della
  richiesta e l'indicazione che gli articoli sono nuovamente disponibili;
- broadcast: il messaggio viene inviato una volta per `TELEGRAM_ID` presente
  in `PROFILI`, con eventuale `MSG_BROADCAST_FOOTER`;
- centro notifiche admin: è una vista filtrata di `LOG`, non un sistema di
  consegna persistente con cursore per evento.

Le chiamate Telegram sono asincrone. I flussi applicativi spostano le
operazioni sincrone Google in `asyncio.to_thread()`; nel thread, il runtime
condiviso coordina accesso, retry e misurazioni.

`services/logging_security.py`, configurato nello startup dopo
`logging.basicConfig()`, imposta `httpx` e `httpcore` almeno a `WARNING` e
redige il segmento sensibile degli URL `api.telegram.org/bot...` anche
prima dell'emissione degli handler root. Il token del bot non deve quindi
comparire nei log HTTP, nemmeno in un record a livello warning o superiore.

## Confini delle responsabilità
- `modules/`: gestione Update/Context e testi del flusso;
- `keyboards/`: costruzione tastiere;
- `services/`: accesso dati e logica riusabile;
- `main.py`: composizione e routing, non logica applicativa pesante.

Le nuove funzioni devono rispettare questi confini.

## Gestione finale v2.3

Il dettaglio admin V2 viene ricostruito dai dati correnti e raggruppa
articoli, quantità, ruolo, username e Telegram ID per proprietario. Le
informazioni di consegna continuano a provenire dalla riga principale.

`cancel_v2_shipping_request_by_admin()` usa
`SPEDIZIONI_ARTICOLI -> SPEDIZIONI`, porta tutte le righe
`PRENOTATO/CONFERMATO` a `RILASCIATO`, quindi imposta la richiesta
`ANNULLATO`. Il retry riconcilia entrambi gli stati parziali. Richieste
legacy, spedite, con tracking o con articoli `SPEDITO` sono bloccate.

## Spedizioni v2.1: registro esterno e prenotazioni

Questa sezione descrive le fondamenta introdotte in v2.1. Dalla fase v2.2
esse sono collegate ai flussi Telegram dietro il doppio feature flag.

```text
Gestione vendite gruppo (SOLA LETTURA)
  ORDINI
     |
     | get_all_values()
     v
services/order_registry.py
     |---- PROFILI (lettura, risoluzione username)
     |---- SPEDIZIONI_ARTICOLI (lettura stati vivi)
     `---- ORDINI_ARTICOLI (read-check-write con worksheet_session)

services/reservations.py
     |---- ORDINI_ARTICOLI (verifica eleggibilità)
     `---- SPEDIZIONI_ARTICOLI (read-check-append/update)

scripts/migrate_shipping_v2.py
     `---- modifica soltanto DATABASE BOT
```

### Registro `ORDINI_ARTICOLI`

Le 23 colonne A:W conservano ID articolo, origine, fingerprint, indice del
duplicato, snapshot, proprietario, stato di sincronizzazione e attività.
`IDENTITY_FINGERPRINT` usa DATA, OGGETTO, QUANTITA, COSTO, VENDITA,
TOT. VENDITA e UTENTI normalizzati. `ROW_FINGERPRINT` usa tutte le celle
A:K normalizzate.

La riconciliazione applica nell'ordine:

1. stessa riga sorgente e stesso row fingerprint;
2. row fingerprint univoco spostato;
3. stessa riga e stesso identity fingerprint con modifica controllata;
4. blocco `AMBIGUO` quando l'associazione non è dimostrabile.

Gli ID hanno formato `ART-UUIDv4`, sono assegnati solo nel DATABASE BOT e
non vengono rigenerati. I record scomparsi non sono eliminati: diventano
`NON_PRESENTE` e inattivi. Un gruppo duplicato che cambia mentre contiene un
articolo prenotato o confermato non viene riassegnato automaticamente.

`LAST_SEEN_AT` è stabile quando sorgente e associazione profilo sono
identiche. Viene aggiornato soltanto insieme a un cambiamento persistito
(contenuto/fingerprint/riga, username o proprietario, stato sorgente,
attività o stato di sincronizzazione). Di conseguenza una sincronizzazione
immediata identica produce `updated=0`, non esegue `batch_update` e conserva
gli ID esistenti.

### Prenotazioni `SPEDIZIONI_ARTICOLI`

Sono prenotabili soltanto record del registro attivi, associati a un Telegram
ID, in stato sorgente `IN MAGAZZINO` e con sincronizzazione `OK` o
`MODIFICATO`. La prenotazione di un gruppo mantiene il lock dalla verifica
alla scrittura e fallisce interamente se un solo articolo non è eleggibile o
è già occupato.

`PRENOTATO` ha TTL; `CONFERMATO` e `SPEDITO` non scadono; `RILASCIATO` è
terminale per la singola riga e consente una nuova prenotazione. UUID e
idempotency key riconciliano timeout incerti successivi all'append.

### Estensione `SPEDIZIONI`

Le colonne legacy A:U restano inalterate. V:X sono rispettivamente
`UUID_SPEDIZIONE`, `IDEMPOTENCY_KEY`, `VERSIONE_SCHEMA`. Le righe legacy non
sono riscritte automaticamente.

### Attivazione e concorrenza

I flag restano disattivati per default. Gli handler importano i nuovi
servizi, ma non leggono né validano le schede v2 finché il motore
centralizzato restituisce `LEGACY`. L'attivazione richiede entrambi i flag e
la validazione schema esplicita.

I `RLock` per worksheet serializzano i thread della singola istanza. Non
esiste transazione distribuita tra fogli o processi: più repliche Railway non
sono sicure e cache/lock Python non possono fungere da autorità distribuita.

### Hardening v2.1.1

Il repository determina sempre il ruolo dalle proprietà del registro:

- proprietario uguale al chiamante → `TITOLARE`;
- proprietario diverso → `CONTRIBUENTE`, ammesso soltanto se l'ID è incluso
  in `authorized_contributor_item_ids`;
- almeno un articolo `TITOLARE` è obbligatorio;
- una bozza viva può contenere più righe titolare, ma un solo Telegram ID
  titolare.

Dentro lo stesso lock di `SPEDIZIONI_ARTICOLI`, prima di un nuovo append,
vengono rilasciate le prenotazioni scadute e viene verificata l'assenza di
un'altra bozza `PRENOTATO` non scaduta o `CONFERMATO` dello stesso titolare.
`SPEDITO` e `RILASCIATO` non bloccano una nuova bozza.

La validazione distingue record storici da prenotazioni vive: uno
`SPEDITO` può riferirsi a un articolo ormai inattivo, mentre `PRENOTATO` e
`CONFERMATO` richiedono ancora un articolo attivo, associato, non ambiguo e
`IN MAGAZZINO`.

La migrazione riporta separatamente schema installato, validità del piano e
schema finale previsto. Un errore operativo interrompe le scritture
successive, conserva i percorsi dei backup e segnala `NO_WRITES` oppure
`POSSIBLY_PARTIAL`; non esiste rollback automatico.
