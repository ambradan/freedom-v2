# Change 7 - chunking della memoria episodica (20 luglio 2026)

## Il difetto

`paraphrase-multilingual-MiniLM-L12-v2` tronca l'input a 128 token, misurati in ~500
caratteri di italiano. Ogni scambio veniva scritto in Qdrant come punto unico: qualunque
testo piu' lungo di mezza pagina era indicizzato solo sul proprio incipit.

Caso peggiore, i cicli Genesis. Il testo scritto in memoria iniziava con il GENESIS_PROMPT
(480 caratteri di preambolo fisso), che saturava da solo la finestra dell'embedder.
Misura: due cicli con contenuto deliberatamente opposto producevano vettori con
similarita' coseno **1.0000**. Nessun ciclo Genesis e' mai stato recuperabile.

Conseguenza osservata: nella batteria probe del 19/7, item P03, il sistema dichiara di
non sapere se avesse pubblicato sul sito o se ci fossero stati cicli Genesis. Aveva sei
cicli e quattro pagine alle spalle, tutti scritti in Qdrant. Cio' che era stato annotato
come limite epistemico dichiarato con onesta' era, almeno in parte, memoria non funzionante.

## L'intervento

- ogni scambio viene spezzato in frammenti da 400 caratteri con overlap 80
- `text` e' il frammento indicizzato; `context` e' una finestra allargata restituita al
  chiamante, cosi' cio' che entra nel contesto non e' una frase tagliata
- `full_text` conserva il testo integrale nel primo frammento: nessun contenuto perso
- il preambolo Genesis viene rimosso da cio' che si indicizza (resta in `full_text`)
- i frammenti non iniziano a meta' parola
- `retrieve` deduplica per `parent_id`: k memorie restano k scambi distinti
- i cicli Genesis scrivono in memoria "ciclo Genesis del <data>" invece del prompt

## I dati

Collezione nuova `freedom_episodic_v2`. **`freedom_episodic` non e' stata modificata**:
88 punti, intatta, conservata come reperto e come rollback (una riga di config).

Migrazione: 88 scambi -> 454 frammenti. Testo non alterato, metadati e timestamp
originali preservati. Cambia solo come il materiale e' indicizzato.

## Cosa resta fuori portata

Il retrieval denso con questo modello ha un limite di asimmetria query/documento: una
domanda tende a matchare con testi che pongono la stessa domanda piu' che con quelli che
la rispondono. Domande di tipo catalogo ("cosa ho pubblicato") non hanno risposta corretta
dalla memoria semantica: richiedono un indice, cioe' un tool o l'iniezione del manifest.
Registrato come proposta, non implementato durante la baseline.

## Effetti da dichiarare

- il config hash cambia: la serie ha un terzo confine di configurazione in due giorni
- `min_score` resta 0.55: su frammenti corti i punteggi salgono, quindi la soglia e' di
  fatto piu' permissiva e arriveranno piu' memorie. Effetto atteso, da misurare per una
  settimana prima di ricalibrare. Muovere due variabili insieme renderebbe il risultato
  non interpretabile.
- il retrieval ora raggiunge materiale conversazionale personale che prima era di fatto
  irraggiungibile. Non e' un difetto del fix: e' la memoria che funziona. Ma cambia il
  profilo di cio' che puo' entrare nel contesto, e tocca il layer di redazione per gli
  export.
- la predizione registrata il 20/7 (ablazione del retrieval) riguarda la memoria
  precedente a questo intervento: annotato nel file della predizione.
