# Setup guide: observability (Week 4)

Once the agent is on Cloud Run, use Google Cloud's tracing and analytics to
see what's actually happening in production.

## 1. Cloud Trace — find the slowest span

ADK emits OpenTelemetry traces automatically. In the console: **Trace →
Trace explorer**, filtered to the `social_poster` service.

Each request fans out into spans: orchestrator → `memory_agent` (remote A2A
hop) → `research_agent` → `draft_agent` → `create_post`. **Identify the slowest
span** — on this pipeline it's almost always either the A2A memory hop (network
+ a full RAG retrieval) or `research_agent` (google_search grounding). That
tells you where the Phase 6 model-downgrade or a caching change would pay off.
📸 Trace waterfall screenshot = Week 4 latency slide.

> Tip: the Model Armor guardrail calls show up as their own spans too. If
> guardrails add noticeable latency, that's the trade-off to name explicitly on
> the governance slide — safety isn't free.

## 2. Prompt/response logging

For debugging what the model actually saw/said in prod, enable ADK's
prompt-response logging (see the ADK observability docs). Keep in mind these
logs may contain user content — apply the same DLP thinking to log retention
that Phase 5 applies to posts.

## 3. BigQuery Agent Analytics

ADK's `BigQueryAgentAnalyticsPlugin` streams structured events (tool calls,
tokens, latencies) to a BigQuery dataset. Register it on the `App`:

```python
from google.adk.apps.app import App
from google.adk.plugins.bigquery_agent_analytics_plugin import (
    BigQueryAgentAnalyticsPlugin,
)

app = App(
    name="social_poster",
    root_agent=root_agent,
    plugins=[BigQueryAgentAnalyticsPlugin(
        project=os.environ["GOOGLE_CLOUD_PROJECT"],
        dataset="agent_analytics",
    )],
)
```

Then deploy the **BigQuery observability agent from Agent Garden** and ask it
questions about your telemetry in natural language, e.g.:

- "What's the p95 latency of create_post over the last day?"
- "How many turns hit the Model Armor refusal path?"

Two answered questions from that agent = the Week 4 observability demo.

## 4. What to actually look at first

For this app, the three numbers worth watching in prod:

1. **Guardrail refusal rate** — how often `guard_input` blocks a turn. A spike
   means either an attack or a false-positive regression (remember the fuzzy PI
   filter from Phase 5).
2. **Approval → post conversion** — how many drafts actually get approved. Low
   conversion means drafting quality, not infra.
3. **Cost per completed post** — ties the Phase 6 model mix back to money; this
   is the number that justifies flash-lite on research/orchestrator.
