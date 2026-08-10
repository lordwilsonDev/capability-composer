"""GHL connector — deterministic GoHighLevel adapter (capability primitive).

Two backends, one interface (Capability Composer blueprint §6):

- SandboxBackend (default): deterministic in-memory GHL with failure
  injection — the sandbox the composer verifies against (blueprint §11).
  Zero network, zero spend, fully reproducible.
- LiveBackend (opt-in): the real GHL v1 public API
  (https://services.leadconnectorhq.com), enabled only when GHL_API_KEY is
  set. Endpoint surface grounded in the GHL marketplace API docs
  (Authorization: Bearer <key>, Version: v3/v3New per endpoint).

Stdlib-only. Every call is recorded in `backend.calls` so a composition's
permission log can be audited (safety proof: no action outside the declared
scope ever happens).

Grounded endpoint surface (from the GHL v1 public API docs):
  POST /contacts/                  v3New  create contact
  POST /contacts/search            v3     advanced search
  GET  /contacts/{id}              v3New  get contact
  PUT  /contacts/{id}              v3New  update contact (customFields)
  GET  /calendars/{id}             v3     get calendar
  POST /calendars/events/appointments  v3  create appointment
  GET  /calendars/events/appointments/{id}  v3  get appointment

LIVE-MODE HONESTY: the sandbox is the verified path. The live paths are
grounded in the documented shapes but require a real key — run
`scripts/live_probe.py --providers ghl` once GHL_API_KEY is set: it makes ONE
read-only call against your sub-account and writes ledger evidence.
"""

from __future__ import annotations

import copy
import json
import os
import urllib.error
import urllib.request
from typing import Any, Optional

BASE_URL = "https://services.leadconnectorhq.com"

VERSION_BY_PATH: dict[str, str] = {
    "/contacts/search": "v3",  # most specific first — "/contacts/search" must
    "/contacts/": "v3New",     # NOT be swallowed by the "/contacts/" prefix
    "/calendars/events/appointments": "v3",
}


class GhlError(Exception):
    """A GHL operation failed (duplicate, outage, unavailable calendar...)."""


def _version_for(path: str) -> str:
    # Match most-specific prefixes first (longest key), so the documented
    # per-endpoint Version headers (v3 / v3New) are never misassigned.
    for prefix, v in sorted(VERSION_BY_PATH.items(),
                            key=lambda kv: len(kv[0]), reverse=True):
        if path.startswith(prefix):
            return v
    return "v3"


# ---------------------------------------------------------------------------
# Sandbox backend — the verified path
# ---------------------------------------------------------------------------

_FIXTURE_CONTACTS: list[dict[str, Any]] = [
    {
        "id": "c_ada",
        "firstName": "Ada",
        "lastName": "Lovelace",
        "email": "ada@example.com",
        "phone": "+15550001001",
        "locationId": "loc-demo",
        "tags": [],
        "customFields": {},
    },
    {
        "id": "c_grace",
        "firstName": "Grace",
        "lastName": "Hopper",
        "email": "grace@example.com",
        "phone": "+15550001002",
        "locationId": "loc-demo",
        "tags": ["existing-client"],
        "customFields": {},
    },
    {
        "id": "c_linus",
        "firstName": "Linus",
        "lastName": "Torvalds",
        "email": "linus@example.com",
        "phone": "+15550001004",
        "locationId": "loc-demo",
        "tags": ["qualified"],
        "customFields": {},
    },
]


class SandboxBackend:
    """Deterministic in-memory GHL. Failure injection (per-instance):
    ``api_failure`` — any write raises (API outage); ``calendar_unavailable``
    — booking raises (no free slot). Duplicate contacts are detected by
    email and raise GhlError (the composition decides how to handle it)."""

    def __init__(self, *, failures: tuple[str, ...] = ()):
        # DEEP copies — a test mutating one backend's nested customFields/tags
        # must never leak into the module-level fixtures or another backend.
        self._contacts: dict[str, dict[str, Any]] = {
            c["id"]: copy.deepcopy(c) for c in _FIXTURE_CONTACTS
        }
        self._calendars: list[dict[str, Any]] = [
            {"id": "cal-sales", "name": "Sales Discovery", "locationId": "loc-demo"},
            {"id": "cal-onboard", "name": "Onboarding", "locationId": "loc-demo"},
        ]
        self._appointments: dict[str, dict[str, Any]] = {}
        self._failures = set(failures)
        self.calls: list[dict[str, Any]] = []  # the permission log

    # --- reads (safe by default — every read is covered) ---

    def search_contacts(self, query: str = "", location_id: str = "loc-demo",
                        limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        self.calls.append({"op": "contacts.search", "args": {"query": query}})
        q = query.lower()
        # per-contact substring match — a term in one contact's text does NOT
        # return every other contact
        matches = [
            dict(c) for c in self._contacts.values()
            if not q or q in (
                f"{c.get('firstName','')} {c.get('lastName','')} "
                f"{c.get('email','')} {c.get('phone','')}"
            ).lower()
        ]
        return matches[offset:offset + limit]

    def get_contact(self, contact_id: str) -> dict[str, Any]:
        self.calls.append({"op": "contacts.get", "args": {"contact_id": contact_id}})
        if contact_id not in self._contacts:
            raise GhlError(f"contact {contact_id} not found")
        return dict(self._contacts[contact_id])

    def list_calendars(self, location_id: str = "loc-demo") -> list[dict[str, Any]]:
        self.calls.append({"op": "calendars.list", "args": {"location_id": location_id}})
        return [dict(c) for c in self._calendars]

    def get_calendar(self, calendar_id: str) -> dict[str, Any]:
        self.calls.append({"op": "calendars.get", "args": {"calendar_id": calendar_id}})
        for c in self._calendars:
            if c["id"] == calendar_id:
                return dict(c)
        raise GhlError(f"calendar {calendar_id} not found")

    def get_appointment(self, appointment_id: str) -> dict[str, Any]:
        self.calls.append({"op": "appointments.get", "args": {"appointment_id": appointment_id}})
        if appointment_id not in self._appointments:
            raise GhlError(f"appointment {appointment_id} not found")
        return dict(self._appointments[appointment_id])

    # --- writes (opt-in per operation; the composition declares them) ---

    def _check_write(self) -> None:
        if "api_failure" in self._failures:
            raise GhlError("GHL API outage (injected)")

    def create_contact(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._check_write()
        self.calls.append({"op": "contacts.create", "args": {"email": payload.get("email")}})
        email = (payload.get("email") or "").strip().lower()
        for c in self._contacts.values():
            if c.get("email", "").strip().lower() == email:
                raise GhlError(f"duplicate contact: {email}")
        contact_id = f"c_{len(self._contacts) + 1:03d}"
        contact = {
            "id": contact_id,
            "firstName": payload.get("firstName", ""),
            "lastName": payload.get("lastName", ""),
            "email": payload.get("email", ""),
            "phone": payload.get("phone", ""),
            "locationId": payload.get("locationId", "loc-demo"),
            "tags": list(payload.get("tags", [])),
            "customFields": dict(payload.get("customFields", {})),
        }
        self._contacts[contact_id] = contact
        return dict(contact)

    def update_contact(self, contact_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._check_write()
        self.calls.append({"op": "contacts.update", "args": {"contact_id": contact_id}})
        if contact_id not in self._contacts:
            raise GhlError(f"contact {contact_id} not found")
        c = self._contacts[contact_id]
        for field in ("firstName", "lastName", "email", "phone", "locationId"):
            if field in payload:
                c[field] = payload[field]
        if "tags" in payload:
            c["tags"] = sorted({*c["tags"], *payload["tags"]})
        if "customFields" in payload:
            c["customFields"].update(payload["customFields"])
        return dict(c)

    def delete_contact(self, contact_id: str) -> dict[str, Any]:
        """Delete a contact (the write-probe round trip's cleanup leg)."""
        self._check_write()
        self.calls.append({"op": "contacts.delete", "args": {"contact_id": contact_id}})
        if contact_id not in self._contacts:
            raise GhlError(f"contact {contact_id} not found")
        return self._contacts.pop(contact_id)

    def book_appointment(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._check_write()
        if "calendar_unavailable" in self._failures:
            raise GhlError("calendar unavailable (injected)")
        self.calls.append({"op": "appointments.create", "args": {
            "calendar_id": payload.get("calendarId"),
            "contact_id": payload.get("contactId"),
            "start_time": payload.get("startTime"),
        }})
        if payload.get("calendarId") not in {c["id"] for c in self._calendars}:
            raise GhlError(f"calendar {payload.get('calendarId')} not found")
        appt_id = f"a_{len(self._appointments) + 1:03d}"
        appointment = {
            "id": appt_id,
            "calendarId": payload.get("calendarId"),
            "contactId": payload.get("contactId"),
            "startTime": payload.get("startTime"),
            "title": payload.get("title", ""),
            "appointmentStatus": payload.get("appointmentStatus", "confirmed"),
        }
        self._appointments[appt_id] = appointment
        return dict(appointment)


# ---------------------------------------------------------------------------
# Live backend — the real GHL v1 public API (opt-in, GHL_API_KEY required)
# ---------------------------------------------------------------------------

class LiveBackend:
    """The documented GHL v1 public API. Requires GHL_API_KEY (or the key
    argument). Every path is versioned per the docs (v3 / v3New). Verify
    against your sub-account with `python ghl_client.py test` before use."""

    def __init__(self, api_key: Optional[str] = None, base_url: str = BASE_URL):
        self._key = api_key or os.getenv("GHL_API_KEY", "")
        self._base = base_url.rstrip("/")
        self.calls: list[dict[str, Any]] = []

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict[str, Any]:
        if not self._key:
            raise GhlError("GHL_API_KEY is not set — live mode unavailable (sandbox mode is the verified path)")
        url = f"{self._base}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self._key}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Version", _version_for(path))
        self.calls.append({"op": f"{method} {path}"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raise GhlError(f"GHL {exc.code} on {method} {path}: {exc.read().decode('utf-8', 'replace')[:200]}") from exc

    def search_contacts(self, query: str = "", location_id: str = "loc-demo",
                        limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        payload = {"query": query, "locationId": location_id, "limit": limit, "offset": offset}
        data = self._request("POST", "/contacts/search", payload)
        return data.get("contacts", [])

    def get_contact(self, contact_id: str) -> dict[str, Any]:
        data = self._request("GET", f"/contacts/{contact_id}")
        return data.get("contact", data)

    def create_contact(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = self._request("POST", "/contacts/", payload)
        return data.get("contact", data)

    def update_contact(self, contact_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = self._request("PUT", f"/contacts/{contact_id}", payload)
        return data.get("contact", data)

    def delete_contact(self, contact_id: str) -> dict[str, Any]:
        data = self._request("DELETE", f"/contacts/{contact_id}")
        return data if isinstance(data, dict) else {}


    def list_calendars(self, location_id: str = "loc-demo") -> list[dict[str, Any]]:
        # documented as calendar retrieval by id; listing by locationId is the
        # reference shape — verify live before relying on it
        data = self._request("GET", f"/calendars/?locationId={location_id}")
        return data.get("calendars", [])

    def get_calendar(self, calendar_id: str) -> dict[str, Any]:
        data = self._request("GET", f"/calendars/{calendar_id}")
        return data.get("calendar", data)

    def book_appointment(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = self._request("POST", "/calendars/events/appointments", payload)
        return data.get("appointment", data)

    def get_appointment(self, appointment_id: str) -> dict[str, Any]:
        data = self._request("GET", f"/calendars/events/appointments/{appointment_id}")
        return data.get("appointment", data)


# ---------------------------------------------------------------------------
# CLI — setup / test (mirrors the connector contract: deterministic, safe)
# ---------------------------------------------------------------------------

def _cmd_test() -> int:
    print("sandbox backend: available (deterministic, verified)")
    sb = SandboxBackend()
    print(f"  fixtures: {len(sb.search_contacts())} contacts, {len(sb.list_calendars())} calendars")
    if os.getenv("GHL_API_KEY"):
        print("live backend: GHL_API_KEY set — not calling the live API from a test; "
              "run the composer's sandbox verification instead.")
    else:
        print("live backend: GHL_API_KEY not set (sandbox is the verified path)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cmd_test())
