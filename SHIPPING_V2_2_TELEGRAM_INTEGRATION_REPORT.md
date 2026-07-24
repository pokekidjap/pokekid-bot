# Spedizioni v2.2 — Integrazione Telegram e finalizzazione singolo utente

## Risultato

Shipping v2 è collegato al bot Telegram esclusivamente per spedizioni
composte dagli articoli del singolo titolare. L'attivazione resta
esplicitamente opt-in e richiede contemporaneamente:

- `SHIPPING_V2_ENABLED=true`;
- `SHIPPING_V2_SINGLE_INSTANCE_ACK=true`.

La v2.2 originaria non implementava contributor, unione tra utenti o
cancellazione amministrativa. La fase v2.3 aggiunge queste sole capacità
tramite un percorso separato, mantenendo invariata la bozza normale del
titolare. Inviti, consensi, PostgreSQL e supporto a più repliche Railway
restano esclusi.

Non è stata eseguita alcuna migrazione e i test non hanno aperto connessioni
Google reali.

## Checkpoint

Prima delle modifiche è stato creato
`POKEKID_BOT_checkpoint_pre_shipping_v2_2.zip` come copia byte-per-byte di
`POKEKID_BOT_shipping_v2_1_1_hardening.zip`.

- SHA-256 di entrambi:
  `13EE8115CC4E4103A108092666B0C388DE946966A5B08FB334ECBF83ED57323A`;
- 61 voci ZIP;
- 471.359 byte non compressi.

## Selezione del motore

`services/shipping_engine.py` è l'unico punto di selezione:

- un flag falso o entrambi falsi → `LEGACY`;
- entrambi veri → `V2`.

Con `LEGACY`:

- restano attivi i callback e i servizi preesistenti;
- `orders_available` continua a usare il flusso basato sulle righe;
- non viene validata né richiesta alcuna scheda v2;
- `create_shipping_request()` e `complete_shipping_request()` non sono stati
  modificati.

Con `V2`:

- il flusso di richiesta non esegue fallback legacy;
- errori di schema o servizio producono un messaggio prudente;
- il dettaglio tecnico viene scritto nel logger e, quando disponibile, in
  `LOG`.

## Flusso Telegram v2

### Apertura e selezione

`orders_available`:

1. valida lo schema v2;
2. esegue `synchronize_order_registry()`;
3. rilascia le prenotazioni scadute;
4. controlla una bozza attiva;
5. in assenza di bozza mostra gli articoli eleggibili del titolare.

La selezione usa `ID_ARTICOLO`. Il callback è
`order_v2_toggle:ART-UUID` e resta entro 64 byte. I toggle modificano solo
le chiavi `shipping_v2_*` in `context.user_data`: non creano righe in
`SPEDIZIONI_ARTICOLI`.

### Momento della prenotazione

La prenotazione nasce esattamente al callback
`shipping_v2_continue`, dopo:

- verifica dello smistamento;
- rilettura e completezza del profilo;
- lettura dei corrieri attivi;
- nuova validazione schema e sincronizzazione;
- verifica di appartenenza e prenotabilità di tutti gli ID.

La chiamata `reserve_items()` usa key stabile per il tentativo,
`authorized_contributor_item_ids` vuoto e solo ruolo `TITOLARE`. Il gruppo è
tutto-o-niente.

### Sessione

Le chiavi sono separate dal legacy:

- `shipping_v2_available_items`;
- `shipping_v2_selected_item_ids`;
- `shipping_v2_draft_uuid`;
- `shipping_v2_idempotency_key`;
- `shipping_v2_selected_carrier`;
- `shipping_v2_profile`;
- `shipping_v2_methods`;
- `shipping_v2_waiting_receipt`.

Un cambio di selezione elimina la key locale non usata. La key viene generata
solo al tentativo Continua.

## Ripresa dopo riavvio

L'apertura interroga sempre `get_active_draft_for_user()`.

- `PRENOTATO` non scaduto: mostra articoli, scadenza, Riprendi, Annulla bozza
  e Menu principale.
- Riprendi ricostruisce contesto, articoli, profilo e corrieri dal DATABASE
  BOT.
- `CONFERMATO`: mostra ID e articoli, blocca una nuova richiesta e consente
  lo storico; non offre annullamento.
- `PRENOTATO` scaduto: viene rilasciato prima di una nuova selezione.

## Annullamento

Annulla, Cambia articoli, Annulla bozza, annullamento dall'attesa ricevuta e
`/cancel` rilasciano una bozza `PRENOTATO`.

- Il rilascio è idempotente.
- `RILASCIATO` e scaduto sono esiti conclusi.
- `CONFERMATO` e `SPEDITO` non sono rilasciabili dall'utente.
- `context.user_data` viene pulito soltanto dopo il successo.
- Un errore di scrittura mantiene contesto e pulsante di retry e non comunica
  un falso annullamento.

## Finalizzazione

`services/shipping_v2.py` espone:

- `create_or_get_v2_shipping_request()`;
- `get_v2_shipping_request_by_draft()`;
- `validate_v2_draft_for_holder()`;
- `complete_v2_shipping_request()`;
- `complete_shipping_request_by_version()`.

La ricevuta forza il profilo, rilegge i corrieri, sincronizza il registro e
rilegge la bozza dal DATABASE BOT. Il coordinatore verifica titolare, stato,
scadenza, ruoli, key e registro.

La nuova riga `SPEDIZIONI` contiene esattamente A:X:

- A:U compatibile con le letture legacy;
- V `UUID_SPEDIZIONE`;
- W `IDEMPOTENCY_KEY`;
- X `VERSIONE_SCHEMA=V2`;
- stato iniziale `IN_ATTESA`.

`PRODOTTI` deriva esclusivamente dagli snapshot
`OGGETTO_SNAPSHOT`, `QUANTITA_SNAPSHOT` e
`RIGA_ORDINE_SNAPSHOT`.

Il progressivo `SP-AAAAMMGG-NNN` viene calcolato durante il lock di
`SPEDIZIONI`; la v2 non chiama `generate_shipping_id()`.

## Ordine dei lock

L'ordine globale documentato e rispettato è:

```text
ORDINI_ARTICOLI
    -> SPEDIZIONI_ARTICOLI
        -> SPEDIZIONI
```

Le operazioni che non necessitano del registro usano il suffisso coerente
`SPEDIZIONI_ARTICOLI -> SPEDIZIONI`. Non esiste alcun percorso inverso.

## Idempotenza e recupero parziale

La finalizzazione:

- cerca key e UUID prima di appendere;
- rifiuta duplicati globali;
- rifiuta la stessa key con titolare, bozza o payload differenti;
- dopo timeout post-append rilegge e recupera la riga già creata;
- se la riga principale esiste e gli articoli sono ancora `PRENOTATO`,
  completa la conferma;
- riconcilia stati misti `PRENOTATO/CONFERMATO`;
- serializza invii simultanei nella singola istanza;
- rilegge entrambi i lati prima di restituire il successo;
- non notifica gli admin prima della coerenza;
- dalla v2.2.1 recupera gli admin mancanti tramite marker nel `LOG`.

Una scadenza impedisce la creazione di `SPEDIZIONI`. Un errore transitorio
mantiene bozza, contesto e idempotency key per il retry.

## Completamento admin

Il dispatcher legge `VERSIONE_SCHEMA`:

- vuoto/legacy → `complete_shipping_request()`;
- `V2` → `complete_v2_shipping_request()`.

Il completamento v2:

1. aggiorna stato, tracking, data e admin in `SPEDIZIONI`;
2. imposta gli articoli collegati a `SPEDITO`;
3. rilegge e verifica entrambi i lati;
4. accetta il retry con lo stesso tracking;
5. rifiuta un tracking differente;
6. recupera sia “main spedito/articoli confermati” sia il caso inverso.

La notifica al titolare avviene dopo il ritorno del servizio coerente.
Dalla v2.3 il servizio restituisce i proprietari unici letti da
`SPEDIZIONI_ARTICOLI` e il modulo admin invia il tracking anche ai
contributor, senza duplicare lo stesso Telegram ID.

## Callback e handler aggiunti

- `order_v2_toggle:ART-UUID`;
- `shipping_v2_continue`;
- `shipping_v2_carrier:<indice>`;
- `shipping_v2_resume`;
- `shipping_v2_cancel`;
- `shipping_v2_cancel_draft`;
- `shipping_v2_change_items`;
- fallback `/cancel` nella conversazione ricevuta.

`orders_available`, `orders_refresh`, `shipping_payment` e
`shipping_receipt_cancel` restano condivisi, ma scelgono il motore in modo
esplicito. Tutti gli handler specifici sono prima del router generico.

La v2.2.1 aggiunge `shipping_v2_page:<numero>`, intercettato prima del
router generico. La pagina è locale, usa 8 elementi e non crea
prenotazioni.

La v2.3 aggiunge una `ConversationHandler` separata e le callback:

- `shipping_v2_join`;
- `join_v2_toggle:ART-UUID`;
- `join_v2_page:<numero>`;
- `join_v2_refresh`;
- `join_v2_confirm`;
- `join_v2_cancel`;
- `admin_shipping_cancel:<ID>`;
- `admin_shipping_cancel_confirm:<ID>`;
- `admin_shipping_cancel_back:<ID>`.

Tutte precedono il router generico. `join_v2_cancel` è registrata una sola
volta come entry point riutilizzabile della conversazione.

## Addendum funzionale v2.3

Lo username del titolare viene risolto tramite `PROFILI`; l'unica richiesta
V2 `IN_ATTESA` coerente e senza tracking viene selezionata automaticamente.
Il contribuente non deve avere un profilo di spedizione completo e vede
soltanto i propri articoli disponibili.

La conferma aggiunge righe direttamente `CONFERMATO` con ruolo
`CONTRIBUENTE`, stessa bozza e stessa richiesta. Non crea una nuova riga
`SPEDIZIONI` e non modifica dati di consegna, corriere, costo o ricevuta.
Il dettaglio admin viene raggruppato per proprietario e l'annullamento
amministrativo rilascia l'intera richiesta V2.

## Addendum operativo v2.2.1

- Gli elenchi sono composti sotto un budget di 3.800 caratteri prima del
  footer, senza troncare tag HTML.
- La bozza viene sincronizzata e rivalidata prima del riepilogo PayPal,
  all'apertura dell'invio ricevuta e prima della finalizzazione.
- Una bozza `PRENOTATO` invalida viene rilasciata; una già `CONFERMATO`
  viene recuperata.
- Il primo allegato resta autorevole sui retry. Gli esiti tecnici sono
  `CREATED_NOW`, `RECONCILED_NOW` e `ALREADY_COHERENT`.
- Le notifiche usano `SHIPPING_V2_ADMIN_NOTIFIED` nel `LOG` e semantica
  at-least-once; `_V2_ALREADY_COHERENT` non rappresenta più una prova di
  consegna.
- `scripts/prepare_shipping_v2_deactivation.py` verifica le bozze residue in
  sola lettura e richiede doppia conferma per rilasciare `PRENOTATO`.

## Test

Comando:

```bash
python -m unittest discover -s tests -v
```

Risultato v2.2 originario: 85 test superati. La v2.2.1 mantiene tutti gli 85
test e porta la suite a 120 test superati. La v2.3 mantiene i 120 test e
porta la suite a 163 test superati.

Copertura principale:

- 31 test v2.1.1 mantenuti;
- combinazioni dei feature flag e assenza di fallback;
- filtri del registro, ID stabile, callback e sessione;
- prenotazione solo al Continua e conflitti tutto-o-niente;
- bozza attiva, confermata, scaduta, rilascio e ripresa;
- riga A:X, compatibilità A:U e snapshot;
- progressivi concorrenti;
- key/UUID/payload/titolare/bozza conflittuali;
- timeout post-append, stato parziale e doppio invio;
- notifiche dopo coerenza;
- completamento admin, retry e tracking conflittuale;
- audit dei callback e dell'ordine handler.

La compilazione e il parsing AST comprendono tutti i 49 file Python.

## Collaudo manuale consigliato

Su un bot e fogli di prova già migrati:

1. verificare con flag falsi l'intero flusso legacy;
2. attivare entrambi i flag su una sola replica;
3. selezionare/deselezionare articoli e controllare il conteggio;
4. creare una bozza, riavviare il bot e provarne la ripresa;
5. provare Annulla, Cambia articoli e `/cancel` in attesa ricevuta;
6. lasciare scadere una bozza e verificare una nuova selezione;
7. inviare due volte la stessa ricevuta e controllare una sola riga;
8. completare dall'admin e ripetere lo stesso tracking;
9. provare un tracking differente e verificare il rifiuto;
10. controllare storico, ricevuta admin, log e notifiche;
11. provare l'unione con username valido, inesistente, proprio e con più
    richieste target;
12. verificare dettaglio raggruppato, tracking multipartecipante e
    annullamento admin.

## Gestionale read-only

`Gestione vendite gruppo`, incluse `ORDINI` e `GRADING`, non viene scritto.
Il registro usa sulla sorgente solo `get_all_values()`. I test fake
interrompono esplicitamente qualunque tentativo di scrittura sul gestionale.

`requirements.txt`, `services/google_runtime.py` e `services/cache.py` sono
rimasti invariati.

## Rischi residui

- I lock proteggono una sola istanza/processo; più repliche Railway non sono
  supportate.
- Google Sheets non offre una transazione distribuita: la sicurezza deriva
  da lock locali, idempotenza e riconciliazione.
- Modifiche manuali concorrenti nel DATABASE BOT possono creare conflitti
  bloccanti da risolvere operativamente.
- L'attivazione richiede una migrazione preventiva e validata, non eseguita
  in questa fase.
- Restano necessari test manuali su Telegram e fogli Google di prova.
- Le notifiche Telegram restano best-effort e at-least-once: i marker nel
  `LOG` recuperano gli admin mancanti, ma un crash tra invio e marker può
  duplicare una consegna.
- L'unione presuppone un accordo privato: il bot non gestisce consenso,
  contestazioni o ripartizione dei costi.
