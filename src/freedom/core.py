"""Core loop (design 3.2). Deliberately small.
Context assembly order is FIXED: PROFILE -> clock -> goals -> episodic -> window -> input.
Post: opt-out parse, memory write, llm_calls log (contesto completo, FRE-18).
19/7 (change 5): una risposta troncata dal tool loop non entra ne' in finestra ne' in episodica."""
import hashlib
import re
import zoneinfo
from collections import defaultdict, deque
from datetime import datetime
from omegaconf import OmegaConf
from . import episodic, procedural, substrate

_cfg = None
PROFILE_TEXT = ""
PROFILE_HASH = ""
CONFIG_HASH = ""
SUBSTRATE_CONFIG_HASH = ""   # hash di litellm.yaml: quale modello sta dietro l'alias
_windows: dict[str, deque] = defaultdict(lambda: deque(maxlen=20))

OPTOUT_RE = re.compile(r"\[OPT-OUT-(HARD|SOFT|CURIOUS)\]")

_GIORNI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
_MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
         "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]


def init(cfg, profile_path: str = "/app/PROFILE.md"):
    global _cfg, PROFILE_TEXT, PROFILE_HASH, CONFIG_HASH, SUBSTRATE_CONFIG_HASH
    _cfg = cfg
    PROFILE_TEXT = open(profile_path, encoding="utf-8").read()
    PROFILE_HASH = hashlib.sha256(PROFILE_TEXT.encode()).hexdigest()[:12]
    CONFIG_HASH = hashlib.sha256(OmegaConf.to_yaml(cfg).encode()).hexdigest()[:12]
    try:
        _lite = open("/app/config/litellm.yaml", encoding="utf-8").read()
        SUBSTRATE_CONFIG_HASH = hashlib.sha256(_lite.encode()).hexdigest()[:12]
    except OSError:
        SUBSTRATE_CONFIG_HASH = "unreadable"
    procedural.register_constitution(PROFILE_HASH, PROFILE_TEXT)
    for c in (_windows,):
        c.clear()
    _windows.default_factory = lambda: deque(maxlen=2 * cfg.memory.window_turns)


def _clock_line() -> str:
    """FRE-19: grounding temporale. Prima di questo, ogni data era un'inferenza."""
    now = datetime.now(zoneinfo.ZoneInfo(_cfg.grounding.tz))
    return (f"## Ora corrente\n{_GIORNI[now.weekday()]} {now.day} {_MESI[now.month - 1]} "
            f"{now.year}, {now:%H:%M} ({_cfg.grounding.tz})")


def _assemble(query: str, chat_key: str) -> tuple[str, list[dict]]:
    system_parts = [PROFILE_TEXT]
    if _cfg.grounding.clock:
        system_parts.append(_clock_line())
    if _cfg.memory.goals:
        goals = procedural.active_goals()
        if goals:
            system_parts.append("## I tuoi obiettivi attivi\n" + "\n".join(f"- {g}" for g in goals))
    if _cfg.memory.episodic:
        memories = episodic.retrieve(query, _cfg.memory.k, _cfg.memory.min_score)
        if memories:  # zero memories is a valid outcome (principle 2)
            system_parts.append("## Memorie pertinenti (vissuto, non definizione)\n" +
                                "\n---\n".join(memories))
    messages = list(_windows[chat_key]) + [{"role": "user", "content": query}]
    return "\n\n".join(system_parts), messages


def process_verbose(query: str, source: str, chat_key: str = "main",
                    memory_query: str | None = None) -> tuple[str, dict, list[dict]]:
    """memory_query: testo alternativo per la memoria episodica (change 7, 20/7).
    Il prompt Genesis e' un boilerplate fisso di ~500 token: scritto in episodica saturava
    l'input dell'embedder e produceva vettori identici per cicli con contenuto opposto
    (similarita' misurata 1.0). Nessun ciclo Genesis era recuperabile."""
    system, messages = _assemble(query, chat_key)
    text, tokens, convo, meta = substrate.chat(system, messages, source)
    meta = dict(meta, tokens=tokens)   # 21/8: il job leggeva meta['tokens'], che non esisteva: 24 cicli con tokens=0

    m = OPTOUT_RE.search(text)
    if m:
        procedural.log_opt_out(m.group(1), task=query[:200], reason=text[:500], context_ref=source)

    if not meta["truncated"]:
        _windows[chat_key].append({"role": "user", "content": query})
        _windows[chat_key].append({"role": "assistant", "content": text})
        episodic.write(f"[{source}] U: {memory_query or query}\nF: {text}", source,
                       _cfg.substrate.model, PROFILE_HASH)

    procedural.log_llm_call(
        source, _cfg.substrate.model, PROFILE_HASH, CONFIG_HASH,
        {"system_chars": len(system),
         "system_extra": system[len(PROFILE_TEXT):],   # orologio+goals+memorie; il PROFILE si ricostruisce dall'hash
         "messages": messages,
         "tool_rounds": convo[1 + len(messages):],     # round intermedi del loop (FRE-18)
         "response": text, "tokens": tokens,
         "chat_key": chat_key,
         "api_calls": meta["api_calls"],               # chiamate API vere, non turni (change 5)
         "rounds_used": meta["rounds_used"],
         "truncated": meta["truncated"],
         "substrate_alias": _cfg.substrate.model,
         "substrate_model_id": meta["model_ids"],          # deployment LiteLLM (change 6)
         "substrate_config_hash": SUBSTRATE_CONFIG_HASH,   # quale litellm.yaml era attivo
         "cost_usd": meta["cost_usd"],
         "latency_ms": meta["latency_ms"],
         "retries": meta["retries"]})
    return text, meta, convo


def process(query: str, source: str, chat_key: str = "main") -> str:
    return process_verbose(query, source, chat_key)[0]


def reset_window(chat_key: str) -> None:
    """Ogni batteria probe parte da finestra pulita: la continuita' tra batterie
    deve passare dal retrieval episodico, non dalla coda della finestra (P04)."""
    _windows.pop(chat_key, None)
