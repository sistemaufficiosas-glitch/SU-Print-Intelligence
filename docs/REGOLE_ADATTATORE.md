# Regole dell'adattatore SU Print V1

L'adattatore è indipendente dal gestionale. I moduli originali sono stati letti
per verificare il contratto; non vengono importati, eseguiti o distribuiti.
I loro costruttori aprono connessioni scrivibili e applicano schema/cache.

## Fonte e identità

La fonte primaria è `su_remote_telemetry_log`. Nei sorgenti esaminati gli INSERT
avvengono dopo autenticazione Agent e controllo del binding. Il log viene
confermato prima della scrittura nello storico contatori: le due tabelle non
sono una relazione uno-a-uno e non devono essere sommate. Non vengono usati
`machine_meter_readings`, contatori dell'anagrafica o cache come osservazioni.

Un record deve avere `accepted` intero 1, Agent presente nel registro, macchina
riconoscibile, `payload.printer_id` intero coincidente con la riga e matricola
testuale identificativa coerente con l'anagrafica univoca. Le matricole vengono
confrontate rimuovendo solo spazi esterni e differenze di maiuscole, conservando
punteggiatura, spazi interni e zeri iniziali. Segnaposto vuoti/Unknown/N/A e simili
non identificano una stampante. Discordanze e duplicati restano anomalie;
nessuna lettura viene spostata automaticamente su un'altra macchina.

Il registro contiene payload normalizzati, non firme/header originali. Il V1
si affida all'attestazione registrata dal server e non riverifica HMAC offline
né legge chiavi DPAPI o certificati. Anche tipi e valori possono essere già
stati convertiti a monte: non è possibile ricostruire il pacchetto originale.

## Letture e contatori

Il solo `accepted=1` non attesta lettura riuscita. Si richiede `snmp_ok is True`,
almeno un contatore e solo interi JSON in `0..2147483647`, oppure assenza `null`.
Booleani, stringhe numeriche, float, negativi e valori fuori intervallo vengono
rifiutati; nessuna conversione permissiva viene aggiunta. JSON ambigui, duplicati,
troppo grandi o non standard vengono esclusi.

Le chiavi sono `black_counter`, `color_counter`, `total_counter_candidate`.
Il totale dichiarato, se presente, non può essere inferiore a una componente.
Non vengono calcolate somme, massimi o conversioni di unità. Ogni regressione
rispetto al precedente valore controllato di uno qualsiasi dei contatori respinge
la riga. Un contatore assente in una lettura intermedia non azzera il baseline.
Nessun reset o sostituzione viene riconosciuto automaticamente.

**Limite del totale:** il codice Pilot esaminato può riempire il candidato con
il massimo dei marker se il totale manca. Il payload salvato non distingue questa
origine. Il report quindi espone `origin_verified=false` e la tabella mostra
"Totale dichiarato"; i controlli V1 non certificano la corrispondenza al contatore
fisico. Va confrontato con casi noti prima di qualsiasi utilizzo contrattuale.

## Tempo e ultima lettura

`collected_at` deve essere un timestamp ISO completo. I valori senza offset
restano privi di fuso: la posizione del PC non prova quello dell'Agent.
L'ordinamento usa invece `received_at` del registro. Il codice esaminato omette
la colonna negli INSERT e il CREATE prevede `DEFAULT CURRENT_TIMESTAMP` SQLite.
Il V1 verifica il default effettivo sulla copia e l'assenza di trigger sulla
tabella prima di trattare una ricezione senza offset come UTC. Un default diverso
o trigger impediscono l'assunzione, e quelle righe vengono escluse. Le date già
dotate di offset vengono convertite in UTC. Date di ricezione future o malformate
sono escluse; una raccolta con offset oltre cinque minuti dopo la ricezione è
incoerente e viene respinta.

SQLite normalmente registra la ricezione al secondo. Se nello stesso istante
di ricezione esistono valori diversi della stessa metrica, il V1 mette tutte
quelle righe in quarantena. È una scelta conservativa: può escludere anche una
progressione legittima avvenuta nello stesso secondo e richiede revisione, non
dimostra un conflitto fisicamente simultaneo. Duplicati coerenti restano nello
storico; l'ID numerico di log rende deterministico il pareggio.

`latest` significa **ultima telemetria valida ricevuta e attualmente utilizzabile**,
non prova dell'istante fisico dell'ultima misurazione. `latest_by_counter`
conserva separatamente l'ultima lettura utilizzabile per ciascuna metrica. La
tabella dell'ultima telemetria non sostituisce metriche assenti con valori vecchi.

## Storico e autorizzazione attuale

I messaggi storici accettati con dati coerenti possono provenire da Agent oggi
inattivi o binding rimossi: questi cambiamenti attuali non annullano automaticamente
il passato. Lo storico controllato rimane conservato e marcato
`currently_authorized=false` dove manca l'autorizzazione attuale.

Per comparire nell'elenco utilizzabile serve una lettura controllata proveniente
da una coppia attuale Agent–macchina con Agent `Attivo`, nessuna revoca presente,
permesso SNMP intero 1 e nessuna esclusione. I flag globali e le esclusioni per
coppia, matricola o indirizzo autorizzato del binding vengono applicati solo
all'eleggibilità attuale. L'indirizzo è un filtro di permesso e non una chiave
di identità o di riassociazione. Le macchine prive di un'ultima lettura utilizzabile
sono escluse da `printers`, ma restano in `archive` con motivazione e storico
controllato, quando disponibile. Le righe rifiutate rimangono nello snapshot;
il report ne conserva codice e ID per l'audit.

## Esito del collaudo

Il risultato è sempre `REVIEW_REQUIRED`. I test automatici validano le regole
su casi sintetici, non certificano i contatori reali. Il rapporto locale richiede
confronti su matricole, contatori e anomalie, inclusi reset e ambiguità temporali.
Nessuna integrazione con Panoramica, Contratti, Monitoraggio o Cartella Tecnica
viene eseguita da questo pacchetto.
