# Stabilità spedizioni v1 — Profilo e validazione finale

Data: 23/07/2026

## Ambito

La fase distingue il profilo Telegram minimo dal profilo di spedizione
completo e aggiunge una validazione aggiornata subito prima della creazione
della richiesta. Non introduce prenotazione degli articoli, deduplicazione,
unione spedizioni o modifiche allo schema Google Sheets.

Il checkpoint precedente è
`POKEKID_BOT_checkpoint_pre_shipping_stability_v1.zip`, copia byte-identica
di `POKEKID_BOT_fluidita_v2.zip`.

## Validazione condivisa del profilo

`services/profiles.py` contiene l'unica definizione dei campi obbligatori:

- `NOME`;
- `EMAIL`;
- `TELEFONO`;
- `INDIRIZZO`;
- `CAP`;
- `CITTA`;
- `PROVINCIA`.

Le funzioni pubbliche sono:

- `get_missing_shipping_profile_fields(profile)`;
- `is_shipping_profile_complete(profile)`.

Entrambe usano `clean_value()`, quindi valori assenti, `None`, stringhe vuote
o composte soltanto da spazi risultano mancanti. Un profilo assente e la riga
minima creata da `/start` sono incompleti.

`create_shipping_request()` usa la funzione condivisa e non mantiene una
seconda lista. L'import è locale per evitare il ciclo tra il servizio profili
e il servizio di persistenza.

`get_profile()` accetta ora `force_refresh: bool = False`. Il valore
predefinito conserva la cache esistente; `True` forza una nuova lettura di
`PROFILI`.

## Schermate Profilo

La schermata distingue:

1. profilo assente: invito normale all'inserimento;
2. profilo Telegram minimo: avviso “Profilo di spedizione da completare”;
3. profilo parziale: stesso avviso, senza campi vuoti;
4. profilo completo: stato completo e pulsanti di visualizzazione, modifica ed
   eliminazione.

I profili minimi e parziali mostrano “Completa dati di spedizione” tramite il
callback esistente `profile_edit_data` e conservano il pulsante dello storico.
Un vecchio callback di visualizzazione su un profilo incompleto viene
ricondotto alla schermata di completamento senza mostrare il riepilogo vuoto.

## Controlli prima della spedizione

### Prima dei corrieri

`continue_shipping_request()` verifica la completezza del profilo. Se il
controllo fallisce:

- non legge i corrieri;
- mostra i pulsanti per completare il profilo, tornare agli ordini o alla
  home;
- conserva `available_orders`, `selected_order_rows` e dati non collegati;
- rimuove soltanto lo stato successivo della spedizione.

### Alla ricezione della ricevuta

Prima di chiamare `create_shipping_request()`:

1. viene ricontrollato `is_sorting_active()`;
2. il profilo viene riletto con `get_profile(..., force_refresh=True)`;
3. la completezza viene rivalidata;
4. gli ordini dell'username corrente vengono riletti con
   `get_user_orders(..., force_refresh=True)`;
5. ogni articolo deve conservare numero di riga, nome, quantità e stato
   `IN MAGAZZINO`.

Il profilo passato alla creazione è quello appena riletto. Nei percorsi
bloccati `create_shipping_request()` non viene chiamata, quindi non vengono
generati ID e non vengono scritti `SPEDIZIONI` o `LOG`.

## Test eseguiti

- profilo assente: incompleto;
- profilo minimo di `/start`: incompleto, sette campi mancanti;
- profilo parziale e valore composto da spazi: incompleto;
- profilo completo: valido;
- `get_profile()` normale e con rilettura forzata;
- `create_shipping_request()` incompleto bloccato prima di ID e scritture;
- `create_shipping_request()` completo accettato;
- apertura schermata Profilo nei quattro casi;
- callback di visualizzazione su profilo incompleto senza campi vuoti;
- accesso spedizione incompleto senza lettura corrieri e con pulizia mirata;
- smistamento iniziato tra pagamento e ricevuta;
- profilo eliminato o reso incompleto durante il flusso;
- articolo con stato, nome, quantità o numero di riga modificati;
- articolo non più associato allo stesso username o non più presente;
- percorso valido con profilo modificato ma completo;
- uso del profilo aggiornato e delle due letture forzate;
- assenza di chiamate a `create_shipping_request()` nei percorsi bloccati.

Verifiche finali superate:

- compilazione e parsing AST di tutti i 34 file Python;
- callback data invariati: stesso insieme di 43 valori letterali;
- conteggio di `query.answer()` invariato per ogni funzione esistente;
- registrazioni handler e `ConversationHandler` in `main.py` invariate;
- ordine del controllo finale verificato:
  `is_sorting_active()` → `get_profile()` → `get_user_orders()` →
  `create_shipping_request()`;
- inventario delle operazioni Google invariato e primitive gspread ancora
  centralizzate;
- `google_runtime.py`, `cache.py`, `sheets.py`, `grading.py`,
  `requirements.txt` e `main.py` identici al checkpoint;
- TTL, chiavi cache e schema dei fogli invariati;
- confronto completo con il checkpoint e scansione credenziali superati.

## Rischi residui

- Gli articoli non vengono prenotati: possono cambiare dopo la rilettura e
  prima della scrittura.
- Non esiste ancora un blocco delle richieste duplicate.
- `generate_shipping_id()` resta non atomico.
- Profilo e ordini appartengono a Spreadsheet differenti e non possono essere
  validati in un'unica transazione.
- La validazione dello smistamento usa la cache `CONFIG`, correttamente
  invalidata dai cambiamenti effettuati tramite il bot; una modifica esterna
  diretta al foglio può restare visibile solo alla scadenza del TTL.
- Il collaudo con Telegram e Google reali resta manuale.

## Test manuali consigliati

1. Aprire Profilo subito dopo `/start` e completare i dati dal nuovo pulsante.
2. Provare a proseguire dagli ordini con una riga profilo minima o parziale.
3. Selezionare articoli, poi eliminare o modificare il profilo prima di
   inviare la ricevuta.
4. Avviare lo smistamento da admin mentre un altro utente è nella schermata
   di invio ricevuta.
5. Modificare stato, nome, quantità o riga di un articolo nel foglio di prova
   prima dell'invio della ricevuta.
6. Completare un percorso valido e verificare una sola nuova riga in
   `SPEDIZIONI`, il relativo `LOG` e la notifica admin.
