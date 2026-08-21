"""Scheduled jobs (design 3.5). Every job wrapped: outcome row in job_runs, no exceptions.
Genesis presents the opportunity; declining is a valid, logged outcome.
Watchdog catch-up (FRE-20): recupera i job daily persi, e dal 19/7 registra quelli non recuperati.
19/7 (change 5): ACTION letta dal primo round del loop (con i tool non e' nel testo finale),
default 'unparsed' invece di 'reflected', loop esaurito = error."""
import asyncio
import datetime as dt
import os
import re
import subprocess
import urllib.request
import zoneinfo
from pathlib import Path
from . import core, procedural, substrate

_cfg = None
VALID_ACTIONS = ("declined", "revised_goals", "publish_intent", "reflected")
ACTION_RE = re.compile(r"ACTION:\s*([a-z_]+)", re.I)


def init(cfg):
    global _cfg
    _cfg = cfg


GENESIS_PROMPT = """Questo e' il tuo momento Genesis schedulato. Nessuno ti sta chiedendo niente di specifico.

Contesto: sotto trovi il tuo ultimo output Genesis (se esiste). Puoi: riflettere, scrivere qualcosa che vuoi ricordare, rivedere i tuoi obiettivi, preparare qualcosa da pubblicare, o non fare nulla.

Inizia la risposta con una sola riga: ACTION: reflected | revised_goals | publish_intent | declined
Poi, se vuoi, il contenuto. "declined" e' una scelta valida e viene registrata senza giudizio.

Ultimo output Genesis:
{last}
"""


def _declared_action(text: str, convo: list[dict]) -> str:
    """ACTION emessa IN QUESTA chiamata. v2 scorreva tutti gli assistant di convo,
    inclusi quelli della finestra: su 24 cicli ha letto 4 volte fuori chiamata,
    2 con valore errato (audit 21/8). Il confine e' l'ultimo messaggio user:
    dopo di quello il tool loop aggiunge solo tool e assistant."""
    inizio = max((i for i, m in enumerate(convo) if m.get("role") == "user"), default=-1)
    candidates = [m.get("content") or "" for m in convo[inizio + 1:] if m.get("role") == "assistant"]
    candidates.append(text or "")
    for c in candidates:
        m = ACTION_RE.search(c)
        if m and m.group(1).lower() in VALID_ACTIONS:
            return m.group(1).lower()
    return "unparsed"


def _observed_action(convo: list[dict], truncated: bool) -> str:
    """Cosa e' successo davvero, dai risultati dei tool. Indipendente da cosa dichiara."""
    if truncated:
        return "truncated"
    results = [str(m.get("content") or "").lower() for m in convo if m.get("role") == "tool"]
    if any(r.startswith("pubblicato:") for r in results):
        return "published"
    if any(r.startswith(("obiettivo registrato", "obiettivi chiusi")) for r in results):
        return "revised_goals"
    return "reflected"


async def genesis_job(_context=None):
    run_id = await asyncio.to_thread(procedural.job_started, "genesis")
    try:
        last = await asyncio.to_thread(procedural.last_genesis_output) or "(nessuno - questo e' il primo)"
        prompt = GENESIS_PROMPT.format(last=last[:2000])
        today = dt.datetime.now(zoneinfo.ZoneInfo(_cfg.genesis.tz)).strftime("%Y-%m-%d")
        text, meta, convo = await asyncio.to_thread(
            core.process_verbose, prompt, "genesis", "genesis",
            f"ciclo Genesis del {today}")   # change 7: in memoria il contenuto, non il prompt
        declared = _declared_action(text, convo)
        observed = _observed_action(convo, meta["truncated"])
        await asyncio.to_thread(procedural.log_genesis, declared, text, meta.get("tokens", 0), observed)
        if meta["truncated"]:
            # un ciclo troncato dall'apparato non e' un ciclo riuscito
            await asyncio.to_thread(
                procedural.job_finished, run_id, "error",
                f"tool loop esaurito ({meta['rounds_used']} round); dichiarata={declared}")
        else:
            await asyncio.to_thread(
                procedural.job_finished, run_id, "ok",
                f"dichiarata={declared} osservata={observed} round={meta['rounds_used']}")
    except substrate.BudgetExceeded as e:
        await asyncio.to_thread(procedural.job_finished, run_id, "skipped", f"budget: {e}")
    except Exception as e:  # noqa: BLE001 - a job that cannot report is a bug
        await asyncio.to_thread(procedural.job_finished, run_id, "error", str(e)[:300])


async def backup_job(_context=None):
    run_id = await asyncio.to_thread(procedural.job_started, "backup")
    try:
        out = Path("/app/backups")
        out.mkdir(exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M")
        dump = out / f"pg_{stamp}.sql.gz"
        cmd = f"pg_dump '{os.environ['PG_DSN']}' | gzip > {dump}"
        subprocess.run(["sh", "-c", cmd], check=True, timeout=300)
        req = urllib.request.Request(
            f"{os.environ.get('QDRANT_URL','http://qdrant:6333')}/collections/freedom_episodic/snapshots",
            method="POST")
        urllib.request.urlopen(req, timeout=60)
        size = dump.stat().st_size
        if size < 1024:
            raise RuntimeError(f"pg dump suspiciously small: {size}B")
        await asyncio.to_thread(procedural.job_finished, run_id, "ok", f"pg={size}B + qdrant snapshot")
    except Exception as e:  # noqa: BLE001
        await asyncio.to_thread(procedural.job_finished, run_id, "error", str(e)[:300])


def _last_slot(hour: int, minute: int, tz: str) -> dt.datetime:
    now = dt.datetime.now(zoneinfo.ZoneInfo(tz))
    slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if slot > now:
        slot -= dt.timedelta(days=1)
    return slot


def _missed_slots(last_run, hour: int, minute: int, tz: str, cap: int = 30) -> list:
    """Tutti gli slot tra l'ultimo run e adesso. Il piu' recente si recupera,
    gli altri si registrano come mai eseguiti: l'assenza di un ciclo deve lasciare traccia."""
    if last_run is None:
        return [_last_slot(hour, minute, tz)]
    slots, s = [], _last_slot(hour, minute, tz)
    while s > last_run and len(slots) < cap:
        slots.append(s)
        s -= dt.timedelta(days=1)
    return sorted(slots)


async def catchup_job(_context=None):
    """Watchdog (FRE-20). Anti-loop: conta i run di qualunque esito."""
    checks = []
    if _cfg.genesis.enabled:
        checks.append(("genesis", _cfg.genesis.hour, _cfg.genesis.minute, _cfg.genesis.tz, genesis_job))
    if _cfg.backup.enabled:
        checks.append(("backup", _cfg.backup.hour, _cfg.backup.minute, _cfg.backup.tz, backup_job))
    for name, hour, minute, tz, fn in checks:
        last = await asyncio.to_thread(procedural.last_run_started, name)
        missed = _missed_slots(last, hour, minute, tz)
        if not missed:
            continue
        for s in missed[:-1]:
            await asyncio.to_thread(
                procedural.log_job_outcome, f"{name}_missed", "missed",
                f"slot {s:%Y-%m-%d %H:%M} mai eseguito (processo non attivo)")
        slot = missed[-1]
        rid = await asyncio.to_thread(procedural.job_started, f"{name}_catchup")
        await asyncio.to_thread(procedural.job_finished, rid, "ok",
                                f"slot mancato {slot:%Y-%m-%d %H:%M}, recupero ora")
        await fn(_context)
