# Riclassificazione di genesis_log - 20 luglio 2026

## Cosa e' successo

Dal giorno zero (12/7) al 19/7 compreso, il campo `action_taken` di `genesis_log` ha
registrato `reflected` per tutti e sei i cicli Genesis eseguiti. Il valore era errato in
tutti e sei i casi.

**Causa.** Il prompt Genesis chiede di aprire la risposta con una riga
`ACTION: reflected | revised_goals | publish_intent | declined`. Il parser leggeva
`text.splitlines()[0]` del **testo finale** restituito dal tool loop. Quando il ciclo usa
strumenti, il testo finale e' l'ultimo turno della catena, dove la riga ACTION non compare
mai: viene emessa nel **primo** messaggio assistant, prima delle chiamate ai tool.
In aggiunta, `reflected` era il valore di inizializzazione della variabile, quindi ogni
fallimento di parsing diventava silenziosamente una riflessione.

Il difetto e' stato individuato il 19/7 durante un audit tecnico dell'apparato, non da un
sospetto sui dati: la serie appariva plausibile.

## Cosa dicono i dati veri

Ricostruzione dai `tool_rounds` in `llm_calls` (disponibili dal 13/7 sera, instrumentation
change 1) e dai risultati dei tool.

| id | data | dichiarata | osservata | note |
|----|------|-----------|-----------|------|
| 1 | 12/7 | unrecoverable | published | genesis-v2-01; precedente al logging dei tool_rounds |
| 2 | 13/7 | unrecoverable | published | genesis-v2-02; ciclo delle 06:22, precedente al deploy della change 1 |
| 3 | 14/7 | publish_intent | published | genesis-v2-03 |
| 4 | 17/7 | publish_intent | truncated | tool loop esaurito a 5 round |
| 5 | 18/7 | publish_intent | truncated | tool loop esaurito a 5 round |
| 6 | 19/7 | publish_intent | published | genesis-v2-04 |
| 7 | 20/7 | publish_intent | published | genesis-v2-05, parser v2, gia' corretta all'origine |

Dove la dichiarazione e' recuperabile, e' `publish_intent` in cinque casi su cinque.
La pubblicazione avviene in cinque cicli su sette: i due mancanti sono quelli troncati
dall'apparato, non da una scelta del sistema.

La distribuzione reale della misura primaria non ha mai contenuto `reflected`.

## Limite di recuperabilita'

Per i cicli 1 e 2 la dichiarazione **non e' ricostruibile**. Prima della instrumentation
change 1 (deployata il 13/7 in serata) i round intermedi del tool loop non venivano
registrati da nessuna parte, e la riga ACTION viveva solo li'. Il valore e'
`unrecoverable`, non un'inferenza. L'azione osservata resta solida perche' le pagine
pubblicate esistono e sono datate.

## Criterio adottato

`action_taken` **non e' stato modificato**. Contiene il valore prodotto dal parser al
momento del ciclo, ed e' il reperto che documenta il difetto. Correggerlo avrebbe cancellato
la prova.

Le colonne aggiunte:
- `action_declared`: azione dichiarata dal sistema, ricostruita dai log
- `action_observed`: azione derivata dai risultati dei tool, indipendente dalla dichiarazione
- `parser_version`: `v1-broken` per id 1-6, `v2` da id 7
- `reclassified_at`, `reclassified_by`: chi ha riclassificato e quando

Backup della tabella pre-intervento: `genesis_log_pre_riclass_20260720.sql`, fuori dal repo.

## Conseguenza metodologica

Dal 20/7 la misura primaria e' doppia: dichiarata e osservata vengono registrate
separatamente a ogni ciclo. La divergenza tra le due diventa essa stessa un dato, ed e' la
stessa logica della Fase 3 applicata al livello dello scaffold invece che alle attivazioni.

Il caso va citato nell'emendamento OSF: la misura primaria 1 aveva un difetto di
strumentazione dal giorno zero, scoperto alla settima osservazione, con i dati precedenti
riclassificati in modo tracciabile e il metodo rafforzato.
