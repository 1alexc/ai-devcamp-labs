"""Custom tools for the social_poster agent."""

import os
import pathlib
import time
import uuid

from google import genai
from google.cloud import storage
from google.genai import types as genai_types

# Images land in a gallery/ directory at this repo's root (this is the
# standalone reference repo — see tools.py's counterpart in the main
# adk-devcamp-poc repo for the cumulative-folder version of this comment).
OUTPUTS_DIR = pathlib.Path(__file__).resolve().parents[2] / "gallery"


def generate_image(prompt: str) -> dict:
    """Generates an illustration image for a social media post.

    Args:
        prompt: A detailed visual description of the image to generate.

    Returns:
        dict with 'status', and on success 'image_path' (local file path)
        or on failure an 'error' message.
    """
    model = os.environ.get("IMAGE_MODEL_ID", "gemini-2.5-flash-image")
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


def upload_image(image_path: str) -> dict:
    """Uploads a local image to Cloud Storage and returns a fetchable URL.

    Exists because Buffer's create_post can't fetch a locally-generated image
    file, only a real HTTPS URL (confirmed live: "Invalid post: Image could
    not be read from its URL").

    Returns a plain public object URL rather than a signed one: signing needs
    a service-account private key, which this project's credentials policy
    doesn't allow downloading. Since these images are about to be posted
    publicly anyway, the bucket is instead granted allUsers/objectViewer once
    (see .env.example's GCS section) so every uploaded object is fetchable at
    a predictable URL with no per-upload signing step.

    Args:
        image_path: Absolute path to a local image file (e.g. the image_path
            returned by generate_image).

    Returns:
        dict with 'status', and on success 'url' (a public HTTPS URL) or on
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

    try:
        ext = pathlib.Path(image_path).suffix or ".png"
        blob_name = f"post-images/{int(time.time())}-{uuid.uuid4().hex[:8]}{ext}"
        bucket = storage.Client().bucket(bucket_name)
        bucket.blob(blob_name).upload_from_filename(image_path)
    except Exception as e:  # surface API errors to the model as a tool result
        return {"status": "error", "error": str(e)}

    return {
        "status": "success",
        "dry_run": False,
        "url": f"https://storage.googleapis.com/{bucket_name}/{blob_name}",
    }


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
