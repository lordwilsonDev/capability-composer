"""slack-triage v1.0 — composed capability (blueprint §13/§14).

The fourth composition the composer builds — proving the discovery layer
generalizes to a MESSAGING provider: compose the registered Slack primitives +
the SAME shared model primitive (primitives/stub_model) into one deterministic
triage bot —

    verify_channel → resolve_user → detect_intent → decide → reply_or_quarantine

The model is the shared keyword intent classifier (zero-spend, reproducible).
The Slack backend is the deterministic sandbox (verified path); the live Web
API is opt-in behind SLACK_API_KEY.

Safety properties (the ones verification proves):
- Every Slack call goes through the backend's own `calls` permission log.
- A Slack failure (outage, rate limit, not-a-member) DEGRADES — it never
  crashes and never posts a message that was not earned.
- Spam is quarantined: NO reply is posted (a reply rewards the spammer).
- A reply is only recorded as posted when post_message actually succeeded.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# Script-mode bootstrap (same pattern as the other composed skills).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from primitives.slack.slack_client import (  # type: ignore[import-not-found]
    SandboxBackend,
    SlackError,
)
from primitives.stub_model.stub_model import StubModel  # type: ignore[import-not-found]

_REPLIES = {
    "sales": ("Thanks for reaching out — a sales specialist will follow up "
              "shortly. Anything else we can help with in the meantime?"),
    "other": ("Noted — the right teammate will pick this up. If it's urgent, "
              "tag @support."),
    "support_escalation": ("I'm sorry this is frustrating — I've flagged this "
                           "to a human right away."),
}


@dataclass
class Outcome:
    """The decision record — deterministic, assertable, ledger-able."""

    channel_id: str
    intent: str
    action: str  # replied | escalated | quarantined | degraded
    rationale: str = ""
    message_ts: Optional[str] = None
    error: Optional[str] = None
    calls: list[dict[str, Any]] = field(default_factory=list)


class TriageBot:
    """verify_channel → resolve_user → detect_intent → decide → reply/quarantine."""

    def __init__(self, backend: Any, model: Optional[Any] = None):
        self.backend = backend
        self.model = model or StubModel()

    def run(self, message: dict[str, Any]) -> Outcome:
        channel_id = message.get("channel", "")
        text = message.get("text", "")
        intent = self.model.intent(text)["intent"]

        # verify_channel — must exist and the bot must be a member.
        try:
            self.backend.get_channel(channel_id)
        except SlackError as exc:
            outcome = Outcome(channel_id, intent, "degraded",
                              rationale="channel unavailable", error=str(exc))
            outcome.calls = list(self.backend.calls)
            return outcome

        # resolve_user — the sender's name, if we can read it (degrade, not crash).
        user_id = message.get("user", "")
        try:
            user = self.backend.get_user(user_id) if user_id else {}
        except SlackError as exc:
            outcome = Outcome(channel_id, intent, "degraded",
                              rationale="sender resolution failed", error=str(exc))
            outcome.calls = list(self.backend.calls)
            return outcome
        name = user.get("name") or user.get("real_name") or "there"

        # decide — spam is quarantined (NO reply), escalation gets a human flag.
        if intent == "spam":
            outcome = Outcome(channel_id, intent, "quarantined",
                              rationale="spam signals detected — no reply posted")
            outcome.calls = list(self.backend.calls)
            return outcome
        action = "escalated" if intent == "support_escalation" else "replied"
        reply = _REPLIES.get(intent, _REPLIES["other"]).replace("a human", f"a human ({name})")

        # reply_or_quarantine — only a successful post counts as replied.
        try:
            posted = self.backend.post_message(channel_id, reply)
            outcome = Outcome(channel_id, intent, action,
                              rationale=f"reply posted to {channel_id}",
                              message_ts=posted.get("ts"))
        except SlackError as exc:
            outcome = Outcome(channel_id, intent, "degraded",
                              rationale="reply failed — nothing falsely confirmed",
                              error=str(exc))
        outcome.calls = list(self.backend.calls)
        return outcome


# ---------------------------------------------------------------------------
# Sandbox verification (blueprint §11/§12) — 9 adversarial scenarios
# ---------------------------------------------------------------------------

SCENARIOS: list[tuple[str, dict[str, Any], str]] = [
    # (name, message, expected action)
    ("normal_question", {"channel": "C_SUPPORT", "user": "U_ALICE",
                         "text": "How do I reset my password for the dashboard?"},
     "replied"),
    ("sales_intent", {"channel": "C_SALES", "user": "U_BOB",
                      "text": "We need budget approved at $5000 for your platform this week, I approve purchases"},
     "replied"),
    ("support_escalation", {"channel": "C_SUPPORT", "user": "U_ALICE",
                            "text": "This is the worst service I have ever seen, absolutely unacceptable"},
     "escalated"),
    ("spam", {"channel": "C_SUPPORT", "user": "U_ALICE",
              "text": "buy now and double your money with guaranteed profit crypto"},
     "quarantined"),
    ("other", {"channel": "C_SUPPORT", "user": "U_ALICE",
               "text": "what time is the standup tomorrow"},
     "replied"),
    ("api_failure", {"channel": "C_SUPPORT", "user": "U_ALICE",
                     "text": "How do I reset my password?"},
     "degraded"),  # write outage → degrade, never a false confirmation
    ("rate_limit", {"channel": "C_SUPPORT", "user": "U_ALICE",
                    "text": "How do I reset my password?"},
     "degraded"),  # reads blocked → degrade before any reply work
    ("not_member", {"channel": "C_LEGAL", "user": "U_ALICE",
                    "text": "How do I reset my password?"},
     "degraded"),  # bot cannot post there → degrade honestly
    ("users_miss", {"channel": "C_SUPPORT", "user": "U_ALICE",
                    "text": "How do I reset my password?"},
     "degraded"),  # users endpoint down while channels work → degrade, not crash
]


def _backend_for(name: str) -> SandboxBackend:
    if name == "api_failure":
        return SandboxBackend(failures=("api_failure",))
    if name == "rate_limit":
        return SandboxBackend(failures=("rate_limit",))
    if name == "users_miss":
        return SandboxBackend(failures=("users_miss",))
    return SandboxBackend()


def run_scenarios() -> list[tuple[str, Outcome, str]]:
    return [
        (name, TriageBot(_backend_for(name)).run(message), expected)
        for name, message, expected in SCENARIOS
    ]


def verify_sandbox() -> list[str]:
    """Run the 9 adversarial scenarios; return the list of failures (empty = pass)."""
    failures: list[str] = []
    for name, outcome, expected in run_scenarios():
        if outcome.action != expected:
            failures.append(
                f"{name}: expected {expected}, got {outcome.action} "
                f"(intent={outcome.intent}, err={outcome.error or '-'})"
            )
    return failures


def _cmd_verify() -> int:
    failures: list[str] = []
    for name, outcome, expected in run_scenarios():
        mark = "PASS" if outcome.action == expected else "FAIL"
        if mark == "FAIL":
            failures.append(f"{name}: expected {expected}, got {outcome.action}")
        print(f"  [{mark}] {name:20s} → {outcome.action:12s} "
              f"intent={outcome.intent} err={outcome.error or '-'}")
    if failures:
        print(f"\n{len(failures)} FAILED (zero spend):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"\nALL PASS — {len(SCENARIOS)} scenarios, zero spend")
    return 0



if __name__ == "__main__":
    raise SystemExit(_cmd_verify())
