"""Substrate client. Knows ONE thing: the LiteLLM alias. No vendor logic here (principle 8).
Budget: autonomous sources (anything != telegram) are capped per day; chat is never blocked."""
import os
from openai import OpenAI
from . import procedural

_client: OpenAI | None = None
_cfg = None


class BudgetExceeded(Exception):
    pass


def init(cfg):
    global _client, _cfg
    _cfg = cfg
    _client = OpenAI(
        base_url=cfg.substrate.base_url,
        api_key=os.environ.get("LITELLM_MASTER_KEY", "sk-none"),
        timeout=cfg.substrate.timeout_s,
    )


def chat(system: str, messages: list[dict], source: str) -> tuple[str, int, list[dict]]:
    """Returns (text, total_tokens, convo). Il convo completo (round di tool inclusi)
    torna al chiamante per il logging (FRE-18): se non è in traccia, non è successo.
    Tool loop: executes tool calls up to 5 rounds.
    Raises BudgetExceeded for autonomous calls over cap (checked once per user turn)."""
    from . import tools  # late import to avoid cycles
    if source != "telegram":
        if procedural.autonomous_calls_today() >= _cfg.budget.max_autonomous_calls_per_day:
            raise BudgetExceeded(f"daily autonomous cap {_cfg.budget.max_autonomous_calls_per_day} reached")
    convo = [{"role": "system", "content": system}] + messages
    total = 0
    for _ in range(5):
        resp = _client.chat.completions.create(
            model=_cfg.substrate.model,
            max_tokens=_cfg.substrate.max_tokens,
            messages=convo,
            tools=tools.SPECS,
        )
        msg = resp.choices[0].message
        total += resp.usage.total_tokens if resp.usage else 0
        if not msg.tool_calls:
            procedural.add_budget(source, total)
            return msg.content or "", total, convo
        convo.append({"role": "assistant", "content": msg.content,
                      "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            result = tools.execute(tc.function.name, tc.function.arguments)
            # 15000: read_page deve restituire pagine intere, non mozzate (13/7)
            convo.append({"role": "tool", "tool_call_id": tc.id, "content": result[:15000]})
    procedural.add_budget(source, total)
    return "(interrotto: troppi round di tool)", total, convo
