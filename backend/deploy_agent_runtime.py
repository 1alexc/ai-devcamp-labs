"""Deploy social_poster's `root_agent` to Agent Runtime.

This is the Scale pillar's baseline deploy. It uses the Python SDK path
(`vertexai.Client().agent_engines.create(agent=AdkApp(...), ...)`), which is one
of the platform's own documented deploy methods — not a workaround.

Run it from the repo root:

    uv run --with cloudpickle python backend/deploy_agent_runtime.py --create
    uv run --with cloudpickle python backend/deploy_agent_runtime.py --stage-only

Almost every non-obvious choice below was paid for with a failed deploy. The
reasoning lives in `docs/LEARNINGS.md`; the short version is inline.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

# --- Deployment target --------------------------------------------------------
PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT") or sys.exit(
    "Set GOOGLE_CLOUD_PROJECT to your GCP project ID (export GOOGLE_CLOUD_PROJECT=...)."
)

# us-central1, deliberately. The Govern pillar's Semantic Governance Policy
# Engine does not exist in `global` or `europe-west2` — the API answers
# "service is not available in location X". A policy's `agent` field must point
# at an Agent Registry entry, and an engine only lands in the registry in the
# region it was deployed to. Deploy anywhere else and Session 3 cannot attach a
# policy to this agent at all.
LOCATION = os.environ.get("AGENT_RUNTIME_LOCATION", "us-central1")

# No default: GCS bucket names are globally unique across all of Google Cloud,
# so one person's bucket is never a sensible fallback for anyone else. Failing
# with a named env var beats failing with someone else's 403.
STAGING_BUCKET = os.environ.get("AGENT_RUNTIME_STAGING_BUCKET", "")

DISPLAY_NAME = "social-spark-social-poster"

# The Buffer token never appears in this file, in `env_vars`, or in the console.
# An earlier deploy passed it as a plain-text env var and it sat there in clear
# text until review caught it. See `--help` for the one-time setup command.
BUFFER_SECRET_ID = os.environ.get("BUFFER_SECRET_ID", "buffer-api-key")

# Pinned regional model. `gemini-flash-latest` — agent.py's default — only
# resolves via the `global` endpoint; every region 404s with "Publisher model
# not found". GOOGLE_CLOUD_LOCATION is a reserved env var on Agent Runtime
# (setting it is rejected outright), so there is no way to force `global`
# resolution once deployed. A pinned version is the only option.
PINNED_MODEL = os.environ.get("AGENT_RUNTIME_MODEL", "gemini-2.5-flash")


def build_requirements() -> list[str]:
    """Pins that a deployed engine needs, exactly.

    `google-cloud-aiplatform[agent_engines,adk]`'s own `adk` extra only
    constrains `google-adk` to `>=1.5.0,<3.0.0` — a wide range. Without pinning
    `google-adk` explicitly alongside it, the resolver picks a version whose
    `mcp_tool` module doesn't export `McpToolset`; a `try/except ImportError`
    inside ADK swallows the real cause and it resurfaces as a baffling
    ImportError in agent.py. The `[mcp]` extra matters too: the `mcp` client
    library is gated behind it, and is only present in local dev by accident,
    pulled in transitively by `fastmcp`.
    """
    return [
        "google-cloud-aiplatform[agent_engines,adk]==1.161.0",
        "google-adk[a2a,eval,mcp]==2.4.0",
        "google-genai==2.11.0",
        "google-cloud-storage==3.13.0",  # tools.py's upload_image
        "cloudpickle",
        "pydantic",
    ]


def build_env_vars(*, secret_id: str) -> dict[str, object]:
    """Remote environment for the deployed engine.

    Deliberately NOT set: GOOGLE_CLOUD_PROJECT and GOOGLE_CLOUD_LOCATION. Agent
    Runtime injects both itself and rejects the deploy with FAILED_PRECONDITION
    if you pass either one.
    """
    return {
        "POST_VIA": "buffer",
        "DRY_RUN": "true",
        # Safety net, kept on for every test deploy. Buffer's remote MCP server
        # has no dry-run concept at all — DRY_RUN only covers the local LinkedIn
        # server — so this forces any real Buffer post to `customScheduled` at
        # least this many minutes out, whatever mode the model picks. An early
        # test published a real post before this existed.
        "BUFFER_REVIEW_DELAY_MINUTES": "60",
        "BUFFER_API_KEY": {"secret": secret_id, "version": "latest"},
        # All three pinned. Also set locally before the agent import below —
        # see set_local_model_env().
        "RESEARCH_MODEL": PINNED_MODEL,
        "DRAFT_MODEL": PINNED_MODEL,
        "ORCHESTRATOR_MODEL": PINNED_MODEL,
    }


def set_local_model_env() -> None:
    """Pin the models in THIS process, before agent.py is imported.

    The model strings are read at module import time and baked into the `Agent`
    objects, which is what cloudpickle then serializes. Setting them only in the
    remote `env_vars` is too late — the pickle already carries whatever the
    local environment resolved, which is `gemini-flash-latest`, which 404s in
    every region. This has to happen before the import, not after.
    """
    for var in ("RESEARCH_MODEL", "DRAFT_MODEL", "ORCHESTRATOR_MODEL"):
        os.environ[var] = PINNED_MODEL

    # Keep the baseline deploy simple: no GCS image hosting. Images stay local
    # and Buffer posts stay text-only, which is what the Scale lab demonstrates.
    # Enabling it would also require the signing service account (see
    # gcs-setup.sh) to exist for the runtime identity, which is Lab 2 scope
    # creep. Opt in with --with-gcs.
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "1")


def stage_source(staging_dir: pathlib.Path) -> list[str]:
    """Build a clean copy of what the engine needs, and return extra_packages.

    Why a staged copy rather than pointing at `backend/` directly:
    `backend/social_poster/.env` holds real secrets and sits right next to the
    source files. `extra_packages` tars whatever path it is given, so passing
    the live directory would upload that .env straight to the staging bucket.

    The layout is a mirror of the repo, and that is load-bearing in two ways:

      * agent.py computes `REPO_ROOT = Path(__file__).resolve().parents[2]` and
        loads `REPO_ROOT / "skills"` at import time via SkillToolset. Staging
        `backend/social_poster/` and `skills/` as siblings makes that resolve to
        the unpacked root remotely, exactly as it does locally.
      * main.py imports `backend.social_poster.agent`, so that is the module
        path cloudpickle records by reference. `backend/` has no __init__.py,
        but namespace packages make the remote import work off the unpacked
        root.

    `mcp/` is deliberately NOT staged. With POST_VIA=buffer, LinkedIn's
    McpToolset objects get constructed but never invoked, and construction
    doesn't touch the filesystem.
    """
    ignore = shutil.ignore_patterns(
        ".env", ".env.*", "__pycache__", "*.pyc", ".DS_Store", "*.db"
    )

    pkg_dst = staging_dir / "backend" / "social_poster"
    shutil.copytree(REPO_ROOT / "backend" / "social_poster", pkg_dst, ignore=ignore)
    shutil.copytree(REPO_ROOT / "skills", staging_dir / "skills", ignore=ignore)

    leaked = sorted(p.name for p in pkg_dst.rglob(".env*"))
    if leaked:  # belt and braces — never ship a staging dir with secrets in it
        raise RuntimeError(f"refusing to deploy: .env files in staged copy: {leaked}")

    return ["backend", "skills"]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "One-time setup, before the first --create:\n"
            "  1. Store the Buffer token (run this yourself; the key must not\n"
            "     pass through this script):\n"
            "       gcloud secrets create buffer-api-key --data-file=- \\\n"
            "         --project=$GOOGLE_CLOUD_PROJECT\n"
            "  2. Grant the PLATFORM service agent access — not the Agent\n"
            "     Identity principal, which doesn't exist until after a\n"
            "     successful deploy:\n"
            "       gcloud secrets add-iam-policy-binding buffer-api-key \\\n"
            "         --member=serviceAccount:service-PROJECT_NUMBER@gcp-sa-aiplatform-re.iam.gserviceaccount.com \\\n"
            "         --role=roles/secretmanager.secretAccessor\n"
        ),
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--create", action="store_true", help="deploy a new engine")
    mode.add_argument("--update", metavar="ENGINE_ID", help="redeploy over an existing engine")
    mode.add_argument(
        "--stage-only",
        action="store_true",
        help="build and inspect the staged package without deploying (free, offline)",
    )
    parser.add_argument(
        "--with-gcs",
        action="store_true",
        help="enable GCS image hosting (needs gcs-setup.sh already run)",
    )
    args = parser.parse_args()

    if not STAGING_BUCKET:
        print(
            "AGENT_RUNTIME_STAGING_BUCKET is not set. Agent Runtime needs a GCS\n"
            "bucket to stage the deployment through — bucket names are globally\n"
            "unique, so there is no default worth guessing:\n"
            "  export AGENT_RUNTIME_STAGING_BUCKET=gs://your-bucket",
            file=sys.stderr,
        )
        return 2

    set_local_model_env()
    if args.with_gcs:
        for var in ("GCS_BUCKET_NAME", "GCS_SIGNING_SERVICE_ACCOUNT"):
            if not os.environ.get(var):
                print(f"--with-gcs needs {var} set (see gcs-setup.sh)", file=sys.stderr)
                return 2

    staging_dir = pathlib.Path(tempfile.mkdtemp(prefix="social-spark-deploy-"))
    try:
        extra_packages = stage_source(staging_dir)

        staged = sorted(p.relative_to(staging_dir).as_posix() for p in staging_dir.rglob("*") if p.is_file())
        print(f"staged {len(staged)} files into {staging_dir}")
        for rel in staged:
            print(f"  {rel}")

        if args.stage_only:
            print("\n--stage-only: nothing deployed. Staging dir left in place for inspection.")
            staging_dir = None  # don't delete it; the caller wants to look
            return 0

        # Imported here, after set_local_model_env(), so the pinned models are
        # baked into the Agent objects that cloudpickle is about to serialize.
        sys.path.insert(0, str(REPO_ROOT))
        import vertexai
        from vertexai.preview.reasoning_engines import AdkApp

        from backend.social_poster.agent import root_agent

        config: dict[str, object] = {
            "display_name": DISPLAY_NAME,
            "description": "Social Spark's social_poster orchestrator — Scale pillar baseline.",
            "staging_bucket": STAGING_BUCKET,
            "requirements": build_requirements(),
            "env_vars": build_env_vars(secret_id=BUFFER_SECRET_ID),
            # Agent Identity. This is what mints the engine's principal, which
            # is also what lands it in Agent Registry with a RuntimeIdentity
            # attribute — the thing Govern's policies bind to in Session 3.
            "identity_type": "AGENT_IDENTITY",
        }

        # tar.add() stores whatever relative path it is handed, so the tarball's
        # internal layout is relative to the current directory.
        os.chdir(staging_dir)
        config["extra_packages"] = extra_packages

        client = vertexai.Client(project=PROJECT, location=LOCATION)
        app = AdkApp(agent=root_agent, enable_tracing=True)

        if args.create:
            print(f"\ncreating engine in {PROJECT}/{LOCATION} (5-10 min)...")
            remote = client.agent_engines.create(agent=app, config=config)
        else:
            name = args.update
            if "/" not in name:
                name = f"projects/{PROJECT}/locations/{LOCATION}/reasoningEngines/{name}"
            print(f"\nupdating {name} (5-10 min)...")
            remote = client.agent_engines.update(name=name, agent=app, config=config)

        resource = remote.api_resource
        print("\ndeployed")
        print(f"  name:     {resource.name}")
        print(f"  identity: {getattr(resource, 'effective_identity', None)}")
        return 0
    finally:
        if staging_dir is not None:
            shutil.rmtree(staging_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
