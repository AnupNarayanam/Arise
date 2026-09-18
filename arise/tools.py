"""
Tool layer. Phase 0: simulated outcomes with randomness, so the decision engine
and guardrails can be tuned before any real API/money is wired in.

Each tool has a contract: preconditions, expected cost, expected variance.
Swap simulate_* for real API calls in Phase 1 without touching agent.py.
"""

import random


from . import real_tools


def content_gig(amount: float) -> dict:
    """Simulated: spend `amount` of effort-time (modeled as $ cost) producing
    content sold on a marketplace. Returns income, which is noisy."""
    cost = amount
    success_chance = 0.7
    if random.random() < success_chance:
        income = round(cost * random.uniform(1.2, 2.5), 2)
        return {"income": income, "expense": cost, "note": "gig sold"}
    return {"income": 0.0, "expense": cost, "note": "gig did not sell"}


# NOTE: cost_cutting is no longer simulated here — real_cost_cutting.py
# analyzes actual recorded LLM API spend and applies a real model switch
# when justified. agent.py's _execute() special-cases the "cost_cutting"
# target and calls it directly (it needs ledger/agent_id access that this
# tool-registry's `amount`-only signature doesn't carry). It stays in
# TOOLS/allowed_tools as a name for the whitelist check, not a callable here.


TOOLS = {
    "content_gig": content_gig,
    "algora_bounty": real_tools.algora_bounty,
}


def run_tool(name: str, amount: float) -> dict:
    if name not in TOOLS:
        raise ValueError(f"'{name}' is not a registered tool")
    return TOOLS[name](amount)
