# Spedizioni v2.2.1 — Hardening operativo, retry e paginazione

## Risultato

La v2.2.1 rafforza esclusivamente il flusso Shipping v2 del singolo
titolare. Il legacy resta selezionato quando uno dei due feature flag è
falso. Contributor e unione non facevano parte della v2.2.1 e vengono
aggiunti soltanto dalla successiva v2.3 tramite moduli separati. Inviti,
consensi, nuove worksheet, migrazioni reali e deploy restano esclusi.

Il gestionale “Gestione vendite gruppo”, incluse `ORDINI` e `GRADING`, resta
in sola lettura. Gli schemi approvati restano:

- `ORDINI_ARTICOLI` A:W;
- `SPEDIZIONI` A:X;
- `SPEDIZIONI_ARTICOLI` A:U.

## Checkpoint

Prima delle modifiche è stato creato
`POKEKID_BOT_checkpoint_pre_shipping_v2_2_1.zip`, copia byte-per-byte di
`POKEKID_BOT_shipping_v2_2_telegram_integration.zip`.

- SHA-256 di entrambi:
  `4D5D6EE764CDB20143D16B152449F0C5BF6443345BDB58B8A62314E588467EF4`.

## Paginazione

La sessione dedicata contiene `shipping_v2_page`. La pagina usa 8 elementi e
la callback `shipping_v2_page:<numero>`.

- selezione e conteggi restano globali;
- Precedente e Successiva appaiono soltanto quando disponibili;
- la pagina è ricondotta automaticamente all'intervallo valido;
- un refresh conserva soltanto gli ID ancora disponibili;
- il cambio pagina non crea prenotazioni;
- Continua usa tutti gli ID selezionati;
- callback articolo e pagina restano entro 64 byte.

L'handler pagina è registrato prima del router generico.

## Budget testi

`services/shipping_v2_text.py` centralizza la composizione degli elenchi:

- massimo 3.800 caratteri prima del footer;
- righe HTML aggiunte soltanto per intero;
- omissione esplicita `… e altri N articoli`;
- totale articoli e unità sempre visibili;
- nessun tag HTML spezzato.

Bozza attiva, scelta corriere, riepilogo, richiesta confermata, notifica
admin e messaggio conclusivo usano il formattatore. I dati completi restano
in `SPEDIZIONI` e `SPEDIZIONI_ARTICOLI`.

`PRODOTTI` viene controllato prima delle scritture di finalizzazione. Oltre
45.000 caratteri viene sollevato un conflitto permanente e non vengono
create né confermate righe di spedizione.

## Rivalidazione

`validate_v2_draft_against_registry()`:

1. valida lo schema;
2. sincronizza `ORDINI_ARTICOLI`;
3. acquisisce `ORDINI_ARTICOLI -> SPEDIZIONI_ARTICOLI`;
4. rilegge la bozza;
5. verifica titolare, stato, scadenza, attività, associazione, non
   ambiguità, stato `IN MAGAZZINO`, proprietà e coerenza degli snapshot.

Viene chiamata:

- prima del riepilogo con PayPal e destinazione;
- quando l'utente preme “Invia ricevuta pagamento”;
- immediatamente prima della finalizzazione.

Una bozza `PRENOTATO` invalida viene rilasciata idempotentemente e l'utente
torna alla selezione con un messaggio specifico. Una bozza `CONFERMATO` non
viene rilasciata: la richiesta cross-worksheet viene recuperata e mostrata.

## Errori permanenti e transitori

- guasto Google/Telegram transitorio: bozza, key e possibilità di retry
  restano disponibili;
- articolo non più valido: rilascio `PRENOTATO` e ritorno alla selezione;
- richiesta già confermata: recupero della richiesta;
- conflitto irreversibile: messaggio prudente, nessun dettaglio tecnico,
  registrazione per intervento e nessun invito a reinviare all'infinito.

## Retry e allegato autorevole

Il primo `PAYMENT_FILE_ID` e il primo tipo allegato salvati sono autorevoli.
Un retry con file ID o tipo differente:

- non sovrascrive l'allegato;
- non crea una seconda riga `SPEDIZIONI`;
- riconcilia gli stati articolo;
- registra `SHIPPING_V2_RETRY_ALLEGATO_MANTENUTO` nel `LOG`.

Restano conflitti bloccanti le differenze di titolare, bozza, articoli,
corriere, costo, profilo, prodotti e versione schema.

Il risultato tecnico, mai mostrato all'utente, distingue:

- `CREATED_NOW`;
- `RECONCILED_NOW`;
- `ALREADY_COHERENT`.

## Notifiche admin recuperabili

La notifica non dipende più da `_V2_ALREADY_COHERENT`. Dopo ogni
finalizzazione o recupero coerente vengono verificati i marker:

- `AZIONE=SHIPPING_V2_ADMIN_NOTIFIED`;
- `DETTAGLI=shipping_id=<ID>|admin_id=<TELEGRAM_ID>`.

Ogni admin mancante viene gestito indipendentemente. Il marker viene scritto
soltanto dopo `send_message()` riuscito. Un `asyncio.Lock` per ID spedizione
evita duplicazioni tra retry concorrenti nella stessa istanza.

La semantica è at-least-once: un crash tra invio e marker può produrre una
notifica duplicata, ma non una perdita silenziosa. UUID, idempotency key e
dati tecnici non vengono inviati agli admin.

## Disattivazione

Modalità di sola lettura:

```powershell
python scripts/prepare_shipping_v2_deactivation.py
```

Riporta `PRENOTATO` attive/scadute, `CONFERMATO` e `SPEDITO`.
`safe_to_disable=true` compare soltanto senza `PRENOTATO` attive.

Rilascio esplicito:

```powershell
python scripts/prepare_shipping_v2_deactivation.py `
  --release-prebooked `
  --confirm-production `
  --report-dir .\shipping_v2_deactivation_reports
```

Rilascia esclusivamente `PRENOTATO`, è idempotente e produce report JSON e
testuale. Non modifica `CONFERMATO`, `SPEDITO` o variabili Railway.

Procedura:

1. impedire nuove richieste;
2. attendere la scadenza o rilasciare `PRENOTATO`;
3. verificare `safe_to_disable=true`;
4. soltanto allora impostare entrambi i flag a `false` o tornare al legacy.

## Test

Comando obbligatorio:

```bash
python -m unittest discover -s tests -v
```

Risultato: 120 test superati. Gli 85 test v2.2 sono mantenuti; i nuovi test
coprono 0/1/8/9/50/150 articoli, selezione e refresh, budget e omissione,
rivalidazione, rilascio, bozza confermata, limite `PRODOTTI`, timeout,
allegato differente, tre stati tecnici, marker admin, invii parziali,
concorrenza e disattivazione senza Google reale.

## Addendum v2.3

La selezione contributor riusa pagine da 8 e lo stesso budget testuale, ma
mantiene chiavi `shipping_v2_join_*` separate. L'idempotency key incorpora un
digest della selezione; append parziali e `PRODOTTI` non aggiornato vengono
riconciliati sotto l'ordine globale dei lock.

La v2.3 mantiene tutti i 120 test e aggiunge 43 test permanenti, per un
totale di 163 test superati senza Google reale.

## Rischi residui

- i lock restano locali alla singola istanza;
- Google Sheets non offre transazioni distribuite multi-worksheet;
- un crash tra notifica e marker può duplicare la notifica;
- modifiche manuali concorrenti nel DATABASE BOT possono richiedere
  intervento;
- la procedura richiede ancora collaudo manuale su Telegram e fogli di prova;
- nessuna migrazione reale, connessione Google reale o modifica Railway è
  stata eseguita.
- consenso e gestione delle contestazioni restano deliberatamente esterni
  al bot nel flusso semplificato v2.3.
