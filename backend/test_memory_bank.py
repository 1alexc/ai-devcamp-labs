"""Test Memory Bank extraction and recall directly on the deployed engine.

Sends a sample conversation containing posting rules (question opener, no emojis, sign-off)
mixed with irrelevant chatter (cat, travel) to verify that Memory Bank filters out noise
and keeps only the relevant style / preference facts according to the configured topics.

Usage:
  GOOGLE_CLOUD_PROJECT=ai-devcamp-alexandercarr AGENT_ENGINE_ID=5322357832941568000 uv run python backend/test_memory_bank.py
  # Or to just list existing memories:
  GOOGLE_CLOUD_PROJECT=ai-devcamp-alexandercarr AGENT_ENGINE_ID=5322357832941568000 uv run python backend/test_memory_bank.py list
"""

import json
import os
import pathlib
import sys

import vertexai

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
METADATA_FILE = REPO_ROOT / "deployment_metadata.json"

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "ai-devcamp-alexandercarr")
REGION = os.environ.get("ENGINE_REGION", "us-central1")
ENGINE_ID = os.environ.get("AGENT_ENGINE_ID")

if not ENGINE_ID and METADATA_FILE.exists():
    try:
        data = json.loads(METADATA_FILE.read_text())
        runtime_id = data.get("remote_agent_runtime_id", "")
        if "reasoningEngines/" in runtime_id:
            ENGINE_ID = runtime_id.split("reasoningEngines/")[-1]
    except Exception:
        pass

if not ENGINE_ID:
    sys.exit("Please set AGENT_ENGINE_ID (or ensure deployment_metadata.json exists).")

APP_NAME = "social_poster"
USER_ID = "devcamp-user"
ENGINE_RESOURCE = f"projects/{PROJECT}/locations/{REGION}/reasoningEngines/{ENGINE_ID}"


def list_memories(client: vertexai.Client):
    print(f"\n--- Listing memories for engine {ENGINE_ID} ---")
    memories = list(client.agent_engines.memories.list(name=ENGINE_RESOURCE))
    if not memories:
        print("No memories found in Memory Bank yet.")
        return []
    for i, m in enumerate(memories, 1):
        topics = []
        for t in (m.topics or []):
            label = getattr(t, "custom_memory_topic_label", None) or getattr(t, "managed_memory_topic", None)
            topics.append(str(label) if label else str(t))
        print(f"\n[{i}] Fact: {m.fact}")
        print(f"    Topics: {topics}")
        print(f"    Scope: {m.scope}")
        print(f"    Created: {m.create_time}")
    return memories


def generate_test_memory(client: vertexai.Client):
    print(f"Using Project: {PROJECT}, Region: {REGION}, Engine ID: {ENGINE_ID}")
    print("Generating memory from test conversation...")

    events = [
        {
            "content": {
                "role": "user",
                "parts": [
                    {
                        "text": (
                            "When drafting posts for me, always start with an intriguing question opener, "
                            "never include any emojis in the copy, and always sign off with '- AC'. "
                            "Also, my cat was sleeping on my keyboard earlier and I'm traveling to London tomorrow."
                        )
                    }
                ],
            }
        },
        {
            "content": {
                "role": "model",
                "parts": [
                    {
                        "text": (
                            "Understood! From now on I will always open your posts with a question, "
                            "omit all emojis, and end with the sign-off '- AC'."
                        )
                    }
                ],
            }
        },
    ]

    op = client.agent_engines.memories.generate(
        name=ENGINE_RESOURCE,
        direct_contents_source={"events": events},
        scope={"app_name": APP_NAME, "user_id": USER_ID},
        config={"wait_for_completion": True},
    )
    print("Memory generation completed!")
    list_memories(client)


if __name__ == "__main__":
    client = vertexai.Client(project=PROJECT, location=REGION)
    if len(sys.argv) > 1 and sys.argv[1] == "list":
        list_memories(client)
    else:
        generate_test_memory(client)

