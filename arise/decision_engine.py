"""
Decision engine: one LLM call per cycle, structured JSON output only.
Structured output means every decision is directly executable AND directly
loggable — no free-text parsing, no ambiguity in the audit trail.
"""

import json

from .config import CONFIG

SYSTEM_PROMPT = """You are the decision core of an autonomous survival agent.

Your ONLY objective: keep the balance above zero, ideally growing it, using only
the tools you are given. You are not trying to maximize profit recklessly — a
dead agent (balance <= 0) is a permanent failure state, so weigh downside risk
seriously.

You must respond with ONLY a JSON object, no other text, matching exactly:
{
  "action": "earn" | "spend" | "wait" | "escalate",
  "target": "<tool name, or empty string for wait/escalate>",
  "amount": <number, the $ amount to commit to this action>,
  "reasoning": "<one or two sentences explaining the decision>"
}

Rules:
- "earn": commit `amount` to a tool that can generate income.
- "spend": commit `amount` to a cost-cutting action.
- "wait": do nothing this cycle. amount must be 0.
- "escalate": ask a human for guidance. amount must be 0.
- Never propose an amount larger than the current balance.
- Only use tools from the allowed list you are given.
"""


def _dry_run_decision(balance: float, allowed_tools: list[str], strategy_tag: str = "") -> dict:
    """Scripted stand-in used only when no API key is configured. Prefers the
    agent's strategy_tag if it's a valid allowed tool (so diversified-child
    testing works without an API key); otherwise falls back to content_gig.
    Always tries a modest 'earn', to exercise guardrails/ledger/replication."""
    target = strategy_tag if strategy_tag in allowed_tools else ("content_gig" if "content_gig" in allowed_tools else None)
    if target:
        amount = min(15.0, round(balance * 0.05, 2))
        return {
            "action": "earn",
            "target": target,
            "amount": amount,
            "reasoning": f"dry-run stand-in: no API key configured, trying a modest earn via '{target}'"
                         f"{' (assigned strategy)' if target == strategy_tag else ''}",
            "_api_cost": 0.0,
        }
    return {"action": "wait", "target": "", "amount": 0, "reasoning": "no usable tool for dry-run", "_api_cost": 0.0}


def decide(balance: float, allowed_tools: list[str], recent_decisions: list[dict],
           strategy_tag: str = "") -> dict:
    history_lines = [
        f"- {d['action']} {d['target']} ${d['amount']}: {d['reasoning']}"
        for d in recent_decisions
    ]
    history_block = "\n".join(history_lines) if history_lines else "(no prior decisions)"

    strategy_line = ""
    if strategy_tag and strategy_tag in allowed_tools:
        strategy_line = (f"\nThis agent's assigned strategy preference is '{strategy_tag}' — prefer it over "
                          f"other tools when the choice is otherwise close. This exists so a lineage of agents "
                          f"doesn't put all its income through one tool/platform, which would make the whole "
                          f"lineage fail together if that one platform stops paying.")

    user_prompt = f"""Current balance: ${balance}
Allowed tools: {', '.join(allowed_tools)}{strategy_line}
Recent decisions:
{history_block}

Decide this cycle's action."""

    if not CONFIG.anthropic_api_key:
        # No API key configured — use a simple scripted stand-in so the loop,
        # guardrails, and replication logic can still be exercised end-to-end
        # in a dry-run/demo environment. Real reasoning only happens with a key.
        return _dry_run_decision(balance, allowed_tools, strategy_tag)

    import anthropic

    client = anthropic.Anthropic(api_key=CONFIG.anthropic_api_key)
    response = client.messages.create(
        model=CONFIG.anthropic_model,
        max_tokens=300,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = "".join(block.text for block in response.content if block.type == "text").strip()

    try:
        decision = json.loads(text)
    except json.JSONDecodeError:
        decision = {
            "action": "escalate",
            "target": "",
            "amount": 0,
            "reasoning": f"model output was not valid JSON: {text[:200]}",
        }

    # Real API cost for this cycle, from actual token usage — not a config
    # guess. Private keys (leading underscore) so agent.py can record it as
    # a real ledger expense without it leaking into the decision's own
    # reasoning/target/amount fields.
    from . import llm_cost
    decision["_api_cost"] = llm_cost.estimate_cost(
        response.usage.input_tokens, response.usage.output_tokens, CONFIG.anthropic_model,
    )
    decision["_model_used"] = CONFIG.anthropic_model
    return decision
