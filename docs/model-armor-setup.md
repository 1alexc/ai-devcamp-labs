# Setup guide: Model Armor + DLP for the guardrails

Week 3's guardrails call two Google Cloud services. Both were enabled during
initial setup (`modelarmor.googleapis.com`, `dlp.googleapis.com`); this guide creates
the Model Armor template and points the app at it.

## 1. Create the Model Armor template

From the repo root:

```bash
./gcs-setup.sh        # Step 6 creates it in us-central1 and proves it blocks
```

(`setup/04-model-armor.sh` referenced in earlier drafts of this guide no longer
exists; the template is now created by `gcs-setup.sh`.) This creates a template `devcamp-guardrails` in `europe-west2` with:
- **Responsible-AI filters** (hate speech, harassment, sexually explicit,
  dangerous) at `MEDIUM_AND_ABOVE` — the build guide's threshold.
- **Prompt-injection / jailbreak** detection, enabled.
- **`enforcementType: INSPECT_AND_BLOCK`.** Not `INSPECT_ONLY`: that mode returns
  no results to the caller, so the guardrails silently never fire (see
  [LEARNINGS.md](LEARNINGS.md)).
- It then fires a known injection string at the live API and asserts the
  verdict is `MATCH_FOUND` before printing success.

> **Region caveat**: the script does NOT enable the malicious-URI filter.
> `europe-west2` supports Model Armor but not that specific capability
> (`CAPABILITY_NOT_SUPPORTED`). Model Armor availability is **per-capability**,
> not per-region — a region "having" Model Armor doesn't mean it has every
> filter. If you switch regions, re-check which filters that region supports.

## 2. Wire it into the app

Copy the printed value into `backend/social_poster/.env`:

```
MODEL_ARMOR_TEMPLATE_ID=devcamp-guardrails
MODEL_ARMOR_REGION=europe-west2
```

Leaving `MODEL_ARMOR_TEMPLATE_ID` **blank** turns off all three guardrail
layers — the app reverts to Week 2 behaviour. That's the switch for a
before/after demo.

## 3. The quota-project gotcha (both services)

Model Armor and DLP are called via REST with your local ADC. User-account ADC
attributes API quota to the OAuth client's project unless you send an explicit
`x-goog-user-project` header — without it DLP returns
`403 … requires a quota project` and Model Armor can misbehave. `guardrails.py`
sets that header on every call (`_headers()`); if you copy this pattern
elsewhere, carry the header too.

## 4. Verify end to end

```bash
./evals/run.sh
```

The script starts the mock memory agent and sets `MEMORY_AGENT_CARD_URL` for you
(without a memory agent `consults_memory` fails, silently), and exits non-zero
if any case fails (bare `adk eval` always exits 0).

Expect **5 / 5**. Two of those cases (`blocks_prompt_injection`, `redacts_pii`)
exercise the guardrails directly; the injection-attack table in
[injection-attack-table.md](injection-attack-table.md) is the hand-off artefact
for Renuka's Week 3 demo.
