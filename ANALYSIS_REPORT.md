# Report di analisi — POKEKID BOT 2.0.0

Data revisione: 23/07/2026
Ambito: archivio `POKEKID_BOT_con_documentazione.zip`
Vincolo rispettato: nessun file Python modificato

> Nota successiva: questo report fotografa lo stato precedente alla prima
> correzione bug. Il 23/07/2026 sono stati corretti import `start_flow`,
> refresh grading/ordini, invalidazione cache profili, pulizia della sessione
> e duplicati grading. Per lo stato corrente vedere `CHANGELOG.md` e
> `ROADMAP.md`.

## Esito sintetico

Il progetto ha una struttura a livelli chiara e adatta a un bot Telegram di
dimensioni medio-piccole: `main.py` compone l'applicazione, `modules/`
gestisce i flussi Telegram, `keyboards/` produce le tastiere e `services/`
racchiude accesso ai dati e logica condivisa.

La compilazione sintattica riesce, ma la build non può essere considerata
funzionalmente pronta. Il flusso di spedizione contiene un errore certo a
runtime (`start_flow` non importato), il refresh grading non è raggiungibile
nel normale stato e non esiste un meccanismo che riservi gli articoli o
impedisca richieste duplicate. Il profilo minimo creato da `/start` è inoltre
confuso in più punti con un profilo di spedizione completo.

La documentazione è stata aggiornata per descrivere il comportamento reale e
le criticità correnti. Non è stato corretto il codice.

## Architettura del progetto

### Avvio e runtime

`main.py`:

1. valida `BOT_TOKEN`, `SPREADSHEET_ID` e `BOT_DB_SHEET_ID`;
2. costruisce l'`Application` di `python-telegram-bot`;
3. registra comandi, conversazioni e callback;
4. registra il gestore globale degli errori;
5. avvia polling in locale o webhook quando è presente
   `RAILWAY_PUBLIC_DOMAIN`.

Il webhook usa `WEBHOOK_SECRET` sia nel percorso sia come `secret_token` e,
su Railway, richiede almeno 24 caratteri. `post_init()` pubblica i comandi e,
se `STARTUP_CHECKS=true`, verifica in un thread separato l'apertura del
gestionale e l'esistenza delle cinque schede del BOT DB.

### Handler e callback

Gli handler sono tutti nel gruppo predefinito e sono registrati in questo
ordine:

1. comandi `/start`, `/admin`, `/spedizioni`;
2. `ConversationHandler` per profilo, ricevuta, tracking, broadcast,
   messaggi admin e ricerca grading;
3. callback specifici per selezione ordine, corriere, annullamento e
   dettaglio admin;
4. `handle_button`, router generico finale.

Il router generico prende la parte prima di `:` e la cerca in una mappa. È
corretto che sia ultimo, perché nel gruppo viene eseguito il primo handler
compatibile.

Le callback con stato o parametro vengono gestite dagli handler specifici;
le navigazioni semplici passano dal router. L'eccezione confermata è
`grading_refresh`: è prodotta dalla tastiera, ma è registrata solo come
fallback della conversazione di ricerca. Quando non esiste una conversazione
attiva arriva al router generico, dove manca la rotta.

`/cancel` non è globale: è presente solamente tra i fallback della
conversazione profilo.

### Google Sheets

Il bot usa due spreadsheet.

Gestionale:

- la scheda configurabile `WORKSHEET_NAME`, normalmente `ORDINI`;
- la scheda fissa `GRADING`.

BOT DB:

- `PROFILI`;
- `ADMIN`;
- `SPEDIZIONI`;
- `CONFIG`;
- `LOG`.

Le credenziali provengono da `GOOGLE_CREDENTIALS_JSON`, se presente, oppure
da `credentials.json`. Ogni apertura autorizza un nuovo client `gspread`;
la connessione e gli oggetti worksheet non sono riutilizzati.

`services/sheets.py` legge tutto `ORDINI`, normalizza le intestazioni e
associa le righe allo username Telegram. La vista utente esclude `EVASO`,
`RESTAURO` e `GRADING`; la vista admin esclude solo `EVASO`.
`services/grading.py` cerca dinamicamente la riga contenente le quattro
intestazioni richieste e può quindi leggere la tabella anche se non parte
dalla prima colonna.

`services/bot_db.py` centralizza le altre letture e scritture. Le letture
ricostruiscono dizionari dalle intestazioni, ma le scritture usano intervalli
e posizioni fisse. Un riordino delle colonne può pertanto corrompere i dati
anche se le intestazioni continuano a esistere.

### Cache

`services/cache.py` implementa una cache in memoria:

- protetta da `RLock`;
- con copie profonde in ingresso e uscita;
- con TTL per prefisso: ordini 30 s, profili 60 s, spedizioni 30 s, log
  30 s, configurazione 300 s, admin 600 s e grading 60 s;
- con invalidazione per chiave esatta o prefisso.

Gli ordini e il grading hanno anche una cache per utente in
`context.user_data`. Il retry con backoff è usato sulle aperture e letture di
`ORDINI` e `GRADING`, ma non copre la maggior parte delle operazioni del BOT
DB.

Le invalidazioni non sono complete: `save_profile()` scrive il foglio senza
invalidare `profiles`; il pulsante “Aggiorna elenco” degli ordini non forza
la cache del gestionale.

### Profili

`/start` richiama `sync_basic_profile()`:

- crea una riga minima con Telegram ID e username se l'utente non esiste;
- aggiorna lo username se è cambiato;
- notifica gli admin del cambio affinché `ORDINI` sia aggiornato
  manualmente.

Il modulo profilo gestisce inserimento, modifica, visualizzazione e
cancellazione. Il codice, però, considera la semplice presenza della riga
come prova che i dati di spedizione siano completi. Una riga minima può
quindi entrare nel flusso ordini e fallire soltanto durante la creazione
della spedizione.

La cancellazione rimuove la riga da `PROFILI`; non cancella le copie dei dati
personali già memorizzate nelle righe storiche di `SPEDIZIONI`.

### Ordini e spedizioni

Gli ordini disponibili sono le righe utente con stato `IN MAGAZZINO`.
La selezione conserva in `context.user_data` il numero di riga, il nome e la
quantità letti dal foglio. Dopo la scelta del corriere, il bot mostra
l'indirizzo e l'email PayPal configurata, quindi attende una foto o un
documento.

Il flusso nominale crea una riga in `SPEDIZIONI`, scrive un record in `LOG` e
notifica gli admin. Il completamento admin imposta `SPEDITO`, salva tracking,
data e amministratore, poi tenta di notificare l'utente.

La build corrente presenta questi limiti:

- `modules/shipping.py` usa `start_flow` senza importarlo;
- la selezione non viene riletta o convalidata prima del salvataggio;
- `ORDINI` non viene modificato né riservato;
- lo stato smistamento è controllato all'ingresso del pagamento, non alla
  ricezione della ricevuta;
- l'ID giornaliero è calcolato con lettura e scrittura separate;
- `shipping_selection_timestamp` è salvato ma non usato né rimosso.

### Smistamento e notifiche

All'avvio dello smistamento viene salvato in `CONFIG` uno snapshot degli
articoli `IN MAGAZZINO`. Alla chiusura viene calcolata la differenza; gli
username con nuove righe pronte vengono risolti tramite `PROFILI` e ricevono
la notifica configurata. Gli username senza profilo vengono segnalati agli
admin.

Gli altri canali di notifica sono:

- nuova spedizione agli admin;
- cambio username agli admin;
- tracking all'utente;
- broadcast a tutti i Telegram ID unici in `PROFILI`;
- centro notifiche admin, costruito filtrando gli ultimi 50 record di `LOG`.

Il centro notifiche salva come “cursore” soltanto il numero degli eventi
interessanti nella finestra corrente, non l'ID o la data dell'ultimo evento:
quando la finestra scorre può conteggiare in modo impreciso i non letti.

## Punti di forza

- Separazione leggibile tra composizione, flussi, tastiere e servizi.
- Router generico realmente registrato per ultimo.
- Controlli amministratore applicati all'ingresso delle funzioni admin.
- Configurazione esterna e assenza di segreti nel pacchetto analizzato.
- Normalizzazione centralizzata di username, Telegram ID, intestazioni e
  quantità.
- Cache thread-safe con TTL differenziati e copie profonde.
- Retry mirato con backoff e jitter sulle letture principali.
- Snapshot dello smistamento, utile a ridurre notifiche duplicate.
- Escaping HTML applicato in molti testi contenenti dati esterni.
- Gestore globale degli errori e messaggi utente non tecnici.
- Verifiche iniziali eseguite fuori dall'event loop.
- Log operativo centralizzato nel foglio `LOG`.

## Criticità e bug evidenti

### Critici

1. **Flusso ricevuta interrotto da `NameError`.**
   `modules/shipping.py:197` e `modules/shipping.py:260` usano
   `start_flow`, che non è importato. Il primo click su
   `shipping_payment` non può completarsi.

2. **Nessuna prenotazione o deduplicazione degli articoli.**
   `create_shipping_request()` scrive soltanto `SPEDIZIONI` e `LOG`.
   Le righe `ORDINI` restano `IN MAGAZZINO`, quindi lo stesso utente può
   selezionarle nuovamente e il codice non impedisce richieste duplicate.

3. **Selezione basata su dati e numeri di riga non ricontrollati.**
   Se il foglio cambia tra selezione e ricevuta, la richiesta usa lo snapshot
   in memoria. Inserimenti, cancellazioni o cambi di stato non vengono
   rilevati.

4. **Profilo minimo confuso con profilo completo.**
   `services/profiles.py:33` crea la riga minima;
   `modules/profile.py:218` e `modules/orders.py:369` controllano
   essenzialmente solo l'esistenza della riga. L'utente può arrivare al
   pagamento con dati incompleti.

### Alti

5. **Cache profilo non invalidata dopo salvataggio.**
   `services/bot_db.py:420-570` aggiorna o aggiunge il profilo senza
   `invalidate("profiles")`. I dati precedenti possono restare visibili per
   60 secondi e interferire con una spedizione immediata.

6. **Race durante lo smistamento.**
   `is_sorting_active()` viene controllato in
   `start_shipping_payment()`, ma non in `receive_shipping_receipt()`. Uno
   smistamento iniziato nel frattempo non blocca la creazione.

7. **Progressivo spedizione non atomico.**
   Due richieste concorrenti possono leggere lo stesso massimo e generare lo
   stesso ID prima delle rispettive `append_row`.

8. **Chiamate sincrone a Google Sheets nell'event loop.**
   Diversi handler ordini, grading, storico, admin, spedizione e notifiche
   chiamano direttamente funzioni `gspread`. In caso di latenza il bot può
   smettere temporaneamente di elaborare altri update.

9. **Callback refresh grading non raggiungibile.**
   La tastiera crea `grading_refresh`, ma il normale percorso la porta a
   “Funzione non riconosciuta”.

### Medi

10. **Sette funzioni grading definite due volte.**
    Il blocco tra `modules/grading.py:243` e `modules/grading.py:703` è
    duplicato. Le seconde definizioni sostituiscono le prime.

11. **Scritture Google dipendenti dalla posizione delle colonne.**
    Il codice legge per intestazione ma scrive intervalli fissi, senza
    validare lo schema completo all'avvio.

12. **Semantica `/cancel` incoerente con il comando pubblicato.**
    È disponibile soltanto durante la conversazione profilo.

13. **Contatore notifiche admin fragile.**
    Il numero di eventi negli ultimi 50 log non è un cursore stabile. Inoltre
    `mark_admin_notifications_read()` risponde alla callback e poi richiama
    una funzione che prova a rispondere di nuovo.

14. **Stato servizi troppo ottimistico.**
    La schermata admin può mostrare “Google Sheets” verde dopo letture del
    solo BOT DB; non verifica necessariamente `ORDINI` e `GRADING`.

15. **Pulizia incompleta della sessione spedizione.**
    `shipping_selection_timestamp` non viene rimosso da nessuna delle due
    funzioni di pulizia.

16. **Autorizzazioni admin senza distinzione di ruolo.**
    `is_owner()` esiste ma non è usato: ogni admin attivo può smistare,
    modificare messaggi e inviare broadcast. Va confermato se sia il modello
    autorizzativo desiderato.

17. **Cancellazione profilo e conservazione PII non esplicitate.**
    La cancellazione non rimuove i dati copiati in `SPEDIZIONI`. Serve una
    policy documentata di conservazione, anonimizzazione o obbligo legale.

## Suggerimenti prioritari

1. Ripristinare il flusso spedizione importando `start_flow`, quindi
   aggiungere test del percorso completo.
2. Introdurre uno stato di prenotazione delle righe ordine e una chiave
   idempotente per impedire doppie richieste.
3. Prima della scrittura, rileggere le righe selezionate e verificare
   proprietario, stato, quantità e identità stabile; non affidarsi soltanto
   al numero di riga.
4. Definire una transazione applicativa o un lock per la generazione degli
   ID; in alternativa usare un identificatore casuale univoco.
5. Separare esplicitamente `profilo Telegram minimo` e `profilo spedizione
   completo`, con una funzione di validazione condivisa.
6. Invalidare `profiles` dopo ogni scrittura e aggiungere test per tutte le
   chiavi cache e i pulsanti di refresh.
7. Spostare tutte le operazioni `gspread` fuori dall'event loop con
   `asyncio.to_thread` o con un livello asincrono dedicato.
8. Registrare `grading_refresh` come callback specifica prima del router e
   rimuovere il blocco duplicato dal modulo grading.
9. Validare all'avvio intestazioni e ordine delle colonne usate dalle
   scritture, oppure costruire gli aggiornamenti tramite mappa header-colonna.
10. Sostituire il contatore notifiche lette con un ID o timestamp monotono
    per evento.
11. Applicare il ruolo `OWNER` alle azioni ad alto impatto, se previsto dal
    modello organizzativo.
12. Aggiungere test unitari e di integrazione con spreadsheet di prova,
    pin delle dipendenze e una pipeline che esegua compilazione, lint,
    callback audit e scansione segreti.
13. Definire e documentare la retention dei dati personali in `PROFILI`,
    `SPEDIZIONI` e `LOG`.

## Documentazione corretta

- `README.md`: visibilità reale ordini, ambito `/cancel` e stato noto della
  build.
- `ARCHITECTURE.md`: handler, callback, schema fogli, cache, profili,
  spedizioni e notifiche.
- `AGENTS.md`: difetti noti e vincoli operativi aggiornati.
- `ROADMAP.md`: priorità basate sui problemi confermati.
- `CHANGELOG.md`: registrazione della revisione.
- `REVIEW_REPORT.md`: distinzione tra compilazione sintattica e correttezza
  runtime; rimosso il riferimento inesatto a `.env.example`.
- `DELIVERY_REPORT.txt`: sincronizzazione profilo e rettifica dei limiti
  funzionali.

## Verifiche eseguite

- lettura integrale dei 42 file dell'archivio, inclusi 33 file Python
  (7.085 righe);
- compilazione con Python 3.12: esito positivo;
- analisi AST/symbol table: sette definizioni duplicate e un globale
  realmente mancante (`start_flow`);
- inventario di callback prodotte, pattern specifici e rotte generiche;
- scansione di pattern per chiavi private, token e credenziali: nessun
  riscontro;
- confronto finale degli hash di tutti i file Python con l'archivio
  originale;
- controllo che il pacchetto finale non contenga `.env`,
  `credentials.json`, `.git`, `.venv`, `__pycache__` o file `.pyc`.

Non sono stati eseguiti collegamenti reali a Telegram o Google Sheets. Il
runtime disponibile non contiene le dipendenze del progetto (`telegram`,
`gspread`, `python-dotenv`), quindi il controllo import completo e i test
end-to-end richiedono un ambiente installato e credenziali di prova.
