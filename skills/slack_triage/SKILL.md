---
name: slack-triage
version: 1.0
kind: composed-skill
composer: capability-composer
verified_by: sandbox scenarios (tests/test_slack_triage.py) + permission log
---

# slack-triage v1.0

Composed capability (Capability Composer blueprint §13/§14) — the fourth
composition proving the discovery layer generalizes to a **messaging**
provider: Slack primitives + the **same shared model primitive** as the CRM
skills. The capability graph shares the model node; nothing is re-implemented.

## Purpose

Triage Slack messages: detect intent via the shared model, post a canned
reply, escalate real support problems to a human, and quarantine spam —
degrading honestly whenever Slack misbehaves.

## Inputs

| input | type | notes |
|---|---|---|
| `message` | object | `{channel, user, text}` — an inbound Slack message |

## Capabilities composed (all registered, none built)

- `slack.channel.read` — verify the channel exists and the bot is a member
- `slack.user.read` — resolve the sender
- `slack.message.write` — post the reply (chat.postMessage)
- `llm.intent` — spam/escalation/sales/other detection (shared stub)

Note: this skill depends on `llm.intent` only — a second proof that the model
node is shared as a *node*, not a bundle.

## Dependencies

- Slack (sandbox backend for verification; `SLACK_API_KEY` for live Web API)
- local model (shared `primitives/stub_model` — same node as the GHL/HubSpot skills)

## Permissions (declared, enforced by the permission log)

- `channels.read`, `users.read`, `message.write`

Every call is recorded in `backend.calls`; verification asserts no call
happens outside this scope.

## Workflow

1. `verify_channel` — channel must exist and the bot must be a member
2. `resolve_user` — sender's name (a read failure degrades, never crashes)
3. `detect_intent` — shared model (spam / support_escalation / sales / other)
4. `decide` — spam → **quarantined, no reply posted** (a reply rewards the
   spammer); escalation → flagged to a human; sales/other → canned reply
5. `reply_or_quarantine` — only a successful `chat.postMessage` counts as
   replied; a failure is `degraded`, never a false confirmation

## Verification

- Sandbox scenarios (9 adversarial: normal-question, sales-intent,
  support-escalation, spam, other, api-failure, rate-limit, not-a-member,
  users-endpoint-miss) — `python triage.py` or `tests/test_slack_triage.py`
- Permission-log audit — the call set is exactly the declared scope
- Zero-spend — shared stub model, in-memory backend, no network

## Running

```bash
# verify the composition in the sandbox (zero spend)
python triage.py

# use it in code
from skills.slack_triage.triage import TriageBot
from primitives.slack.slack_client import SandboxBackend, LiveBackend
backend = LiveBackend() if SLACK_API_KEY else SandboxBackend()
outcome = TriageBot(backend).run({"channel": "C_SUPPORT", "user": "U_ALICE",
                                  "text": "How do I reset my password?"})
```
