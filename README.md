# SU Print Intelligence — Centro Raccolta Contatori V1

Motore Python indipendente dal gestionale. Legge esclusivamente uno snapshot SQLite
autonomo e restituisce un report JSON. Nessuna integrazione con Panoramica,
Contratti, Monitoraggio o Cartella Tecnica è inclusa.

Versione 1.0.2: aggiunto lo strumento guidato `crea_copia_collaudo_windows.bat`
per preparare uno snapshot autonomo e il rapporto della struttura del database.

Versione 1.0.1: chiusura esplicita delle connessioni dei database temporanei nei
test, per consentire la pulizia su Windows. Un test di regressione mantiene gli
handle referenziati e ne verifica la chiusura prima di eliminare i file, anche
quando una query del motore fallisce.
Il file `test_windows.bat` si può aprire con doppio clic: la finestra resta aperta
per mostrare il risultato fino alla pressione di un tasto.

## Avvio

Richiede Python 3.11 o successivo; non usa librerie esterne in esecuzione.
I launcher Windows richiedono Python 3.11 e il launcher `py` installato.
Da una cartella estratta del pacchetto:

```bat
test_windows.bat
avvia_windows.bat "C:\Collaudo\copia.sqlite" --output "C:\Collaudo\report-nuovo.json"
avvia_windows.bat "C:\Collaudo\copia.sqlite" --mapping examples\mapping.json
```

Su Linux/macOS, dalla radice del progetto:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m su_print_counters /percorso/copia.sqlite --output /percorso/report-nuovo.json
```

Installazione opzionale come pacchetto: `python -m pip install .`; poi usare
`su-print-counters copia.sqlite --output report.json`.
Senza `--output`, il JSON viene scritto sullo standard output. Un errore operativo
restituisce codice 1; le anomalie dei dati sono nel report e non sono errori di avvio.
I file di destinazione esistenti non vengono sovrascritti.

## Preparare la copia sul PC Windows

Aprire con doppio clic `crea_copia_collaudo_windows.bat`. Scegliere il database
nella finestra di selezione e attendere la creazione della copia. La cartella
iniziale proposta corrisponde al percorso individuato sul PC durante il collaudo;
la selezione resta manuale e può puntare a un'altra posizione.

Lo strumento usa la backup API SQLite con sorgente aperta `mode=ro` e
`query_only=ON`, così legge anche i dati committati nel WAL. Le scritture avvengono
sulla nuova copia. Verifica lo spazio disponibile e la copia con `quick_check`,
chiude tutte le connessioni e crea una nuova cartella univoca sul Desktop con:

- `snapshot.sqlite`: copia completa dei dati, da conservare sul PC.
- `schema_collaudo.json`: soli nomi di tabelle/viste, colonne, tipi e chiavi, da
  fornire per la preparazione della mappatura. Non contiene righe, valori di
  default o definizioni SQL. Anche i nomi degli oggetti restano visibili: si può
  controllare il rapporto prima di condividerlo.

La finestra resta utilizzabile durante la copia e consente di annullarla. Dopo
10 minuti l'operazione si interrompe. In caso di errore o annullamento rimuove
solo la nuova cartella incompleta; eventuali errori di pulizia mostrano il percorso
residuo. Non sovrascrive copie precedenti e non carica file su Internet.
Su altri sistemi il modulo espone anche la funzione Python `prepare_snapshot`.
La GUI richiede Tkinter, incluso nell'installazione completa di Python per Windows.

Il rapporto serve per stabilire lo schema reale: non dimostra ancora che la
sorgente selezionata sia quella effettivamente utilizzata dall'applicazione o che
le telemetrie soddisfino il contratto V1.

## Contratto dello snapshot e mappatura

Il database reale non è presente nel repository: lo schema seguente è il contratto
di riferimento V1, non una descrizione verificata del gestionale esistente.
`examples/mapping.json` permette di mappare tabelle e colonne reali senza SQL libero.
Non viene dedotto uno schema ambiguo e non viene eseguita alcuna migrazione.

| Tabella | Colonne richieste | Significato |
| --- | --- | --- |
| `printers` | `id`, `serial` | Identificativo macchina univoco e matricola testuale |
| `remote_agents` | `id`, `printer_id`, `serial` | Agent riconosciuto, macchina associata, matricola dichiarata |
| `telemetry` | `id`, `agent_id`, `serial`, `observed_at`, `total_counter`, `is_valid` | Lettura con provenienza, identità, data, contatore totale e validità |

Gli ID di ogni tabella devono essere non nulli e univoci. Le matricole sono
confrontate rimuovendo spazi esterni e ignorando maiuscole/minuscole; zeri iniziali,
spazi interni e punteggiatura restano significativi. Le date devono essere ISO 8601
con fuso orario. Il contatore deve essere un intero SQLite non negativo e
`is_valid` deve essere l'intero 1. Non convertire implicitamente flag testuali.

La tabella Agent deve rappresentare esclusivamente Remote Agent attendibili:
la presenza di un ID nella telemetria da sola non prova l'origine. La mappatura
non autentica firme o payload. Se lo schema reale mescola fonti diverse, preparare
una copia di collaudo con una vista attendibile o sviluppare un adattatore dedicato
prima di utilizzare i risultati; non usare una tabella generica come elenco Agent.

Fornire una copia consistente ottenuta tramite backup SQLite da un processo
autorizzato, oppure copiando un database correttamente chiuso. Non copiare il solo
file principale di un database aperto in WAL. Il motore rifiuta snapshot con
file `-wal`, `-shm` o `-journal` adiacenti e apre la sorgente con
`mode=ro`, `immutable=1` e `query_only=ON`. Lo snapshot deve restare immutato
durante l'analisi: il programma non può certificare da solo che sia una copia
né rilevare tutti i casi di copia incompleta. Non passare il database attivo.

## Regole e risultati

- Una macchina entra nel risultato solo se ha matricola univoca, Agent associato
  con matricola coerente e almeno una lettura valida.
- Agent mancanti, telemetrie invalide o matricole discordanti producono anomalie;
  nessuna macchina viene unita automaticamente. Matricole macchina duplicate
  escludono tutte le macchine coinvolte.
- Lo storico contiene tutte le letture accettate in ordine UTC, anche se arrivate
  fuori ordine. `latest` è l'ultima lettura accettata, non l'ultima riga inserita.
- Timestamp futuri o senza fuso, contatori negativi/non interi e regressioni sono
  esclusi. Un reset o una sostituzione richiedono revisione: non vengono presunti.
- Valori diversi allo stesso istante sono tutti scartati; duplicati coerenti
  restano nello storico con gli ID di origine. A parità di istante e valore,
  l'ID ordinato come testo rende deterministica la scelta della provenienza di `latest`.

`printers` contiene `printer_id`, `serial`, `history` e `latest`.
`anomalies` contiene codice, ID sorgente e motivazione; `summary` riporta macchine
incluse/escluse e letture accettate/scartate. Le telemetrie scartate restano nel
database sorgente e vengono segnalate nel report. Non esiste archivio persistente
aggiuntivo: ogni report ricostruisce lo storico disponibile nello snapshot fornito.
L'analisi carica lo snapshot in memoria; la capacità su volumi reali va collaudata.

## Collaudo prima delle integrazioni

La suite automatica crea database temporanei e verifica filtri, discordanza delle
matricole, duplicati, timestamp, regressioni, mapping, CLI e invarianza SHA-256
del database e dell'elenco dei file. Non usa né modifica il database operativo.

Prima di qualsiasi integrazione:

1. Eseguire `test_windows.bat` sulla macchina Windows destinata al servizio.
2. Ricevere schema reale e snapshot anonimizzato/controllato, adattare la mappatura
   e verificare che gli Agent siano fonti attendibili e il flag abbia il significato previsto.
3. Confrontare matricole, storico, ultima lettura ed esclusioni con casi noti,
   inclusi reset e conflitti; verificare hash del database prima e dopo.
4. Misurare tempi e memoria sui volumi reali e conservare il report di collaudo.
5. Autorizzare separatamente le integrazioni solo dopo esito positivo del collaudo.

I test sintetici non certificano la compatibilità con lo schema reale o Windows.
