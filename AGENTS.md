# AGENTS.md — POKEKID BOT

## Scopo
Questo file è la guida operativa per Codex e per qualunque assistente che modifichi il progetto. Prima di intervenire sul codice, leggere anche `README.md`, `ARCHITECTURE.md`, `CHANGELOG.md` e `ROADMAP.md`.

## Progetto
POKEKID BOT è un bot Telegram scritto in Python per:
- consultazione ordini e articoli disponibili;
- profili e dati di spedizione;
- richieste di spedizione con ricevuta;
- storico e tracking;
- stato SUB Grading;
- notifiche di magazzino;
- funzioni amministrative, smistamento, broadcast e configurazione messaggi.

## Stack e runtime
- Python
- `python-telegram-bot==22.8`
- Google Sheets tramite `gspread` e `google-auth`
- configurazione tramite variabili ambiente e `python-dotenv`
- polling in locale; webhook su Railway quando è presente `RAILWAY_PUBLIC_DOMAIN`

## Struttura principale
- `main.py`: avvio, registrazione handler, router callback generico, webhook/polling.
- `config.py`: variabili ambiente e validazione configurazione.
- `modules/`: flussi Telegram e logica di presentazione.
- `keyboards/`: tastiere inline.
- `services/`: accesso dati, cache, retry, notifiche, stato, statistiche e utilità.
- `utils/`: helper generici.

## Moduli applicativi
- `modules/profile.py`: creazione, modifica, visualizzazione e cancellazione profilo.
- `modules/orders.py`: ordini, articoli disponibili, selezione e avvio richiesta spedizione.
- `modules/shipping.py`: ricevuta pagamento, creazione richiesta e notifica admin.
- `modules/history.py`: storico spedizioni utente.
- `modules/grading.py`: elenco, paginazione, ricerca e refresh SUB Grading.
- `modules/admin.py`: dashboard, smistamento, richieste, tracking, storico, broadcast, notifiche, messaggi e statistiche.

## Google Sheets
Il progetto usa due file Google distinti.

### Gestionale (`SPREADSHEET_ID`)
- scheda ordini configurabile tramite `WORKSHEET_NAME`, valore predefinito `ORDINI`;
- scheda `GRADING`.

Il gestionale è un confine di sola lettura per tutte le fondamenta Shipping
v2.1. Non aggiungere colonne, ID, note o stati e non invocare operazioni
`update`, `append`, `delete`, `clear` o `batch_update` su questo spreadsheet.

### Database bot (`BOT_DB_SHEET_ID`)
- `PROFILI`;
- `ADMIN`;
- `SPEDIZIONI`;
- `CONFIG`;
- `LOG`.
- `ORDINI_ARTICOLI` per ID stabili, fingerprint e snapshot della sorgente;
- `SPEDIZIONI_ARTICOLI` per prenotazioni e associazioni articolo-spedizione.

Non modificare nomi, intestazioni o significato delle colonne senza autorizzazione esplicita e senza migrazione compatibile.

L'accesso Google è centralizzato in `services/google_runtime.py`. Il runtime:
- crea in modo lazy una sola istanza `Credentials` e un solo client `gspread`
  per processo;
- conserva uno `Spreadsheet` per ID e una `Worksheet` per coppia
  `(spreadsheet_id, worksheet_name)`;
- usa lock distinti per inizializzazione e accesso, con un `RLock` per ogni
  worksheet;
- esegue le operazioni remote tramite retry e misurazione prestazionale;
- non conserva dati delle celle, che restano responsabilità di
  `services/cache.py`;
- può essere azzerato esplicitamente con `reset_google_resources()` durante
  test o recovery.

Le operazioni remote nei servizi devono passare da `worksheet_operation()`,
`worksheet_session()` o `spreadsheet_operation()`. Per una sequenza
read-modify-write usare una singola `worksheet_session()` e non rilasciare il
lock tra lettura e scrittura. Non chiamare direttamente `gspread.authorize()`,
`open_by_key()` o `spreadsheet.worksheet()` fuori dal runtime.

## Fondamenta Spedizioni v2.1

- `services/order_registry.py` legge `ORDINI` e riconcilia
  `ORDINI_ARTICOLI` nel DATABASE BOT.
- `services/reservations.py` gestisce prenotazioni tutto-o-niente in
  `SPEDIZIONI_ARTICOLI`.
- `services/shipping_v2_schema.py` contiene intestazioni e validatori.
- `scripts/migrate_shipping_v2.py` crea le strutture soltanto con
  `--apply --confirm-production`; non viene mai eseguito dallo startup.

Gli ID `ART-UUIDv4` risiedono esclusivamente in `ORDINI_ARTICOLI`. La
riconciliazione usa fingerprint, riga precedente e indice dei duplicati; in
caso di dubbio deve produrre `AMBIGUO`, mai collegare silenziosamente un ID
alla riga sbagliata. I record non più presenti diventano `NON_PRESENTE` e
`IS_ACTIVE=FALSE`, senza cancellazione.

Il nuovo sistema è disattivato per impostazione predefinita. L'integrazione
Telegram v2.2 si attiva soltanto quando
`SHIPPING_V2_ENABLED=true` e
`SHIPPING_V2_SINGLE_INSTANCE_ACK=true`. La scelta passa esclusivamente da
`services/shipping_engine.py`. Con motore v2 attivo, errori di schema o
servizio sono bloccanti e non producono fallback legacy.

### Hardening v2.1.1

- `--validate-only` prima della migrazione valida il piano e considera
  normale l'assenza delle due schede v2; dopo la migrazione valida invece i
  dati installati.
- Uno stesso Telegram ID può avere una sola bozza viva come titolare.
- I ruoli derivano dal proprietario: il chiamante non può sceglierli.
- Gli articoli di altri utenti richiedono
  `authorized_contributor_item_ids`; questo parametro non sostituisce il
  futuro flusso di consenso.
- Ogni bozza deve contenere almeno un articolo del titolare e una
  idempotency key comprende implicitamente anche l'identità del titolare.
- Le validazioni di registro e prenotazioni devono restare bloccanti per il
  futuro flusso v2, ma non sono collegate allo startup attuale.
- I test permanenti si eseguono con
  `python -m unittest discover -s tests -v` e non usano Google reale.

La protezione concorrente vale solo tra thread dello stesso processo.
`worksheet_session()` e i lock/cache Python non sono autorità distribuite:
Railway deve mantenere una sola istanza. Più processi o repliche non sono
supportati.

### Integrazione Telegram v2.2

- `services/shipping_engine.py` seleziona centralmente `LEGACY` o `V2`.
- `services/shipping_v2_session.py` contiene soltanto chiavi di sessione v2
  basate su `ID_ARTICOLO`, mai sui numeri di riga del gestionale.
- `modules/shipping_v2.py` gestisce selezione, ripresa, annullamento,
  corriere e ricevuta per il singolo titolare.
- `services/shipping_v2.py` finalizza e completa le richieste v2 con
  idempotenza e riconciliazione cross-worksheet.
- La prenotazione viene creata soltanto quando l'utente preme
  `Continua con la spedizione`, non durante i toggle.
- Le bozze `PRENOTATO` sono rilasciate in modo idempotente da Annulla,
  Cambia articoli, annullamento ricevuta e `/cancel`.
- Le bozze `CONFERMATO` non sono annullabili dall'utente e bloccano una nuova
  richiesta finché l'admin non completa la spedizione.
- L'ordine globale dei lock è
  `ORDINI_ARTICOLI -> SPEDIZIONI_ARTICOLI -> SPEDIZIONI`.
- `SPEDIZIONI` v2 usa A:X e `VERSIONE_SCHEMA=V2`; A:U resta compatibile con
  le letture legacy.
- Il flusso normale v2.2 continua a supportare esclusivamente articoli del
  titolare. I contributor sono gestiti soltanto dal servizio e dal flusso
  separato v2.3; non inserirli nella bozza normale del titolare.

### Hardening operativo v2.2.1

- La selezione v2 usa pagine da 8 elementi e conserva la pagina nella chiave
  `shipping_v2_page`; selezione e conteggi restano globali.
- Tutti gli elenchi v2 passano da `services/shipping_v2_text.py`: il testo
  prima del footer non deve superare 3.800 caratteri e le righe HTML non
  possono essere troncate.
- `PRODOTTI` deve restare entro 45.000 caratteri; il controllo precede ogni
  scrittura di finalizzazione.
- `validate_v2_draft_against_registry()` è obbligatoria prima del riepilogo
  di pagamento, all'avvio della ricevuta e subito prima della
  finalizzazione.
- Una bozza `PRENOTATO` non più coerente viene rilasciata; una bozza già
  `CONFERMATO` viene recuperata e non deve essere rilasciata.
- Il primo allegato salvato per una richiesta esistente è autorevole. Gli
  esiti tecnici sono `CREATED_NOW`, `RECONCILED_NOW` e
  `ALREADY_COHERENT`.
- Le notifiche admin usano marker `SHIPPING_V2_ADMIN_NOTIFIED` in `LOG` e
  semantica at-least-once. Il marker viene scritto soltanto dopo
  `send_message()` riuscito; un crash tra invio e marker può duplicare, mai
  far perdere silenziosamente, la notifica.
- `scripts/prepare_shipping_v2_deactivation.py` è read-only per default.
  Non eseguire `--release-prebooked --confirm-production` senza
  autorizzazione esplicita dell'operatore.

### Unione semplificata v2.3

- `modules/shipping_v2_join.py` riceve lo username, mostra soltanto gli
  articoli del contribuente e gestisce le callback `join_v2_*`.
- `services/shipping_v2_join_session.py` contiene esclusivamente le chiavi
  `shipping_v2_join_*`; toggle e cambio pagina non scrivono sui fogli.
- `services/shipping_v2_join.py` risolve una sola richiesta V2 `IN_ATTESA`,
  aggiunge righe direttamente `CONFERMATO` con ruolo `CONTRIBUENTE` e
  ricostruisce `PRODOTTI` dagli snapshot di tutti gli articoli collegati.
- L'idempotency key dell'unione include un digest della selezione. La stessa
  key non può essere riutilizzata con contribuente, richiesta o articoli
  differenti.
- L'unione non crea righe `SPEDIZIONI`, non modifica destinatario, profilo,
  corriere, costo o ricevuta e non introduce consenso, inviti o pagamenti.
- Il dettaglio admin V2 raggruppa `SPEDIZIONI_ARTICOLI` per proprietario; il
  tracking viene inviato a tutti gli ID unici.
- Soltanto l'admin può annullare l'intera richiesta V2 `IN_ATTESA`; gli
  articoli collegati passano a `RILASCIATO`.

## Regole obbligatorie per le modifiche
1. Non modificare credenziali, token o valori reali di ambiente.
2. Non inserire nel repository `.env`, `credentials.json`, chiavi private o token.
3. Non cambiare nomi delle schede o colonne Google Sheets senza richiesta esplicita.
4. Non rinominare callback esistenti senza aggiornare tastiere, handler e documentazione.
5. Mantenere il router generico `handle_button` come ultimo `CallbackQueryHandler`.
6. Registrare prima gli handler specifici e le `ConversationHandler`, poi il router generico.
7. Evitare chiamate sincrone lente a Google Sheets direttamente nell'event loop; usare `asyncio.to_thread` quando necessario.
8. Mantenere cache, invalidazione mirata, single-flight per chiave e retry già
   presenti.
9. Non eliminare funzioni funzionanti durante un refactoring non richiesto.
10. Fare modifiche piccole, verificabili e retrocompatibili.
11. Aggiornare `CHANGELOG.md` per ogni modifica completata.
12. Aggiornare `ROADMAP.md` quando un'attività cambia stato.

## Convenzioni callback
Le callback usano prefissi funzionali, tra cui:
- `menu_*` per navigazione principale;
- `orders_*` e `order_toggle:*` per ordini;
- `shipping_*` per spedizioni;
- `profile_*` per profilo;
- `grading_*` per grading;
- `admin_*` per amministrazione.

Le callback con parametro usano normalmente `azione:valore`. Il router generico estrae la parte prima di `:`.

Shipping v2 aggiunge:
- `order_v2_toggle:ART-UUID` per la selezione tramite ID stabile;
- `shipping_v2_page:<numero>` per la paginazione locale;
- `shipping_v2_continue`;
- `shipping_v2_carrier:<indice>`;
- `shipping_v2_resume`;
- `shipping_v2_cancel`, `shipping_v2_cancel_draft` e
  `shipping_v2_change_items`.
- `shipping_v2_join` come ingresso della conversazione username;
- `join_v2_toggle:ART-UUID`, `join_v2_page:<numero>`,
  `join_v2_refresh`, `join_v2_confirm` e `join_v2_cancel`;
- `admin_shipping_cancel:<ID>`, `admin_shipping_cancel_confirm:<ID>` e
  `admin_shipping_cancel_back:<ID>`.

Tutti gli handler v2 specifici devono restare prima del router generico. La
callback articolo deve restare entro 64 byte.

`grading_refresh` è registrato anche come handler specifico prima del router
generico, oltre a restare disponibile come fallback della conversazione di
ricerca.

## Flusso spedizione legacy
1. L'utente apre gli ordini disponibili.
2. Seleziona una o più righe.
3. Conferma la selezione.
4. Il bot verifica che il profilo di spedizione sia completo.
5. L'utente sceglie il corriere.
6. Invia una foto o un documento/PDF come ricevuta.
7. Il bot ricontrolla smistamento, profilo e righe ordine con letture
   aggiornate.
8. Se tutti i dati sono ancora coerenti, crea una riga in `SPEDIZIONI`.
9. Gli amministratori ricevono la notifica.
10. L'admin può aprire la richiesta, leggere la ricevuta e inserire il
    tracking.

La creazione della richiesta scrive `SPEDIZIONI` e `LOG`, ma non riserva né
aggiorna le righe di `ORDINI`. Resta quindi da impedire richieste duplicate e
da progettare la prenotazione degli articoli.

Questo comportamento resta intenzionalmente invariato quando il motore
centralizzato restituisce `LEGACY`.

## Flusso spedizione v2

1. L'apertura valida lo schema, sincronizza il registro e rilascia le
   prenotazioni scadute.
2. Una bozza già attiva viene ricostruita dal DATABASE BOT anche dopo un
   riavvio.
3. I toggle modificano soltanto `context.user_data`.
4. Il cambio pagina non crea prenotazioni e non modifica la selezione.
5. `shipping_v2_continue` ricontrolla smistamento, profilo, corrieri e
   registro, quindi crea la prenotazione tutto-o-niente.
6. La bozza viene rivalidata prima di mostrare PayPal, all'avvio della
   ricevuta e prima della finalizzazione.
7. La ricevuta rilegge profilo, corrieri, registro e bozza dal DATABASE BOT.
8. Il coordinatore crea o recupera una sola riga `SPEDIZIONI` v2 e conferma
   tutte le righe `SPEDIZIONI_ARTICOLI`.
9. Gli admin mancanti vengono notificati soltanto dopo la verifica di
   coerenza e marcati nel `LOG`.
10. Il completamento admin aggiorna entrambi i lati e solo dopo notifica il
   titolare e, per le richieste V2, tutti i contributor deduplicati.

Il percorso v2.3 è separato: risolve lo username tramite `PROFILI`, seleziona
automaticamente l'unica richiesta V2 `IN_ATTESA` coerente, mostra soltanto
gli articoli del chiamante e li collega direttamente come `CONTRIBUENTE`.
Non usa il profilo di spedizione del contribuente e non crea una nuova bozza
o una nuova riga `SPEDIZIONI`.

La completezza del profilo è definita esclusivamente in
`services/profiles.py` tramite `get_missing_shipping_profile_fields()` e
`is_shipping_profile_complete()`. Non duplicare l'elenco dei campi
obbligatori in altri moduli.

## Punti delicati noti
- I pulsanti “Aggiorna” degli ordini e del grading forzano la rilettura della
  rispettiva cache. Il refresh ordini conserva solo selezioni ancora
  corrispondenti a riga, nome e quantità.
- Le operazioni Google Sheets possono rallentare il bot: evitare aperture o letture duplicate.
- La riga minima creata da `/start` è un profilo Telegram, non un profilo di
  spedizione completo. Schermate e flussi devono usare la validazione
  condivisa.
- Alla ricezione della ricevuta il profilo e gli ordini sono riletti con
  `force_refresh=True`; mantenere questi controlli prima di
  `create_shipping_request()`.
- Le funzioni di pulizia spedizione rimuovono anche
  `shipping_selection_timestamp`; mantenere l'elenco allineato alle nuove
  chiavi di sessione.
- L'unione v2.3 presuppone un accordo privato tra utenti: non aggiungere
  consenso, inviti o visualizzazione degli articoli altrui senza una nuova
  specifica esplicita.

## Procedura prima di modificare
1. Leggere i file di documentazione.
2. Individuare handler, callback, servizi e fogli coinvolti.
3. Descrivere il piano prima di scrivere codice.
4. Non modificare file non necessari.
5. Eseguire almeno:
   - `python -m compileall .`
   - controllo import;
   - ricerca callback create ma non gestite;
   - verifica che non siano presenti segreti.
6. Riassumere file modificati, motivazione, test e rischi residui.

## Criterio di completamento
Una modifica è completa solo quando:
- il flusso funziona senza rompere quelli esistenti;
- gli errori sono gestiti con messaggi comprensibili;
- cache e Google Sheets restano coerenti;
- sono indicati i test eseguiti;
- `CHANGELOG.md` e, se necessario, `ROADMAP.md` sono aggiornati.
