"""Blueprint §11/§12 — the sandbox verification for the cross-connector
composition (ghl-hubspot-router). Same contract as the other skills, but the
permission audit is MERGED across both connectors — the multi-provider edge is
proven machine-checked."""

from __future__ import annotations

import pytest

from skills.ghl_hubspot_router.router import (
    SCENARIOS,
    LeadRouter,
    _scenario_backends,
)

# Declared permission scope on EACH connector (SKILL.md §permissions).
_ALLOWED_GHL = {
    "contacts.search", "contacts.get", "contacts.create", "contacts.update",
    "calendars.list", "calendars.get", "appointments.create", "appointments.get",
}
_ALLOWED_HUBSPOT = {
    "contacts.search", "contacts.get", "contacts.create", "contacts.update",
    "deals.search", "deals.get", "deals.create", "deals.update",
}

# Name-keyed scenario lookup — order-proof, unlike positional indices.
_BY_NAME = {s[0]: s for s in SCENARIOS}


@pytest.mark.parametrize("name,leads,expected", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_scenario_actions(name, leads, expected):
    ghl, hubspot = _scenario_backends(name)
    outcomes = LeadRouter(ghl, hubspot).route(leads)
    assert [o.action for o in outcomes] == expected


def test_merged_permission_log_stays_in_declared_scope():
    """Across BOTH connectors, no call leaves the declared scope."""
    ghl, hubspot = _scenario_backends("routes_new_lead_to_hubspot")
    LeadRouter(ghl, hubspot).route(SCENARIOS[0][1])
    ghl_ops = {c["op"] for c in ghl.calls}
    hub_ops = {c["op"] for c in hubspot.calls}
    assert ghl_ops <= _ALLOWED_GHL, f"out-of-scope GHL call: {ghl_ops - _ALLOWED_GHL}"
    assert hub_ops <= _ALLOWED_HUBSPOT, f"out-of-scope HubSpot call: {hub_ops - _ALLOWED_HUBSPOT}"


def test_confirm_tags_the_ghl_lead_only_after_route():
    ghl, hubspot = _scenario_backends("routes_new_lead_to_hubspot")
    LeadRouter(ghl, hubspot).route(SCENARIOS[0][1])
    grace = ghl.get_contact("c_grace")
    assert "synced-to-hubspot" in grace["tags"], "the confirm step must tag the lead"
    assert len(hubspot.search_deals("c_grace")) == 1


def test_existing_contact_and_open_deal_are_never_duplicated():
    """advances_existing: contact reused (no contacts.create), open deal
    advanced (one deals.update, zero deals.create)."""
    ghl, hubspot = _scenario_backends("advances_existing_hubspot_deal")
    outcomes = LeadRouter(ghl, hubspot).route(SCENARIOS[1][1])
    assert outcomes[0].action == "advanced"
    assert outcomes[0].deal_id == "d_001"
    hub_ops = [c["op"] for c in hubspot.calls]
    assert hub_ops.count("deals.create") == 0
    assert hub_ops.count("contacts.create") == 0
    assert "deals.update" in hub_ops
    assert hubspot.get_deal("d_001")["amount"] == 9000


def test_ghl_only_lead_creates_hubspot_contact_then_deal():
    """A lead that exists in GHL but not HubSpot (c_linus fixture) creates
    the contact, then the deal — never a duplicate, verified upstream first."""
    ghl, hubspot = _scenario_backends("creates_hubspot_contact_and_deal")
    LeadRouter(ghl, hubspot).route(_BY_NAME["creates_hubspot_contact_and_deal"][1])
    hub_ops = [c["op"] for c in hubspot.calls]
    assert hub_ops.count("contacts.create") == 1
    assert hub_ops.count("deals.create") == 1
    assert "contacts.get" in {c["op"] for c in ghl.calls}, "verify_in_ghl must run first"


def test_gated_leads_never_touch_any_connector_write():
    """skipped_unqualified + quarantined + skipped_missing: zero writes on
    EITHER side — the gates fire before any write."""
    for name, leads, _ in (_BY_NAME["skips_unqualified"],
                           _BY_NAME["quarantines_spam"],
                           _BY_NAME["skips_missing_ghl_lead"]):
        ghl, hubspot = _scenario_backends(name)
        LeadRouter(ghl, hubspot).route(leads)
        hub_writes = [c["op"] for c in hubspot.calls
                      if c["op"].endswith(("create", "update"))]
        assert not hub_writes, f"{name}: unexpected HubSpot writes: {hub_writes}"
        ghl_ops = {c["op"] for c in ghl.calls}
        assert not {op for op in ghl_ops if op.endswith(("create", "update"))}, name
        if name == "skips_missing_ghl_lead":
            assert "contacts.get" in ghl_ops, "verify_in_ghl must run for a missing lead"
        else:
            assert ghl_ops == set(), f"{name}: gates must fire before ANY connector call"


def test_unconfirmed_records_partial_state_honestly():
    """Route succeeded, GHL confirm failed → the deal EXISTS in HubSpot and
    the outcome says unconfirmed with the error — never a lie."""
    ghl, hubspot = _scenario_backends("ghl_confirm_failure_unconfirmed")
    outcomes = LeadRouter(ghl, hubspot).route(SCENARIOS[6][1])
    assert outcomes[0].action == "unconfirmed"
    assert outcomes[0].error, "the confirm failure must be recorded"
    assert outcomes[0].deal_id
    # the partial state is real: the deal was advanced in HubSpot
    assert hubspot.get_deal("d_001")["amount"] == 9000


def test_hubspot_failure_leaves_ghl_untouched():
    """Failed route: no deal on the HubSpot side, no false confirmation tag
    on the GHL side."""
    ghl, hubspot = _scenario_backends("hubspot_failure_failed")
    outcomes = LeadRouter(ghl, hubspot).route(SCENARIOS[7][1])
    assert outcomes[0].action == "failed"
    assert outcomes[0].error
    assert hubspot.search_deals("c_grace") == []
    assert "synced-to-hubspot" not in ghl.get_contact("c_grace")["tags"]
