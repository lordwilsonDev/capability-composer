---
name: hubspot-deal-pipeline
version: 1.0
kind: composed-skill
composer: capability-composer
verified_by: sandbox scenarios (tests/test_hubspot_pipeline.py) + permission log
---

# hubspot-deal-pipeline v1.0

Composed capability (Capability Composer blueprint §13/§14) — the second
composition proving the discovery layer generalizes: HubSpot CRM primitives +
the **same shared model primitive** as `gohighlevel-lead-qualifier`. The
capability graph shares the model node; nothing is re-implemented.

## Purpose

Track and update the HubSpot deal pipeline from inbound signals: match (or
create) the contact, score deal fit, and create or advance the open deal —
never duplicating contacts or deals.

## Inputs

| input | type | notes |
|---|---|---|
| `contact` | object | `{firstname, lastname, email, phone}` (id optional) |
| `conversation` | string | the inbound signal/message |

## Capabilities composed (all registered, none built)

- `hubspot.contact.read` — find the contact by email
- `hubspot.contact.write` — create when new; tag outcomes
- `hubspot.deal.read` — find the open deal for a contact
- `hubspot.deal.write` — create or advance the deal
- `llm.intent` — spam/escalation/sales detection (shared stub)
- `llm.qualify` — BANT scoring (shared stub)

## Dependencies

- HubSpot (sandbox backend for verification; `HUBSPOT_API_KEY` for live v3)
- local model (shared `primitives/stub_model` — same node as the GHL skill)

## Permissions (declared, enforced by the permission log)

- `crm.contacts.read`, `crm.contacts.write`, `crm.deals.read`, `crm.deals.write`

Every call is recorded in `backend.calls`; verification asserts no call
happens outside this scope.

## Workflow

1. `detect_deal_signal` — spam → quarantine; escalation → escalate
2. `match_contact` — search by email; reuse if found, create only if new
3. `assess_deal` — BANT signals (shared model)
4. `create_or_update_deal` — qualified: create a new deal, or advance the
   existing open deal (search-then-update, never duplicate)
5. `update_crm` — tag + annotate the outcome; failures recorded

## Verification

- Sandbox scenarios (9 adversarial: normal, existing-open-deal, new-contact,
  spam, incomplete, angry, api-failure, rate-limit, duplicate) —
  `python pipeline.py` or `tests/test_hubspot_pipeline.py`
- Permission-log audit — the call set is exactly the declared scope
- Zero-spend — shared stub model, in-memory backend, no network

## Running

```bash
# verify the composition in the sandbox (zero spend)
python pipeline.py

# use it in code
from skills.hubspot_deal_pipeline.pipeline import DealPipeline
from primitives.hubspot.hubspot_client import SandboxBackend, LiveBackend
backend = LiveBackend() if HUBSPOT_API_KEY else SandboxBackend()
outcome = DealPipeline(backend).run(contact, conversation)
```
