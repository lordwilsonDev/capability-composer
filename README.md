# Capability Composer — §21 prototype

A **capability-composition layer**, not another AI stack. If a capability
already exists, compose it. If it doesn't, create only the missing primitive.

```text
"I want this capability"
        ↓
  anti-reinvention gate (does it exist? can we compose it? is the gap real?)
        ↓
  discover → decompose → compose → sandbox-verify → register → reuse next time
```

## The five laws (constitutional, machine-checked)

1. **Reuse before build** — `composer.py run` checks the registry first.
2. **Compose before fork** — a dag/skill of registered primitives, not a rebuild.
3. **Prove the gap** — a missing primitive aborts the compose (exit 1); only
   `system-connector`-style work may fill it, and only after evidence.
4. **Verify before promote** — the 10 adversarial sandbox scenarios run before
   anything is registered; a failure means no registration.
5. **Every discovery becomes knowledge** — the promoted skill is registered
   (status `VERIFIED`); the identical request is then served by the catalog.

## The §21 demo (one command, four capabilities — multi-provider + multi-domain)

```bash
# first requests — compose + verify + register (zero spend, no network)
python composer.py run "create a ghl lead qualification agent"
python composer.py run "create a hubspot deal pipeline agent"
python composer.py run "route qualified ghl leads into hubspot deals"
python composer.py run "create a slack triage bot"

# second requests — the compounding primitive: REUSE, no rebuild
python composer.py run "create a ghl lead qualification agent"
python composer.py run "create a hubspot deal pipeline agent"
python composer.py run "route qualified ghl leads into hubspot deals"
python composer.py run "create a slack triage bot"
```

## Layout

| path | what |
|---|---|
| `composer.py` | the §21 loop (CLI) — one DECOMPOSER spec + one sandbox verifier per capability |
| `registry/` | the §6 capability registry + `registry_tool.py` (anti-reinvention lookup) |
| `primitives/ghl/` | the GHL connector — `SandboxBackend` (verified) + `LiveBackend` (opt-in, `GHL_API_KEY`) |
| `primitives/hubspot/` | the HubSpot CRM connector — same contract, `HUBSPOT_API_KEY` live |
| `primitives/slack/` | the Slack messaging connector — same contract, `SLACK_API_KEY` live (third provider, different domain) |
| `primitives/stub_model/` | the SHARED model primitive (`llm.intent` + `llm.qualify`) every skill depends on — the graph's shared node |
| `skills/gohighlevel_lead_qualifier/` | composed skill #1 — `qualifier.py` + `SKILL.md` (produces qualified GHL leads) |
| `skills/hubspot_deal_pipeline/` | composed skill #2 — `pipeline.py` + `SKILL.md` |
| `skills/ghl_hubspot_router/` | composed skill #3 — `router.py` + `SKILL.md`: **cross-connector** — routes skill #1's qualified leads into HubSpot deals (multi-provider edges, input = skill #1's output) |
| `skills/slack_triage/` | composed skill #4 — `triage.py` + `SKILL.md`: messaging domain, depends on `llm.intent` only |
| `tests/` | §11 adversarial sandbox scenarios + the fundamental-primitive reuse test, parametrized over ALL FOUR capabilities |

## Live verification (opt-in, never in CI)

The sandbox is the verified path; the live API surface is only trusted after
`scripts/live_probe.py` proves it once, explicitly:

```bash
GHL_API_KEY=... HUBSPOT_API_KEY=... SLACK_API_KEY=... python scripts/live_probe.py
```

What it does, honestly: **one READ-ONLY call per provider that has a key**
(GHL `POST /contacts/search`, HubSpot `POST /crm/v3/objects/contacts/search`,
Slack `GET /api/conversations.list` — list, never write), then writes a
§8-shaped ledger evidence artifact to
`evidence/live/` (content-addressed `artifact_hash`, polarity SUPPORTING /
CONTRADICTING, full provenance layers). Safety invariants: no key → SKIP
(exit 0, inert, no evidence); keyed provider fails → CONTRADICTING evidence +
exit 1 (fail loud); `--dry-run` shows what would be probed with no network;
write verification is deliberately out of scope (a real write belongs on a
dedicated sandbox account, manually audited). Never wired into CI — CI stays
zero-spend. `evidence/` is gitignored; commit a run explicitly for a durable
record.

## Honesty notes

- The live backends are grounded in the documented API surfaces (GHL v1:
  `services.leadconnectorhq.com`, Bearer key, `Version` header; HubSpot v3:
  `api.hubapi.com`, Bearer PAT, version in path) — verified by the probe
  above, never assumed.
- The model is a deterministic keyword BANT scorer (zero-spend), shared by
  all four skills as the `llm.intent` / `llm.qualify` primitives. Replacing
  it with a real model is a provider change, not a contract change — no skill
  changes. `slack-triage` proves the model node is a *node*, not a bundle: it
  depends on `llm.intent` only.
- `registry.json` is committed **pristine** (primitives only): CI's first runs
  exercise the full compose→verify→register path for all four capabilities,
  the second runs prove REUSE for all four.
