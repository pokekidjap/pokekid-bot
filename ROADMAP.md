# ROADMAP — POKEKID BOT

Le attività sono separate tra problemi osservati, miglioramenti approvati e idee da valutare. La roadmap non autorizza automaticamente modifiche al codice.

## Priorità alta

### Ripristino e integrità del flusso spedizione
Stato: validazione legacy completata; fondamenta dati e repository v2.1
sottoposti a hardening v2.1.1; integrazione Telegram v2.2 completata per le
sole spedizioni del singolo titolare e protetta da doppio feature flag;
hardening operativo v2.2.1 completato.
Unione semplificata, gestione finale admin e tracking multipartecipante v2.3
completati. Hotfix v2.3.2 dei conflitti di disponibilità completata.

Completato:
- import corretto di `start_flow` in `modules/shipping.py`;
- rimozione di `shipping_selection_timestamp` durante la pulizia della
  sessione;
- verifica della completezza del profilo prima della scelta del corriere;
- ricontrollo dello smistamento alla ricezione della ricevuta;
- rilettura forzata del profilo e uso dei dati aggiornati;
- rilettura e validazione di riga, nome, quantità e stato degli articoli
  selezionati.
- registro esterno `ORDINI_ARTICOLI` nel DATABASE BOT, senza modificare il
  gestionale;
- ID articolo stabili, fingerprint e riconciliazione conservativa;
- repository prenotazioni idempotente con TTL e blocco dei duplicati;
- migrazione protetta con backup locale e validazione schema.
- validate-only utilizzabile sia prima sia dopo la migrazione;
- titolare, ruoli, contributor autorizzati e bozza unica rafforzati;
- suite permanente `unittest` senza Google reale.
- selettore centralizzato `LEGACY`/`V2`, senza letture delle schede v2 quando
  i flag non sono entrambi attivi;
- selezione Telegram tramite `ID_ARTICOLO`, prenotazione al callback
  Continua e sessione v2 separata;
- ripresa della bozza dal DATABASE BOT dopo perdita del contesto Telegram;
- annullamento idempotente e rilascio delle prenotazioni `PRENOTATO`;
- finalizzazione A:X idempotente con progressivo calcolato sotto lock;
- recupero da timeout post-append e stati cross-worksheet parziali;
- completamento admin v2 con riconciliazione degli articoli e notifica solo
  dopo coerenza.
- selezione v2 paginata a 8 articoli, con conteggi e selezioni globali;
- testi v2 compatti sotto il budget operativo Telegram;
- rivalidazione del registro prima di mostrare pagamento e prima della
  finalizzazione;
- retry con primo allegato autorevole e tre esiti tecnici distinti;
- notifiche admin recuperabili tramite marker best-effort in `LOG`;
- procedura verificabile per la disattivazione sicura della v2.
- unione diretta tramite username verso l'unica richiesta V2 `IN_ATTESA`;
- selezione dei soli articoli del contribuente e finalizzazione diretta
  `CONTRIBUENTE`, senza nuova riga `SPEDIZIONI`;
- recupero idempotente di append e aggiornamento `PRODOTTI` parziali;
- dettaglio admin raggruppato per proprietario, tracking a tutti gli ID
  unici e annullamento amministrativo dell'intera richiesta.
- refresh autorevole dopo conflitto in Continua, selezione riconciliata e
  pulsante disponibile soltanto con almeno un articolo selezionato;
- callback scadute ed edit identici gestiti nei soli casi Telegram previsti;
- diagnostica dei cinque predicati di prenotabilità e protezione del token
  negli URL dei log HTTP.

Obiettivi residui:
- mantenere `ORDINI` permanentemente in sola lettura;
- eseguire la migrazione soltanto con la procedura operativa autorizzata;
- collaudare il flusso su Telegram e su fogli di prova prima
  dell'attivazione in produzione;
- valutare in una fase separata un lock distribuito o un database
  transazionale prima di supportare più repliche.

### Correzione pulsanti “Aggiorna”
Stato: refresh grading e ordini corretti.

Completato:
- `grading_refresh` registrato prima del router generico e collegato alla
  rilettura forzata;
- refresh ordini con invalidazione esplicita e nuova lettura dal gestionale;
- conservazione delle sole selezioni ancora coerenti.

Obiettivo residuo:
- verificare in una fase dedicata gli altri pulsanti refresh amministrativi.

### Coerenza profilo e cache
Stato: invalidazione e validazione del profilo completate.

Completato:
- distinzione tra profilo assente, Telegram minimo, parziale e completo;
- lista unica dei campi obbligatori in `services/profiles.py`;
- interfaccia coerente per profili minimi o incompleti;
- validazione prima dei corrieri e rilettura forzata prima della scrittura.

Obiettivo residuo:
- chiarire la conservazione dei dati personali già copiati in `SPEDIZIONI`
  quando l'utente cancella il profilo.

### Unione con la spedizione di un altro utente
Stato: completata nella fase Shipping v2.3.

Decisione definitiva:
1. gli utenti si accordano privatamente, senza inviti o consenso nel bot;
2. il contribuente inserisce lo username del titolare;
3. il bot risolve `PROFILI` e seleziona automaticamente l'unica richiesta V2
   `IN_ATTESA`, coerente e priva di tracking;
4. il contribuente vede e seleziona esclusivamente i propri articoli;
5. gli articoli vengono collegati direttamente come `CONTRIBUENTE`;
6. destinatario, ricevuta, corriere e costo restano quelli del titolare;
7. admin e notifiche leggono i proprietari da `SPEDIZIONI_ARTICOLI`.

Restano volutamente esclusi inviti, consensi, codici, chat, ripartizione dei
costi, rimozione autonoma dei contributor e nuove worksheet.

## Priorità media

### Prestazioni
Stato: fasi “Fluidità v1”, hotfix v1.1, “Fluidità v2” e
Performance Hotfix v2.3.1 completate.

Completato:
- misurazione dei principali flussi utente e amministrativi;
- spostamento fuori dall'event loop delle operazioni Google usate dalle
  funzioni async;
- soglia warning a 1500 ms per i riepiloghi prestazionali;
- verifica della continuità dell'event loop, delle eccezioni dei thread e dei
  `contextvars`;
- riuso lazy e thread-safe di credenziali, client, Spreadsheet e Worksheet;
- accesso remoto centralizzato con un `RLock` per worksheet, retry e
  misurazione;
- single-flight della cache per evitare letture duplicate e refresh
  concorrenti sulla stessa chiave;
- conservazione di TTL, chiavi cache e comportamento funzionale.
- snapshot coerenti e cache breve per l'apertura Shipping v2, refresh
  forzato, zero accessi Sheets su toggle/paginazione e sincronizzazione senza
  riscritture quando i dati sono invariati;
- versione bot caricata nello startup e letta dalla memoria nei render.

Obiettivi residui per una fase successiva:
- analizzare i dati prestazionali raccolti nell'uso reale;
- valutare test d'integrazione con un foglio Google dedicato.

### Manutenibilità
- centralizzare la notifica admin delle nuove spedizioni nel servizio
  notifiche;
- sostituire le scritture posizionali sui fogli con una verifica esplicita
  dello schema;
- definire un cursore stabile per le notifiche admin lette, invece del
  conteggio degli ultimi 50 log.

### Esperienza utente
Stato: fase “Interfaccia utente v1” completata per le schermate utente; il
pannello amministratore resta escluso.

Completato:
- titoli, sezioni, spaziature, footer e stati visibili uniformati;
- navigazione, paginazione, ricerca, aggiornamento e annullamento resi più
  coerenti;
- etichette degli articoli abbreviate in modo sicuro per l'uso mobile;
- schermate di smistamento, ricevuta e conferma spedizione rese più leggibili.

Obiettivi residui:
- verificare testi e dimensioni dei pulsanti su dispositivi Telegram reali;
- valutare in una fase separata l'interfaccia del pannello amministratore;
- ridurre passaggi non necessari nelle spedizioni soltanto dopo una revisione
  funzionale dedicata.

### Test
- introdurre test unitari per parsing ordini, profili e spedizioni;
- aggiungere test dei callback registrati;
- aggiungere una modalità di collaudo con Google Sheets di prova;
- creare checklist di deploy e rollback.

## Idee future
- dashboard amministrativa più completa;
- ricerca e filtri nello storico spedizioni;
- notifiche configurabili dall'utente;
- gestione gruppi o nuclei di spedizione ricorrenti;
- esportazione report;
- statistiche operative avanzate;
- gestione blacklist e limitazioni;
- tracking automatico tramite API dei corrieri, se disponibile e sostenibile.

## Attività completate
- profili utente;
- ordini e articoli disponibili;
- struttura nominale della richiesta spedizione con ricevuta, da ripristinare
  secondo la priorità alta;
- sospensione richieste durante smistamento;
- notifiche nuovi prodotti;
- storico e tracking;
- pannello admin;
- SUB Grading;
- cache, retry, log e controlli iniziali;
- prima correzione bug: import spedizione, refresh grading/ordini,
  invalidazione profili, pulizia sessione e rimozione duplicati grading;
- documentazione base per Codex.
- revisione completa di coerenza tra codice e documentazione del 23/07/2026,
  senza modifiche ai file Python.
- fase “Fluidità v1”: operazioni Google fuori dall'event loop, callback a
  risposta singola e misurazione completa dei flussi principali.
- hotfix “Fluidità v1.1”: protezione amministrativa ripristinata e conferma
  callback singola nei percorsi corretti.
- fase “Fluidità v2”: risorse Google condivise, accesso concorrente
  thread-safe e cache single-flight.
- fase “Stabilità spedizioni v1”: profilo completo distinto dalla riga
  Telegram minima e validazione finale di smistamento, profilo e ordini.
- fase “Interfaccia utente v1”: schermate utente e navigazione uniformate per
  leggibilità e utilizzo mobile, senza modifiche funzionali.
- fase “Spedizioni v2.2”: integrazione Telegram e finalizzazione idempotente
  per il singolo titolare, senza contributor o unione tra utenti.
- fase “Spedizioni v2.2.1”: paginazione, budget testi, rivalidazione,
  riconciliazione allegati, notifiche admin at-least-once e procedura di
  disattivazione sicura.
- fase “Spedizioni v2.3”: unione diretta tramite username, contributor
  confermati, notifiche, dettaglio admin raggruppato, tracking a tutti e
  annullamento amministrativo.
