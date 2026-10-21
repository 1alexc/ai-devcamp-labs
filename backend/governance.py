"""Agent Registry and Semantic Governance CLI for Social Spark.

Usage:
  uv run python backend/governance.py registry-agents
  uv run python backend/governance.py registry-search "bigquery"
  uv run python backend/governance.py provision
  uv run python backend/governance.py create "The agent must never..." --name no-unapproved-posts
  uv run python backend/governance.py list
  uv run python backend/governance.py delete no-unapproved-posts
"""

import argparse
import json
import logging
import os
import pathlib
import sys
import time
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
                uid = item.get("uid") or item.get("name", "")
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
        identity = (
            agent.get("attributes", {})
            .get("agentregistry.googleapis.com/system/RuntimeIdentity", {})
            .get("principal", "")
        )
        print(f"  • {name}")
        print(f"    Resource: {uid}")
        if identity:
            print(f"    identity: {identity}")
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


def _get_sgp_base_url() -> tuple[str, str, str]:
    """Returns (project, region, base_url) for Vertex AI Semantic Governance API."""
    project, _ = get_config()
    region = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    if region == "global":
        region = "us-central1"
    base_url = f"https://{region}-aiplatform.googleapis.com/v1beta1/projects/{project}/locations/{region}"
    return project, region, base_url


def _resolve_default_agent(project: str, region: str) -> str:
    """Resolves the Agent Registry resource name for social-spark-poster."""
    agents = list_registry_resources("agents", locations=[region, "global"])
    for a in agents:
        if "social-spark" in (a.get("displayName") or "").lower():
            return a["name"]
    if agents:
        return agents[0]["name"]
    return f"projects/{project}/locations/{region}/agents/social-spark-poster"


def _resolve_default_mcp_tools(project: str) -> List[Dict[str, Any]]:
    """Resolves default MCP server/tool entry for Semantic Governance Policy."""
    servers = list_registry_resources("mcpServers", locations=["global"])
    for s in servers:
        if "bigquery.googleapis.com" in (s.get("displayName") or ""):
            return [{"mcpServer": s["name"], "tools": ["execute_sql"]}]
    if servers:
        first_tools = [t.get("name") for t in servers[0].get("tools", [])[:1] if t.get("name")]
        return [{"mcpServer": servers[0]["name"], "tools": first_tools}]
    return []


def _wait_for_lro(op_data: Dict[str, Any], region: str, headers: Dict[str, str]) -> Dict[str, Any]:
    """Polls a Vertex AI v1beta1 long-running operation until completion."""
    op_name = op_data.get("name")
    if not op_name or op_data.get("done"):
        return op_data
    op_url = f"https://{region}-aiplatform.googleapis.com/v1beta1/{op_name}"
    for _ in range(60):
        time.sleep(2)
        resp = httpx.get(op_url, headers=headers, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("done"):
                return data
    return op_data


def _wait_for_engine_active(engine_url: str, headers: Dict[str, str]) -> str:
    """Polls the Semantic Governance Policy Engine until state is ACTIVE."""
    for i in range(120):
        resp = httpx.get(engine_url, headers=headers, timeout=30)
        if resp.status_code == 200:
            state = resp.json().get("state", "UNKNOWN")
            if state == "ACTIVE":
                return state
            if i % 3 == 0 and i > 0:
                print(f"  Still {state}... waiting ({i * 5}s elapsed)")
        time.sleep(5)
    return "PROVISIONING"


def cmd_provision(args: argparse.Namespace) -> None:
    """Checks or provisions the Vertex AI Semantic Governance Policy Engine."""
    project, region, base_url = _get_sgp_base_url()
    headers = get_headers(project)
    engine_url = f"{base_url}/semanticGovernancePolicyEngine"

    print(f"\n=== Semantic Governance Policy Engine ({project} / {region}) ===\n")
    resp = httpx.get(engine_url, headers=headers, timeout=30)
    if resp.status_code == 200:
        data = resp.json()
        state = data.get("state", "UNKNOWN")
        print(f"Current Engine Resource: {data.get('name')}")
        print(f"Current Engine State:    {state}")
        if state == "ACTIVE":
            print("Policy Engine is already provisioned and ACTIVE.")
            return
        if state == "PROVISIONING":
            print("Policy Engine is currently PROVISIONING. Waiting for ACTIVE state...")
            final_state = _wait_for_engine_active(engine_url, headers)
            print(f"Engine State: {final_state}")
            return

    # Provision / activate the singleton engine
    patch_resp = httpx.patch(engine_url, headers=headers, json={}, timeout=30)
    if patch_resp.status_code == 200:
        op = patch_resp.json()
        print(f"Provisioning engine (operation: {op.get('name', 'OK')})...")
        _wait_for_lro(op, region, headers)
        final_state = _wait_for_engine_active(engine_url, headers)
        print(f"Engine State after provision: {final_state}")
    else:
        print(f"Note: Engine update returned HTTP {patch_resp.status_code}: {patch_resp.text}")


def cmd_create(args: argparse.Namespace) -> None:
    """Creates a Semantic Governance Policy in Vertex AI."""
    project, region, base_url = _get_sgp_base_url()
    headers = get_headers(project)

    policy_id = args.name
    agent_res = args.agent or _resolve_default_agent(project, region)
    mcp_tools = _resolve_default_mcp_tools(project)

    body: Dict[str, Any] = {
        "displayName": policy_id,
        "description": f"Semantic Governance Policy ({policy_id})",
        "naturalLanguageConstraint": args.constraint,
        "agent": agent_res,
    }
    if mcp_tools:
        body["mcpTools"] = mcp_tools

    url = f"{base_url}/semanticGovernancePolicies"
    params = {"semanticGovernancePolicyId": policy_id}

    print(f"\n=== Creating Semantic Governance Policy '{policy_id}' ({project} / {region}) ===\n")
    print(f"  Constraint: {args.constraint}")
    print(f"  Agent:      {agent_res}")
    if mcp_tools:
        print(f"  MCP Tools:  {json.dumps(mcp_tools)}")

    resp = httpx.post(url, headers=headers, params=params, json=body, timeout=30)
    if resp.status_code in (200, 201):
        op = resp.json()
        final_op = _wait_for_lro(op, region, headers)
        if "error" in final_op:
            print(f"\nFailed to create policy:\n{json.dumps(final_op['error'], indent=2)}")
            sys.exit(1)
        res_name = final_op.get("response", {}).get("name") or op.get("name")
        print(f"\nCreated successfully: {res_name}")
    elif resp.status_code == 409:
        patch_url = f"{url}/{policy_id}"
        patch_resp = httpx.patch(patch_url, headers=headers, json=body, timeout=30)
        if patch_resp.status_code == 200:
            op = patch_resp.json()
            final_op = _wait_for_lro(op, region, headers)
            res_name = final_op.get("response", {}).get("name") or f"{url}/{policy_id}"
            print(f"\nPolicy '{policy_id}' already existed — updated successfully: {res_name}")
        else:
            print(f"\nPolicy '{policy_id}' already exists.")
    else:
        print(f"\nFailed to create policy (HTTP {resp.status_code}):\n{resp.text}")
        sys.exit(1)


def cmd_list(args: argparse.Namespace) -> None:
    """Lists Semantic Governance Policies in Vertex AI."""
    project, region, base_url = _get_sgp_base_url()
    headers = get_headers(project)
    url = f"{base_url}/semanticGovernancePolicies"

    print(f"\n=== Semantic Governance Policies ({project} / {region}) ===\n")
    resp = httpx.get(url, headers=headers, timeout=30)
    if resp.status_code != 200:
        print(f"Failed to list policies (HTTP {resp.status_code}): {resp.text}")
        sys.exit(1)

    policies = resp.json().get("semanticGovernancePolicies", [])
    if not policies:
        print("  (No semantic governance policies found)")
        return

    for p in policies:
        pid = p.get("name", "").split("/")[-1]
        display = p.get("displayName", pid)
        constraint = p.get("naturalLanguageConstraint", "")
        agent = p.get("agent", "")
        identity = p.get("agentIdentity", "")
        print(f"  • {display} (ID: {pid})")
        print(f"    Resource:   {p.get('name')}")
        print(f"    Constraint: {constraint}")
        print(f"    Agent:      {agent}")
        if identity:
            print(f"    identity:   {identity}")
        if p.get("mcpTools"):
            print(f"    MCP Tools:  {json.dumps(p['mcpTools'])}")
        print()


def cmd_delete(args: argparse.Namespace) -> None:
    """Deletes a Semantic Governance Policy by ID."""
    project, region, base_url = _get_sgp_base_url()
    headers = get_headers(project)
    url = f"{base_url}/semanticGovernancePolicies/{args.name}"

    resp = httpx.delete(url, headers=headers, timeout=30)
    if resp.status_code in (200, 204):
        print(f"Deleted policy '{args.name}'.")
    else:
        print(f"Failed to delete policy '{args.name}' (HTTP {resp.status_code}): {resp.text}")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Google Cloud Agent Registry & Semantic Governance CLI"
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

    # Subcommand: provision
    subparsers.add_parser(
        "provision", help="Provision or inspect the Semantic Governance Policy Engine"
    )

    # Subcommand: create
    create_parser = subparsers.add_parser(
        "create", help="Create a Semantic Governance Policy"
    )
    create_parser.add_argument(
        "constraint", help="Natural-language constraint for the policy"
    )
    create_parser.add_argument(
        "--name", required=True, help="Policy ID / display name (e.g. no-unapproved-posts)"
    )
    create_parser.add_argument(
        "--agent", help="Full Agent Registry resource name (auto-detected if omitted)"
    )

    # Subcommand: list
    subparsers.add_parser(
        "list", help="List Semantic Governance Policies"
    )

    # Subcommand: delete
    delete_parser = subparsers.add_parser(
        "delete", help="Delete a Semantic Governance Policy"
    )
    delete_parser.add_argument("name", help="Policy ID to delete")

    args = parser.parse_args()

    if args.command == "registry-agents":
        cmd_registry_agents(args)
    elif args.command == "registry-search":
        cmd_registry_search(args)
    elif args.command == "provision":
        cmd_provision(args)
    elif args.command == "create":
        cmd_create(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "delete":
        cmd_delete(args)


if __name__ == "__main__":
    main()

