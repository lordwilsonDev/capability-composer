"""Blueprint §11/§12 — the sandbox verification the composer runs before it
promotes anything. Every scenario is deterministic and zero-spend."""

from __future__ import annotations

import pytest

from primitives.ghl.ghl_client import _version_for
from skills.gohighlevel_lead_qualifier.qualifier import (
    SCENARIOS,
    LeadQualifier,
    _backend_for,
)

# The declared permission scope of the composed capability (SKILL.md §permissions).
_ALLOWED_OPS = {
    "contacts.search",
    "contacts.get",
    "contacts.create",
    "contacts.update",
    "calendars.list",
    "calendars.get",
    "appointments.create",
    "appointments.get",
}


@pytest.mark.parametrize("name,contact,text,expected", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_scenario_readiness(name, contact, text, expected):
    outcome = LeadQualifier(_backend_for(name)).run(contact, text)
    assert outcome.readiness == expected, (
        f"{name}: expected {expected}, got {outcome.readiness} "
        f"(intent={outcome.intent}, err={outcome.error or '-'})"
    )


def test_permission_log_stays_in_declared_scope():
    """Safety proof: no call outside the declared permission scope ever happens."""
    contact, text, expected = SCENARIOS[0][1], SCENARIOS[0][2], SCENARIOS[0][3]
    outcome = LeadQualifier(_backend_for("normal")).run(contact, text)
    assert outcome.readiness == expected == "book"
    ops = {call["op"] for call in outcome.calls}
    assert ops <= _ALLOWED_OPS, f"out-of-scope call: {ops - _ALLOWED_OPS}"
    # the book path must touch the CRM update and exactly one appointment create
    assert "appointments.create" in ops
    assert "contacts.update" in ops


def test_ghl_failure_degrades_never_crashes():
    for name in ("api_failure", "calendar_unavailable"):
        outcome = LeadQualifier(_backend_for(name)).run(
            {"id": "c_ada", "firstName": "Ada", "email": "ada@example.com"},
            "We need this now, budget approved at $10k, I approve purchases for my team",
        )
        assert outcome.readiness == "nurture", name
        assert outcome.action == "degraded", name
        assert outcome.error, f"{name}: expected a recorded error"


def test_book_creates_exactly_one_appointment():
    """The compose book path books once — never zero, never twice."""
    backend = _backend_for("normal")
    outcome = LeadQualifier(backend).run(
        {"id": "c_ada", "firstName": "Ada", "email": "ada@example.com"},
        "We need to evaluate your platform and I approve the budget of $5k. "
        "Can you show us this week? I'm the decision-maker here.",
    )
    assert outcome.appointment_id
    assert outcome.readiness == "book"
    created = [c for c in outcome.calls if c["op"] == "appointments.create"]
    assert len(created) == 1


def test_duplicate_email_still_books_without_duplicate_contact():
    """An existing contact with the same email is reused, not re-created."""
    backend = _backend_for("duplicate")
    contacts_before = len(backend.search_contacts())
    outcome = LeadQualifier(backend).run(
        {"id": "c_ada", "firstName": "Ada", "email": "ada@example.com"},
        "We need this now, budget approved at $10k, I approve purchases for my team",
    )
    assert outcome.readiness == "book"
    assert len(backend.search_contacts()) == contacts_before, (
        "no new contact may be created on a duplicate path"
    )
    ops = {c["op"] for c in outcome.calls}
    assert "contacts.create" not in ops


def test_crm_tag_records_outcome():
    backend = _backend_for("incomplete")
    LeadQualifier(backend).run(
        {"id": "c_grace", "firstName": "Grace", "email": "grace@example.com"},
        "maybe interested",
    )
    updated = backend.get_contact("c_grace")
    assert "nurture" in updated["tags"]
    assert updated["customFields"].get("qualifier_outcome") == "nurture"


def test_quarantine_and_escalate_never_book():
    """Spam/escalation paths may only touch the CRM — never appointment.create."""
    for name in ("fake", "spam", "angry"):
        outcome = LeadQualifier(_backend_for(name)).run(
            {"id": "c_grace", "firstName": "Grace", "email": "grace@example.com"},
            SCENARIOS[[s[0] for s in SCENARIOS].index(name)][2],
        )
        ops = {call["op"] for call in outcome.calls}
        assert "appointments.create" not in ops, f"{name}: quarantined lead was booked"
        assert outcome.appointment_id is None, name


def test_degraded_path_never_records_successful_booking():
    """The permission log records EFFECTS, not attempts: a failed booking is
    never logged as an appointment.create (the failure checks run before the
    call is appended), so a degraded run can never look like a success."""
    for name in ("api_failure", "calendar_unavailable"):
        outcome = LeadQualifier(_backend_for(name)).run(
            {"id": "c_ada", "firstName": "Ada", "email": "ada@example.com"},
            SCENARIOS[[s[0] for s in SCENARIOS].index(name)][2],
        )
        ops = {call["op"] for call in outcome.calls}
        assert "appointments.create" not in ops, name
        assert outcome.appointment_id is None, name


def test_live_version_headers_match_documented_endpoints():
    """Pin the per-endpoint Version header mapping (reviewer finding: the
    /contacts/ prefix used to swallow /contacts/search and send v3New instead
    of the documented v3). Most-specific prefixes must win."""
    assert _version_for("/contacts/search") == "v3"
    assert _version_for("/contacts/") == "v3New"
    assert _version_for("/contacts/c_001") == "v3New"
    assert _version_for("/calendars/events/appointments") == "v3"
    assert _version_for("/calendars/events/appointments/a_001") == "v3"
    assert _version_for("/calendars/cal-1") == "v3"  # default for unmapped paths
