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


def _dry_run_decision(balance: float, allowed_tools: list[str]) -> dict:
    """Scripted stand-in used only when no API key is configured. Always
    tries a modest 'earn' via the first allowed tool, so Phase 0 testing can
    exercise guardrails, ledger, and replication without hitting the API."""
    if "content_gig" in allowed_tools:
        amount = min(15.0, round(balance * 0.05, 2))
        return {
            "action": "earn",
            "target": "content_gig",
            "amount": amount,
            "reasoning": "dry-run stand-in: no API key configured, trying a modest earn action",
        }
    return {"action": "wait", "target": "", "amount": 0, "reasoning": "no usable tool for dry-run"}


def decide(balance: float, allowed_tools: list[str], recent_decisions: list[dict]) -> dict:
    history_lines = [
        f"- {d['action']} {d['target']} ${d['amount']}: {d['reasoning']}"
        for d in recent_decisions
    ]
    history_block = "\n".join(history_lines) if history_lines else "(no prior decisions)"

    user_prompt = f"""Current balance: ${balance}
Allowed tools: {', '.join(allowed_tools)}
Recent decisions:
{history_block}

Decide this cycle's action."""

    if not CONFIG.anthropic_api_key:
        # No API key configured — use a simple scripted stand-in so the loop,
        # guardrails, and replication logic can still be exercised end-to-end
        # in a dry-run/demo environment. Real reasoning only happens with a key.
        return _dry_run_decision(balance, allowed_tools)

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
    return decision
