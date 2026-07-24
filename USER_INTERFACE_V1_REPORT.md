# Interfaccia utente v1 — Report

Data: 23/07/2026  
Versione bot: 2.0.0  
Fase: Coerenza, leggibilità e utilizzo mobile

## Ambito

La fase ha modificato esclusivamente la presentazione delle schermate rivolte
agli utenti. Non sono stati modificati logica funzionale, callback data,
handler, `ConversationHandler`, cache, accessi Google Sheets, dati salvati,
validazioni o autorizzazioni.

Il pannello amministratore è rimasto escluso. `modules/admin.py` e
`keyboards/admin.py` sono byte-identici al checkpoint.

## Checkpoint

Checkpoint precedente:
`POKEKID_BOT_checkpoint_pre_user_interface_v1.zip`

SHA-256:
`079A0BB531D8074F8159FD6D8B3CAC103F6F42A08CEDA8B14E494958F9649C51`

Il checkpoint è una copia byte-identica del progetto sorgente
`POKEKID_BOT_shipping_stability_v1.zip`.

## Regole grafiche introdotte

- titoli nel formato `emoji + <b>Titolo</b>`;
- maiuscole normali e spaziatura uniforme tra titolo, riepilogo e contenuto;
- intestazioni semplici per le sezioni;
- indicazione `Pagina X di Y` su una riga dedicata;
- massimo due divisori per schermata e nessun divisore finale;
- footer idempotente tramite `with_footer()`;
- stati tecnici convertiti soltanto al momento del rendering;
- errori operativi con titolo comprensibile, spiegazione breve e indicazione
  di riprovare o tornare indietro;
- etichette dei pulsanti degli articoli limitate a 42 caratteri, con ellissi,
  icona di selezione e quantità sempre conservate.

`LAST_UPDATE` è stato aggiornato al 23/07/2026. `BOT_VERSION` è rimasto
`2.0.0`.

## Schermate migliorate

- home predefinita;
- menu ordini, elenco completo, elenco vuoto e selezione disponibili;
- scelta corriere, riepilogo spedizione e annullamento;
- profilo assente, minimo, incompleto e completo;
- dati del profilo, eliminazione, inserimento, modifica e riepilogo;
- SUB Grading vuoto, ricerca senza risultati, risultati e paginazione;
- storico spedizioni da comando e da profilo;
- smistamento, richiesta scaduta, invio ricevuta, allegato non valido,
  profilo o articoli modificati e conferma finale.

La conferma finale mostra `Stato: In attesa` senza esporre `IN_ATTESA`.

## Pulsanti uniformati

Le etichette visibili usano in modo coerente:

- `⬅️ Indietro`;
- `🏠 Menu principale`;
- `❌ Annulla`;
- `🔄 Aggiorna`;
- `◀️ Precedente`;
- `Successiva ▶️`.

Le etichette sono state riordinate secondo azioni principali, secondarie,
navigazione e home. Non sono stati aggiunti o rimossi pulsanti e nessun
`callback_data` è cambiato.

## Verifiche eseguite

- compilazione e parsing AST: 34 file Python, esito positivo;
- callback: 93 occorrenze e 56 firme AST, identiche al checkpoint;
- handler e `ConversationHandler`: 51 costruttori e 50 firme AST, identici;
- chiamate `query.answer()`: 35, conteggio AST invariato;
- rendering simulato: 22 schermate utente;
- pulsanti simulati: 53, lunghezza massima 42 caratteri;
- profili: assente, Telegram minimo, incompleto e completo;
- ordini: vuoti, pagina singola, più pagine e selezione con nome molto lungo;
- grading: vuoto, ricerca vuota, risultati e più pagine;
- storico: vuoto, in attesa, spedito, annullato e tracking opzionale;
- riepilogo corriere, pagamento, destinazione e conferma finale;
- `Message is not modified` ignorato selettivamente per ordini e grading,
  con gli altri `BadRequest` ancora propagati e una sola `query.answer()`;
- escaping HTML di username, prodotti, profilo, ricerca, storico, corriere,
  PayPal, ID e tracking;
- footer presente una sola volta nelle schermate principali simulate;
- divisori: massimo due e mai in chiusura;
- pannello admin, servizi dati/Google, `requirements.txt` e `config.py`
  byte-identici;
- scansione credenziali ad alta confidenza: nessuna credenziale rilevata;
- confronto completo con il checkpoint: modifiche limitate ai file
  autorizzati.

## File modificati

- `services/ui.py`
- `main.py`
- `modules/orders.py`
- `modules/profile.py`
- `modules/grading.py`
- `modules/history.py`
- `modules/shipping.py`
- `keyboards/home.py`
- `keyboards/orders.py`
- `keyboards/profile.py`
- `keyboards/grading.py`
- `CHANGELOG.md`
- `ROADMAP.md`
- `USER_INTERFACE_V1_REPORT.md`

`AGENTS.md` e `ARCHITECTURE.md` non sono stati modificati perché
l'architettura e le regole operative non sono cambiate.

## Rischi residui

- la resa effettiva dipende dalla larghezza del dispositivo, dal client
  Telegram e dalle dimensioni del carattere impostate dall'utente;
- nomi di corrieri eccezionalmente lunghi provengono dalla configurazione e
  non sono stati abbreviati, perché il requisito limita l'abbreviazione ai
  pulsanti degli articoli;
- i test sono simulati e non sostituiscono una verifica visiva finale su
  Telegram Android e iOS;
- questa fase non modifica flussi o passaggi funzionali della spedizione.
