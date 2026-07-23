"""Filesystem helpers for the Streamlit app."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


FRONTEND_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = FRONTEND_ROOT


def get_output_root() -> Path:
    """Return a writable output root suitable for local use or Cloud Run."""
    configured_root = os.getenv("FINANCE_OUTPUT_ROOT", "").strip()
    if configured_root:
        return Path(configured_root).expanduser()
    if os.getenv("K_SERVICE"):
        return Path(tempfile.gettempdir()) / "finance-automation-platform" / "outputs"
    return FRONTEND_ROOT / "outputs"


def ensure_directories() -> None:
    """Create the main project directories if they do not exist yet."""
    required_directories = [
        FRONTEND_ROOT / "data" / "sample_bank",
        FRONTEND_ROOT / "data" / "sample_gstr",
        get_output_root(),
        get_output_root() / "reports",
        get_output_root() / "reconciliation_exports",
    ]

    for directory in required_directories:
        directory.mkdir(parents=True, exist_ok=True)


def list_output_files(folder_path: str | Path) -> list[dict]:
    """List files in a folder with metadata for download screens."""
    folder = Path(folder_path)
    if not folder.exists():
        return []

    files = []
    for file_path in sorted(folder.iterdir()):
        if file_path.is_file():
            files.append(
                {
                    "file_name": file_path.name,
                    "type": file_path.suffix.lstrip(".").upper() or "FILE",
                    "last_modified": file_path.stat().st_mtime,
                    "path": file_path,
                }
            )
    return files
