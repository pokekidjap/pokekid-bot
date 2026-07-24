# Migrazione Shipping v2.1

## Ambito

Lo script `scripts/migrate_shipping_v2.py` legge `ORDINI` dal file
“Gestione vendite gruppo” e modifica esclusivamente il “DATABASE BOT”.
Non deve essere importato o eseguito dallo startup.

Il gestionale è sempre read-only: nessuna colonna, cella, riga, nota o
intestazione viene aggiunta o modificata.

## Prerequisiti

- `SPREADSHEET_ID`: file sorgente del gestionale;
- `WORKSHEET_NAME`: normalmente `ORDINI`;
- `BOT_DB_SHEET_ID`: file destinazione;
- credenziali Google con accesso ai due file;
- source e destination obbligatoriamente differenti;
- una sola istanza Railway attiva.

Le intestazioni A:U di `SPEDIZIONI` devono coincidere con lo schema legacy.
Qualunque intestazione o dato incompatibile in V:X blocca la migrazione.

## Modalità

Dry-run predefinito:

```powershell
python scripts/migrate_shipping_v2.py
```

Validazione senza modifiche:

```powershell
python scripts/migrate_shipping_v2.py --validate-only
```

Prima della migrazione, `--validate-only` considera normale che
`ORDINI_ARTICOLI` e `SPEDIZIONI_ARTICOLI` non esistano: valida che possano
essere create, controlla `SPEDIZIONI`, simula la sincronizzazione e restituisce
`safe_to_apply=true` se il piano è coerente. Dopo la migrazione valida invece
intestazioni e dati realmente installati.

Il report distingue:

- `schema_attualmente_installato`;
- `piano_migrazione_valido`;
- `schema_finale_previsto`.

Applicazione reale:

```powershell
python scripts/migrate_shipping_v2.py `
  --apply `
  --confirm-production `
  --backup-dir .\backup_shipping_v2_20260723
```

`--apply` senza `--confirm-production` viene rifiutato. Per fogli di prova si
possono specificare `--source-spreadsheet-id`, `--source-sheet`,
`--bot-db-spreadsheet-id` e i nomi delle worksheet del DATABASE BOT.

Lo script stampa sempre l'ID del DATABASE BOT destinazione e l'ID/nome della
sorgente read-only prima di procedere.

L'integrazione Telegram v2.2 non esegue mai questo script e non crea
automaticamente le schede. Prima di attivare i flag in un ambiente già
migrato eseguire almeno `--validate-only` con un operatore autorizzato.

La fase v2.3 non richiede una nuova migrazione: riusa esclusivamente
`ORDINI_ARTICOLI` A:W, `SPEDIZIONI` A:X e `SPEDIZIONI_ARTICOLI` A:U.
L'unione tra utenti, il raggruppamento admin e l'annullamento non aggiungono
worksheet o colonne.

## Backup obbligatori prima della prima scrittura

In modalità apply viene creata una nuova directory locale; se esiste già,
l'operazione si interrompe. Contiene:

- `worksheet_list.json` e `worksheet_list.csv`;
- `SPEDIZIONI_A_X.json` e `SPEDIZIONI_A_X.csv`;
- `ORDINI_ARTICOLI.json` e `ORDINI_ARTICOLI.csv`;
- `SPEDIZIONI_ARTICOLI.json` e `SPEDIZIONI_ARTICOLI.csv`;
- `pre_migration_report.json`;
- `pre_migration_report.txt`.

Le schede mancanti sono rappresentate da snapshot vuoti. Il backup viene
completato prima di creare schede o intestazioni.

Le directory `shipping_v2_backups/` e i report
`migration_shipping_v2_report*.json|txt` sono esclusi da Git e dallo ZIP di
consegna perché possono contenere dati personali. I backup locali esistenti
non devono essere eliminati automaticamente.

## Ordine delle operazioni

1. valida ID, nomi e separazione source/destination;
2. ispeziona la lista delle worksheet;
3. legge ORDINI, PROFILI, SPEDIZIONI e le eventuali schede v2;
4. prepara piano e report pre-migrazione;
5. salva i backup locali JSON/CSV;
6. crea/valida `ORDINI_ARTICOLI`;
7. crea/valida `SPEDIZIONI_ARTICOLI`;
8. aggiunge soltanto V:X a `SPEDIZIONI`;
9. sincronizza il registro esterno;
10. rilegge e valida lo schema finale;
11. confronta il digest logico di ORDINI prima/dopo.

La seconda esecuzione non duplica schede, intestazioni o ID.

## Conflitti bloccanti

- source e DATABASE BOT uguali;
- ORDINI selezionato come foglio modificabile;
- nomi destinazione duplicati;
- A:U di SPEDIZIONI incompatibile;
- intestazioni/dati preesistenti incompatibili in V:X;
- schede v2 con intestazioni o dati incompatibili;
- ID articolo duplicati o record attivi senza ID;
- riconciliazione non sicura.

## Rollback

Non esiste un rollback automatico, per evitare sovrascritture non
controllate. Prima di disattivare il flusso Telegram v2:

1. impedire nuove richieste v2;
2. attendere la scadenza delle bozze `PRENOTATO` oppure rilasciarle con la
   procedura esplicita;
3. eseguire in sola lettura:

```powershell
python scripts/prepare_shipping_v2_deactivation.py
```

4. verificare `safe_to_disable=true`;
5. soltanto allora impostare entrambi i flag a `false` o tornare al legacy;
6. conservare report e backup e confrontare manualmente il DATABASE BOT se
   serve un ripristino dati.

Per rilasciare esclusivamente le bozze `PRENOTATO`, senza toccare
`CONFERMATO` o `SPEDITO`, l'operatore può eseguire:

```powershell
python scripts/prepare_shipping_v2_deactivation.py `
  --release-prebooked `
  --confirm-production `
  --report-dir .\shipping_v2_deactivation_reports
```

La modalità di rilascio è idempotente, produce report JSON/testo e non viene
mai eseguita automaticamente. Lo script non modifica variabili Railway.

Le righe legacy di `SPEDIZIONI` non vengono riscritte dalla migrazione.

## Attivazione dell'integrazione Telegram

Dopo una migrazione verificata:

1. mantenere una sola replica Railway;
2. eseguire `--validate-only`;
3. attivare insieme `SHIPPING_V2_ENABLED=true` e
   `SHIPPING_V2_SINGLE_INSTANCE_ACK=true`;
4. riavviare il bot;
5. verificare selezione, bozza, ricevuta e completamento admin su dati di
   prova;
6. verificare su dati di prova l'unione tramite username, il tracking a
   titolare e contributor e l'annullamento amministrativo.

Con un solo flag attivo il motore resta legacy. Con entrambi attivi gli
errori di schema sono bloccanti e non causano fallback verso il flusso
legacy.

## Errori operativi

Quando un'operazione fallisce, lo script interrompe immediatamente le
scritture successive e produce comunque i report quando il filesystem locale
è disponibile. Il report include:

- tipo e messaggio dell'eccezione;
- `write_state=NO_WRITES` oppure `POSSIBLY_PARTIAL`;
- `could_be_partially_applied`;
- percorsi dei backup già completati;
- exit code diverso da zero.

Non viene tentato alcun rollback automatico.
