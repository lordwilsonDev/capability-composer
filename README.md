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

## The §21 demo (one command)

```bash
# first request — compose + verify + register (zero spend, no network)
python composer.py run "create a ghl lead qualification agent"

# second request — the compounding primitive: REUSE, no rebuild
python composer.py run "create a ghl lead qualification agent"
```

## Layout

| path | what |
|---|---|
| `composer.py` | the §21 loop (CLI) |
| `registry/` | the §6 capability registry + `registry_tool.py` (anti-reinvention lookup) |
| `primitives/ghl/` | the GHL connector — `SandboxBackend` (verified) + `LiveBackend` (opt-in, `GHL_API_KEY`) |
| `skills/gohighlevel_lead_qualifier/` | the composed skill — `qualifier.py` (workflow) + `SKILL.md` (§14 shape) |
| `tests/` | §11 adversarial sandbox scenarios + the fundamental-primitive reuse test |

## Honesty notes

- The sandbox is the verified path. The live GHL backend is grounded in the
  documented v1 API surface (`services.leadconnectorhq.com`, Bearer key,
  `Version` header) but must be verified against a real sub-account with
  `GHL_API_KEY` before trust.
- The model is a deterministic keyword BANT scorer (zero-spend). Replacing it
  with a real model is a provider change, not a contract change.
- `registry.json` is committed **pristine** (primitives only): CI's first run
  exercises the full compose→verify→register path, the second run proves REUSE.
