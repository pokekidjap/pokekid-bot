# Spedizioni v2.1.1 — Hardening report

## Correzioni

### Validate-only

Prima della migrazione l'assenza di `ORDINI_ARTICOLI` e
`SPEDIZIONI_ARTICOLI` non è più un errore. Vengono verificati compatibilità,
creabilità, piano di sincronizzazione e schema finale previsto. Dopo
l'installazione vengono validati i dati reali.

### Bozza unica e titolare

Sotto il lock di `SPEDIZIONI_ARTICOLI` vengono prima rilasciate le scadenze,
poi controllate le bozze vive del titolare. `PRENOTATO` non scaduto e
`CONFERMATO` bloccano una nuova bozza; `SPEDITO` e `RILASCIATO` no.

I ruoli derivano esclusivamente dal proprietario. Gli articoli altrui
richiedono `authorized_contributor_item_ids`, ma una bozza deve comunque
contenere almeno un articolo del chiamante. Il parametro legacy `roles` può
solo coincidere col ruolo derivato e non consente override.

La verifica dell'idempotency key confronta sia contenuto sia unico Telegram
ID titolare.

### Validazione

Il registro verifica formato `ART-UUIDv4`, source row positivo, fingerprint,
versione e ID univoci. Le prenotazioni verificano campi obbligatori, UUID
dettaglio univoco, ruoli/stati, timestamp timezone-aware, transizioni
documentali e titolare unico delle bozze vive.

Una prenotazione viva richiede un record attivo, associato, non ambiguo e
`IN MAGAZZINO`. Lo storico `SPEDITO` può invece riferirsi a un record ormai
inattivo.

### Errori migrazione

Il primo errore interrompe la sequenza. I report indicano eccezione, backup,
assenza di scritture oppure possibile applicazione parziale. Non viene
eseguito rollback automatico.

## Test permanenti

La cartella `tests/` contiene fake spreadsheet, worksheet e sessioni
thread-safe. Sono coperti validate-only pre/post, dry-run, errori operativi,
gestionale read-only, bozza unica, rilascio/spedizione/scadenza, ruoli,
contributor, idempotenza, 20 thread concorrenti e tutte le nuove regole di
validazione.

Risultato: 31 test superati; compilazione e parsing AST completati per tutti
i 43 file Python.

## Confini invariati

Nessun collegamento ai flussi Telegram, nessuna migrazione reale e nessuna
modifica a `main.py`, `modules/`, `keyboards/`, runtime Google, cache o
requirements. I feature flag restano disattivati per default.

## Limiti residui

- sicurezza limitata a una singola istanza/processo;
- nessuna transazione distribuita multi-worksheet;
- casi ambigui da risolvere operativamente;
- test su Google reale intenzionalmente esclusi;
- rollback dai backup manuale.
