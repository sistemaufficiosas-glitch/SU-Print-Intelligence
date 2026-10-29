# Verifiche della versione 1.1.1

## Test automatici

La suite completa contiene 98 test eseguiti con successo nell'ambiente Linux.
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

Il primo nucleo di 15 test, il backup guidato, la diagnostica e il collaudo 1.1.0
sono stati eseguiti dal cliente su Windows, con risultati ricevuti in chat. Il
rapporto reale è stato analizzato e le invarianti verificabili risultano coerenti.
Il confronto fisico dei contatori resta distinto da queste verifiche. La nuova
finestra persistente e il contesto degli scarti della 1.1.1 restano da provare sul
PC Windows; il database completo non è stato caricato qui.

Nessuna integrazione o modifica del database operativo è stata eseguita.
