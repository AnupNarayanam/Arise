# Arise — Architecture & Spec

**One-liner:** An autonomous agent seeded with $500 of real money whose only mandate is to stay alive — earn more than it spends, or shut itself down when funds hit zero. When it earns enough, it splits into an independent child agent, growing into a lineage of autonomous economic agents.

**Why this is a strong resume project:** it's not a toy. Real money, real APIs, real failure modes, real decision-making under constraints. A "the agent died on day 12" outcome is still a compelling story if the postmortem is good — this is closer to an ML/systems case study than a demo.

See `ROADMAP.md` for the version-by-version build plan.

---

## 1. Core Loop

```
┌─────────────────────────────────────────────┐
│                SURVIVAL LOOP                 │
│  (runs on a schedule, e.g. every 6–24h)      │
└─────────────────────────────────────────────┘
        │
        ▼
 1. SENSE   → check balance, pending income, upcoming expenses
        │
 2. THINK   → LLM reasons over state + history, picks an action
        │        (earn / cut cost / wait / escalate to human)
        │
 3. ACT     → executes via a tool (bounded, logged, reversible where possible)
        │
 4. RECORD  → ledger entry + reasoning trace (this trace IS the portfolio artifact)
        │
 5. CHECK   → balance <= 0 ? → SHUTDOWN sequence : loop again
```

The reasoning trace at step 2 is the most important design decision in the whole project — it's what turns "I built a bot" into "I built an agent whose decisions I can audit."

## 2. System Components

**Ledger (source of truth)** — append-only SQLite. Balance is *always* derived from the ledger, never stored as a mutable field. Free audit trail; "how did it die" is trivial to reconstruct.

**Decision Engine** — one Claude API call per cycle, given balance, recent history, and available tools. Outputs structured JSON (action, target, amount, reasoning) — directly executable and directly loggable. Falls back to a scripted dry-run stand-in with no API key configured.

**Action/Tool Layer** — each earning or cost-cutting method is a tool with an explicit contract: preconditions, expected cost, expected variance, max exposure.

**Guardrail Layer** — every action is checked before it executes: tool whitelist (enforced on both `earn` and `spend`), per-action/per-cycle/per-day caps, human-approval threshold, and a **survival floor** below which any spend/earn is rejected and forced into `escalate`. Kill switch cascades to every descendant.

**Notifier Layer** — every escalate/approval/near-floor/death event is logged locally always, and pushed to Telegram once configured. The actual safety net, not the ledger.

**Observability / Dashboard** — static, read-only, no backend: balance-over-time chart, lineage tree, decision log with reasoning.

## 3. Data Model

```
agents:          agent_id, parent_id, created_at, balance_at_last_split, status
ledger_entries:  id, agent_id, ts, type, amount, category, counterparty, decision_id
decisions:       id, agent_id, ts, balance_at_time, action, target, amount, reasoning, status
approvals:       id, decision_id, requested_at, resolved_at, approved(bool)
```

## 4. Tech Stack

- **Runtime:** Python
- **LLM:** Claude API, structured JSON output for decisions
- **DB:** SQLite → Postgres if outgrown
- **Dashboard:** static HTML + Chart.js, reads a JSON export
- **Notifications/approval:** Telegram bot API
- **Income (v0.3):** Algora bounty API — chosen because it's built for programmatic use, unlike survey/microtask platforms which explicitly ban bots
- **Payout:** crypto wallet, read-only public-address balance checks only — no private key ever touches this codebase
- **Deployment:** a $5 VPS is enough; keeping infra cost near-zero is part of the "survival" theme

## 5. Replication Rule ("Fork on Profit")

When an agent's balance doubles relative to its last split point, it splits: half the balance funds a new child agent that runs independently from that point on.

**Trigger:** `balance >= 2 × balance_at_last_split` → split. Re-arms after every split.

**Split mechanics:** parent keeps operating and a small reserve; every agent has a `parent_id` so lineage is a tree query; a split only fires if both sides clear the fixed-cost floor.

**Guardrails specific to replication:** kill switch cascades to every descendant; children nudged toward different strategies than the parent so one bad market condition can't wipe out the whole lineage; ToS-awareness around multiple agents on the same platform; lineage-wide daily spend cap independent of any one agent.

## 6. Survival Floor (implemented, v0.1)

Any `earn` or `spend` action that would drop balance below `max(min_safe_balance_floor, balance × min_safe_balance_fraction)` is rejected before execution, forcing `escalate`. Early warning fires at 1.5× the floor even for allowed actions. Tested end-to-end: guardrail-before-execute ordering confirmed correct in dry runs.

## 7. Real Income Tool (v0.3 → v0.4)

**Platform: Algora** — GitHub-issue bounties, official API, paid crypto/USD. `real_tools.py` implements `list_open_bounties()` and `algora_bounty()`, both dry-run-safe by default (`ALGORA_LIVE_MODE=true` + `ALGORA_API_KEY` needed to go live).

**v0.3.1 patch drafting (`completion.py`):**
1. `claim_bounty` (v0.4: now real) — calls Algora's claim endpoint when live; dry-run returns `verified: False`, the signal that keeps PR creation gated downstream
2. `fetch_issue` — **real**, public GitHub API. Hit GitHub's real 60/hour unauthenticated rate limit during testing (shared across the sandbox's egress IP); fixed with `GITHUB_TOKEN` support for the 5,000/hour tier
3. `generate_patch` — **real**, Claude drafts a fix from the issue text alone (no repo code in context yet)

**v0.3.2 shipping pipeline (`shipping.py`):**
4. `clone_repo` — **real**, shallow git clone, tested against a live public repo
5. `apply_patch` — **real**, `git apply --check` then apply. Proven with a synthetic fixture repo (real bug, real failing test): a correct patch applied and passed, a broken patch was rejected before touching any file, an unpatched repo correctly failed
6. `run_tests` — **real**, auto-detects pytest/npm/go/cargo, hard timeout so a hung suite can't stall the survival loop
7. `open_pull_request` — goes live only when a **verified claim** + `PR_LIVE_MODE=true` + `GITHUB_TOKEN` are all true simultaneously. Missing any one keeps it dry-run — deliberate, not incomplete: a verified claim is the actual authorization signal for touching someone else's repo, not an env var alone.

**v0.4 payout tracking:**
- `real_tools.check_payout()` — real status check when live; dry-run always reports `pending`, never fabricates a paid status
- `pending_bounties` ledger table + a per-cycle check in `agent.py`'s SENSE phase — a shipped bounty is tracked through `claimed` → `pending`/`merged` → `paid`, and a real income entry is only ever created on confirmed `paid`
- **Proven with a full lifecycle test:** dry-run claim stayed at `pending` with zero balance change across a cycle; a simulated confirmed-payout override then correctly added exactly one income entry and cleared the bounty from the pending list — no double-counting, no premature income anywhere in the chain

Every intermediate result stays `requires_human=True` — a patch that applies, passes tests, and even ships a real PR is still not income until a real merge+payout is confirmed on a later cycle.

**Wallet:** a read-only balance check (`check_wallet_balance()`) via a public block explorer API. Needs only the public address, never a private key.

## 8. Observability (v1.0 slice, built)

**Dashboard (`dashboard.html` + `export.py`):** static, no backend. Beyond the v0.2 baseline, now includes a real lineage tree (parent/child nesting, not a flat table — verified against a mixed alive/dead lineage), a days-survived counter per agent, an income/expense breakdown chart by category, and a searchable + status-filterable reasoning trace log.

**Postmortem (`postmortem.py`):** auto-generates a report for any dead agent — reconstructs the exact ledger entry where balance crossed zero, reviews the decisions immediately preceding death, and distinguishes three cases: (1) guardrails correctly rejected risky actions but the agent ran out of safe options (a strategy/tool diversity gap, not a guardrail bug), (2) the agent escalated but nothing suggests the notification was acted on in time, or (3) neither — pointing at either an unanticipated single loss or a market-level platform failure no code-level guardrail could have caught. Tested against two distinct real scenarios with correctly differentiated analysis in each.

## 9. What Makes the Resume Story Strong

- Real financial stakes, not a simulation.
- A full audit trail from balance change back to the LLM reasoning that caused it.
- Explicit guardrail design — fits a security-conscious engineering background well.
- A number to point to: days survived, net income generated, decisions made autonomously vs. escalated.
- An honest failure mode is fine, and so is an honest "not automated yet" — v0.3's bounty tool reporting rather than faking completion is itself a good engineering-integrity story.

## 10. Open Questions Going Into v0.3.1

- What does automated bounty completion actually look like — a single Claude call writing a diff, or a small agentic loop (read issue → write fix → run tests → open PR)?
- What's the quality gate before a PR is submitted, so a bad automated PR doesn't burn trust with the platform/maintainers?
- What's the shutdown behavior — does the lineage just stop looping, or does it do something more deliberate (final report, refund remaining balance, post an "obituary")?
