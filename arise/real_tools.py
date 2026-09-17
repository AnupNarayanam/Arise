"""
Real-world tool adapters for v0.3. Each function mirrors the shape of the
simulated tools in tools.py: takes an amount/budget, returns a result dict.

LIVE_MODE gate: everything here defaults to a safe dry-run/simulation unless
ALGORA_LIVE_MODE=true is explicitly set, so this file is safe to ship and
run even without real credentials configured.

Algora bounties: https://algora.io — GitHub-issue-based bounties, paid in
crypto/USD, with an official API designed for programmatic use (not a
human-only platform), which is why it's the v0.3 pick over survey sites.
"""

import os
import json
import urllib.request
import urllib.error

ALGORA_API_KEY = os.environ.get("ALGORA_API_KEY", "")
ALGORA_LIVE_MODE = os.environ.get("ALGORA_LIVE_MODE", "false").lower() == "true"
ALGORA_API_BASE = "https://console.algora.io/api"  # verify current base path before going live

WALLET_ADDRESS = os.environ.get("ARISE_WALLET_ADDRESS", "")
# Public block explorer APIs only need an address, never a private key —
# balance-checking is read-only and safe to run even before any real payout exists.
POLYGONSCAN_API_KEY = os.environ.get("POLYGONSCAN_API_KEY", "")


def _http_get(url: str, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def list_open_bounties(min_amount: float = 0, max_amount: float = 100) -> list[dict]:
    """Returns open Algora bounties in the given $ range. Dry-run returns a
    fixed sample set so the decision engine and guardrails can be exercised
    without hitting the real API."""
    if not ALGORA_LIVE_MODE:
        return [
            {"id": "sample-1", "title": "Fix flaky test in CI", "amount": 25.0,
             "url": "https://algora.io/sample-1", "repo": "octocat/Hello-World", "issue_number": 349},
            {"id": "sample-2", "title": "Add input validation to endpoint", "amount": 15.0,
             "url": "https://algora.io/sample-2", "repo": None, "issue_number": None},
        ]
    try:
        data = _http_get(
            f"{ALGORA_API_BASE}/bounties?status=open",
            headers={"Authorization": f"Bearer {ALGORA_API_KEY}"},
        )
        bounties = data.get("bounties", [])
        return [b for b in bounties if min_amount <= b.get("amount", 0) <= max_amount]
    except (urllib.error.URLError, json.JSONDecodeError, KeyError) as e:
        # Never let a bad API response crash the survival loop — surface it
        # as "no bounties available" and let the agent try again next cycle.
        return []


def claim_bounty(bounty: dict) -> dict:
    """v0.4: REAL claim attempt when ALGORA_LIVE_MODE is set (calls Algora's
    claim endpoint on our account); dry-run otherwise. A real claim_id is
    what makes open_pull_request in shipping.py eligible to go live —
    unclaimed/dry-run work never unlocks real PR creation."""
    if not ALGORA_LIVE_MODE:
        return {"claimed": True, "verified": False, "claim_id": f"dryrun-{bounty['id']}",
                "note": f"[dry-run] would claim bounty {bounty['id']} via Algora API"}
    try:
        req = urllib.request.Request(
            f"{ALGORA_API_BASE}/bounties/{bounty['id']}/claim",
            method="POST",
            headers={"Authorization": f"Bearer {ALGORA_API_KEY}"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        return {"claimed": True, "verified": True, "claim_id": data.get("claim_id"),
                "note": "claim confirmed by Algora API"}
    except (urllib.error.URLError, json.JSONDecodeError, KeyError) as e:
        return {"claimed": False, "verified": False, "claim_id": None, "note": f"claim failed: {e}"}


def check_payout(claim_id: str) -> dict:
    """v0.4: checks whether a previously-claimed, shipped bounty has been
    merged and paid. Dry-run always reports 'pending' — there is no real
    payment to confirm without a live claim, so this never fabricates a
    paid status."""
    if not ALGORA_LIVE_MODE or claim_id.startswith("dryrun-"):
        return {"status": "pending", "note": "[dry-run] no real claim to check payout status for"}
    try:
        data = _http_get(
            f"{ALGORA_API_BASE}/claims/{claim_id}/status",
            headers={"Authorization": f"Bearer {ALGORA_API_KEY}"},
        )
        return {"status": data.get("status", "pending"), "amount": data.get("amount"), "note": "live status check"}
    except (urllib.error.URLError, json.JSONDecodeError, KeyError) as e:
        return {"status": "pending", "note": f"payout check failed, treating as still pending: {e}"}


def algora_bounty(amount: float) -> dict:
    """Tool contract matching tools.py: `amount` is the budget ceiling for
    this attempt. v0.3.1: given a bounty, drafts a patch proposal via the
    completion pipeline (real GitHub issue fetch + real Claude draft). Does
    NOT apply/test/open a PR yet (v0.3.2) — always surfaces as requires_human
    so nothing gets counted as income before it's actually shipped and paid."""
    bounties = list_open_bounties(max_amount=amount)
    if not bounties:
        return {"income": 0.0, "expense": 0.0, "note": "no open bounties in range"}

    best = max(bounties, key=lambda b: b["amount"])
    repo = best.get("repo")
    issue_number = best.get("issue_number")

    if repo and issue_number:
        from . import completion  # lazy import: completion imports real_tools too
        return completion.complete_bounty(best, repo=repo, issue_number=issue_number)

    return {
        "income": 0.0,
        "expense": 0.0,
        "note": f"found claimable bounty '{best['title']}' (${best['amount']}) at {best['url']} — "
                f"no repo/issue metadata available to draft automatically, needs manual claim",
        "requires_human": True,
    }


def check_wallet_balance(chain: str = "polygon") -> dict:
    """Read-only balance check via a public block explorer API. Needs only
    the public wallet address — never a private key."""
    if not WALLET_ADDRESS:
        return {"balance": None, "note": "ARISE_WALLET_ADDRESS not configured"}
    if not POLYGONSCAN_API_KEY:
        return {"balance": None, "note": "POLYGONSCAN_API_KEY not configured — balance check skipped"}
    try:
        url = (f"https://api.polygonscan.com/api?module=account&action=balance"
               f"&address={WALLET_ADDRESS}&apikey={POLYGONSCAN_API_KEY}")
        data = _http_get(url)
        wei = int(data.get("result", 0))
        return {"balance": wei / 1e18, "unit": "MATIC", "note": "read-only, public-address lookup"}
    except Exception as e:
        return {"balance": None, "note": f"balance check failed: {e}"}
