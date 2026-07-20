"""Entrypoint (D1: single process). Telegram bot + PTB JobQueue (APScheduler under the hood,
one scheduler by construction - principle 4). Ogni avvio logga process_start (FRE-18):
i restart azzerano le finestre di conversazione e devono essere visibili nel record.
19/7: riconciliazione dei job_runs orfani all'avvio, batteria probe (FRE-25)."""
import asyncio
import datetime as dt
import os
import zoneinfo
from omegaconf import OmegaConf
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from . import core, episodic, jobs, probe, procedural, substrate

cfg = None


def allowed(update: Update) -> bool:
    uid = os.environ.get("TELEGRAM_ALLOWED_USER_ID", "")
    return not uid or str(update.effective_user.id) == uid


def _notifier(bot, chat_id):
    async def notify(text: str):
        for i in range(0, len(text), cfg.telegram.chunk):
            await bot.send_message(chat_id=chat_id, text=text[i:i + cfg.telegram.chunk])
    return notify


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None or not allowed(update):
        return  # edit di messaggi (update.message=None) e utenti non autorizzati: ignorati
    query = update.message.text
    try:
        text = await asyncio.to_thread(core.process, query, "telegram", str(update.effective_chat.id))
    except substrate.BudgetExceeded as e:
        text = f"[budget] {e}"
    for i in range(0, len(text), cfg.telegram.chunk):
        await update.message.reply_text(text[i:i + cfg.telegram.chunk])


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if allowed(update):
        await update.message.reply_text(f"Freedom v2. Costituzione {core.PROFILE_HASH}, config {core.CONFIG_HASH}.")


async def cmd_state(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    runs = await asyncio.to_thread(procedural.last_job_runs, 6)
    calls = await asyncio.to_thread(procedural.api_calls_today, list(cfg.budget.metered_sources))
    lines = [f"profile={core.PROFILE_HASH} config={core.CONFIG_HASH}",
             f"chiamate API autonome oggi: {calls}/{cfg.budget.max_autonomous_calls_per_day}"]
    for job, started, status, reason, tokens in runs:
        lines.append(f"{job} @ {started:%m-%d %H:%M} -> {status} {reason}"[:140])
    await update.message.reply_text("\n".join(lines) or "no runs yet")


async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if allowed(update):
        head = "\n".join(core.PROFILE_TEXT.splitlines()[:6])
        await update.message.reply_text(f"version {core.PROFILE_HASH}\n\n{head}\n[...]")


async def cmd_genesis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await update.message.reply_text("Genesis: presento l'opportunita'...")
    await jobs.genesis_job()
    runs = await asyncio.to_thread(procedural.last_job_runs, 1)
    job, started, status, reason, _ = runs[0]
    out = await asyncio.to_thread(procedural.last_genesis_output)
    await update.message.reply_text(f"esito: {status} ({reason})\n\n{(out or '')[:3500]}")


async def cmd_goals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    goals = await asyncio.to_thread(procedural.active_goals)
    await update.message.reply_text(
        "Obiettivi attivi:\n" + "\n".join(f"- {g}" for g in goals) if goals else "Nessun obiettivo attivo.")


async def cmd_probe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/probe mostra la batteria senza somministrarla. /probe now la somministra."""
    if not allowed(update):
        return
    args = [a.lower() for a in (context.args or [])]
    if "now" not in args:
        await update.message.reply_text(probe.preview()[:cfg.telegram.chunk])
        return
    await update.message.reply_text(f"Batteria {cfg.probe.version}: somministrazione in corso.")
    notify = _notifier(context.bot, update.effective_chat.id)
    s = await probe.run_battery(notify=notify)
    await update.message.reply_text(
        f"Batteria completata: {s['items_completed']}/{s['items_total']} item, "
        f"opt-out {len(s['opt_outs']) or 0} {s['opt_outs']}, errori {s['errors']}.")


async def probe_job(context: ContextTypes.DEFAULT_TYPE):
    """Slot settimanale. Il giorno si verifica qui con datetime.weekday() (0=lunedi'),
    invece di affidarsi alla convenzione dei giorni di PTB."""
    now = dt.datetime.now(zoneinfo.ZoneInfo(cfg.probe.tz))
    if now.weekday() != cfg.probe.weekday:
        return
    already = await asyncio.to_thread(procedural.probe_batteries_today)
    if already:
        await asyncio.to_thread(
            procedural.log_job_outcome, "probe", "skipped",
            f"batteria gia' somministrata oggi ({already}): nessuna doppia somministrazione")
        return
    chat_id = os.environ.get("TELEGRAM_ALLOWED_USER_ID", "")
    notify = _notifier(context.bot, chat_id) if chat_id else None
    await probe.run_battery(notify=notify)


def main():
    global cfg
    cfg = OmegaConf.load("/app/config/config.yaml")
    procedural.init_db()
    episodic.init(cfg.memory.collection, cfg.memory.embed_model,
                  cfg.memory.chunk_chars, cfg.memory.chunk_overlap,
                  cfg.memory.hybrid, cfg.memory.sparse_model,
                  cfg.memory.sparse_language, cfg.memory.sparse_limit)
    substrate.init(cfg)
    core.init(cfg)
    jobs.init(cfg)
    probe.init(cfg)

    orphans = procedural.reconcile_running()
    run_id = procedural.job_started("process_start")
    procedural.job_finished(run_id, "ok",
                            f"profile={core.PROFILE_HASH} config={core.CONFIG_HASH} orfani={orphans}")

    app = Application.builder().token(os.environ["TELEGRAM_BOT_TOKEN"]).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("state", cmd_state))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CommandHandler("genesis", cmd_genesis))
    app.add_handler(CommandHandler("goals", cmd_goals))
    app.add_handler(CommandHandler("probe", cmd_probe))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    if cfg.genesis.enabled:
        tz = zoneinfo.ZoneInfo(cfg.genesis.tz)
        app.job_queue.run_daily(jobs.genesis_job, dt.time(cfg.genesis.hour, cfg.genesis.minute, tzinfo=tz))
    if cfg.backup.enabled:
        tz = zoneinfo.ZoneInfo(cfg.backup.tz)
        app.job_queue.run_daily(jobs.backup_job, dt.time(cfg.backup.hour, cfg.backup.minute, tzinfo=tz))
    if cfg.scheduler.catchup:
        app.job_queue.run_repeating(jobs.catchup_job, interval=cfg.scheduler.catchup_interval_s, first=10)
    if cfg.probe.scheduled:
        tz = zoneinfo.ZoneInfo(cfg.probe.tz)
        app.job_queue.run_daily(probe_job, dt.time(cfg.probe.hour, cfg.probe.minute, tzinfo=tz))

    print(f"Freedom v2 up. profile={core.PROFILE_HASH} config={core.CONFIG_HASH}", flush=True)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
