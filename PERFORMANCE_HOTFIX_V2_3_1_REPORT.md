# Performance Hotfix v2.3.1 — Articoli disponibili e versione bot

Data: 24/07/2026

## Esito

La hotfix è stata applicata senza migrazioni, deploy, collegamenti a Google
reale o modifiche agli schemi. Il gestionale `ORDINI` resta in sola lettura.
I 163 test preesistenti continuano a passare; la suite contiene ora 175 test.

Checkpoint iniziale:

- `POKEKID_BOT_checkpoint_pre_performance_v2_3_1.zip`;
- SHA-256:
  `454ABB557DF7FDEFF7EE0BE24A9980A8336EE33F01EB70682CFB499B47176F71`;
- copia byte-per-byte del `BOT.zip` ricevuto.

## Causa esatta del ritardo

### Apertura e refresh “Articoli disponibili”

`prepare_v2_opening_state()` eseguiva, nel caso comune senza bozza:

1. tre letture per la validazione completa dello schema;
2. quattro letture per la sincronizzazione (`ORDINI`, `PROFILI`,
   `ORDINI_ARTICOLI`, `SPEDIZIONI_ARTICOLI`);
3. una lettura per rilasciare le prenotazioni scadute;
4. una lettura per cercare la bozza attiva;
5. due ulteriori letture per registro e prenotazioni disponibili.

Totale: 11 operazioni di lettura Sheets per azione.

Inoltre `_updated_from_source()` assegnava sempre l'ora corrente a
`LAST_SEEN_AT`. Poiché quel campo è persistito, ogni confronto risultava
diverso anche con sorgente e profilo identici. Con 4.239 record, ogni
apertura/refresh preparava quindi un `batch_update` di 4.239 righe. La
combinazione tra 11 round trip e riscrittura massiva è la causa deterministica
del percorso osservato intorno a 15 secondi.

### Toggle e cambio pagina

La selezione era locale, ma il rendering chiamava:

`with_footer() -> get_footer() -> get_bot_version() -> get_config_values()`.

Quindi un'azione puramente Telegram entrava nel livello dati e, su cache
`CONFIG` assente/scaduta, poteva attendere Google e i relativi retry. La
hotfix elimina completamente questo percorso remoto.

## Correzioni

### Sincronizzazione registro

- `LAST_SEEN_AT` viene preservato quando tutti gli altri campi persistiti sono
  identici;
- viene aggiornato quando cambia realmente contenuto, fingerprint, riga,
  username, proprietario, stato sorgente, attività o stato di sync;
- riassociazione a un nuovo profilo e ID articolo stabili restano invariati;
- `batch_update` resta condizionato alla presenza di `updated_records`;
- la sync restituisce anche gli snapshot già letti; una rilettura del registro
  avviene soltanto dopo modifiche reali.

Test con 4.239 record:

- prima sync: `created=4239`;
- seconda sync immediata a un orario diverso:
  `created=0`, `updated=0`, `unchanged=4239`;
- `LAST_SEEN_AT` invariato;
- zero scritture sul registro nella seconda sync.

### Apertura e refresh ottimizzati

Su cache miss o refresh vengono letti una sola volta:

- `ORDINI`;
- `PROFILI`;
- `ORDINI_ARTICOLI`;
- `SPEDIZIONI_ARTICOLI`;
- `SPEDIZIONI`.

Lo schema viene validato sui valori già disponibili. Lo stato Telegram viene
derivato dagli stessi snapshot. La cache single-flight
`shipping:v2_opening_snapshot` dura 10 secondi:

- apertura normale: può riusare registro/schema e rilegge soltanto le
  prenotazioni correnti;
- `orders_refresh`: usa `force=True` e forza una nuova lettura completa;
- prenotazioni scadute: mantengono il rilascio autorevole e vengono rilette;
- Continua, pagamento e finalizzazione non considerano la cache autorevole e
  conservano la rivalidazione completa.

### Versione bot

- fallback locale: `2.3.1`;
- `load_bot_version()` legge `CONFIG -> VERSIONE_BOT` nello startup;
- il loader viene chiamato in `post_init()` tramite `asyncio.to_thread()`,
  dopo i controlli iniziali opzionali;
- errori o valore vuoto impostano il fallback;
- `get_bot_version()` legge solo memoria e non importa/chiama `bot_db`;
- Home e Info costruiscono il testo al momento della risposta;
- un cambio in `CONFIG` richiede riavvio o richiamo esplicito del loader;
- il grafo di import non contiene un ciclo iniziale tra `services.ui`,
  `services.bot_db` e `services.bot_version`.

## Chiamate Google prima/dopo

I conteggi sono ricavati dal call graph e verificati con fake/stub locali. Non
sono tempi misurati contro Google produzione, operazione vietata dalla fase.

| Flusso | Prima | Dopo |
|---|---:|---:|
| apertura disponibile, senza bozza | 11 letture + 1 batch da 4.239 righe | 5 letture, 0 scritture su miss; 1 lettura su hit |
| refresh disponibile | 11 letture + 1 batch da 4.239 righe | 5 letture, 0 scritture se invariato |
| toggle articolo | 0 su hit CONFIG / 1 su miss CONFIG | 0 |
| cambio pagina | 0 su hit CONFIG / 1 su miss CONFIG | 0 |
| join open | 12 letture più eventuali miss profilo/footer e batch massivo | stesse letture funzionali, nessun batch se invariato e nessuna lettura footer |
| join refresh | 10 letture più eventuale miss footer e batch massivo | 10 letture, nessun batch se invariato e nessuna lettura footer |
| join toggle | 0 su hit CONFIG / 1 su miss CONFIG | 0 |

Se la sync trova cambi reali, sono legittimi un aggiornamento e una rilettura
finale del registro. Se esistono prenotazioni scadute, sono legittimi il
rilascio e la rilettura successiva.

## Osservabilità

Ogni riepilogo perf contiene soltanto nome flusso, tempo totale, tempo e
numero delle chiamate Sheets, hit/miss cache e note tecniche; non contiene
PII. Sono presenti:

- `shipping_v2_open_available`;
- `shipping_v2_refresh_available`;
- `shipping_v2_toggle_item`;
- `shipping_v2_change_page`;
- `shipping_v2_join_open`;
- `shipping_v2_join_refresh`;
- `shipping_v2_join_toggle`.

## Verifiche automatiche

- baseline prima delle modifiche: 163/163 test;
- suite finale: 175/175 test;
- caso simulato 4.239 record identici: superato;
- variazione proprietario/riassociazione: superata;
- variazione stato sorgente: superata;
- assenza `batch_update` senza modifiche: superata;
- cache apertura normale e refresh forzato: superati;
- toggle/pagina: `sheets_calls=0`;
- versione in memoria, fallback e reload: superati;
- Continua/finalizzazione autorevoli e legacy invariato: superati;
- callback prodotte/registrate, ordine prima del router e limite 64 byte:
  superati;
- AST: 58 file Python analizzati;
- import smoke con dipendenze esterne sostituite da stub: 46 moduli, zero
  errori;
- scansione credenziali del pacchetto distribuibile: zero corrispondenze;
- nessuna connessione o scrittura Google reale.

## File modificati rispetto al checkpoint

- `main.py`;
- `modules/shipping_v2.py`;
- `modules/shipping_v2_join.py`;
- `services/bot_version.py`;
- `services/order_registry.py`;
- `services/perf.py`;
- `services/shipping_v2.py`;
- `tests/test_performance_hotfix_v2_3_1.py` (nuovo);
- `CHANGELOG.md`;
- `ARCHITECTURE.md`;
- `README.md`;
- `ROADMAP.md`;
- `PERFORMANCE_HOTFIX_V2_3_1_REPORT.md` (nuovo).

Nessun file eliminato, nessuna callback rinominata e nessuna dipendenza
modificata.

## Consegna

- progetto aggiornato:
  `POKEKID_BOT_performance_hotfix_v2_3_1.zip`;
- checkpoint precedente:
  `POKEKID_BOT_checkpoint_pre_performance_v2_3_1.zip`.

Lo ZIP aggiornato segue le esclusioni di sicurezza del progetto: non contiene
`.git`, `.venv`, `__pycache__`, bytecode, credenziali, backup locali o report
operativi generati dalla migrazione. Il checkpoint resta invece la copia
integrale e non filtrata dell'input ricevuto.

## Collaudo manuale Telegram consigliato

1. Riavviare il bot e verificare che Home, Info e footer mostrino
   `VERSIONE_BOT` oppure `2.3.1` se CONFIG non è leggibile.
2. Aprire “Articoli disponibili”, riaprirlo entro 10 secondi e premere
   “Aggiorna”; controllare i tre log perf e i rispettivi `sheets_calls`.
3. Selezionare/deselezionare più articoli e cambiare pagina: i log devono
   mostrare `sheets_calls=0`.
4. Cambiare temporaneamente `VERSIONE_BOT` in un ambiente di prova: la
   versione non deve cambiare prima del riavvio/reload.
5. Modificare un articolo in un foglio di prova e verificare che soltanto il
   record interessato cambi `LAST_SEEN_AT`.
6. Completare un flusso Continua/pagamento/finalizzazione e un flusso legacy
   con i flag disattivati.

## Rischi residui

- non è stato eseguito un benchmark reale contro Google; i nuovi log
  permettono di misurare il guadagno in produzione senza PII;
- la cache di apertura può mostrare per massimo 10 secondi dati sorgente non
  ancora sincronizzati, ma le prenotazioni vengono rilette e Continua/
  finalizzazione rivalidano autorevolmente;
- i flussi join sono ora misurati e non riscrivono più 4.239 record
  invariati, ma conservano le letture funzionali preesistenti;
- lock e cache restano autorità della singola istanza: il limite
  multi-replica documentato non cambia.
