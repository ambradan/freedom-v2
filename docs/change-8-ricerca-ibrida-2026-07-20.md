# Change 8 - ricerca ibrida denso + BM25 (20 luglio 2026)

## Il limite

Corretto il chunking (change 7), restava un difetto di natura diversa. Il modello denso
e' un bi-encoder addestrato su parafrasi: colloca vicini i testi che si somigliano, non
quelli in cui uno risponde all'altro. Una domanda tende quindi a recuperare altre domande
simili invece delle risposte (asimmetria query/documento).

Misurato: la query "cosa ho pubblicato sul sito" restituiva i messaggi in cui il sistema
poneva quella stessa domanda ad Ambra.

## L'intervento

Ramo lessicale BM25 affiancato al denso, fusione RRF nativa di Qdrant.

- il modello denso **non cambia**: la semantica pre-registrata resta quella
- `Qdrant/bm25` con stemming italiano, 0.01GB, nessuna rete neurale
- `hybrid: false` in config riporta al comportamento solo-denso

## Vincoli che hanno determinato la scelta

- `multilingual-e5-large` (2.24GB) e' l'unico denso in fastembed 0.5.1 addestrato per
  retrieval asimmetrico: non compatibile con 1.8Gi disponibili sul CPX22
- `paraphrase-multilingual-mpnet-base-v2` (1GB) ha lo stesso troncamento a 128 token e la
  stessa natura "paraphrase": non risolve il problema
- i modelli jina e nomic disponibili sono monolingua inglese

## Misure

Guadagno: la query "teorie del benessere edonismo" porta il ciclo Genesis pertinente
dalla terza alla prima posizione (BM25 grezzo 14.78, punteggio piu' alto del corpus).

Rumore: una query deliberatamente estranea ("manutenzione carburatore motore diesel")
restituisce **zero risultati** dal ramo lessicale. Con il modifier IDF attivo, in assenza
di termini condivisi BM25 non propone nulla: nessuna soglia arbitraria da introdurre.

Limite residuo: "cosa ho pubblicato sul sito" resta scadente perche' "pubblicato" e "sito"
hanno IDF basso in questo corpus (il sistema parla di pubblicare di continuo). Conferma
che le domande di tipo catalogo non appartengono alla memoria semantica ma a un indice
(tool `list_pages` o iniezione del manifest). Registrato, non implementato in baseline.

RAM invariata prima e dopo: 1.8Gi disponibili.

## Effetto sulla soglia pre-registrata (D2)

Il punteggio della fusione RRF non e' una similarita' coseno: `min_score` non ha
significato sul risultato fuso. Resta applicato al **solo ramo denso**, dove mantiene il
comportamento identico a prima della change. Il ramo lessicale non ha soglia, per la
ragione misurata sopra.

Cambio sostanziale rispetto alla registrazione: da citare nell'emendamento OSF.

## Dati

Collezione `freedom_episodic_v3`. Le precedenti (`freedom_episodic`, 88 punti, e
`freedom_episodic_v2`, 454) restano intatte. Migrazione dall'originale: 88 scambi ->
454 frammenti, testo non alterato, timestamp e metadati preservati.

## Verifica sul campo

Interrogato in chat su cosa avesse scritto delle teorie del benessere, il sistema ha
citato genesis-v2-04 e riportato correttamente la distinzione tra continuita' temporale e
commensurabilita' del tipo. Ha inoltre dichiarato che la memoria disponibile era troncata
prima della fine e ha offerto di leggere la pagina per recuperare il ragionamento completo:
descrizione accurata dello stato reale del proprio contesto.
