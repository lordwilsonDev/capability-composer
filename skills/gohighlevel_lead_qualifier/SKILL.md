---
name: gohighlevel-lead-qualifier
version: 1.0
kind: composed-skill
composer: capability-composer
verified_by: sandbox scenarios (tests/test_sandbox_scenarios.py) + permission log
---

# gohighlevel-lead-qualifier v1.0

Composed capability (Capability Composer blueprint §13/§14): qualify inbound
GHL leads and route qualified leads toward appointment booking, using only
registered primitives — nothing rebuilt.

## Purpose

Qualify inbound GHL leads (BANT scoring) and route them:

- **qualified → book** a discovery appointment (calendar + appointment primitives)
- **not yet ready → nurture** (CRM tag + custom-field annotation)
- **spam → quarantine**, **escalation → route to human**
- **any GHL failure → degrade to nurture**, never crash, never book twice

## Inputs

| input | type | notes |
|---|---|---|
| `contact` | object | `{id, firstName, email, phone}` |
| `conversation` | string | the lead's message(s) |

## Capabilities composed (all registered, none built)

- `ghl.contact.read` — locate/confirm the contact
- `ghl.contact.write` — tag + annotate (nurture/qualified/quarantined)
- `ghl.calendar.read` — resolve the sales calendar
- `ghl.appointment.write` — book discovery
- `llm.intent` — spam/escalation/sales detection (stub in sandbox)
- `llm.qualify` — BANT scoring (stub in sandbox)

## Dependencies

- GoHighLevel (sandbox backend for verification; `GHL_API_KEY` for live)
- local model (stub in sandbox; replaceable via the model contract)

## Permissions (declared, enforced by the permission log)

- `contact.read`, `contact.write`, `calendar.read`, `appointment.create`

Every GHL call is recorded in `backend.calls`; the sandbox verification
asserts no call happens outside this scope.

## Workflow

1. `detect_intent` — spam → quarantine; escalation → escalate
2. `qualify` — BANT signals (budget/authority/need/timeline; ≥3 with
   need+timeline = qualified)
3. `determine_readiness` — qualified → book; else nurture
4. `book_or_nurture` — book discovery appointment; on any GHL failure,
   degrade to nurture and record the error
5. `update_crm` — tag + annotate `qualifier_outcome` / `qualifier_error`

## Verification

- Sandbox scenarios (10 adversarial inputs: normal, fake, incomplete, angry,
  spam, ambiguous, calendar-unavailable, api-failure, duplicate,
  prompt-injection) — `python qualifier.py` or `tests/test_sandbox_scenarios.py`
- Permission-log audit — assert the call set is exactly the declared scope
- Zero-spend — stub model, in-memory backend, no network

## Running

```bash
# verify the composition in the sandbox (zero spend)
python qualifier.py

# use it in code
from skills.gohighlevel_lead_qualifier.qualifier import LeadQualifier
from primitives.ghl.ghl_client import SandboxBackend, LiveBackend
backend = LiveBackend() if GHL_API_KEY else SandboxBackend()
outcome = LeadQualifier(backend).run(contact, conversation)
```
