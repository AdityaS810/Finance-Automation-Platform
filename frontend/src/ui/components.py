"""Reusable UI components for consistent business-facing pages."""

from __future__ import annotations

import html

import streamlit as st


STATUS_CLASS_MAP = {
    "success": "success",
    "completed": "success",
    "ready": "success",
    "ready to save": "success",
    "matched": "success",
    "exact match": "success",
    "warning": "warning",
    "missing columns": "warning",
    "mismatch": "warning",
    "fuzzy match": "warning",
    "under review": "info",
    "in progress": "info",
    "info": "info",
    "backend integration pending": "info",
    "unsupported file": "error",
    "error": "error",
    "unmatched": "error",
    "high risk": "error",
}


def status_badge(status: str) -> str:
    """Return an HTML badge for a status label."""
    safe_status = html.escape(status)
    badge_class = STATUS_CLASS_MAP.get(status.strip().lower(), "info")
    return f'<span class="fa-badge {badge_class}">{safe_status}</span>'


def page_header(title: str, subtitle: str) -> None:
    """Render a page header hero section."""
    st.markdown(
        f"""
        <div class="fa-page-header">
            <h1>{html.escape(title)}</h1>
            <p>{html.escape(subtitle)}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def metric_card(title: str, value: str, caption: str | None = None, status: str | None = None, icon: str | None = None) -> None:
    """Render a metric card."""
    caption_html = f'<div class="fa-metric-caption">{html.escape(caption)}</div>' if caption else ""
    status_html = status_badge(status) if status else ""
    icon_label = html.escape(icon or title[:1].upper())
    st.markdown(
        f"""
        <div class="fa-metric-card">
            <div class="fa-metric-top">
                <div class="fa-metric-label">{html.escape(title)}</div>
                <div class="fa-metric-icon">{icon_label}</div>
            </div>
            <div class="fa-metric-value">{html.escape(value)}</div>
            {caption_html}
            {status_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def section_card(title: str, body_html: str | None = None, content: str | None = None) -> None:
    """Render a simple section card with optional body copy."""
    body = body_html if body_html is not None else f"<p>{html.escape(content or '')}</p>"
    st.markdown(
        f"""
        <div class="fa-section-card">
            <h3>{html.escape(title)}</h3>
            {body}
        </div>
        """,
        unsafe_allow_html=True,
    )


def empty_state(title: str, message: str) -> None:
    """Render a polished empty state."""
    st.markdown(
        f"""
        <div class="fa-empty-state">
            <h3>{html.escape(title)}</h3>
            <p>{html.escape(message)}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def file_summary_card(file_name: str, file_type: str, row_count: int | None = None) -> None:
    """Render a concise file summary card."""
    row_text = f"<p>Rows detected: {row_count}</p>" if row_count is not None else "<p>Ready for download.</p>"
    st.markdown(
        f"""
        <div class="fa-file-card">
            <div class="fa-file-label">{html.escape(file_type)}</div>
            <div class="fa-file-name">{html.escape(file_name)}</div>
            {row_text}
        </div>
        """,
        unsafe_allow_html=True,
    )


def insight_row(label: str, value: str, change: str | None = None, status: str | None = None) -> None:
    """Render a single key insight row."""
    change_html = ""
    if change:
        change_class = STATUS_CLASS_MAP.get((status or "").strip().lower(), "info")
        change_html = f'<span class="fa-insight-change {change_class}">{html.escape(change)}</span>'

    st.markdown(
        f"""
        <div class="fa-insight-row">
            <div class="fa-insight-label">{html.escape(label)}</div>
            <div>
                <span class="fa-insight-value">{html.escape(value)}</span>
                {change_html}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
