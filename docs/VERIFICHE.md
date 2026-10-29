# Verifiche della versione 1.1.4

## Test automatici

La suite completa contiene 149 test eseguiti con successo nell'ambiente Linux.
Include il contratto di riferimento, backup WAL, diagnostica, indice dei sorgenti,
adattatore reale, CLI e generazione dei rapporti. I controlli SQLite conservano
riferimenti alle connessioni per rilevare handle lasciati aperti anche su Linux;
verificano SHA-256, assenza di sidecar aggiunti e tentativi di scrittura respinti.

Fra i casi dell'adattatore: `accepted` con SNMP fallito, identità discordanti,
matricole mancanti/duplicate/placeholder, provenienze manuali ignorate, regressioni
con letture parziali, conflitti, binding molti-a-molti, autorizzazioni attuali
distinte dallo storico, default UTC e trigger, JSON ambigui, annullamento e timeout.

L'esito condivisibile è verificato per non includere record, matricole, ID,
percorsi o valori liberi. L'HTML è escapato e non attribuisce a una lettura
parziale i valori di una precedente. I risultati rimangono `REVIEW_REQUIRED`.

Nella revisione successiva sono stati aggiunti confronti per letture parziali:
due casi sintetici incoerenti fallivano prima della correzione e ora sono respinti;
due casi legittimi restano validi. Il caso limite non è stato riscontrato nelle
letture ammesse del rapporto reale ricevuto. Sono inoltre verificati contesto
degli scarti, baseline corretta, limitazione dei gruppi, privacy del riepilogo e
degli appunti, integrità ZIP e pulizia in caso di annullamento della compressione.

L'avvio semplice aggiunge controlli sulla scelta della copia guidata più recente,
senza ripiego su copie vecchie quando quella nuova è incompleta, ambigua o contiene
collegamenti e sidecar. Sono verificati anche archivio incorporato identico ai
sorgenti, riproducibilità, caricamento isolato da percorsi con spazi e accenti,
avvio senza selettore manuale e apertura Explorer con argomenti separati. Una
pipeline sintetica completa seleziona la copia, raccoglie i dati e controlla lo
ZIP e l'impronta invariata del database.

La correzione 1.1.3 aggiunge sette casi di ricerca fra più posizioni: Desktop di
Windows diverso dalla cartella di avvio, scelta della copia globalmente più
recente, duplicazione dello stesso percorso, cartelle non disponibili, conflitti
e copie recenti incomplete che devono bloccare il processo. Il test del file
incorporato controlla anche il percorso del launcher passato a `sys.argv[0]`.

La 1.1.4 aggiunge 18 casi di ricerca diretta dei file in cartelle rinominate o
annidate, indipendentemente dal formato del nome. Verifica ordine per rapporto
di completamento, copie incomplete recenti, schemi malformati, radici sovrapposte,
collegamenti e limiti di scansione. La profondità definisce l'ambito cercato;
superare i limiti di tempo o numero di voci impedisce l'uso di risultati parziali.
Tre ulteriori casi GUI coprono il selettore quando nessun file è trovato,
l'annullamento e la validazione effettiva della scelta. La pipeline completa
sintetica usa una cartella rinominata e controlla ancora ZIP e database invariato.

## Prova rappresentativa di capacità

Con la versione 1.1.0 è stata eseguita la pipeline SQLite → adattatore → quattro file su
63.506 righe **interamente sintetiche**, con un database di 527,2 MiB. Ogni payload
contiene campi del contratto e padding testuale; non riproduce tutte le strutture
nidificate dei dati reali. Tutte le righe previste sono state controllate e i
quattro file verificati; SHA-256 della sorgente invariato prima e dopo.

Nel processo Linux usato: 2,56 secondi per la pipeline e picco RSS di circa
160,4 MiB. Il file era appena stato generato, quindi la cache del filesystem può
aver influito. Queste misure non garantiscono i tempi o la memoria sul PC Windows
né sui payload reali. Il container esponeva 5 CPU e un limite di 32 GiB; il motore
non avvia elaborazioni parallele.

## Verifiche ancora necessarie

Il primo nucleo di 15 test, il backup guidato, la diagnostica e i collaudi 1.1.0 e
1.1.1 sono stati eseguiti dal cliente su Windows, con rapporti ricevuti in chat.
Il rapporto 1.1.1 include il contesto degli scarti e lo ZIP automatico. Le
invarianti verificabili risultano coerenti. Un riscontro fotografico su una
macchina è compatibile con la progressione dei contatori, ma non è una misurazione
contemporanea e non spiega le anomalie storiche. Il confronto fisico resta
distinto dai test delle regole; il database completo non è stato caricato qui.

Gli avvii unici 1.1.2 e 1.1.3 hanno avviato Python e mostrato la GUI sul PC
Windows, ma hanno segnalato l'assenza di copie riconosciute. La schermata 1.1.3
conferma che il Desktop controllato corrisponde al percorso mostrato in Esplora
file. Il nome della cartella visibile è compatibile con il filtro precedente;
la causa esatta dello zero candidati resta da accertare. La 1.1.4 sostituisce
quel filtro con la ricerca dei file, senza interpretare date nel nome delle
cartelle. Deve ancora essere eseguita sul PC. Il completamento del flusso
automatico e l'apertura effettiva di Explorer restano da verificare. I test GUI
verificano il flusso con componenti simulati.

Nessuna integrazione o modifica del database operativo è stata eseguita.
