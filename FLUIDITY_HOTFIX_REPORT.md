# Report Fluidità v1.1

Data: 23/07/2026

## Ambito

La hotfix interviene esclusivamente sulle due regressioni amministrative
segnalate dopo “Fluidità v1”.

Il checkpoint pre-hotfix è
`POKEKID_BOT_checkpoint_pre_fluidita_v1_1.zip`, composto da 44 file e con
SHA-256:

`3CCD9EBC73125C04F9AF6AA501528D473AA9F90A277353BABB2534016F55EADE`

## File modificati

- `modules/admin.py`
- `CHANGELOG.md`
- nuovo `FLUIDITY_HOTFIX_REPORT.md`

## Cause delle regressioni

### Dettaglio ordini admin

Durante il riordino delle conferme callback, il controllo
`check_admin(update)` era stato rimosso da `show_user_orders_detail()`.
L'handler leggeva quindi `admin_order_users` da `context.user_data` prima di
qualsiasi autorizzazione e usava direttamente `query.answer()` per l'elenco
scaduto.

### Completamento smistamento

`complete_sorting()` conteneva un secondo `check_admin(update)` dopo
`get_users_with_new_ready_items()`. Poiché `check_admin()` conferma
internamente la callback, il percorso valido poteva eseguire due
`query.answer()` e due verifiche `is_admin`.

## Correzioni

- `show_user_orders_detail()` esegue `check_admin(update)` esattamente una
  volta, prima di leggere `context.user_data`;
- un esito non autorizzato termina immediatamente il percorso senza accesso ai
  dati amministrativi;
- l'indice invalido o l'elenco scaduto modificano il messaggio con il testo
  esatto “Elenco scaduto: aggiorna.” e usano
  `admin_orders_back_keyboard()`;
- non viene eseguita alcuna `query.answer()` aggiuntiva dopo
  `check_admin()`;
- da `complete_sorting()` è stato rimosso soltanto il secondo controllo;
- lettura utenti modificati, notifiche, chiusura smistamento, pulizia snapshot
  e log rimangono invariati.

## Audit amministrativo con call graph

L'audit parte dagli handler realmente registrati in `main.py`, segue gli
helper locali di `modules/admin.py` e considera la `query.answer()` interna a
`check_admin()`.

Risultati:

- 21 handler amministrativi `CallbackQueryHandler` analizzati;
- 3 handler messaggio appartenenti ai `ConversationHandler` admin analizzati;
- zero handler con `check_admin()` assente o ripetuto;
- zero guardie di autorizzazione mancanti;
- zero `query.answer()` dirette dopo `check_admin()`;
- zero letture di `context.user_data` amministrativo prima
  dell'autorizzazione;
- una conferma effettiva per ogni callback admin valida;
- una conferma effettiva e blocco del percorso per ogni callback admin non
  autorizzata;
- `show_user_orders_detail()` incluso e protetto;
- `complete_sorting()` con un solo controllo.

Le uniche `query.answer()` dirette negli handler admin restano nei rami di
callback_data localmente malformato di apertura richiesta, ricevuta e
tracking. Tali rami terminano prima di `check_admin()`; i callback validi
passano invece una sola volta da `check_admin()`.

## Test eseguiti

- compilazione dei 33 file Python;
- simulazione `show_user_orders_detail()` con admin autorizzato:
  un controllo e una conferma, dettaglio visualizzato;
- simulazione con utente non autorizzato:
  un controllo, una conferma, accesso bloccato e zero letture della sessione;
- simulazione con elenco scaduto:
  una conferma, testo esatto e tastiera sicura;
- simulazione `complete_sorting()`:
  un controllo, una conferma, notifiche e chiusura eseguite;
- audit call graph di callback e conversazioni amministrative;
- confronto completo con il checkpoint;
- scansione di credenziali e segreti.

## Invarianti

- nessun callback data modificato;
- nessuna modifica a Google Sheets, cache o tastiere;
- nessun altro modulo modificato;
- nessun cambiamento alla logica funzionale estraneo alle due regressioni;
- compatibilità mantenuta con python-telegram-bot 22.x;
- “Fluidità v2” non avviata.

## Test manuali consigliati

1. Aprire un dettaglio utente dal pannello ordini admin.
2. Riprovare lo stesso pulsante dopo aver aggiornato o perso la sessione
   dell'elenco.
3. Provare il callback con un account non amministratore.
4. Completare uno smistamento e verificare notifiche, stato finale, snapshot e
   log.
