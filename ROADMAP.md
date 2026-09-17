# Arise — Version Roadmap

Each version is a real, working, demoable milestone — not internal refactor
churn. The bar for "elite/outstanding" isn't more features, it's: every
version survives real scrutiny (working code, tested, honestly documented
limitations) before the next one starts.

---

## v0.1 — Genesis (DONE)
The loop exists and is provably correct.
- Core SENSE→THINK→ACT→RECORD→CHECK loop
- Append-only ledger, derived balance, full audit trail
- Guardrail layer: whitelist, per-action/cycle/day caps, approval threshold
- Survival floor + forced escalate-before-die
- Fork-on-profit replication with cascading kill switch
- Notifier stub (local log)
- Dry-run tested end-to-end with no API key required
**Status:** built, tested, working.

## v0.2 — Eyes and Ears
Make the system observable and reachable by a human, before it touches real money.
- Real Telegram (or Discord) notifier — `escalate`/`pending_approval`/`near_floor`/`dead` actually reach your phone
- Approve/reject via reply, not just a log line
- Read-only dashboard: balance-over-time, lineage tree, decision log with reasoning — the artifact you actually demo
- Structured export (`arise export`) — dumps ledger to JSON for the dashboard to render, no live DB coupling
**This is the next thing to build.**

## v0.3 — First Real Income (IN PROGRESS)
One real income tool goes live, with the tightest possible guardrails.
- **Platform: Algora** — GitHub-issue bounties, official API, built for programmatic use, paid crypto/USD. Chosen over survey/microtask sites, which explicitly ban bots in their ToS.
- `real_tools.py` — `list_open_bounties()` and `algora_bounty()` built and tested in dry-run (safe default; `ALGORA_LIVE_MODE=true` + `ALGORA_API_KEY` required to go live)
- `check_wallet_balance()` — read-only public-address balance check (Polygon), never touches a private key
- **Honest scope for this version:** the tool *finds* a real claimable bounty and reports it — it does NOT yet write code and open a PR automatically. That's forced into `escalate` rather than faked as income. Automated completion is v0.3.1.
- Guardrail fix made while wiring this in: the tool whitelist check previously only applied to `spend` actions, not `earn` — now enforced on both.

### v0.3.1 — Automated Completion (BUILT — draft stage only)
- `completion.py` — 4-stage pipeline: claim (dry-run stub), fetch issue (**real**, public GitHub API), draft patch (**real**, Claude), apply/test/PR (**not built** — staged as v0.3.2)
- Tested against a real public GitHub issue — and immediately hit a real constraint: unauthenticated GitHub API calls are capped at 60/hour and share a rate limit across the whole sandbox's IP. Confirmed by actually hitting the limit during testing, not by reading the docs. Fixed by adding `GITHUB_TOKEN` support for the 5,000/hour authenticated tier.
- Every result stays `requires_human=True` — a drafted patch is not a shipped fix, and is never counted as income. This is deliberate: faking stage 4 would be exactly the kind of dishonest scope the ledger design exists to prevent.

### v0.3.2 — Ship It (BUILT — quality gate proven, PR opening deliberately still dry-run)
- `shipping.py`: clone (**real**, shallow git clone), apply patch (**real**, `git apply --check` then apply), run tests (**real**, auto-detects pytest/npm/go/cargo, hard timeout), open PR (**dry-run only, by design**)
- **Proven, not assumed:** built a synthetic fixture repo with a real bug and a real failing test, then ran a genuinely correct patch and a genuinely broken patch through the actual pipeline. Results: bad patch rejected before touching any file, good patch applied and tests passed, unpatched repo correctly failed. This is the quality gate demonstrably working.
- **PR opening stays dry-run even with `GITHUB_TOKEN` + `PR_LIVE_MODE` set.** This is a deliberate design choice, not a missing feature: opening automated PRs against arbitrary public repos is an open-source etiquette problem as much as a technical one. Going properly live requires `claim_bounty()` to be a *real, verified* claim first — proving authorization to work that specific issue — which isn't built yet either. Documented as the actual gate to v0.4, not something an env var alone should unlock.
- Known honest failure mode, confirmed in testing: `generate_patch` drafts from issue text alone (no repo code in context), so LLM-drafted diffs frequently fail `apply_patch`'s clean-apply check against real files. Surfaced plainly, never retried with guesses.

## v0.4 — Second Leg to Stand On (Algora claim + payout tracking BUILT)
- `real_tools.claim_bounty()` — **real** claim attempt when `ALGORA_LIVE_MODE` is set; dry-run reports `verified: False`, which is exactly what keeps `open_pull_request` gated (see below)
- `real_tools.check_payout()` — **real** status check when live; dry-run always reports `pending`, never fabricates a paid status
- `shipping.open_pull_request()` now goes live only when THREE things are true simultaneously: a verified claim, `PR_LIVE_MODE=true`, and `GITHUB_TOKEN` set. Missing any one keeps it dry-run — this is the actual authorization gate discussed in v0.3.2, now wired for real.
- New `pending_bounties` ledger table + a per-cycle payout check in `agent.py`'s SENSE phase — a shipped bounty is tracked (`claimed` → `pending`/`merged` → `paid`), and a real income ledger entry is only ever created on a confirmed `paid` status.
- **Proven, not assumed:** tested the full lifecycle by simulating a confirmed payout override — dry-run claim correctly stayed at `pending` with zero balance change across a cycle, then a simulated real "paid" status correctly added exactly one income entry and removed the bounty from the pending list. No double-counting, no premature income.
- Second, uncorrelated income tool — still open, next up for v0.4 completeness
- Real cost-cutting tool (currently still simulated) — still open
- Approval threshold loosening based on a real track record — still open
- Replication logic tested with real (small) money for the first time, fully manually approved — still open

## v1.0 — Arise (full autonomy, tier 1) — dashboard/observability slice BUILT
The first version that's genuinely "done" as a product, not a prototype.
- Full lineage running unattended for extended stretches, human only pinged on escalate/approval
- **Dashboard upgraded to public-demo quality (done):** real lineage tree (not a flat table), days-survived counter per agent, income/expense breakdown by category, searchable + status-filterable reasoning trace. All verified against real exported data including a mixed alive/dead lineage.
- **Postmortem tooling (done):** `postmortem.py` auto-generates a report on any dead agent — reconstructs exactly when balance crossed zero, reviews the decisions immediately preceding death, and distinguishes "guardrail worked but ran out of safe options" from "guardrail didn't anticipate this" from "market-level failure no code could catch." Tested against two real scenarios (rejected-decisions-before-death, and a sudden external loss) with correct, distinct analysis for each.
- Diversified strategy per child (replication rule's "don't clone identically" guardrail actually implemented, not just documented) — still open
- README + architecture doc are portfolio-ready as-is

## v2.0 — Elite Tier
Where this stops being "a cool project" and becomes something with real depth to discuss in an interview.
- Jarvis integration: Arise exposed as a callable tool/status source (status, spend explanation, override) — same pattern as ALTER
- Adaptive survival floor — floor tightens automatically after a near-miss, loosens slowly after a sustained healthy streak, instead of being a static config number
- Cross-agent learning: children report outcomes back to a shared strategy pool so the lineage as a whole gets smarter, not just each agent independently
- Full observability stack: metrics, alerting thresholds, historical replay of any past decision cycle
- Public write-up: the "days survived, net income generated, decisions made autonomously vs. escalated" numbers become a real case study, including any deaths and what they taught the system

---

## Rule for moving between versions
No version starts until the previous one has actually run, not just compiled.
"Elite" here means every claim in the eventual writeup is something you can
point to a ledger entry or a decision trace for — not a feature that exists
only in the roadmap.
