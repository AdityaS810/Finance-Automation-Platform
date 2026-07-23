"""Tests for Cloud Run-safe runtime resources."""

from __future__ import annotations

import tempfile
from pathlib import Path

from src.utils.file_helpers import get_output_root


def test_cloud_run_outputs_use_system_temp(monkeypatch):
    monkeypatch.delenv("FINANCE_OUTPUT_ROOT", raising=False)
    monkeypatch.setenv("K_SERVICE", "finance-automation-platform")

    output_root = get_output_root()

    assert output_root == Path(tempfile.gettempdir()) / "finance-automation-platform" / "outputs"


def test_configured_output_root_takes_precedence(monkeypatch, workspace_tmp_path):
    configured_root = workspace_tmp_path / "configured-outputs"
    monkeypatch.setenv("K_SERVICE", "finance-automation-platform")
    monkeypatch.setenv("FINANCE_OUTPUT_ROOT", str(configured_root))

    assert get_output_root() == configured_root
