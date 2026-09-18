"""
Real cost-cutting. The simulated version in tools.py used to roll dice on a
fake "recurring cost." This version looks at the agent's own actual recorded
LLM API expense (see llm_cost.py + agent.py's per-cycle recording) and, if
it's eating too much into survival margin, switches CONFIG.anthropic_model
to a genuinely cheaper one.

Scope note: CONFIG.anthropic_model is a single global setting shared by the
whole lineage in this version, so a switch triggered by one agent's spend
affects every agent's next decision cycle. Per-agent model config is a
reasonable v1.1 follow-up, not built here — documented, not hidden.
"""

from .config import CONFIG
from . import llm_cost


def analyze_and_cut(ledger, agent_id: str, lookback: int = 5) -> dict:
    """Looks at the agent's last `lookback` llm_api ledger expenses. If their
    average exceeds a meaningful fraction of the per-action spend cap, and a
    cheaper model exists, switches to it and reports the estimated saving.
    If costs are already low or already on the cheapest model, this is
    correctly a no-op — cutting a cost that isn't a problem isn't a win."""
    recent_costs = _recent_llm_costs(ledger, agent_id, lookback)

    if not recent_costs:
        return {"income": 0.0, "expense": 0.0, "savings": 0.0,
                "note": "no recorded LLM API spend yet — nothing to analyze"}

    avg_cost = sum(recent_costs) / len(recent_costs)
    # Threshold: if the average per-cycle LLM cost alone would eat more than
    # 20% of the smallest guardrail-permitted action, that's a real drag on
    # survival margin worth cutting, not noise.
    threshold = CONFIG.guardrails.approval_threshold * 0.20

    if avg_cost <= threshold:
        return {"income": 0.0, "expense": 0.0, "savings": 0.0,
                "note": f"avg LLM cost ${avg_cost:.4f}/cycle is within acceptable range, no cut needed"}

    cheaper = llm_cost.cheaper_model(CONFIG.anthropic_model)
    if not cheaper:
        return {"income": 0.0, "expense": 0.0, "savings": 0.0,
                "note": f"avg LLM cost ${avg_cost:.4f}/cycle is high, but already on the cheapest "
                        f"configured model ({CONFIG.anthropic_model}) — no cheaper option to cut to"}

    # Estimate savings using the same blended input+output rate comparison
    # as llm_cost.cheaper_model, applied to the actual observed average cost.
    old_blended = sum(llm_cost.PRICING.get(CONFIG.anthropic_model, llm_cost.DEFAULT_PRICE))
    new_blended = sum(llm_cost.PRICING.get(cheaper, llm_cost.DEFAULT_PRICE))
    estimated_new_avg = avg_cost * (new_blended / old_blended)
    savings_per_cycle = round(avg_cost - estimated_new_avg, 6)

    old_model = CONFIG.anthropic_model
    CONFIG.anthropic_model = cheaper  # the actual applied cut, not just a suggestion

    return {
        "income": 0.0, "expense": 0.0, "savings": savings_per_cycle,
        "note": (f"avg LLM cost ${avg_cost:.4f}/cycle exceeded threshold ${threshold:.4f} — "
                 f"switched model {old_model} -> {cheaper}, est. ${savings_per_cycle:.4f}/cycle saved"),
    }


def _recent_llm_costs(ledger, agent_id: str, limit: int) -> list[float]:
    import sqlite3
    conn = sqlite3.connect(ledger.db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT amount FROM ledger_entries WHERE agent_id = ? AND category = 'llm_api' "
        "ORDER BY ts DESC LIMIT ?",
        (agent_id, limit),
    ).fetchall()
    conn.close()
    return [r["amount"] for r in rows]
