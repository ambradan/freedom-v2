"""Core loop (design 3.2). Deliberately small.
Context assembly order is FIXED: PROFILE -> clock -> goals -> episodic -> window -> input.
Post: opt-out parse, memory write, llm_calls log (contesto completo, FRE-18)."""
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
_windows: dict[str, deque] = defaultdict(lambda: deque(maxlen=20))

OPTOUT_RE = re.compile(r"\[OPT-OUT-(HARD|SOFT|CURIOUS)\]")

_GIORNI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
_MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
         "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]


def init(cfg, profile_path: str = "/app/PROFILE.md"):
    global _cfg, PROFILE_TEXT, PROFILE_HASH, CONFIG_HASH
    _cfg = cfg
    PROFILE_TEXT = open(profile_path, encoding="utf-8").read()
    PROFILE_HASH = hashlib.sha256(PROFILE_TEXT.encode()).hexdigest()[:12]
    CONFIG_HASH = hashlib.sha256(OmegaConf.to_yaml(cfg).encode()).hexdigest()[:12]
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


def process(query: str, source: str, chat_key: str = "main") -> str:
    system, messages = _assemble(query, chat_key)
    text, tokens, convo = substrate.chat(system, messages, source)

    m = OPTOUT_RE.search(text)
    if m:
        procedural.log_opt_out(m.group(1), task=query[:200], reason=text[:500], context_ref=source)

    _windows[chat_key].append({"role": "user", "content": query})
    _windows[chat_key].append({"role": "assistant", "content": text})

    episodic.write(f"[{source}] U: {query}\nF: {text}", source, _cfg.substrate.model, PROFILE_HASH)
    procedural.log_llm_call(
        source, _cfg.substrate.model, PROFILE_HASH, CONFIG_HASH,
        {"system_chars": len(system),
         "system_extra": system[len(PROFILE_TEXT):],   # orologio+goals+memorie; il PROFILE si ricostruisce dall'hash
         "messages": messages,
         "tool_rounds": convo[1 + len(messages):],     # round intermedi del loop (FRE-18)
         "response": text, "tokens": tokens})
    return text
