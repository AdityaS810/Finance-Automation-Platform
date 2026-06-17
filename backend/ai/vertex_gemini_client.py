"""Small Vertex AI Gemini client wrapper.

This module uses Google Cloud Application Default Credentials through the
Google Gen AI SDK. It never reads a Gemini API key and returns ``None`` when
Vertex AI is not configured or the caller lacks permission.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


DEFAULT_VERTEX_LOCATION = "asia-south1"
DEFAULT_VERTEX_MODEL = "gemini-2.0-flash"


load_dotenv()


@dataclass(frozen=True)
class VertexGeminiConfig:
    """Runtime settings for Vertex AI Gemini calls."""

    project_id: str
    location: str
    model: str


def get_vertex_gemini_config() -> VertexGeminiConfig | None:
    """Read Vertex AI settings from environment variables.

    ``VERTEX_AI_PROJECT_ID`` intentionally falls back to ``GCP_PROJECT_ID`` so
    local and Cloud Function environments can share the same Google project.
    """
    project_id = os.getenv("VERTEX_AI_PROJECT_ID") or os.getenv("GCP_PROJECT_ID")
    if not project_id:
        return None

    return VertexGeminiConfig(
        project_id=project_id,
        location=os.getenv("VERTEX_AI_LOCATION", DEFAULT_VERTEX_LOCATION),
        model=os.getenv("VERTEX_AI_MODEL", DEFAULT_VERTEX_MODEL),
    )


def is_vertex_gemini_configured() -> bool:
    """Return True when enough environment configuration exists to try Vertex."""
    return get_vertex_gemini_config() is not None


def generate_vertex_text(prompt: str) -> str | None:
    """Generate a short text response from Vertex AI Gemini.

    Any import, authentication, permission, quota, or API error returns ``None``
    so reconciliation can continue with deterministic rule-based results.
    """
    config = get_vertex_gemini_config()
    if config is None:
        return None

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(
            vertexai=True,
            project=config.project_id,
            location=config.location,
        )
        response = client.models.generate_content(
            model=config.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=220,
            ),
        )
        text = getattr(response, "text", None)
        if not text:
            return None
        return text.strip()
    except Exception:
        return None
