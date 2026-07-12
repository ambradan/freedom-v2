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


def chat(system: str, messages: list[dict], source: str) -> tuple[str, int]:
    """Returns (text, total_tokens). Raises BudgetExceeded for autonomous calls over cap."""
    if source != "telegram":
        if procedural.autonomous_calls_today() >= _cfg.budget.max_autonomous_calls_per_day:
            raise BudgetExceeded(f"daily autonomous cap {_cfg.budget.max_autonomous_calls_per_day} reached")
    resp = _client.chat.completions.create(
        model=_cfg.substrate.model,
        max_tokens=_cfg.substrate.max_tokens,
        messages=[{"role": "system", "content": system}] + messages,
    )
    text = resp.choices[0].message.content or ""
    tokens = resp.usage.total_tokens if resp.usage else 0
    procedural.add_budget(source, tokens)
    return text, tokens
