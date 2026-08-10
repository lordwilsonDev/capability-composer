"""Self-tests for the HubSpot connector primitive (sandbox path)."""

from __future__ import annotations

import pytest

from primitives.hubspot.hubspot_client import HubspotError, SandboxBackend


def test_fixture_reads():
    sb = SandboxBackend()
    assert len(sb.search_contacts()) == 2
    assert len(sb.search_deals()) == 1
    assert sb.get_contact("c_ada")["email"] == "ada@example.com"
    assert sb.get_deal("d_001")["contactId"] == "c_ada"


def test_search_contacts_by_email():
    sb = SandboxBackend()
    hits = sb.search_contacts("ada@example.com")
    assert len(hits) == 1
    assert hits[0]["id"] == "c_ada"


def test_create_and_update_contact():
    sb = SandboxBackend()
    created = sb.create_contact({
        "firstname": "Katherine", "lastname": "Johnson",
        "email": "katherine@example.com", "phone": "+15550001003",
    })
    assert created["id"].startswith("c_")
    updated = sb.update_contact(created["id"], {"tags": ["nurture"]})
    assert "nurture" in updated["tags"]
    assert len(sb.search_contacts()) == 3


def test_duplicate_email_raises():
    sb = SandboxBackend()
    with pytest.raises(HubspotError, match="duplicate contact"):
        sb.create_contact({"firstname": "Ada", "lastname": "Lovelace",
                           "email": "ada@example.com"})


def test_deals_create_update_search_by_contact():
    sb = SandboxBackend()
    deal = sb.create_deal({
        "dealname": "Grace Hopper — onboard", "amount": 2000,
        "pipeline": "default", "dealstage": "appointmentscheduled",
        "contactId": "c_grace",
    })
    assert deal["id"].startswith("d_")
    sb.update_deal(deal["id"], {"amount": 3000, "dealstage": "qualifiedtobuy"})
    assert sb.get_deal(deal["id"])["amount"] == 3000
    hits = sb.search_deals("c_grace")
    assert len(hits) == 1


def test_failure_injection():
    sb = SandboxBackend(failures=("api_failure",))
    with pytest.raises(HubspotError, match="outage"):
        sb.create_deal({"dealname": "x", "amount": 1})
    rl = SandboxBackend(failures=("rate_limit",))
    with pytest.raises(HubspotError, match="rate limit"):
        rl.search_contacts()


def test_permission_log_records_every_call():
    sb = SandboxBackend()
    sb.search_contacts("ada")
    sb.create_deal({"dealname": "x", "amount": 1, "contactId": "c_ada"})
    ops = [c["op"] for c in sb.calls]
    assert ops == ["contacts.search", "deals.create"]
