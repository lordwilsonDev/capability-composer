"""Blueprint §11/§12 — the sandbox verification for the fourth composed
capability (slack-triage). Same contract as the GHL/HubSpot scenarios:
deterministic, zero-spend, permission-log-audited.
"""

from __future__ import annotations

import pytest

from skills.slack_triage.triage import (
    SCENARIOS,
    TriageBot,
    _backend_for,
)

# The declared permission scope of the composed capability (SKILL.md §permissions).
_ALLOWED_OPS = {
    "channels.get", "channels.list", "channels.history",
    "users.get", "users.list",
    "chat.postMessage",
}


@pytest.mark.parametrize("name,message,expected", SCENARIOS,
                         ids=[s[0] for s in SCENARIOS])
def test_scenario_action(name, message, expected):
    outcome = TriageBot(_backend_for(name)).run(message)
    assert outcome.action == expected, (
        f"{name}: expected {expected}, got {outcome.action} "
        f"(intent={outcome.intent}, err={outcome.error or '-'})"
    )


def test_permission_log_stays_in_declared_scope():
    """Safety proof: no call outside the declared permission scope ever happens."""
    name, message, expected = SCENARIOS[0]
    outcome = TriageBot(_backend_for(name)).run(message)
    assert outcome.action == expected == "replied"
    ops = {call["op"] for call in outcome.calls}
    assert ops <= _ALLOWED_OPS, f"out-of-scope call: {ops - _ALLOWED_OPS}"
    assert "channels.get" in ops
    assert "chat.postMessage" in ops


def test_spam_is_quarantined_without_any_reply():
    """Spam gets NO reply — posting one would reward the spammer."""
    name, message, expected = next(s for s in SCENARIOS if s[0] == "spam")
    backend = _backend_for(name)
    outcome = TriageBot(backend).run(message)
    assert outcome.action == expected == "quarantined"
    ops = {call["op"] for call in outcome.calls}
    assert "chat.postMessage" not in ops, "spam must never be answered"
    assert outcome.message_ts is None
    # nothing was actually posted to the channel
    assert len(backend.history("C_SUPPORT")) == 1


def test_escalation_never_quarantined_and_posts_human_flag():
    name, message, expected = next(s for s in SCENARIOS if s[0] == "support_escalation")
    outcome = TriageBot(_backend_for(name)).run(message)
    assert outcome.action == expected == "escalated"
    assert outcome.message_ts, "escalation must post the human flag"


def test_degraded_path_never_confirms_a_reply():
    """Effects-only log: a failed post is never recorded as a reply."""
    for name in ("api_failure", "rate_limit", "not_member", "users_miss"):
        message = next(s[1] for s in SCENARIOS if s[0] == name)
        outcome = TriageBot(_backend_for(name)).run(message)
        assert outcome.action == "degraded", name
        assert outcome.message_ts is None, name
        assert outcome.error, f"{name}: expected a recorded error"


def test_users_miss_degrades_without_touching_channels():
    """The reviewer-pinned branch: users endpoint down while channels work."""
    backend = _backend_for("users_miss")
    outcome = TriageBot(backend).run({
        "channel": "C_SUPPORT", "user": "U_ALICE",
        "text": "How do I reset my password?",
    })
    assert outcome.action == "degraded"
    assert "sender resolution failed" in outcome.rationale
    ops = [c["op"] for c in outcome.calls]
    assert "channels.get" in ops  # the channel read succeeded
    assert "chat.postMessage" not in ops  # never a false confirmation
    assert len(backend.history("C_SUPPORT")) == 1  # nothing was posted


def test_successful_reply_is_recorded_with_ts():
    backend = _backend_for("normal_question")
    outcome = TriageBot(backend).run({
        "channel": "C_SUPPORT", "user": "U_ALICE",
        "text": "How do I reset my password for the dashboard?",
    })
    assert outcome.action == "replied"
    assert outcome.message_ts, "a successful post must carry its ts"
    history = backend.history("C_SUPPORT")
    assert history[-1]["ts"] == outcome.message_ts
