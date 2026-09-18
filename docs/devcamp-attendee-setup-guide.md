# AI DevCamp Pro: Agents — Pre-Camp Setup Guide
**Sent via email + pinned in Discord from 24 September. Complete everything before Saturday 26 September.**
Setup takes 45 to 60 minutes. Do it early: the Discord #setup-help channel, your buddy group, and a live setup clinic (week of 21 September, date TBC) are there to unblock you before the first workshop.

---

## What you're setting up, in one line
A Google Cloud account with $300 of free credit and a spend cap, a local dev environment, a coding agent (Antigravity by default) with a Vertex AI fallback, and a buddy who has checked your setup.

## Important notes before you start
- **Cost:** the whole camp is designed to fit well inside Google's $300 free trial credit (90 days). You'll set a real spend cap (not just an email alert) in Step 1 so nothing surprises you.
- **Card required:** Google asks for a credit/debit card to verify identity when you start the free trial. You are not charged during the trial, and it does not auto-charge when the trial ends without you upgrading.
- **The $300 credit covers Vertex AI, not the separate Gemini API / AI Studio billing system.** All model calls in this camp (including image generation) go through **Vertex AI inside your GCP project**, so they're covered. If you ever grab a standalone API key from aistudio.google.com, that's a different account with its own billing (Google is moving it to a mandatory prepaid balance from September 2026); don't put a card on that one.
- **Your coding agent, your account:** you'll code with Google Antigravity by default (Claude Code or Gemini CLI also work; the labs give you prompts and checkpoints, not code). Any AI IDE accounts, quotas, or subscriptions are your own responsibility; the camp only uses your GCP credit for cloud resources.
- **Windows users:** you'll need WSL 2 (Windows Subsystem for Linux). Everything below happens inside WSL.

---

## Step 1: Google Cloud account + project + spend cap (20 min)
1. Go to cloud.google.com/free and start the free trial with a Google account (a personal Gmail is fine; work accounts often have restrictions, avoid them).
2. Create a new project named `ai-devcamp-<yourname>`.
3. Set the default region when asked: **europe-west1**.
4. Enable these APIs (console → APIs & Services → Enable): Vertex AI API, Cloud Run, Cloud Build, Sensitive Data Protection (DLP), Model Armor, Discovery Engine, Cloud Resource Manager API, App Hub API. The last two aren't obviously "Vertex AI" but Agent Runtime deploys (Session 2, Scale) fail or show a degraded console without them — confirmed by actually deploying, not just reading docs.
5. **Set a spend cap** (console → Billing → Budgets & alerts): create a budget scoped to your `ai-devcamp-<yourname>` project, alert thresholds at 50/80/100%, **and** turn on the spend-cap option scoped to the Vertex AI / Gemini API service (currently in preview). Unlike a plain alert, the spend cap actually pauses further Vertex AI usage once you hit 100%. A plain email alert doesn't stop anything.

✅ Checkpoint: the Vertex AI page in the console loads without an "enable API" prompt, and Budgets & alerts shows your budget with the spend cap enabled.

## Step 2: Local tools (15 min)
Install, in order:
1. **Python 3.12+** (python.org or your package manager; the project requires 3.12)
2. **uv** (Python package manager): `curl -LsSf https://astral.sh/uv/install.sh | sh`
3. **Node.js 20+** (nodejs.org)
4. **Google Cloud SDK** (cloud.google.com/sdk), then:
   ```
   gcloud auth login
   gcloud auth application-default login
   gcloud config set project ai-devcamp-<yourname>
   ```
5. **ADK**: `uv pip install google-adk` (or `pip install google-adk`)
6. **Git** + a **GitHub account**. In Lab 1 you'll clone the starter repo: the web frontend, the LinkedIn MCP server and the Agent Skills come prebuilt; you build the agent backend.

✅ Checkpoint: `adk --version`, `node --version`, and `gcloud config get-value project` all return sensible answers.

## Step 3: Antigravity with the Vertex AI fallback (10 min, required)
1. Download Google Antigravity and sign in with your Google account.
2. **Required, not optional:** configure the Vertex AI fallback. With hundreds of people in the workshop at once, Antigravity's free quota can run out mid-session; the fallback bills to your $300 credit instead and keeps you moving. In Antigravity settings, add a Vertex AI connection (your project). **Prefer `global` over `europe-west1` for the connection's location if Antigravity offers the choice** — `europe-west1` doesn't serve `gemini-flash-latest` (see Step 4's checkpoint below; not separately re-verified for Antigravity's own connection UI specifically, so if `global` isn't an option there, try it and confirm with the checkpoint before Saturday rather than assuming). Full walkthrough with screenshots: [link to guide, TBC].
3. Optional second backup: a **free-tier** key from aistudio.google.com (no card, no billing setup). It's rate-limited but enough to get through a stall, and it's separate from your $300 credit. Do **not** enable Prepay/billing on that account.
4. Prefer another coding agent (Claude Code, Gemini CLI)? Fine. The labs give you the prompt to type and the result to check, not the code, so any agent works.

✅ Checkpoint: ask Antigravity to create a hello-world Python file; review its plan, accept, run it. Then switch the model connection to Vertex AI and repeat once.

## Step 4: Environment variables (5 min)
Create a `.env` you'll reuse all camp:
```
GOOGLE_GENAI_USE_VERTEXAI=TRUE
GOOGLE_CLOUD_PROJECT=ai-devcamp-<yourname>
GOOGLE_CLOUD_LOCATION=global
DRY_RUN=true
```
(`DRY_RUN=true` means the LinkedIn posting tool logs posts instead of publishing. Nobody posts anything by accident in this camp.)

**Use `global`, not `europe-west1`, here — confirmed by direct testing, not assumption.** `gemini-flash-latest` returns a 404 "Publisher model not found" in `europe-west1` (and in `europe-west2`, hit the same way during Scale deploy testing): regional Vertex AI endpoints can lag behind `global` for the newest flash models. `global` is the one location confirmed to actually serve `gemini-flash-latest`. This is unrelated to Step 1's project region (europe-west1 is fine there — that's just project metadata, not the model call's location) and to Model Armor's region in Session 3 (Model Armor specifically needs a real region, `global` doesn't work for *that* call — the two settings are independent, don't conflate them).

✅ Checkpoint: `uv run adk web` against any agent and send one message — a 404 "Publisher model ... was not found" means `GOOGLE_CLOUD_LOCATION` is still set to a region, not `global`.

## Step 5: Buddy check (5 min)
You'll be placed in a small buddy group in Discord before the first workshop. Before Saturday, post your checkpoint results from Steps 1–3 in your group thread and confirm one buddy's results in return. Broken setups get fixed in the group or at the setup clinic, not during the workshop.

## Step 6: Recommended before Saturday (10 min)
- **Buffer account:** Lab 1 publishes through Buffer's MCP server. Create a free Buffer account and connect at least one channel (a LinkedIn profile is fine). No card needed for the free tier. You'll grab the API key during the lab.
- **LinkedIn Developer app** (optional bonus step in Lab 1; everything else works through Buffer or in dry-run): skip for now if short on time, a guide is linked from the lab.
- **GCS image hosting for Buffer** (optional, advanced — not part of Lab 1's taught steps, which keep Buffer text-only by design; only relevant if you want Buffer posts to carry an image like the reference build does): needs a one-time IAM setup outside the app's own reach — a dedicated service account, granted signing rights via impersonation rather than a downloaded key. Set `GOOGLE_CLOUD_PROJECT`/`GCS_BUCKET_NAME` in your `.env`, then run `./gcs-setup.sh` from the repo root — it does the IAM setup and updates your `.env` for you (see `.env.example`'s GCS section for what it does). Skip this entirely unless you're specifically extending past Lab 1.
- Join **Discord** [invite link] and say hi in #introductions. Channels: #setup-help, #session-1 to #session-4, #show-your-use-case.
- Think of **one use case of your own**: an agent you'd like to exist. You'll declare it in Lab 1 and build it alongside ours as each lab's Assignment.

---

## The running use case you'll build
An agent that turns your idea into a formatted social post: it researches the web, drafts with Agent Skills, generates an image, waits for your approval in a proper web UI, and publishes via MCP. Built in Lab 1, made production-ready across Labs 2 to 4.

## Schedule
| Session | Thursday (theory, 1hr) | Saturday (workshop, 2hr) | Pillar |
|---|---|---|---|
| Kickoff | 23 Sept, 6–9pm, hybrid: Skyscanner + online | — | Series overview + trends |
| 1 | 24 Sept | 26 Sept | Build |
| 2 | 1 Oct | 3 Oct | Scale |
| 3 | 8 Oct | 10 Oct | Govern |
| 4 | 15 Oct | 17 Oct | Optimise |

## If something breaks
1. Ask your buddy group, then search #setup-help; someone has probably hit it.
2. Post: your OS, the exact command, the exact error.
3. Still stuck on Saturday? Pair with a buddy; broken environments follow along on the instructor project and fix after.

See you on Saturday 26 September.
GDG London × AI DevCamp mentors
