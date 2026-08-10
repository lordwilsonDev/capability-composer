"""Blueprint §11/§12 — the sandbox verification for the second composed
capability (hubspot-deal-pipeline). Same contract as the GHL scenarios:
deterministic, zero-spend, permission-log-audited."""

from __future__ import annotations

import pytest

from skills.hubspot_deal_pipeline.pipeline import (
    SCENARIOS,
    DealPipeline,
    _backend_for,
)

# The declared permission scope of the composed capability (SKILL.md §permissions).
_ALLOWED_OPS = {
    "contacts.search", "contacts.get", "contacts.create", "contacts.update",
    "deals.search", "deals.get", "deals.create", "deals.update",
}


@pytest.mark.parametrize("name,contact,text,expected", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_scenario_action(name, contact, text, expected):
    outcome = DealPipeline(_backend_for(name)).run(contact, text)
    assert outcome.action == expected, (
        f"{name}: expected {expected}, got {outcome.action} "
        f"(intent={outcome.intent}, err={outcome.error or '-'})"
    )


def test_permission_log_stays_in_declared_scope():
    """Safety proof: no call outside the declared permission scope ever happens."""
    name, contact, text, expected = SCENARIOS[0]
    outcome = DealPipeline(_backend_for(name)).run(contact, text)
    assert outcome.action == expected == "deal_created"
    ops = {call["op"] for call in outcome.calls}
    assert ops <= _ALLOWED_OPS, f"out-of-scope call: {ops - _ALLOWED_OPS}"
    assert "deals.create" in ops
    assert "contacts.update" in ops


def test_quarantine_and_escalate_never_touch_deals():
    """Spam/escalation paths may only tag the CRM — never deals.create/update."""
    for name in ("spam", "angry"):
        outcome = DealPipeline(_backend_for(name)).run(
            {"firstname": "Grace", "lastname": "Hopper", "email": "grace@example.com"},
            SCENARIOS[[s[0] for s in SCENARIOS].index(name)][2],
        )
        ops = {call["op"] for call in outcome.calls}
        assert not {"deals.create", "deals.update"} & ops, f"{name}: deal touched"
        assert outcome.deal_id is None, name


def test_degraded_path_never_records_a_deal():
    """Effects-only log: a failed create/update is never logged as a deal op."""
    for name in ("api_failure", "rate_limit"):
        outcome = DealPipeline(_backend_for(name)).run(
            {"firstname": "Ada", "lastname": "Lovelace", "email": "ada@example.com"},
            SCENARIOS[[s[0] for s in SCENARIOS].index(name)][2],
        )
        ops = {call["op"] for call in outcome.calls}
        assert not {"deals.create", "deals.update"} & ops, name
        assert outcome.deal_id is None, name
        assert outcome.error, f"{name}: expected a recorded error"


def test_existing_contact_and_open_deal_are_reused_never_duplicated():
    """The update path: existing contact (no contacts.create) + open deal
    advanced (one deals.update, zero deals.create)."""
    backend = _backend_for("existing_open_deal")
    outcome = DealPipeline(backend).run(
        {"id": "c_ada", "firstname": "Ada", "lastname": "Lovelace",
         "email": "ada@example.com"},
        "We need this now, budget approved at $9000, I approve purchases for my team",
    )
    assert outcome.action == "deal_updated"
    ops = [c["op"] for c in outcome.calls]
    assert "contacts.create" not in ops, "existing contact must be reused"
    assert ops.count("deals.create") == 0, "no duplicate deal may be created"
    assert "deals.update" in ops
    # the open deal was actually advanced, amount updated
    assert backend.get_deal("d_001")["amount"] == 9000


def test_new_contact_creates_then_deals():
    """The create path for an unknown email: one contacts.create, one deals.create."""
    backend = _backend_for("new_contact")
    before = len(backend.search_contacts())
    outcome = DealPipeline(backend).run(
        {"firstname": "Katherine", "lastname": "Johnson",
         "email": "katherine@example.com", "phone": "+15550001003"},
        "We need this now, budget approved at $2000, I decide for our team",
    )
    assert outcome.action == "deal_created"
    assert len(backend.search_contacts()) == before + 1
    ops = [c["op"] for c in outcome.calls]
    assert ops.count("contacts.create") == 1
    assert ops.count("deals.create") == 1
    # amount extracted deterministically from the conversation
    created = [c for c in outcome.calls if c["op"] == "deals.create"][0]
    assert created["args"]["contact_id"]  # deal linked to the new contact


def test_crm_tag_records_outcome():
    backend = _backend_for("incomplete")
    DealPipeline(backend).run(
        {"firstname": "Grace", "lastname": "Hopper", "email": "grace@example.com"},
        "maybe interested",
    )
    updated = backend.get_contact("c_grace")
    assert "nurture" in updated["tags"]
