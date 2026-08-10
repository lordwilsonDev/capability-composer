---
name: ghl-hubspot-router
version: 1.0
kind: composed-skill
composer: capability-composer
verified_by: sandbox scenarios (tests/test_ghl_hubspot_router.py) + permission log
---

# ghl-hubspot-router v1.0

Cross-connector composed capability (Capability Composer blueprint §13/§14):
route qualified GHL leads into HubSpot deals — ONE workflow composing TWO
connectors + the shared model primitive. The capability graph's multi-provider
edge, machine-checked.

**Input contract = the output of `gohighlevel-lead-qualifier`**: qualified
GHL leads (`contact` + `conversation`). Skill #1 produces them; this skill
routes them.

## Purpose

Take qualified GHL leads and sync them into HubSpot: match or create the
contact, create or advance the open deal, then confirm the sync back in GHL —
never duplicating a contact or deal on either side, and never confirming a
sync that didn't happen.

## Inputs

| input | type | notes |
|---|---|---|
| `leads` | list[object] | `{id, firstname, lastname, email, phone, conversation}` — qualified GHL leads |

## Capabilities composed (all registered, none built)

| capability | used for |
|---|---|
| `ghl.contact.read` | verify the lead still exists upstream |
| `ghl.contact.write` | tag the lead `synced-to-hubspot` on success |
| `llm.intent` | spam / escalation quarantine (shared node) |
| `llm.qualify` | BANT re-gate before routing (shared node) |
| `hubspot.contact.read` / `hubspot.contact.write` | match-or-create by email |
| `hubspot.deal.read` / `hubspot.deal.write` | create new or advance the open deal |

## Dependencies

- GoHighLevel (sandbox backend for verification; `GHL_API_KEY` live)
- HubSpot (sandbox backend; `HUBSPOT_API_KEY` live)
- local model (shared `primitives/stub_model` — same node as skills #1 and #2)

## Permissions (declared, enforced by the permission log)

- `contact.read`, `contact.write` (GHL) · `crm.contacts.read/write`,
  `crm.deals.read/write` (HubSpot)

Every call on BOTH backends is recorded; verification asserts the merged call
set stays inside this scope.

## Workflow

1. `receive_leads` — the qualified-lead contract from skill #1
2. `assess` — intent quarantine (spam/escalation) + BANT re-gate
3. `verify_in_ghl` — the lead must still exist upstream
4. `route_to_hubspot` — match-or-create contact → create-or-advance deal
5. `confirm_in_ghl` — tag `synced-to-hubspot` only after the route succeeded

## Failure semantics

| state | meaning |
|---|---|
| `routed` / `advanced` | synced, confirmed back in GHL |
| `unconfirmed` | deal exists in HubSpot but the GHL confirm failed — partial state recorded, never silently dropped |
| `failed` | HubSpot write failed — GHL untouched, no false confirmation |
| `skipped_unqualified` / `quarantined` / `skipped_missing` | gated before any write |

## Verification

- Sandbox scenarios (8 adversarial: route-new, advance-existing,
  create-unknown, skip-unqualified, quarantine-spam, skip-missing,
  confirm-failure-partial, hubspot-outage) — `python router.py` or
  `tests/test_ghl_hubspot_router.py`
- Merged permission-log audit across both connectors
- Zero-spend — shared stub model, in-memory backends, no network

## Running

```bash
# verify the composition in the sandbox (zero spend)
python router.py

# use it in code
from skills.ghl_hubspot_router.router import LeadRouter
from primitives.ghl.ghl_client import SandboxBackend as GhlBackend
from primitives.hubspot.hubspot_client import SandboxBackend as HubspotBackend
outcomes = LeadRouter(GhlBackend(), HubspotBackend()).route(qualified_leads)
```
