# Spedizioni v2.3 — Unione semplificata e gestione finale

## Risultato

La fase v2.3 completa Shipping v2 con un percorso separato per aggiungere i
propri articoli alla richiesta di un altro utente. Il titolare e il
contribuente si accordano privatamente: il bot non implementa inviti,
consenso, accettazione o rifiuto.

Il gestionale “Gestione vendite gruppo”, incluse `ORDINI` e `GRADING`, resta
in sola lettura. Non sono state aggiunte worksheet o colonne. Gli schemi
restano:

- `ORDINI_ARTICOLI` A:W;
- `SPEDIZIONI` A:X;
- `SPEDIZIONI_ARTICOLI` A:U.

Non sono state eseguite migrazioni, connessioni Google reali, modifiche
Railway o deploy.

## Checkpoint

Prima delle modifiche è stato creato
`POKEKID_BOT_checkpoint_pre_shipping_v2_3.zip`, copia byte-per-byte di
`POKEKID_BOT_shipping_v2_2_1_operational_hardening.zip`.

- SHA-256:
  `1D71BB07B00F195B6F34E5C7BB6BD1EF0421D60D0C8357CC92635AB83292D80A`;
- 72 voci ZIP;
- 714.930 byte non compressi;
- CRC verificato.

## File modificati e nuovi

Nuovi:

- `modules/shipping_v2_join.py`;
- `services/shipping_v2_join.py`;
- `services/shipping_v2_join_session.py`;
- `tests/test_shipping_v2_simple_join.py`;
- `SHIPPING_V2_3_SIMPLE_JOIN_REPORT.md`.

Modificati:

- `main.py`;
- `keyboards/admin.py`, `keyboards/orders.py`;
- `modules/admin.py`, `modules/orders.py`, `modules/shipping_v2.py`;
- `services/shipping_v2.py`;
- `tests/test_shipping_v2_callbacks.py`;
- `AGENTS.md`, `ARCHITECTURE.md`, `CHANGELOG.md`,
  `MIGRATION_SHIPPING_V2.md`, `README.md`, `ROADMAP.md`,
  `SHIPPING_V2_FOUNDATION_REPORT.md`,
  `SHIPPING_V2_2_TELEGRAM_INTEGRATION_REPORT.md` e
  `SHIPPING_V2_2_1_OPERATIONAL_HARDENING_REPORT.md`.

Nessun file del checkpoint è stato rimosso.

## Flusso tramite username

Il pulsante `📦 Unisci a una spedizione` compare nel menu ordini soltanto
quando entrambi i flag Shipping v2 sono attivi.

1. il contribuente invia lo username del titolare, con o senza `@`;
2. `get_profile_by_username()` normalizza il confronto in minuscolo e
   restituisce il Telegram ID;
3. il servizio seleziona automaticamente l'unica richiesta V2
   `IN_ATTESA`, priva di tracking e coerente;
4. zero richieste produce un messaggio di indisponibilità; più richieste
   bloccano il flusso e rimandano allo staff; il self-join viene rifiutato;
5. non viene richiesto il profilo di spedizione completo del contribuente.

La conversazione usa lo stato `SHIPPING_V2_JOIN_USERNAME`. `/cancel` e
`join_v2_cancel` puliscono soltanto la sessione e non eseguono scritture.

## Assenza di consenso

Non esistono inviti, richieste pendenti, codici, accettazione, rifiuto o
pulsanti di consenso. Il bot assume che gli utenti si siano già accordati
privatamente. Non mostra indirizzo, email, telefono, ricevuta o articoli del
titolare al contribuente.

## Selezione dei soli articoli propri

La selezione usa chiavi `shipping_v2_join_*`, ID `ART-UUIDv4`, pagine da 8 e
callback entro 64 byte. Sono mostrati soltanto record:

- attivi;
- con sincronizzazione `OK` o `MODIFICATO`;
- in stato sorgente `IN MAGAZZINO`;
- appartenenti al Telegram ID del contribuente;
- non occupati da `PRENOTATO`, `CONFERMATO` o `SPEDITO`.

Toggle e cambio pagina modificano solo `context.user_data`. Il refresh
valida lo schema, sincronizza il registro, rilegge la richiesta e conserva
soltanto le selezioni ancora valide.

## Finalizzazione diretta come CONTRIBUENTE

`services/shipping_v2_join.py` acquisisce:

```text
ORDINI_ARTICOLI
    -> SPEDIZIONI_ARTICOLI
        -> SPEDIZIONI
```

Per ogni articolo aggiunge una riga con:

- nuova `UUID_DETTAGLIO`;
- stessa `UUID_BOZZA`, `UUID_SPEDIZIONE` e `ID_SPEDIZIONE` del titolare;
- proprietario uguale al contribuente;
- `RUOLO=CONTRIBUENTE`;
- snapshot dal registro rivalidato;
- `STATO_PRENOTAZIONE=CONFERMATO`;
- timestamp timezone-aware;
- `VERSIONE=V1`.

Non viene creata una nuova riga `SPEDIZIONI`. Destinatario, profilo,
ricevuta, corriere e costo non vengono modificati. `PRODOTTI` viene
ricostruito dagli snapshot di tutti gli articoli collegati e resta soggetto
al limite di 45.000 caratteri.

## Idempotenza e recupero parziale

La key `JOIN-V2-*` include un digest canonico degli ID selezionati. La stessa
key è valida soltanto per lo stesso contribuente, la stessa richiesta e lo
stesso insieme di articoli.

Il retry:

- recupera righe contributor già presenti;
- aggiunge soltanto eventuali righe mancanti;
- non duplica `ID_ARTICOLO` o `UUID_DETTAGLIO`;
- ricostruisce `PRODOTTI` se l'append era riuscito ma l'aggiornamento della
  riga principale no;
- rilegge e verifica entrambi i lati prima del successo.

Sono gestiti timeout dopo append, timeout dopo update, stati parziali,
conferme simultanee e cambi concorrenti di tracking o stato. I lock restano
locali alla singola istanza.

## Più aggiunte

Lo stesso contribuente può eseguire operazioni successive con nuove key.
Contributor differenti possono aggiungere i propri articoli alla stessa
richiesta. Gli articoli già collegati non vengono più mostrati; il controllo
è sul singolo articolo, non sull'identità del partecipante.

## Notifiche

Dopo la coerenza completa:

- il contribuente vede la conferma e il riepilogo dei propri articoli;
- il titolare riceve username del contribuente, quantità e riepilogo;
- gli admin ricevono richiesta, titolare, contribuente e quantità, senza
  pulsanti.

L'operazione è già conclusa prima degli invii Telegram. Il fallimento di una
notifica viene registrato e non annulla né altera l'unione. Il `LOG` riceve
`SHIPPING_V2_CONTRIBUTOR_ADDED` con i soli identificativi necessari.

## Dettaglio admin

Per le richieste V2 il dettaglio viene costruito dai dati correnti di
`SPEDIZIONI_ARTICOLI` e raggruppa:

- titolare e contributor;
- username e Telegram ID;
- ruolo;
- numero di articoli e quantità complessiva;
- riepilogo compatto degli snapshot.

Destinatario, indirizzo, contatti, corriere, costo, ricevuta, stato e
tracking continuano a provenire dalla riga `SPEDIZIONI`. Il legacy usa
ancora il proprio `PRODOTTI`.

## Tracking

`complete_v2_shipping_request()` restituisce i partecipanti deduplicati
letti dagli articoli collegati. Dopo la coerenza `SPEDITO`, il modulo admin
invia il normale `MSG_SPEDIZIONE` a ogni Telegram ID unico. Il legacy
continua a notificare soltanto il titolare. Gli errori di invio non
annullano il completamento.

## Annullamento amministrativo

Il pulsante `❌ Annulla richiesta` è visibile soltanto per V2 `IN_ATTESA`
senza tracking ed è seguito da una conferma esplicita.

`cancel_v2_shipping_request_by_admin()` usa l'ordine
`SPEDIZIONI_ARTICOLI -> SPEDIZIONI`:

1. blocca legacy, tracking, richieste o articoli `SPEDITO`;
2. porta tutte le righe `PRENOTATO/CONFERMATO` a `RILASCIATO`;
3. valorizza timestamp e motivo `ANNULLATA_ADMIN:<id>`;
4. imposta la richiesta `ANNULLATO`, aggiornamento e admin;
5. riconcilia entrambi i possibili stati parziali;
6. verifica il risultato e notifica tutti gli utenti coinvolti.

Corriere, costo, ricevuta e dati storici restano invariati. Il doppio clic è
idempotente.

## Funzioni volutamente escluse

- inviti e consenso;
- scelta tramite ID richiesta;
- gestione amici o rubrica;
- chat e messaggi interni;
- contestazioni o segnalazioni;
- divisione dei costi e pagamenti;
- modifica di destinatario o corriere da parte del contributor;
- rimozione autonoma di articoli dopo l'unione;
- annullamento da parte degli utenti;
- schermate privacy;
- storico dedicato;
- `SPEDIZIONI_PARTECIPANTI` o altre worksheet.

## Test

Comando:

```bash
python -m unittest discover -s tests -v
```

Risultato: 163 test superati.

- 120 test v2.2.1 mantenuti;
- 43 test v2.3 per username, target, filtri, paginazione, sessione,
  callback, unione, payload, timeout, recupero parziale, concorrenza,
  aggiunte successive, notifiche, raggruppamento, tracking e annullamento;
- nessun accesso Google reale;
- nessuna nuova dipendenza.

## Compilazione e audit

- 56 file Python compilati senza errori;
- parsing AST riuscito per tutti i 56 file;
- 44 moduli applicativi importati con dipendenze isolate, senza errori;
- 33 handler principali registrati: 23 `CallbackQueryHandler` e 7
  `ConversationHandler`;
- callback statiche e campioni dinamici tutti raggiungibili; dimensione
  massima verificata 56 byte;
- handler `join_v2_*` e `admin_shipping_cancel_*` registrati prima del
  router generico, che resta ultimo;
- i quattro ingressi admin nuovi o modificati eseguono `check_admin()`
  una volta e non invocano direttamente `query.answer()`;
- `requirements.txt`, runtime Google, cache, registro ordini, prenotazioni,
  schema v2 e lettura gestionale sono byte-identici al checkpoint;
- scansione di 77 file: nessun `.env`, file credenziali, token, chiave
  privata o segreto incorporato;
- confronto col checkpoint: 5 file nuovi, 17 modificati, 0 rimossi e 55
  invariati.

## Rischi residui

- Google Sheets non offre una transazione distribuita tra worksheet;
- i lock proteggono soltanto thread della singola istanza e non rendono
  sicure più repliche Railway;
- modifiche manuali concorrenti nel DATABASE BOT possono produrre conflitti
  da risolvere operativamente;
- le notifiche Telegram sono best-effort e possono richiedere gestione
  manuale se un utente ha bloccato il bot;
- l'accordo e gli eventuali problemi tra utenti restano esterni al bot;
- servono ancora test manuali su Telegram e fogli Google di prova prima
  dell'attivazione in produzione.
