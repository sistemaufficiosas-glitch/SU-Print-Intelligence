# Schema reale e condizioni per il collaudo

Il rapporto strutturale fornito dall'utente il 29 ottobre 2026 elenca 86 oggetti
SQLite. Non contiene record, valori predefiniti o il codice che interpreta i dati.
Non è stato allegato il database completo. Non sono state eseguite letture reali
né integrazioni nel programma attivo da questo ambiente.

| Tabella | Evidenza verificata | Regola per l'adattatore |
| --- | --- | --- |
| `printers` | `id`, `serial_number`; contatori e campi `last_*` in anagrafica | Identificare per matricola non vuota/univoca; non usare i contatori anagrafici come letture Agent |
| `su_remote_agents` | Agent con `id`, `status`, iscrizione/revoca e certificato | Verificare stato e attendibilità temporale; `certificate_serial` identifica il certificato, non la stampante |
| `su_remote_agent_printers` | PK composta `agent_id, printer_id` | Gestire associazioni molti-a-molti; non fabbricare una matricola dell'Agent copiando quella anagrafica |
| `su_remote_telemetry_log` | `id`, `agent_id`, `printer_id`, `accepted`, `received_at`, `payload_json` | Candidato per provenienza e storico; verificare matricola e contatori nel payload |
| `machine_meter_readings` | `machine_id`, `agent_id`, `source`, `read_at`, contatori, `raw_json` | Può contenere fonti diverse; non prendere tutte le righe come telemetrie valide |
| `su_remote_monitoring_exclusions` | Esclusioni per coppia Agent–macchina | Confermare applicazione e significato temporale prima di includere/escludere lo storico |
| `su_remote_agent_ignored_devices` | Dispositivi ignorati per Agent, matricola e indirizzo | Segnale di esclusione da interpretare nel contesto del codice autorevole |

Non identificare macchine tramite IP, MAC, nome, UUID Agent o certificato.
Discordanze di matricola devono restare segnalate e non causare fusioni automatiche.
`snmp_readonly` è un nome di permesso, non una prova di lettura riuscita.

## Evidenza dai sorgenti esaminati

Sono stati forniti `remote_agent_ingest.py`, `remote_agent_pilot.py` e
`meter_readings_service.py`. Sono conservati fuori dal repository e non inclusi
nei pacchetti. Dalla lettura statica risultano:

1. Il payload salvato ha campi top-level `printer_id`, `serial_number`, `snmp_ok`,
   `collected_at`, `total_counter_candidate`, `black_counter`, `color_counter`.
2. `accepted=1` viene scritto dopo autenticazione e controllo del binding, anche
   con SNMP fallito, matricola vuota o contatori assenti. Non equivale a lettura corretta.
3. Lo stato Agent verificato è `Attivo` (confronto senza distinzione di maiuscole);
   la fonte dello storico contatori è `Remote Agent`, con spazio. Il log precede
   il tentativo di scrivere lo storico, in una transazione separata.
4. Il codice non valorizza `received_at` negli INSERT e il CREATE della tabella
   usa `CURRENT_TIMESTAMP`. Il default effettivo viene ricontrollato sulla copia:
   la dichiarazione `IF NOT EXISTS` da sola non prova lo schema già installato.
5. `collected_at` può essere locale senza fuso; il V1 non lo presume UTC o Roma.
6. Il Pilot può derivare il candidato totale dal massimo dei marker, senza salvare
   un indicatore dell'origine. Non è possibile certificarla dal payload normalizzato.
7. Il vecchio discovery può ricercare/riassociare per indirizzo e aggiornare la
   matricola; questi comportamenti non vengono riprodotti dal V1.

La diagnostica recente non copre automaticamente ogni formato storico. Il V1
accetta solo il contratto verificato, segnala righe non conformi e conserva le
distinzioni fra controllo dei dati e attestazione del registro centrale.

## Stato corrente

L'adattatore reale è implementato e testato su database sintetici conformi alle
colonne e al codice osservati, con una procedura guidata di collaudo locale.
Il database completo non è stato caricato in questo ambiente. La certificazione
delle letture reali richiede eseguire il controllo sullo snapshot del PC e confrontare
risultati/anomalie con casi noti. Limiti e regole sono in `REGOLE_ADATTATORE.md`.

Le integrazioni con Panoramica, Contratti, Monitoraggio e Cartella Tecnica rimangono
successive al collaudo delle letture reali e alla revisione dei casi anomali.
