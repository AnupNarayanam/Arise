# Arise

An autonomous agent seeded with $500 whose only mandate is to stay alive:
earn more than it spends, or shut down when balance hits zero. When it earns
enough, it splits into an independent child agent — a lineage of autonomous
economic agents, each with its own ledger and its own decisions.

See `ROADMAP.md` for the full version plan and `arise-spec.md` for the
architecture doc. **Current version: v0.4 (Algora claim + payout tracking) — built and tested.**

## Architecture

```
SENSE (balance, history) -> THINK (LLM decision) -> ACT (tool call)
   -> RECORD (ledger + reasoning trace) -> CHECK (dead? split? near floor?)
```

- `ledger.py` — append-only SQLite ledger. Balance is always *derived* by
  summing entries, never stored as a mutable field.
- `decision_engine.py` — one Claude API call per cycle, structured JSON
  output only. Falls back to a scripted stand-in with no `ANTHROPIC_API_KEY`,
  so the rest of the system is testable without one.
- `guardrails.py` — every action is checked *before* it executes: tool
  whitelist, per-action/per-cycle/per-day caps, human-approval threshold,
  and a **survival floor** — no action may drop balance below
  `max(min_safe_balance_floor, balance × min_safe_balance_fraction)`.
- `notifier.py` — the actual safety net. Logs every escalate/approval/near-floor/
  death event locally always; also pushes to Telegram if `TELEGRAM_BOT_TOKEN`
  and `TELEGRAM_CHAT_ID` are set. Degrades cleanly with neither.
- `shipping.py` — real clone, real patch apply (validated first), real test
  execution (auto-detects pytest/npm/go/cargo). PR opening now goes live
  (v0.4) only when a verified Algora claim + `PR_LIVE_MODE` + `GITHUB_TOKEN`
  are all set simultaneously.
- `completion.py` — patch-drafting pipeline: real GitHub issue fetch, real
  Claude patch draft, honest `requires_human=True` on every result all the
  way through v0.4 (a passing quality gate and a live PR still aren't
  income until a real payout is confirmed).
- `real_tools.py` — v0.4: real `claim_bounty()` and `check_payout()` against
  Algora (dry-run-safe by default), plus the read-only wallet balance check.
- `real_tools.py` — v0.3 income adapter: Algora bounty listing (dry-run by
  default, `ALGORA_LIVE_MODE=true` to go live), wired to `completion.py` for
  real bounties, plus a read-only wallet balance check (never touches a
  private key).
- `tools.py` — simulated income/cost-cutting tools plus the `algora_bounty`
  wrapper. Simulated tools remain for guardrail/replication testing.
- `agent.py` — one agent's SENSE→THINK→ACT→RECORD→CHECK cycle.
- `orchestrator.py` — runs every live agent each cycle, is the only thing
  allowed to spawn a child on split, owns the cascading kill switch.
- `export.py` — dumps the ledger to `arise_data.json`: balance-over-time,
  income/expense category breakdown, and days-survived per agent.
- `dashboard.html` — static, read-only, no backend. Balance-over-time chart,
  category breakdown chart, a real lineage tree (not a flat table), and a
  searchable/status-filterable reasoning trace log.
- `postmortem.py` — auto-generates a report for any dead agent: exactly when
  balance crossed zero, the decisions immediately preceding death, and
  whether a guardrail caught it but ran out of safe options, missed it
  outright, or the cause was a market-level failure no code could prevent.

## Running it

```bash
pip install -r requirements.txt

# Dry run — no API key needed, scripted stand-in decisions each cycle,
# still exercises the real ledger/guardrails/replication/notifier logic.
python -m arise.main --cycles 20

# With real reasoning:
export ANTHROPIC_API_KEY=sk-...
python -m arise.main --cycles 20

# Optional: real Telegram alerts for escalate/approval/death events
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...

# Optional: go live on Algora bounty listing + real claim/payout checks
export ALGORA_LIVE_MODE=true
export ALGORA_API_KEY=...

# Optional: go live on PR creation (also requires a verified Algora claim,
# which only happens when ALGORA_LIVE_MODE above actually confirms one).
# GITHUB_TOKEN also raises the issue-read rate limit from 60/hour to 5,000/hour.
export PR_LIVE_MODE=true
export GITHUB_TOKEN=...

# Optional: read-only wallet balance check (public address only, no private key)
export ARISE_WALLET_ADDRESS=0x...
export POLYGONSCAN_API_KEY=...

# Generate dashboard data, then open dashboard.html in a browser
# (from the same folder, so the relative fetch works)
python -m arise.export

# If an agent has died, generate its postmortem report
python -m arise.postmortem <agent_id>
```

## Known demo-mode quirk

Cycles run back-to-back with no real time passing between them, so the
24-hour lineage spend cap can be hit and stay hit for the rest of a long dry
run — that's the guardrail working correctly, not a bug. In production,
cycles are spaced hours apart (`cycle_hours` in `config.py`), so the rolling
window advances naturally.

## What's NOT here yet

- Automated bounty completion — writing code + opening a PR (v0.3.1)
- Approve/reject via Telegram reply, not just alert (v0.2 stretch / v0.3)
- Diversified strategy per child, adaptive floor, Jarvis integration (v1.0–v2.0)

Full plan: `ROADMAP.md`. Architecture rationale: `arise-spec.md`.
