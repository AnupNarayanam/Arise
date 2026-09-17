"""
Notifier: the actual safety net for 'escalate' and 'pending_approval' decisions.

Logs to a local file always, so nothing is silently lost even if no real
channel is configured. If TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set,
also pushes a real Telegram message — this is the v0.2 "eyes and ears"
milestone. Everything upstream (agent.py, guardrails.py) is unchanged either way.
"""

import os
import time
import urllib.request
import urllib.parse
import json

NOTIFY_LOG = "arise_notifications.log"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def _send_telegram(message: str) -> bool:
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": TELEGRAM_CHAT_ID, "text": message}).encode()
    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        # Never let a notification failure take down the survival loop.
        _write_log(f"[notifier error] failed to reach Telegram: {e}")
        return False


def _write_log(line: str):
    with open(NOTIFY_LOG, "a") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {line}\n")


def send(agent_id: str, kind: str, message: str):
    """kind: 'escalate' | 'pending_approval' | 'near_floor' | 'dead' | 'payout'"""
    line = f"agent={agent_id} kind={kind} :: {message}"
    _write_log(line)
    print(f"[NOTIFY] {line}")

    if kind in ("escalate", "pending_approval", "dead", "payout"):
        # These need a human eyes-on; near_floor is informational and can
        # stay log-only to avoid alert fatigue.
        _send_telegram(f"[Arise/{kind}] {message} (agent {agent_id[:8]})")

