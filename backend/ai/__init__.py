"""Optional AI helpers for finance workflows.

The finance data and reconciliation decisions stay rule-based. AI helpers in
this package only add short explanations for human review.
"""

from backend.ai.reconciliation_insights import (
    AI_UNAVAILABLE_MESSAGE,
    MAX_AI_ROWS,
    add_bank_ai_insights,
    add_gst_ai_insights,
)
from backend.ai.vertex_gemini_client import generate_vertex_text


__all__ = [
    "AI_UNAVAILABLE_MESSAGE",
    "MAX_AI_ROWS",
    "add_bank_ai_insights",
    "add_gst_ai_insights",
    "generate_vertex_text",
]
