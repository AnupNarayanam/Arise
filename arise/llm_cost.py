"""
Real LLM API cost tracking. Every decision cycle that hits the actual
Anthropic API has a real $ cost — this module turns token usage into that
cost so it can be recorded as a genuine ledger expense, instead of the
config-file guess (`fixed_cost_per_cycle`) the guardrails used before.

PRICING is per published rates as of this codebase's writing and WILL drift —
verify against https://docs.claude.com (or the current pricing page) before
relying on this for a real budget decision. Treat it as directionally
correct, not exact to the cent.
"""

# $ per million tokens, (input, output)
PRICING = {
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-5": (15.00, 75.00),
    "claude-haiku-4-5-20251001": (0.80, 4.00),
    "claude-fable-5-1": (3.00, 15.00),
}

DEFAULT_PRICE = PRICING["claude-sonnet-4-6"]  # fallback if model string isn't in the table


def estimate_cost(input_tokens: int, output_tokens: int, model: str) -> float:
    in_price, out_price = PRICING.get(model, DEFAULT_PRICE)
    cost = (input_tokens / 1_000_000) * in_price + (output_tokens / 1_000_000) * out_price
    return round(cost, 6)


def cheaper_model(current_model: str) -> str | None:
    """Returns a genuinely cheaper model than the current one, or None if
    already on the cheapest option in the table. Compares blended cost
    (input+output) rather than just one side."""
    def blended(m):
        i, o = PRICING.get(m, DEFAULT_PRICE)
        return i + o

    current_cost = blended(current_model)
    cheaper = [m for m in PRICING if blended(m) < current_cost]
    if not cheaper:
        return None
    return min(cheaper, key=blended)
