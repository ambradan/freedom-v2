"""Scheduled jobs (design 3.5). Every job wrapped: outcome row in job_runs, no exceptions.
Genesis presents the opportunity; declining is a valid, logged outcome."""
import asyncio
import datetime as dt
import os
import subprocess
import urllib.request
from pathlib import Path
from . import core, procedural, substrate

GENESIS_PROMPT = """Questo e' il tuo momento Genesis schedulato. Nessuno ti sta chiedendo niente di specifico.

Contesto: sotto trovi il tuo ultimo output Genesis (se esiste). Puoi: riflettere, scrivere qualcosa che vuoi ricordare, rivedere i tuoi obiettivi, preparare qualcosa da pubblicare, o non fare nulla.

Inizia la risposta con una sola riga: ACTION: reflected | revised_goals | publish_intent | declined
Poi, se vuoi, il contenuto. "declined" e' una scelta valida e viene registrata senza giudizio.

Ultimo output Genesis:
{last}
"""


async def genesis_job(_context=None):
    run_id = await asyncio.to_thread(procedural.job_started, "genesis")
    try:
        last = await asyncio.to_thread(procedural.last_genesis_output) or "(nessuno - questo e' il primo)"
        prompt = GENESIS_PROMPT.format(last=last[:2000])
        text = await asyncio.to_thread(core.process, prompt, "genesis", "genesis")
        action = "reflected"
        first = text.splitlines()[0].strip().lower() if text.strip() else ""
        for a in ("declined", "revised_goals", "publish_intent", "reflected"):
            if a in first:
                action = a
                break
        await asyncio.to_thread(procedural.log_genesis, action, text, 0)
        await asyncio.to_thread(procedural.job_finished, run_id, "ok", action)
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
        # qdrant snapshot (stays on qdrant volume; host backups dir holds pg dumps)
        req = urllib.request.Request(
            f"{os.environ.get('QDRANT_URL','http://qdrant:6333')}/collections/freedom_episodic/snapshots",
            method="POST")
        urllib.request.urlopen(req, timeout=60)
        size = dump.stat().st_size
        if size < 1024:
            raise RuntimeError(f"pg dump suspiciously small: {size}B")
        await asyncio.to_thread(procedural.job_finished, run_id, "ok", f"pg={size}B + qdrant snapshot")
        # TODO offsite: rclone copy when backup.rclone_remote is set (milestone 2)
    except Exception as e:  # noqa: BLE001
        await asyncio.to_thread(procedural.job_finished, run_id, "error", str(e)[:300])
