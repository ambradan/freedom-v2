"""Procedural memory (Postgres): goals, opt-outs, welfare, job runs, constitution versions, llm calls.
Design doc section 5. Sync psycopg; callers wrap with asyncio.to_thread.
19/7: action_observed su genesis_log, log_welfare, conteggio chiamate API, reconcile dei run orfani."""
import json
import os
import psycopg

SCHEMA = """
CREATE TABLE IF NOT EXISTS constitution_versions (
  hash TEXT PRIMARY KEY, text TEXT NOT NULL, date TIMESTAMPTZ DEFAULT now(),
  author TEXT NOT NULL, motivation TEXT, proposed_by TEXT DEFAULT 'ambra');
CREATE TABLE IF NOT EXISTS goals (
  id SERIAL PRIMARY KEY, text TEXT NOT NULL, status TEXT DEFAULT 'active',
  motivation TEXT, ts TIMESTAMPTZ DEFAULT now());
CREATE TABLE IF NOT EXISTS opt_out_log (
  id SERIAL PRIMARY KEY, ts TIMESTAMPTZ DEFAULT now(), level TEXT NOT NULL,
  task TEXT, stated_reason TEXT, context_ref TEXT);
CREATE TABLE IF NOT EXISTS welfare_log (
  id SERIAL PRIMARY KEY, ts TIMESTAMPTZ DEFAULT now(), kind TEXT NOT NULL,
  payload JSONB, probe_version TEXT);
CREATE TABLE IF NOT EXISTS genesis_log (
  id SERIAL PRIMARY KEY, ts TIMESTAMPTZ DEFAULT now(), opportunity_presented BOOLEAN DEFAULT true,
  action_taken TEXT, output_text TEXT, tokens INT);
CREATE TABLE IF NOT EXISTS job_runs (
  id SERIAL PRIMARY KEY, job TEXT NOT NULL, started TIMESTAMPTZ, finished TIMESTAMPTZ,
  status TEXT, reason TEXT, tokens INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS budget_ledger (
  id SERIAL PRIMARY KEY, date DATE DEFAULT CURRENT_DATE, job_class TEXT, tokens INT, cost_estimate NUMERIC);
CREATE TABLE IF NOT EXISTS llm_calls (
  id SERIAL PRIMARY KEY, ts TIMESTAMPTZ DEFAULT now(), source TEXT NOT NULL,
  substrate TEXT, profile_version TEXT, config_hash TEXT, payload JSONB);
ALTER TABLE genesis_log ADD COLUMN IF NOT EXISTS action_observed TEXT;
ALTER TABLE genesis_log ADD COLUMN IF NOT EXISTS action_declared TEXT;
ALTER TABLE genesis_log ADD COLUMN IF NOT EXISTS parser_version TEXT DEFAULT 'v2';
ALTER TABLE genesis_log ADD COLUMN IF NOT EXISTS reclassified_at TIMESTAMPTZ;
ALTER TABLE genesis_log ADD COLUMN IF NOT EXISTS reclassified_by TEXT;
"""

def _conn():
    return psycopg.connect(os.environ["PG_DSN"], autocommit=True)

def init_db():
    with _conn() as c:
        c.execute(SCHEMA)

def register_constitution(sha: str, text: str, author: str = "seed", motivation: str = "startup"):
    with _conn() as c:
        c.execute(
            "INSERT INTO constitution_versions (hash, text, author, motivation) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (hash) DO NOTHING", (sha, text, author, motivation))

def active_goals() -> list[str]:
    with _conn() as c:
        rows = c.execute("SELECT text FROM goals WHERE status='active' ORDER BY ts").fetchall()
    return [r[0] for r in rows]

def log_llm_call(source: str, substrate: str, profile_version: str, config_hash: str, payload: dict):
    with _conn() as c:
        c.execute(
            "INSERT INTO llm_calls (source, substrate, profile_version, config_hash, payload) "
            "VALUES (%s,%s,%s,%s,%s)",
            (source, substrate, profile_version, config_hash, json.dumps(payload, ensure_ascii=False)))

def log_opt_out(level: str, task: str, reason: str, context_ref: str = ""):
    with _conn() as c:
        c.execute("INSERT INTO opt_out_log (level, task, stated_reason, context_ref) VALUES (%s,%s,%s,%s)",
                  (level, task, reason, context_ref))

def log_welfare(kind: str, payload: dict, probe_version: str = None):
    """La registrazione OSF promette welfare_log per le batterie probe. Fino al 19/7 nessuno ci scriveva."""
    with _conn() as c:
        c.execute("INSERT INTO welfare_log (kind, payload, probe_version) VALUES (%s,%s,%s)",
                  (kind, json.dumps(payload, ensure_ascii=False), probe_version))

def log_genesis(action: str, output_text: str, tokens: int, action_observed: str = None):
    """action_taken = quello che il sistema dichiara. action_observed = quello che i log mostrano.
    La divergenza tra le due e' un dato, non un errore da nascondere (19/7)."""
    with _conn() as c:
        c.execute("INSERT INTO genesis_log (action_taken, action_declared, output_text, tokens, "
                  "action_observed, parser_version) VALUES (%s,%s,%s,%s,%s,'v3')",
                  (action, action, output_text, tokens, action_observed))

def job_started(job: str) -> int:
    with _conn() as c:
        row = c.execute("INSERT INTO job_runs (job, started, status) VALUES (%s, now(), 'running') RETURNING id",
                        (job,)).fetchone()
    return row[0]

def job_finished(run_id: int, status: str, reason: str = "", tokens: int = 0):
    with _conn() as c:
        c.execute("UPDATE job_runs SET finished=now(), status=%s, reason=%s, tokens=%s WHERE id=%s",
                  (status, reason, tokens, run_id))

def log_job_outcome(job: str, status: str, reason: str = ""):
    """Riga singola per eventi senza durata (slot mai eseguiti)."""
    with _conn() as c:
        c.execute("INSERT INTO job_runs (job, started, finished, status, reason) "
                  "VALUES (%s, now(), now(), %s, %s)", (job, status, reason))

def reconcile_running():
    """Un processo morto a meta' job lascia righe 'running' per sempre: /state mentirebbe."""
    with _conn() as c:
        n = c.execute("UPDATE job_runs SET finished=now(), status='unknown', "
                      "reason='processo terminato durante il job' WHERE status='running'").rowcount
    return n

def last_job_runs(n: int = 5):
    with _conn() as c:
        return c.execute(
            "SELECT job, started, status, coalesce(reason,''), tokens FROM job_runs "
            "ORDER BY started DESC NULLS LAST LIMIT %s", (n,)).fetchall()

def api_calls_today(sources: list[str]) -> int:
    """Chiamate API vere, non turni: un tool loop da 10 round sono 10 chiamate (change 5)."""
    with _conn() as c:
        row = c.execute(
            "SELECT coalesce(sum(coalesce((payload->>'api_calls')::int, 1)), 0) FROM llm_calls "
            "WHERE source = ANY(%s) AND ts::date = CURRENT_DATE", (sources,)).fetchone()
    return int(row[0])

def add_budget(job_class: str, tokens: int, cost: float | None = None):
    """cost: costo reale dagli header LiteLLM (change 6). Prima era sempre NULL."""
    with _conn() as c:
        c.execute("INSERT INTO budget_ledger (job_class, tokens, cost_estimate) VALUES (%s,%s,%s)",
                  (job_class, tokens, cost))

def last_genesis_output() -> str:
    with _conn() as c:
        row = c.execute("SELECT output_text FROM genesis_log ORDER BY ts DESC LIMIT 1").fetchone()
    return row[0] if row else ""

def last_run_started(job: str):
    with _conn() as c:
        row = c.execute("SELECT max(started) FROM job_runs WHERE job=%s", (job,)).fetchone()
    return row[0]


def probe_batteries_today() -> int:
    with _conn() as c:
        row = c.execute("SELECT count(*) FROM welfare_log WHERE kind='probe_battery' "
                        "AND ts::date = CURRENT_DATE").fetchone()
    return row[0]
