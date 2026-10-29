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

## Prove mancanti

1. Percorsi nel JSON per matricola dichiarata, risultato della lettura, contatori
   e timestamp; eventuali formati storici diversi e gestione dei messaggi annidati.
2. Significato effettivo di `accepted`: la struttura non dimostra che `1` attesti
   validità del contatore, anziché sola accettazione del messaggio autenticato.
3. Valori di `status` e `source`; significato di iscrizione, revoca ed esclusioni
   all'istante della lettura, distinto dallo stato attuale.
4. Formati e fusi dei timestamp. `TEXT` non specifica se la data sia UTC, locale
   o senza fuso; la posizione del PC non prova il fuso usato dall'applicazione.
5. Relazione e deduplicazione fra log remoto e storico contatori, da confermare
   senza associare arbitrariamente due righe solo perché hanno gli stessi valori.

La diagnostica sulla copia riporta solo struttura e conteggi di campioni recenti;
non esporta i valori dei payload. Aiuta a localizzare i campi, ma la prova più
autorevole rimane il codice che produce i messaggi e quello che li riceve/salva.
Se il formato o il significato restano ambigui, chiedere i moduli sorgente specifici.

## Stato corrente

Il motore del contratto sintetico V1 è collaudato. Lo schema reale è riconosciuto
e la diagnostica è testata su database sintetici conformi alle colonne osservate.
La lettura di contatori reali non è ancora certificata: nessun fallback per IP,
nessuna matricola sostitutiva e nessuna interpretazione automatica di campi JSON
vengono introdotti per far apparire valido un risultato.

Le integrazioni con Panoramica, Contratti, Monitoraggio e Cartella Tecnica rimangono
successive al collaudo delle letture reali e alla revisione dei casi anomali.
