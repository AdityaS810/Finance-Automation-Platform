"""Test path setup for frontend and backend imports."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = REPO_ROOT / "frontend"

for import_path in [str(REPO_ROOT), str(FRONTEND_ROOT)]:
    if import_path not in sys.path:
        sys.path.insert(0, import_path)


@pytest.fixture
def workspace_tmp_path(request):
    """Create a writable temp folder inside the repo for Windows-safe test output."""
    test_folder = FRONTEND_ROOT / "tests" / "_tmp" / request.node.name
    test_folder.mkdir(parents=True, exist_ok=True)
    return test_folder
