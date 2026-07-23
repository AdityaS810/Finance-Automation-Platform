"""Shared, retrying HTTP transport for Zoho API calls."""

from __future__ import annotations

from functools import lru_cache

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


ZOHO_REQUEST_TIMEOUT_SECONDS = (5, 30)


@lru_cache(maxsize=1)
def get_zoho_http_session() -> requests.Session:
    """Return one connection-pooled session with bounded transient retries."""
    retry_policy = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry_policy, pool_connections=10, pool_maxsize=10)
    session = requests.Session()
    session.mount("https://", adapter)
    return session
