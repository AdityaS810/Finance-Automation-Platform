"""Tests for backend reconciliation flows."""

from __future__ import annotations

from backend.agents.bank_reconciliation import run_bank_reconciliation
from backend.agents.gst_reconciliation import run_gst_reconciliation


def test_bank_reconciliation_returns_sample_dataframe(workspace_tmp_path):
    result = run_bank_reconciliation(workspace_tmp_path)

    assert result["summary"]["matched_records"] > 0
    assert not result["results"].empty
    assert result["export_path"].exists()


def test_gst_reconciliation_returns_sample_dataframe(workspace_tmp_path):
    result = run_gst_reconciliation(workspace_tmp_path)

    assert result["summary"]["exact_matches"] > 0
    assert not result["results"].empty
    assert result["export_path"].exists()
