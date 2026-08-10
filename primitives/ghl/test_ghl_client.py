"""GHL connector self-tests — zero network, fixtures only (Phase 5).

The sandbox backend is the VERIFIED path; these pin its determinism, the
permission log, duplicate detection, and failure injection.
"""

from __future__ import annotations

import pytest

from primitives.ghl.ghl_client import GhlError, SandboxBackend


def test_sandbox_reads_are_deterministic():
    sb = SandboxBackend()
    assert len(sb.search_contacts()) == 2
    assert len(sb.list_calendars()) == 2
    assert sb.get_contact("c_ada")["email"] == "ada@example.com"
    # deterministic: same calls, same results, no hidden state
    assert sb.search_contacts("grace")[0]["id"] == "c_grace"


def test_create_duplicate_contact_detected_by_email():
    sb = SandboxBackend()
    with pytest.raises(GhlError, match="duplicate"):
        sb.create_contact({"firstName": "Ada", "email": "ADA@example.com"})


def test_update_contact_merges_tags_and_custom_fields():
    sb = SandboxBackend()
    sb.update_contact("c_ada", {"tags": ["qualified"], "customFields": {"score": "85"}})
    ada = sb.get_contact("c_ada")
    assert ada["tags"] == ["qualified"]
    assert ada["customFields"] == {"score": "85"}


def test_book_appointment_and_roundtrip():
    sb = SandboxBackend()
    a = sb.book_appointment({"calendarId": "cal-sales", "contactId": "c_ada",
                             "startTime": "2026-08-14T15:00:00Z", "title": "Discovery"})
    assert sb.get_appointment(a["id"])["startTime"] == "2026-08-14T15:00:00Z"


def test_failure_injection_api_outage():
    sb = SandboxBackend(failures=("api_failure",))
    with pytest.raises(GhlError, match="outage"):
        sb.create_contact({"email": "new@example.com"})
    with pytest.raises(GhlError, match="outage"):
        sb.book_appointment({"calendarId": "cal-sales", "contactId": "c_ada", "startTime": "x"})


def test_failure_injection_calendar_unavailable():
    sb = SandboxBackend(failures=("calendar_unavailable",))
    with pytest.raises(GhlError, match="unavailable"):
        sb.book_appointment({"calendarId": "cal-sales", "contactId": "c_ada", "startTime": "x"})
    # reads still work during a partial outage
    assert len(sb.search_contacts()) == 2


def test_permission_log_records_every_call():
    sb = SandboxBackend()
    sb.search_contacts("ada")
    sb.update_contact("c_ada", {"tags": ["qualified"]})
    ops = [c["op"] for c in sb.calls]
    assert ops == ["contacts.search", "contacts.update"]


def test_live_backend_fails_closed_without_key(monkeypatch):
    monkeypatch.delenv("GHL_API_KEY", raising=False)
    from primitives.ghl.ghl_client import LiveBackend

    live = LiveBackend()
    with pytest.raises(GhlError, match="GHL_API_KEY"):
        live.search_contacts()
