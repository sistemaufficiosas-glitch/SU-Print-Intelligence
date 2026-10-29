# Preparazione della Panoramica

Il motore V1 autonomo ha completato il collaudo funzionale. La fase successiva
parte dalla Panoramica: per implementarla servono il codice della schermata,
l'avvio/navigazione e i servizi da cui riceve i dati. Nel repository attuale
questi file non sono presenti. Non viene costruito un collegamento ipotetico.

## Acquisizione dei sorgenti

`PREPARA_PANORAMICA.bat` contiene soltanto il nostro strumento di raccolta e usa
Python 3.11 già installato. Il percorso iniziale deriva dal launcher fornito
durante il collaudo. La lettura è statica: nessun import, avvio o modifica del
gestionale. Non legge database o documenti e non invia dati in rete.

L'ambito comprende `app/**/*.py`, layout `.ui`, stili `.qss`, i file Python nella
radice del programma, `requirements.txt` e `pyproject.toml`. Le cartelle di dati,
log, cache, backup, ambienti e dipendenze sono escluse; vengono omessi collegamenti
e file con nomi riferiti a credenziali e segreti. L'allowlist non comprende
database, JSON originali, file `.env`, certificati, CSV, XML o documenti.
Il codice può contenere configurazioni incorporate: l'archivio va trattato come
codice privato del programma, da condividere solo per questo lavoro.

Il manifesto elenca nomi relativi, dimensioni, SHA-256 ed esclusioni. Il pacchetto
è scritto in una nuova cartella temporanea esterna al gestionale. Annullamento
ed errori eliminano soltanto quella cartella. I limiti proteggono dimensione e
durata della raccolta; i sorgenti non vengono pubblicati su GitHub.

## Collegamento da preparare dopo la lettura dei sorgenti

- La Panoramica deve consumare i risultati del motore sulla copia, mantenendo
  indipendente la raccolta; i costruttori dei servizi originali che aggiornano
  schema o cache non devono essere usati per leggere i contatori V1.
- L'associazione richiede una matricola univoca coerente con quella mostrata
  nella schermata. Una discordanza deve rimanere visibile e non può provocare
  riassociazioni tramite IP, nome o ID numerico da solo.
- I contatori mancanti restano mancanti. La data di ricezione e quella della
  copia devono permettere di valutare l'età del dato; non si simula una lettura
  in tempo reale o un'autorizzazione aggiornata dopo la copia.
- La prima modifica deve essere preparata e collaudata su una copia dei sorgenti,
  con istruzioni di ripristino, prima dell'installazione sul programma attivo.
- Contratti, Monitoraggio e Cartella Tecnica sono passaggi successivi. L'origine
  e l'unità del totale richiedono una qualifica prima dell'uso contrattuale;
  il V1 non autorizza automatismi di fatturazione.

L'esito dei rapporti dati rimane `REVIEW_REQUIRED`: il collaudo funzionale delle
regole non equivale a certificare ogni dato o a installare l'integrazione.
