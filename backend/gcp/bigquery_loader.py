"""Compatibility wrapper for the generic ETL bronze loader."""

from backend.etl.bronze_loader import load_raw_records_to_bigquery


__all__ = ["load_raw_records_to_bigquery"]
