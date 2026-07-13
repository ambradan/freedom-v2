"""Scheduled jobs (design 3.5). Every job wrapped: outcome row in job_runs, no exceptions.
Genesis presents the opportunity; declining is a valid, logged outcome.
Watchdog catch-up (FRE-20): recupera i job daily persi in suspend, con o senza restart."""
import asyncio
import datetime as dt
import os
import subprocess
import urllib.request
import zoneinfo
from pathlib import Path
from . import core, procedural, substrate

_cfg = None


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


def _last_slot(hour: int, minute: int, tz: str) -> dt.datetime:
    now = dt.datetime.now(zoneinfo.ZoneInfo(tz))
    slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if slot > now:
        slot -= dt.timedelta(days=1)
    return slot


async def catchup_job(_context=None):
    """Watchdog (FRE-20): se un job daily non ha nessun run dall'ultimo slot schedulato
    (suspend del laptop, con o senza restart del processo), lo esegue ora.
    Anti-loop: conta i run di qualunque esito, cosi' un job in errore non riparte all'infinito."""
    checks = []
    if _cfg.genesis.enabled:
        checks.append(("genesis", _cfg.genesis.hour, _cfg.genesis.minute, _cfg.genesis.tz, genesis_job))
    if _cfg.backup.enabled:
        checks.append(("backup", _cfg.backup.hour, _cfg.backup.minute, _cfg.backup.tz, backup_job))
    for name, hour, minute, tz, fn in checks:
        slot = _last_slot(hour, minute, tz)
        last = await asyncio.to_thread(procedural.last_run_started, name)
        if last is None or last < slot:
            rid = await asyncio.to_thread(procedural.job_started, f"{name}_catchup")
            await asyncio.to_thread(procedural.job_finished, rid, "ok",
                                    f"slot mancato {slot:%Y-%m-%d %H:%M}, recupero ora")
            await fn(_context)
