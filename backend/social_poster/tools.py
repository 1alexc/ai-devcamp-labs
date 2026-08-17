"""Custom tools for the social_poster agent."""

import os
import pathlib
import time
import uuid

from google import genai
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
