"""
v0.3.2: ship it. Clone the repo, apply the drafted patch, run the repo's own
tests in isolation, and only then consider opening a PR.

Honest scope:
  - clone_repo       -> REAL: shallow git clone over the network
  - apply_patch       -> REAL: `git apply`, validated with --check first
  - run_tests         -> REAL: auto-detects pytest/npm/go/cargo, runs with a timeout
  - open_pull_request -> DRY-RUN ONLY, deliberately. See note on that function
    for why this stays gated even with a token configured.

Also: generate_patch in completion.py only sees the issue TEXT, not the
actual repo code, which makes LLM-drafted diffs frequently fail to apply
cleanly against real files. That failure is expected and surfaced honestly
by apply_patch rather than silently retried or faked.
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
PR_LIVE_MODE = os.environ.get("PR_LIVE_MODE", "false").lower() == "true"

TEST_TIMEOUT_SECONDS = 120


def clone_repo(repo: str, depth: int = 1) -> dict:
    """REAL: shallow-clones a public repo into a temp dir. Caller is
    responsible for cleaning up the returned path when done."""
    dest = tempfile.mkdtemp(prefix="arise_clone_")
    url = f"https://github.com/{repo}.git"
    try:
        result = subprocess.run(
            ["git", "clone", "--depth", str(depth), url, dest],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            shutil.rmtree(dest, ignore_errors=True)
            return {"ok": False, "note": f"clone failed: {result.stderr[-500:]}"}
        return {"ok": True, "path": dest}
    except subprocess.TimeoutExpired:
        shutil.rmtree(dest, ignore_errors=True)
        return {"ok": False, "note": "clone timed out after 60s"}


def apply_patch(repo_path: str, diff_text: str) -> dict:
    """REAL: writes the diff to a temp file and applies it with `git apply`,
    checking first so a bad patch never partially applies. LLM-drafted
    diffs against unseen files frequently fail this check — that's expected
    and reported honestly, not retried with guesses."""
    patch_path = os.path.join(repo_path, "_arise_patch.diff")
    with open(patch_path, "w") as f:
        f.write(diff_text)

    check = subprocess.run(
        ["git", "apply", "--check", "_arise_patch.diff"],
        cwd=repo_path, capture_output=True, text=True,
    )
    if check.returncode != 0:
        os.remove(patch_path)
        return {"ok": False, "note": f"patch does not apply cleanly: {check.stderr[-500:]}"}

    apply_result = subprocess.run(
        ["git", "apply", "_arise_patch.diff"],
        cwd=repo_path, capture_output=True, text=True,
    )
    os.remove(patch_path)
    if apply_result.returncode != 0:
        return {"ok": False, "note": f"patch check passed but apply failed: {apply_result.stderr[-500:]}"}
    return {"ok": True, "note": "patch applied"}


def _detect_test_command(repo_path: str) -> list[str] | None:
    files = os.listdir(repo_path)
    if "pytest.ini" in files or "pyproject.toml" in files or "setup.py" in files:
        return ["pytest", "-q"]
    if "package.json" in files:
        return ["npm", "test", "--silent"]
    if "go.mod" in files:
        return ["go", "test", "./..."]
    if "Cargo.toml" in files:
        return ["cargo", "test"]
    return None


def run_tests(repo_path: str) -> dict:
    """REAL: auto-detects the test runner and executes it with a hard
    timeout so a hung/malicious test suite can't stall the survival loop."""
    cmd = _detect_test_command(repo_path)
    if not cmd:
        return {"ok": False, "passed": False, "note": "no recognized test runner found — cannot verify safely"}

    try:
        result = subprocess.run(
            cmd, cwd=repo_path, capture_output=True, text=True, timeout=TEST_TIMEOUT_SECONDS,
        )
        passed = result.returncode == 0
        return {
            "ok": True, "passed": passed,
            "note": f"ran `{' '.join(cmd)}` — {'passed' if passed else 'FAILED'}",
            "log": (result.stdout[-1000:] + result.stderr[-1000:]),
        }
    except subprocess.TimeoutExpired:
        return {"ok": True, "passed": False, "note": f"tests timed out after {TEST_TIMEOUT_SECONDS}s"}
    except FileNotFoundError:
        return {"ok": False, "passed": False, "note": f"test runner not installed in this environment: {cmd[0]}"}


def open_pull_request(repo: str, title: str, body: str, claim_verified: bool = False) -> dict:
    """v0.4: goes live ONLY when claim_verified=True (a real, confirmed
    Algora claim — see real_tools.claim_bounty) AND PR_LIVE_MODE + GITHUB_TOKEN
    are set. Any one of those missing keeps this dry-run, on purpose:

    Why claim_verified specifically matters, beyond the token/flag: opening
    automated PRs against arbitrary public repos is a real open-source
    etiquette issue, not just a technical one — maintainers did not opt into
    receiving bot-authored PRs just because an issue has a bounty label. A
    verified claim is the actual authorization signal; an env var alone
    isn't."""
    if not (claim_verified and PR_LIVE_MODE and GITHUB_TOKEN):
        missing = []
        if not claim_verified:
            missing.append("no verified claim")
        if not PR_LIVE_MODE:
            missing.append("PR_LIVE_MODE not set")
        if not GITHUB_TOKEN:
            missing.append("no GITHUB_TOKEN")
        return {
            "ok": True, "live": False,
            "note": f"[dry-run] would open PR '{title}' against {repo} — blocked by: {', '.join(missing)}",
        }

    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/pulls",
            method="POST",
            headers={"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"},
            data=json.dumps({"title": title, "body": body, "head": "arise-fix", "base": "main"}).encode(),
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        return {"ok": True, "live": True, "pr_url": data.get("html_url"), "note": "PR opened live"}
    except Exception as e:
        return {"ok": False, "live": False, "note": f"live PR creation failed: {e}"}


def ship_bounty(repo: str, diff_text: str, title: str, body: str, claim_verified: bool = False) -> dict:
    """Orchestrates clone -> apply -> test -> (dry-run) PR. Cleans up the
    clone regardless of outcome."""
    cloned = clone_repo(repo)
    if not cloned["ok"]:
        return {"stage": "clone", "shipped": False, "note": cloned["note"]}

    repo_path = cloned["path"]
    try:
        patched = apply_patch(repo_path, diff_text)
        if not patched["ok"]:
            return {"stage": "apply_patch", "shipped": False, "note": patched["note"]}

        tested = run_tests(repo_path)
        if not tested["ok"]:
            return {"stage": "run_tests", "shipped": False, "note": tested["note"]}
        if not tested["passed"]:
            return {"stage": "run_tests", "shipped": False, "note": tested["note"], "log": tested.get("log", "")}

        pr = open_pull_request(repo, title, body, claim_verified=claim_verified)
        return {"stage": "open_pr", "shipped": pr.get("live", False), "note": pr["note"]}
    finally:
        shutil.rmtree(repo_path, ignore_errors=True)
