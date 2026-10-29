# SU Print Intelligence — Centro Raccolta Contatori V1

Motore Python indipendente dal gestionale. Legge esclusivamente uno snapshot SQLite
autonomo e restituisce un report JSON. Nessuna integrazione con Panoramica,
Contratti, Monitoraggio o Cartella Tecnica è inclusa.

Versione 1.1.2: avvio Windows in un solo file, con scelta automatica della copia
più recente e apertura del file di supporto già selezionato. Mantiene dettagli
delle anomalie, controllo delle letture parziali e archivio compresso.

Adattatore del registro reale Remote Agent, ricavato dallo schema,
dalla diagnostica e dai sorgenti di ricezione forniti per la revisione. Il risultato
resta `REVIEW_REQUIRED` fino al confronto delle letture con casi noti. Vedere
`docs/REGOLE_ADATTATORE.md` per filtri, cronologia, esclusioni e limiti del totale.

Il file `test_windows.bat` si può aprire con doppio clic: la finestra resta aperta
per mostrare il risultato fino alla pressione di un tasto.

## Avvio

Richiede Python 3.11 o successivo; non usa librerie esterne in esecuzione.
I launcher Windows richiedono Python 3.11 e il launcher `py` installato.

### Avvio semplice per il collaudo già avviato

Scaricare `AVVIA_COLLAUDO_SEMPLICE.bat` e aprirlo con doppio clic. Il file contiene
già il pacchetto Python: non occorre estrarre, installare o cercare altri file.
Durante l'esecuzione non scarica codice o dati dalla rete.

Trova sul Desktop effettivo la cartella `SU_Print_Collaudo_*` più recente, usando
la data nel nome generato dal nostro strumento di copia. Richiede una copia
completata con `schema_collaudo.json` e `snapshot.sqlite`, senza collegamenti o
sidecar. Se la più recente è incompleta o due copie hanno la stessa data di
creazione, segnala il problema senza usare di nascosto una copia più vecchia.
La verifica completa della copia rimane affidata all'adattatore in sola lettura.

Prepara i rapporti accanto alla copia e apre Esplora file con
`supporto_collaudo.zip` selezionato. La finestra resta aperta con il pulsante
**Mostra file da inviare**. L'invio avviene solo quando l'utente allega il file.
Questa modalità serve al collaudo per cui è stato richiesto il rapporto completo,
che contiene matricole, contatori e storico. Non crea un'altra copia del database.

L'avvio è un batch con archivio zipapp Python incorporato, creato da
`python tools/build_simple_launcher.py`. Il comando termina prima dell'archivio;
Python lo esegue in modalità isolata. Non scrive codice in cartelle del gestionale.

### Avvio dalla cartella del pacchetto

Da una cartella estratta del pacchetto:

```bat
test_windows.bat
collaudo_reale_windows.bat
avvia_windows.bat "C:\Collaudo\snapshot.sqlite" --schema su-print --output "C:\Collaudo\report-nuovo.json"
```

Su Linux/macOS, dalla radice del progetto:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m su_print_counters /percorso/snapshot.sqlite --schema su-print --output /percorso/report-nuovo.json
```

Installazione opzionale come pacchetto: `python -m pip install .`; poi usare
`su-print-counters snapshot.sqlite --schema su-print --output report.json`.
Senza `--output`, il JSON viene scritto sullo standard output. Un errore operativo
restituisce codice 1; le anomalie dei dati sono nel report e non sono errori di avvio.
I file di destinazione esistenti non vengono sovrascritti.

## Collaudo locale delle letture reali

Aprire `collaudo_reale_windows.bat` con doppio clic e scegliere lo
`snapshot.sqlite` già preparato sul Desktop. Il controllo legge il registro delle
telemetrie e crea una nuova cartella `Risultati_Contatori_V1_*` accanto alla copia:

| File | Uso |
| --- | --- |
| `risultati_contatori.html` | Tabella locale con matricole e ultima telemetria valida ricevuta, da aprire con doppio clic |
| `risultati_contatori.json` | Storico completo controllato, macchine escluse, ultime letture per contatore e anomalie; contiene dati da conservare sul PC |
| `esito_collaudo.json` | Solo conteggi, codici, controlli booleani e impronta della copia, da condividere per proseguire il collaudo |
| `LEGGIMI.txt` | Istruzioni e limiti del controllo |
| `supporto_collaudo.zip` | Rapporto completo, riepilogo e istruzioni compressi; contiene dati privati e va inviato solo se richiesto per l'approfondimento |

La finestra finale rimane aperta con i pulsanti **Apri tabella**, **Apri cartella**,
**Copia riepilogo** e **Chiudi**. La copia negli appunti include solo l'esito
filtrato, anche se al file sono stati aggiunti campi estranei. Il programma non
trasmette in rete lo ZIP o altri risultati.

L'HTML è un file statico locale, senza risorse di rete o codice proveniente dal
database. Non sostituisce il confronto con il contatore della macchina. I contatori
assenti restano assenti, non diventano zero e non sono riportati avanti nella
tabella. Il totale è etichettato come **dichiarato**: il vecchio sistema poteva
derivarlo dai marker e non ne salvava l'origine. Le date di raccolta senza fuso
restano senza fuso. La cronologia usa la ricezione server UTC, previa verifica
dello schema per i valori senza offset. Lo storico di vecchie associazioni resta
distinto dall'ultima lettura attualmente utilizzabile.

Il motore applica un limite di tre minuti e consente l'annullamento; la successiva
serializzazione dei risultati può richiedere altro tempo. In caso di errore o
annullamento elimina solo la propria cartella incompleta. Nessuna scrittura sul
database, importazione dei servizi originali o integrazione nel gestionale.

Le anomalie delle letture includono ora un `context` privato: identità della
macchina, matricole da confrontare, contatori respinti, metrica in regressione e
valore/ID precedente, oppure i record coinvolti nel conflitto. I gruppi molto grandi
mostrano fino a 20 record e dichiarano il taglio. Non vengono copiati payload
integrali, chiavi o campi estranei; il contesto rimane fuori dall'esito condivisibile.

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

## Diagnostica dello snapshot reale

Aprire `diagnostica_copia_windows.bat` con doppio clic e selezionare
`snapshot.sqlite` nella cartella di collaudo sul Desktop. La lettura usa
`mode=ro&immutable=1` e `query_only=ON`; rifiuta nomi diversi e sidecar WAL/SHM/journal.
Il nome e l'assenza di sidecar non possono da soli provare che un file sia una
copia: usare esclusivamente lo snapshot chiuso creato nel passaggio precedente.

La diagnostica produce un nuovo file `diagnostica_collaudo_*.json` accanto alla
copia. Registra il suo SHA-256, conteggi, relazioni mancanti, tipi e percorsi JSON
di un campione delle 40 righe più recenti per gruppo (accettate, rifiutate, flag
diversi e storico contatori). Registra le forme temporali senza scegliere un fuso.
Non include record, payload, matricole, IP, clienti, certificati, chiavi o valori
liberi. I nomi JSON non appartenenti a un vocabolario fisso diventano `<other>`;
anche i nomi dinamici possono infatti contenere dati. Stati e fonti riconosciuti
sono categorie predefinite, gli altri diventano `other`.

I limiti di dimensione/profondità dei JSON e copertura dei campioni sono dichiarati
nel rapporto; JSON con chiavi duplicate o numeri non standard sono contati come
invalidi. Il risultato è `SEMANTICS_UNCONFIRMED` oppure `SCHEMA_INCOMPLETE`, mai
una certificazione delle letture. Dopo due minuti il lavoro si interrompe; è
possibile annullarlo dalla finestra. Il rapporto non dimostra che i formati meno
recenti siano coperti e non sostituisce la verifica del produttore/gestore dei messaggi.

La sola interfaccia guidata aggiunge un indice opzionale dei moduli Python nella
cartella `app` del percorso d'installazione individuato. Legge i file senza
importarli o eseguirli e riporta solo nomi relativi e tre nomi di tabelle cercate,
mai righe di codice. L'indice ha limiti espliciti (500 file, 5 secondi, 1 MiB per
file, 20 candidati) e segnala scansioni incomplete. Serve a chiedere il modulo
specifico, se necessario. `diagnose_snapshot` e il motore non richiedono che il
programma attivo sia installato. Il rapporto comprende questi nomi di moduli:
controllarlo prima di condividerlo.

## Contratto di riferimento e mappatura semplice

Il database reale non è presente nel repository. Lo schema seguente è il contratto
sintetico usato nei primi test; rimane disponibile tramite `--schema reference`
(predefinito) e `examples/mapping.json`. La mappatura dei soli nomi non è adatta al
registro reale, che richiede invece `--schema su-print` e il parsing dei payload.
Non viene eseguita alcuna migrazione.

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

## Regole del contratto di riferimento

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
Il contratto di riferimento carica le righe in memoria. L'adattatore reale scorre
i payload del log e conserva solo le letture normalizzate e le anomalie; lo storico
ricostruito e la sua serializzazione richiedono memoria proporzionale alle righe.

## Collaudo prima delle integrazioni

La suite automatica crea database temporanei e verifica filtri, discordanza delle
matricole, duplicati, timestamp, regressioni, mapping, CLI e invarianza SHA-256
del database e dell'elenco dei file. Non usa né modifica il database operativo.

Prima di qualsiasi integrazione:

1. Eseguire `test_windows.bat` sulla macchina Windows destinata al servizio.
2. Eseguire `collaudo_reale_windows.bat` sulla copia controllata e verificare i
   risultati e le anomalie. Controllare che l'impronta corrisponda alla copia scelta.
3. Confrontare matricole, storico, ultima lettura ed esclusioni con casi noti,
   inclusi reset e conflitti; verificare hash del database prima e dopo.
4. Misurare tempi e memoria sui volumi reali e conservare il report di collaudo.
5. Autorizzare separatamente le integrazioni solo dopo esito positivo del collaudo.

I test sintetici non certificano da soli le letture effettive o la GUI Windows.
