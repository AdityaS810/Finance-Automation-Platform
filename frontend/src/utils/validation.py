"""Reusable validation and dataframe normalization helpers."""

from __future__ import annotations

import re
from typing import Iterable

import pandas as pd


def normalize_column_name(column_name: object) -> str:
    """Convert a column label into lowercase snake_case text."""
    normalized = str(column_name).strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized)
    return normalized.strip("_")


def normalize_dataframe_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of a dataframe with normalized column names."""
    normalized_df = df.copy()
    normalized_df.columns = [normalize_column_name(column) for column in normalized_df.columns]
    return normalized_df


def validate_required_columns(df: pd.DataFrame, required_columns: Iterable[str]) -> dict:
    """Check whether a dataframe contains a set of required columns."""
    required = [normalize_column_name(column) for column in required_columns]
    available = [normalize_column_name(column) for column in df.columns]
    missing = [column for column in required if column not in available]

    return {
        "is_valid": not missing,
        "missing_columns": missing,
        "available_columns": list(df.columns),
    }
