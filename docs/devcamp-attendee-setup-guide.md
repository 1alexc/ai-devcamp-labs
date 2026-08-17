# AI DevCamp 2026: Pre-Camp Setup Guide
**Send after registration, via email + pinned in Discord. Complete everything before Saturday 5 September.**
Setup takes 45 to 60 minutes. Do it early: the Discord #setup-help channel and a live setup clinic (date TBC, week of 25 Aug) are there to unblock you before Day 1.

---

## What you're setting up, in one line
A Google Cloud account with $300 of free credit, a local dev environment, and the Antigravity IDE, everything the four workshops need.

## Important notes before you start
- **Cost:** the whole camp is designed to fit well inside Google's $300 free trial credit (90 days). We'll set a real spend cap (not just an email alert) together in Week 1 so nothing surprises you.
- **Card required:** Google asks for a credit/debit card to verify identity when you start the free trial. You are not charged during the trial, and it does not auto-charge when the trial ends without you upgrading.
- **The $300 credit covers Vertex AI, not the separate Gemini API / AI Studio billing system.** All model calls in this camp (including image generation) go through **Vertex AI inside your GCP project**, so they're covered. If you ever grab a standalone API key from aistudio.google.com instead, that's on a different account with its own billing (Google is moving it to a mandatory prepaid balance from Sep 2026); don't put a card on that one. Its free tier is enough as a backup (see Step 3).
- **Your IDE, your account:** you'll code in Google Antigravity (free tier). Any AI IDE accounts, quotas, or subscriptions are your own responsibility; the camp only uses your GCP credit for cloud resources.
- **Windows users:** you'll need WSL 2 (Windows Subsystem for Linux). Everything below happens inside WSL.

---

## Step 1: Google Cloud account + project (15 min)
1. Go to cloud.google.com/free and start the free trial with a Google account (a personal Gmail is fine; work accounts often have restrictions, avoid them).
2. Create a new project named `ai-devcamp-<yourname>`.
3. Set the default region when asked: **europe-west1**.
4. Enable these APIs (console → APIs & Services → Enable): Vertex AI API, Cloud Run, Cloud Build, Sensitive Data Protection (DLP), Model Armor, Discovery Engine.

✅ Checkpoint: the Vertex AI page in the console loads without an "enable API" prompt.

5. **Set a spend cap** (console → Billing → Budgets & alerts): create a budget scoped to your `ai-devcamp-<yourname>` project, alert thresholds at 50/80/100%, **and** turn on the spend-cap option scoped to the Vertex AI / Gemini API service (currently in preview). Unlike a plain alert, the spend cap actually pauses further Vertex AI usage once you hit 100%. A plain email alert doesn't stop anything.

✅ Checkpoint: Budgets & alerts shows your budget with the spend cap enabled, not just an alert.

## Step 2: Local tools (15 min)
Install, in order:
1. **Python 3.11+** (python.org or your package manager)
2. **uv** (Python package manager): `curl -LsSf https://astral.sh/uv/install.sh | sh`
3. **Node.js 20+** (nodejs.org)
4. **Google Cloud SDK** (cloud.google.com/sdk), then:
   ```
   gcloud auth login
   gcloud auth application-default login
   gcloud config set project ai-devcamp-<yourname>
   ```
5. **ADK**: `pip install google-adk` (or `uv pip install google-adk`)
6. **Git** + a **GitHub account** (in Week 1 you'll create your own private repo from our template with one click; your code stays private, you just grant the mentor account read access so we can support you and track progress)

✅ Checkpoint: `adk --version`, `node --version`, and `gcloud config get-value project` all return sensible answers.

## Step 3: Antigravity (10 min)
1. Download Google Antigravity and sign in with your Google account.
2. Configure the quota fallback so a busy workshop can't stall you: in Antigravity settings, add a Vertex AI connection (your project, europe-west1) as your primary model access. For the backup key, grab a **free-tier** key from aistudio.google.com (no card, no billing setup). It's rate-limited but enough to get you through a stall, and it's completely separate from your $300 Vertex AI credit. Do **not** enable Prepay/billing on that AI Studio account; you shouldn't need it. Full walkthrough with screenshots: [link to guide, TBC].

✅ Checkpoint: ask Antigravity to create a hello-world Python file; review its plan, accept, run it.

## Step 4: Environment variables (5 min)
Create a `.env` you'll reuse all camp:
```
GOOGLE_GENAI_USE_VERTEXAI=TRUE
GOOGLE_CLOUD_PROJECT=ai-devcamp-<yourname>
GOOGLE_CLOUD_LOCATION=europe-west1
DRY_RUN=true
```
(`DRY_RUN=true` means the LinkedIn posting tool logs posts instead of publishing. Nobody posts anything by accident in this camp.)

## Step 5: Optional but recommended (10 min)
- **LinkedIn Developer app** (only needed if you want to publish a real post in Week 4; everything else works in dry-run): developer guide will be shared in Week 3. Skip for now if short on time.
- Join **Discord** [invite link] and say hi in #introductions. Channels: #setup-help, #week-1 to #week-4, #show-your-use-case.
- Think of **one use case of your own**: an agent you'd like to exist. You'll declare it in Week 1 and build it alongside ours.

---

## The running use case you'll build
An agent that turns your idea (text, with a voice demo) into a formatted LinkedIn post: it researches the web, remembers your past posts, drafts with Agent Skills, waits for your approval in a proper web UI, and posts via MCP. Built in Week 1, made production-ready by Week 4.

## Schedule
| Week | Thursday (theory, 1hr) | Saturday (workshop, 2hr) | Stage |
|---|---|---|---|
| 1 | 3 Sept (kickoff, hybrid: Skyscanner + online) | 5 Sept | Build |
| 2 | 10 Sept | 12 Sept | Scale |
| 3 | 17 Sept | 19 Sept | Govern |
| 4 | 24 Sept | 26 Sept | Optimise |

## If something breaks
1. Search #setup-help first, someone has probably hit it.
2. Post: your OS, the exact command, the exact error.
3. Still stuck on Saturday? Pair with a neighbour; broken environments follow along on the instructor project and fix after.

See you on the 3rd.
GDG London × AI DevCamp mentors
