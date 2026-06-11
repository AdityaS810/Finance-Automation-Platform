"""Filesystem helpers for the Streamlit app."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def ensure_directories() -> None:
    """Create the main project directories if they do not exist yet."""
    required_directories = [
        PROJECT_ROOT / "data" / "sample_bank",
        PROJECT_ROOT / "data" / "sample_gstr",
        PROJECT_ROOT / "outputs" / "reports",
        PROJECT_ROOT / "outputs" / "reconciliation_exports",
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
