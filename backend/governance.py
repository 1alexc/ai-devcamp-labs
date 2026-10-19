"""Agent Registry and Governance CLI for Social Spark.

Usage:
  uv run python backend/governance.py registry-agents
  uv run python backend/governance.py registry-search "bigquery"
"""

import argparse
import json
import logging
import os
import pathlib
import sys
from typing import Any, Dict, List

from dotenv import load_dotenv
import google.auth
import google.auth.transport.requests
import httpx

# Load environment variables from backend/social_poster/.env or project root
_ENV_PATH = pathlib.Path(__file__).resolve().parent / "social_poster" / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH)
else:
    load_dotenv()

log = logging.getLogger("governance")


def get_config() -> tuple[str, str]:
    """Resolves project ID and location from environment."""
    project = os.environ.get("PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        sys.exit(
            "ERROR: GOOGLE_CLOUD_PROJECT or PROJECT_ID must be set in backend/social_poster/.env or environment."
        )
    # Agent Registry hosts Google MCP servers in 'global' and regional runtime agents in region
    location = os.environ.get("AGENT_REGISTRY_LOCATION", "global")
    return project, location


def get_headers(project_id: str) -> Dict[str, str]:
    """Generates GCP bearer token authentication headers."""
    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    request = google.auth.transport.requests.Request()
    creds.refresh(request)
    return {
        "Authorization": f"Bearer {creds.token}",
        "x-goog-user-project": project_id,
        "Content-Type": "application/json",
    }


def list_registry_resources(
    resource_type: str,
    filter_str: str | None = None,
    locations: List[str] | None = None,
) -> List[Dict[str, Any]]:
    """Fetches resources (agents, mcpServers, endpoints) from Agent Registry API."""
    project, default_loc = get_config()
    region = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    if locations is None:
        # Query global (where Google MCP servers live) and the configured region
        locations = ["global"]
        if region not in locations:
            locations.append(region)
        if default_loc not in locations:
            locations.append(default_loc)

    headers = get_headers(project)
    results: List[Dict[str, Any]] = []
    seen_names: set[str] = set()

    for loc in locations:
        url = f"https://agentregistry.googleapis.com/v1/projects/{project}/locations/{loc}/{resource_type}"
        params = {}
        if filter_str:
            params["filter"] = filter_str

        try:
            resp = httpx.get(url, headers=headers, params=params, timeout=30)
            resp.raise_for_status()
            for item in resp.json().get(resource_type, []):
                uid = item.get("name", "")
                if uid not in seen_names:
                    seen_names.add(uid)
                    results.append(item)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                continue
            log.error(
                "Failed to query Agent Registry (%s in %s): %s",
                resource_type,
                loc,
                e.response.text,
            )
        except Exception as e:
            log.error("Network error accessing Agent Registry (%s): %s", loc, e)

    return results


def _format_mcp_server(server: Dict[str, Any], indent: str = "  • ") -> None:
    """Prints formatted MCP server details including tool count and tool names."""
    name = server.get("displayName") or server.get("name", "Unknown")
    uid = server.get("name", "")
    desc = (server.get("description") or "No description provided.").strip().splitlines()[0]
    tools = server.get("tools", [])
    tool_names = [t.get("name", "") for t in tools if t.get("name")]
    interfaces = server.get("interfaces", [])
    urls = [i.get("url", "") for i in interfaces if i.get("url")]

    print(f"{indent}{name} (Tools: {len(tools)})")
    print(f"    Resource: {uid}")
    if urls:
        print(f"    Endpoint: {urls[0]}")
    print(f"    Description: {desc}")
    if tool_names:
        print(f"    Tools ({len(tool_names)}): {', '.join(tool_names)}")
    print()


def cmd_registry_agents(args: argparse.Namespace) -> None:
    """Lists registered agents and MCP servers in Agent Registry."""
    project, _ = get_config()
    region = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    locs = "global" if region == "global" else f"global + {region}"
    print(f"\n=== Google Cloud Agent Registry ({project} / {locs}) ===\n")

    agents = list_registry_resources("agents")
    print(f"Registered Agents ({len(agents)}):")
    if not agents:
        print("  (None found)")
    for agent in agents:
        name = agent.get("displayName") or agent.get("name", "Unknown")
        uid = agent.get("name", "")
        desc = agent.get("description", "No description provided.")
        skills = [s.get("name") for s in agent.get("skills", [])]
        print(f"  • {name}")
        print(f"    Resource: {uid}")
        print(f"    Description: {desc}")
        if skills:
            print(f"    Skills: {', '.join(skills)}")
        print()

    mcp_servers = list_registry_resources("mcpServers")
    print(f"Registered MCP Servers ({len(mcp_servers)}):")
    if not mcp_servers:
        print("  (None found)")
    for server in mcp_servers:
        _format_mcp_server(server)


def cmd_registry_search(args: argparse.Namespace) -> None:
    """Searches Agent Registry for agents and MCP servers matching a query."""
    query = args.query.lower()
    project, _ = get_config()
    region = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    locs = "global" if region == "global" else f"global + {region}"
    print(f"\n=== Searching Agent Registry for '{args.query}' in {project} ({locs}) ===\n")

    # Fetch agents and mcpServers across global and regional locations
    agents = list_registry_resources("agents")
    mcp_servers = list_registry_resources("mcpServers")

    matches = []

    for agent in agents:
        searchable = " ".join(
            [
                agent.get("displayName", ""),
                agent.get("name", ""),
                agent.get("description", ""),
                " ".join(s.get("name", "") for s in agent.get("skills", [])),
            ]
        ).lower()
        if query in searchable:
            matches.append(("Agent", agent))

    for server in mcp_servers:
        short_desc = (server.get("description") or "").strip().splitlines()[0] if server.get("description") else ""
        searchable = " ".join(
            [
                server.get("displayName", ""),
                server.get("name", ""),
                server.get("mcpServerId", ""),
                short_desc,
                " ".join(t.get("name", "") for t in server.get("tools", [])),
            ]
        ).lower()
        if query in searchable:
            matches.append(("MCP Server", server))

    if not matches:
        print(f"No registered agents or MCP servers matched query: '{args.query}'")
        return

    print(f"Found {len(matches)} match(es):\n")
    for kind, item in matches:
        if kind == "MCP Server":
            _format_mcp_server(item, indent=f"  [{kind}] ")
        else:
            name = item.get("displayName") or item.get("name", "Unknown")
            uid = item.get("name", "")
            desc = item.get("description", "No description.")
            skills = [s.get("name") for s in item.get("skills", [])]
            print(f"  [{kind}] {name}")
            print(f"    Resource: {uid}")
            print(f"    Description: {desc}")
            if skills:
                print(f"    Skills: {', '.join(skills)}")
            print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Google Cloud Agent Registry & Governance CLI"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcommand: registry-agents
    subparsers.add_parser(
        "registry-agents", help="List registered Agents and MCP Servers"
    )

    # Subcommand: registry-search
    search_parser = subparsers.add_parser(
        "registry-search", help="Search Agent Registry for agents or tools"
    )
    search_parser.add_argument("query", help="Keyword or text to search for")

    args = parser.parse_args()

    if args.command == "registry-agents":
        cmd_registry_agents(args)
    elif args.command == "registry-search":
        cmd_registry_search(args)


if __name__ == "__main__":
    main()

