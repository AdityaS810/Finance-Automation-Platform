"""Process-scoped Google Cloud clients for Streamlit pages."""

from __future__ import annotations

import os

import streamlit as st
from google.cloud import bigquery


DEFAULT_PROJECT_ID = "internal-project-work-497507"
DEFAULT_BIGQUERY_LOCATION = "asia-south1"


@st.cache_resource(show_spinner=False)
def get_bigquery_client() -> bigquery.Client:
    """Reuse one thread-safe BigQuery client for the Streamlit process."""
    return bigquery.Client(
        project=os.getenv("GCP_PROJECT_ID") or DEFAULT_PROJECT_ID,
        location=os.getenv("BIGQUERY_LOCATION") or DEFAULT_BIGQUERY_LOCATION,
    )
