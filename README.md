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

## The §21 demo (one command, three capabilities — including a multi-provider edge)

```bash
# first requests — compose + verify + register (zero spend, no network)
python composer.py run "create a ghl lead qualification agent"
python composer.py run "create a hubspot deal pipeline agent"
python composer.py run "route qualified ghl leads into hubspot deals"

# second requests — the compounding primitive: REUSE, no rebuild
python composer.py run "create a ghl lead qualification agent"
python composer.py run "create a hubspot deal pipeline agent"
python composer.py run "route qualified ghl leads into hubspot deals"
```

## Layout

| path | what |
|---|---|
| `composer.py` | the §21 loop (CLI) — one DECOMPOSER spec + one sandbox verifier per capability |
| `registry/` | the §6 capability registry + `registry_tool.py` (anti-reinvention lookup) |
| `primitives/ghl/` | the GHL connector — `SandboxBackend` (verified) + `LiveBackend` (opt-in, `GHL_API_KEY`) |
| `primitives/hubspot/` | the HubSpot CRM connector — same contract, `HUBSPOT_API_KEY` live |
| `primitives/stub_model/` | the SHARED model primitive (`llm.intent` + `llm.qualify`) both skills depend on — the graph's shared node |
| `skills/gohighlevel_lead_qualifier/` | composed skill #1 — `qualifier.py` + `SKILL.md` (produces qualified GHL leads) |
| `skills/hubspot_deal_pipeline/` | composed skill #2 — `pipeline.py` + `SKILL.md` |
| `skills/ghl_hubspot_router/` | composed skill #3 — `router.py` + `SKILL.md`: **cross-connector** — routes skill #1's qualified leads into HubSpot deals (multi-provider edges, input = skill #1's output) |
| `tests/` | §11 adversarial sandbox scenarios + the fundamental-primitive reuse test, parametrized over ALL THREE capabilities |

## Honesty notes

- The sandbox is the verified path. The live backends are grounded in the
  documented API surfaces (GHL v1: `services.leadconnectorhq.com`, Bearer key,
  `Version` header; HubSpot v3: `api.hubapi.com`, Bearer PAT, version in path)
  but must be verified against a real account with `GHL_API_KEY` /
  `HUBSPOT_API_KEY` before trust.
- The model is a deterministic keyword BANT scorer (zero-spend), shared by
  both skills as the `llm.intent` / `llm.qualify` primitives. Replacing it
  with a real model is a provider change, not a contract change — no skill
  changes.
- `registry.json` is committed **pristine** (primitives only): CI's first runs
  exercise the full compose→verify→register path for both capabilities, the
  second runs prove REUSE for both.
