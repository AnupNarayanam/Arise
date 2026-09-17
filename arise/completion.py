"""
v0.3.1: automated bounty completion.

Honest scope for this version — two of the four pipeline stages are REAL
and tested here today; the other two are deliberately staged as v0.3.2
rather than faked:

  1. claim_bounty        -> dry-run stub (needs Algora auth we don't have yet)
  2. fetch_issue          -> REAL: public GitHub API, no token needed for public repos
  3. generate_patch       -> REAL: Claude drafts a patch proposal from the issue
  4. apply + test + PR    -> NOT BUILT YET (v0.3.2) — see note below

Why stages 4 is staged rather than built now: cloning an arbitrary repo,
detecting its test runner, running it in an isolated sandbox, and only then
opening a PR is a real subsystem of its own (different languages, different
test frameworks, different flakiness per repo). Faking that part would be
exactly the kind of dishonest scope this project's ledger design is meant
to prevent — so instead this returns a clearly-labeled 'draft_only' result
that the agent forces into escalate rather than counting as done.
"""

import json
import os
import urllib.request
import urllib.error

from .config import CONFIG
from . import shipping
from . import real_tools

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")  # only needed for stage 4 (v0.3.2), not for reading public issues


def _http_get(url: str, headers: dict | None = None) -> dict:
    default_headers = {"Accept": "application/vnd.github+json", "User-Agent": "arise-agent"}
    if headers:
        default_headers.update(headers)
    req = urllib.request.Request(url, headers=default_headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def fetch_issue(repo: str, issue_number: int) -> dict:
    """Stage 2. REAL: reads a public GitHub issue. Unauthenticated calls are
    capped at 60/hour and share a rate limit across this whole sandbox's
    egress IP — confirmed by hitting exactly that limit during testing. Set
    GITHUB_TOKEN (a plain read-only PAT is enough) for the 5,000/hour
    authenticated limit before running this for real.
    `repo` is 'owner/name'. Returns title/body/labels or an error note."""
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}"} if GITHUB_TOKEN else None
    try:
        data = _http_get(f"https://api.github.com/repos/{repo}/issues/{issue_number}", headers=headers)
        return {
            "title": data.get("title", ""),
            "body": data.get("body", "") or "",
            "labels": [l["name"] for l in data.get("labels", [])],
            "url": data.get("html_url", ""),
            "ok": True,
        }
    except (urllib.error.URLError, json.JSONDecodeError, KeyError) as e:
        return {"ok": False, "note": f"could not fetch issue: {e}"}


def generate_patch(issue_title: str, issue_body: str) -> dict:
    """Stage 3. REAL: asks Claude to draft a patch proposal from the issue
    text alone (no repo checkout yet — that's v0.3.2). This is a PROPOSAL,
    not a tested, applicable diff, and is labeled as such."""
    if not CONFIG.anthropic_api_key:
        return {"ok": False, "note": "no ANTHROPIC_API_KEY configured — cannot draft a patch"}

    import anthropic

    client = anthropic.Anthropic(api_key=CONFIG.anthropic_api_key)
    prompt = f"""A GitHub issue is open for a small bounty. Draft a plausible fix approach
and, if the issue is concrete enough, a rough unified-diff-style patch proposal.
Be honest if the issue doesn't give you enough context to write real code —
say so plainly rather than inventing file contents you haven't seen.

Issue title: {issue_title}
Issue body:
{issue_body}
"""
    response = client.messages.create(
        model=CONFIG.anthropic_model,
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in response.content if b.type == "text")
    return {"ok": True, "proposal": text}


def complete_bounty(bounty: dict, repo: str | None = None, issue_number: int | None = None) -> dict:
    """Orchestrates the pipeline. Returns a tool-contract-shaped dict, always
    with requires_human=True — even a fully shipped bounty isn't income
    until a real merge+payout is confirmed later (see real_tools.check_payout,
    called each cycle by agent.py)."""
    claim = real_tools.claim_bounty(bounty)

    if not repo or not issue_number:
        return {
            "income": 0.0, "expense": 0.0, "requires_human": True,
            "note": f"{claim['note']}; no repo/issue_number provided to fetch — cannot proceed to drafting",
        }

    issue = fetch_issue(repo, issue_number)
    if not issue.get("ok"):
        return {"income": 0.0, "expense": 0.0, "requires_human": True, "note": issue["note"]}

    patch = generate_patch(issue["title"], issue["body"])
    if not patch.get("ok"):
        return {"income": 0.0, "expense": 0.0, "requires_human": True, "note": patch["note"]}

    # v0.3.2 + v0.4: actually try to ship it — clone, apply, test, and only
    # go live on the PR if the claim above was real+verified (not dry-run).
    ship_result = shipping.ship_bounty(
        repo, patch["proposal"],
        title=f"Fix: {issue['title']}",
        body=f"Automated draft for {issue['url']}\n\n{patch['proposal'][:500]}",
        claim_verified=claim.get("verified", False),
    )

    if ship_result["stage"] == "apply_patch" and not ship_result["shipped"]:
        # Expected, honest failure mode: an LLM draft from issue text alone
        # often won't apply cleanly against real files it never saw.
        return {
            "income": 0.0, "expense": 0.0, "requires_human": True,
            "note": (f"drafted a patch for '{issue['title']}' ({issue['url']}) but it did not apply "
                     f"cleanly to the real repo: {ship_result['note']}"),
            "draft_proposal": patch["proposal"],
        }

    if ship_result["stage"] == "run_tests" and not ship_result["shipped"]:
        return {
            "income": 0.0, "expense": 0.0, "requires_human": True,
            "note": (f"patch applied to '{issue['title']}' ({issue['url']}) but the repo's own tests "
                     f"failed or couldn't run: {ship_result['note']}"),
        }

    if ship_result["stage"] == "clone" and not ship_result["shipped"]:
        return {"income": 0.0, "expense": 0.0, "requires_human": True, "note": ship_result["note"]}

    # Patch applied AND tests passed. Whether the PR actually went live
    # depends on claim_verified (see shipping.open_pull_request).
    return {
        "income": 0.0, "expense": 0.0, "requires_human": True,
        "note": (f"patch for '{issue['title']}' ({issue['url']}) applied cleanly AND passed the repo's "
                 f"own tests. {ship_result['note']} — real income only after a real PR merges and pays out."),
        "quality_gate_passed": True,
        "pending_bounty": {
            "bounty_id": bounty["id"], "claim_id": claim.get("claim_id"),
            "repo": repo, "issue_number": issue_number, "amount": bounty["amount"],
        } if ship_result.get("shipped") or claim.get("verified") else None,
    }
