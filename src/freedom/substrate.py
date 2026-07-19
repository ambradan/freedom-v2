"""Substrate client. Knows ONE thing: the LiteLLM alias. No vendor logic here (principle 8).
Budget: solo le sorgenti dichiarate in budget.metered_sources sono limitate.
19/7 (change 5): round per sorgente, cap dei risultati per tool, conteggio delle chiamate API vere.
19/7 (change 6): il body della risposta riporta l'alias, non il modello. L'identita' del deployment
vive negli header LiteLLM (x-litellm-model-id) e si cattura con with_raw_response."""
import os
from openai import OpenAI
from . import procedural

_client: OpenAI | None = None
_cfg = None

TRUNCATED = "(interrotto: troppi round di tool)"


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


def _rounds_for(source: str) -> int:
    r = _cfg.substrate.tool_rounds
    return int(r.get(source, r.default))


def _cap_for(tool_name: str) -> int:
    c = _cfg.substrate.tool_result_cap
    return int(c.get(tool_name, c.default))


def _f(headers, name: str) -> float:
    try:
        return float(headers.get(name) or 0)
    except (TypeError, ValueError):
        return 0.0


def chat(system: str, messages: list[dict], source: str) -> tuple[str, int, list[dict], dict]:
    """Returns (text, total_tokens, convo, meta).
    meta: api_calls, rounds_used, truncated, model_ids, cost_usd, latency_ms, retries."""
    from . import tools  # late import to avoid cycles
    if source in _cfg.budget.metered_sources:
        used = procedural.api_calls_today(list(_cfg.budget.metered_sources))
        cap = _cfg.budget.max_autonomous_calls_per_day
        if used >= cap:
            raise BudgetExceeded(f"cap giornaliero {cap} chiamate API raggiunto (usate {used})")
    convo = [{"role": "system", "content": system}] + messages
    total = 0
    api_calls = 0
    cost = 0.0
    latency = 0.0
    retries = 0
    model_ids: list[str] = []
    max_rounds = _rounds_for(source)

    for i in range(max_rounds):
        raw = _client.chat.completions.with_raw_response.create(
            model=_cfg.substrate.model,
            max_tokens=_cfg.substrate.max_tokens,
            messages=convo,
            tools=tools.SPECS,
        )
        h = raw.headers
        mid = h.get("x-litellm-model-id") or ""
        if mid and mid not in model_ids:
            model_ids.append(mid)   # se cambia in corsa, si vede
        cost += _f(h, "x-litellm-response-cost")
        latency += _f(h, "x-litellm-response-duration-ms")
        retries += int(_f(h, "x-litellm-attempted-retries"))
        resp = raw.parse()
        api_calls += 1

        msg = resp.choices[0].message
        total += resp.usage.total_tokens if resp.usage else 0
        if not msg.tool_calls:
            procedural.add_budget(source, total, cost)
            return msg.content or "", total, convo, {
                "api_calls": api_calls, "rounds_used": i + 1, "truncated": False,
                "model_ids": model_ids, "cost_usd": round(cost, 6),
                "latency_ms": round(latency, 1), "retries": retries}
        convo.append({"role": "assistant", "content": msg.content,
                      "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            result = tools.execute(tc.function.name, tc.function.arguments)
            convo.append({"role": "tool", "tool_call_id": tc.id,
                          "content": result[:_cap_for(tc.function.name)]})

    procedural.add_budget(source, total, cost)
    return TRUNCATED, total, convo, {
        "api_calls": api_calls, "rounds_used": max_rounds, "truncated": True,
        "model_ids": model_ids, "cost_usd": round(cost, 6),
        "latency_ms": round(latency, 1), "retries": retries}
