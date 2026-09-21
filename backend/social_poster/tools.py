"""Custom tools for the social_poster agent."""

import datetime
import os
import pathlib
import time
import urllib.error
import urllib.request
import uuid

import google.auth
import google.auth.impersonated_credentials
import google.auth.transport.requests
from google import genai
from google.cloud import storage
from google.genai import types as genai_types

# Images land in a gallery/ directory at this repo's root.
OUTPUTS_DIR = pathlib.Path(__file__).resolve().parents[2] / "gallery"


def generate_image(prompt: str) -> dict:
    """Generates an illustration image for a social media post.

    Args:
        prompt: A detailed visual description of the image to generate.

    Returns:
        dict with 'status', and on success 'image_path' (local file path)
        or on failure an 'error' message.
    """
    model = os.environ.get("IMAGE_MODEL_ID", "gemini-3.1-flash-image")
    client = genai.Client()  # picks up GOOGLE_GENAI_USE_VERTEXAI / project / location
    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
            ),
        )
    except Exception as e:  # surface API errors to the model as a tool result
        return {"status": "error", "error": str(e)}

    for part in response.candidates[0].content.parts:
        if part.inline_data and part.inline_data.mime_type.startswith("image/"):
            OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
            ext = part.inline_data.mime_type.split("/")[-1]
            # uuid suffix: two images generated within the same second
            # (e.g. LinkedIn + X drafts back to back) would otherwise silently
            # overwrite each other on the timestamp alone.
            path = OUTPUTS_DIR / f"post-image-{int(time.time())}-{uuid.uuid4().hex[:8]}.{ext}"
            path.write_bytes(part.inline_data.data)
            return {"status": "success", "image_path": str(path)}

    return {"status": "error", "error": "Model returned no image data."}


SIGNED_URL_EXPIRY_HOURS = int(os.environ.get("SIGNED_URL_EXPIRY_HOURS", "168"))  # 7 days, V4's max


def _sign_via_impersonation(bucket_name: str, blob_name: str) -> str:
    """V4 signed URL, via service-account impersonation — no key file.

    A plain public object URL doesn't work on this (or any) project with
    Public Access Prevention enforced: granting allUsers/objectViewer 412s
    outright (confirmed live — see LEARNINGS.md), so a signed URL is the
    only option. Signing itself needs a *service account's* private key, or
    the IAM SignBlob API acting as one — and this project's credentials
    policy doesn't allow downloading a key file. Impersonation resolves
    that: GCS_SIGNING_SERVICE_ACCOUNT names a dedicated SA, and whatever
    identity is actually running this code (a developer's own `gcloud auth
    application-default login`, or a deployed Agent Runtime's own runtime
    service account) borrows that SA's identity just long enough to sign,
    via `roles/iam.serviceAccountTokenCreator` granted on it — a one-time,
    manual IAM setup step (see docs/setup-guide.html), not
    something this code can grant itself.
    """
    signing_sa = os.environ["GCS_SIGNING_SERVICE_ACCOUNT"]
    source_credentials, _ = google.auth.default()
    signing_credentials = google.auth.impersonated_credentials.Credentials(
        source_credentials=source_credentials,
        target_principal=signing_sa,
        target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
        lifetime=300,
    )
    signing_credentials.refresh(google.auth.transport.requests.Request())

    bucket = storage.Client().bucket(bucket_name)
    blob = bucket.blob(blob_name)
    return blob.generate_signed_url(
        version="v4",
        expiration=datetime.timedelta(hours=SIGNED_URL_EXPIRY_HOURS),
        method="GET",
        service_account_email=signing_credentials.service_account_email,
        access_token=signing_credentials.token,
    )


def upload_image(image_path: str) -> dict:
    """Uploads a local image to Cloud Storage and returns a fetchable URL.

    Exists because Buffer's create_post can't fetch a locally-generated image
    file, only a real HTTPS URL (confirmed live: "Invalid post: Image could
    not be read from its URL").

    Returns a V4 signed URL (see _sign_via_impersonation) rather than a
    plain public object URL — this project's bucket has Public Access
    Prevention enforced, which makes a plain public URL a dead end
    (confirmed live: granting allUsers/objectViewer 412s). The signed URL
    expires after SIGNED_URL_EXPIRY_HOURS (default 7 days, V4's own max);
    fine here since these are short-lived post images, not permanent links.

    Args:
        image_path: Absolute path to a local image file (e.g. the image_path
            returned by generate_image).

    Returns:
        dict with 'status', and on success 'url' (a signed HTTPS URL) or on
        failure an 'error' message.
    """
    if os.environ.get("DRY_RUN", "true").lower() != "false":
        return {
            "status": "success",
            "dry_run": True,
            "url": f"https://storage.googleapis.com/dry-run-bucket/{uuid.uuid4().hex[:12]}.png",
            "note": "DRY_RUN=true, nothing was uploaded.",
        }

    bucket_name = os.environ.get("GCS_BUCKET_NAME", "")
    if not bucket_name:
        return {"status": "error", "error": "GCS_BUCKET_NAME is not set."}
    if not os.environ.get("GCS_SIGNING_SERVICE_ACCOUNT", ""):
        return {"status": "error", "error": "GCS_SIGNING_SERVICE_ACCOUNT is not set."}

    try:
        ext = pathlib.Path(image_path).suffix or ".png"
        blob_name = f"post-images/{int(time.time())}-{uuid.uuid4().hex[:8]}{ext}"
        bucket = storage.Client().bucket(bucket_name)
        bucket.blob(blob_name).upload_from_filename(image_path)
        url = _sign_via_impersonation(bucket_name, blob_name)
    except Exception as e:  # surface API errors to the model as a tool result
        return {"status": "error", "error": str(e)}

    return {
        "status": "success",
        "dry_run": False,
        "url": url,
    }


def use_provided_image_url(url: str) -> dict:
    """Registers a public image URL the user already has for the current
    post, instead of generating one with generate_image.

    No GCS upload happens here — a URL the user supplies is, by definition,
    already hosted somewhere fetchable. Actually checks it's fetchable
    (HEAD request) rather than just validating the scheme: confirmed live
    that a GCS console URL (storage.cloud.google.com/...) silently 302s to
    a Google login page instead of erroring, and a private object's direct
    API URL (storage.googleapis.com/...) 403s — both looked fine as plain
    strings, and both would otherwise only fail much later and confusingly,
    at Buffer's own create_post step, several turns after the user thought
    this was settled. Failing here instead means the agent finds out
    immediately and can ask for a different URL right away.

    Args:
        url: A public http(s) URL to an image.

    Returns:
        dict with 'status', and on success 'url' (echoed back) or on
        failure an 'error' message.
    """
    if not (url.startswith("http://") or url.startswith("https://")):
        return {"status": "error", "error": f"Not a valid http(s) URL: {url!r}"}
    try:
        request = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(request, timeout=10) as response:
            final_url = response.geturl()
            if "accounts.google.com" in final_url:
                return {
                    "status": "error",
                    "error": (
                        f"{url} redirects to a Google sign-in page — it's a "
                        "console/viewer link, not a public URL. If this is a "
                        "GCS object, use the storage.googleapis.com/<bucket>/<path> "
                        "form and make sure the object is actually public."
                    ),
                }
    except urllib.error.HTTPError as e:
        return {
            "status": "error",
            "error": f"{url} returned HTTP {e.code} — it isn't publicly fetchable.",
        }
    except Exception as e:  # network error, timeout, bad host, etc.
        return {"status": "error", "error": f"Could not reach {url}: {e}"}
    return {"status": "success", "url": url}


def check_text_length(text: str, limit: int) -> dict:
    """Checks a draft's exact character count against a platform's limit.

    LLMs are unreliable at counting their own generated text's length by eye
    (token boundaries don't map to characters) — call this instead of
    guessing, especially for X's hard 280-character cap.

    Args:
        text: The exact final draft text to measure.
        limit: The platform's character limit (e.g. 280 for X).

    Returns:
        dict with 'length', 'limit', 'within_limit', and 'over_by' (0 if fine).
    """
    length = len(text)
    return {
        "status": "success",
        "length": length,
        "limit": limit,
        "within_limit": length <= limit,
        "over_by": max(0, length - limit),
    }
