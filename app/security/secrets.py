"""Secrets are referenced, never stored in the database or code (§28).
A secret_ref like 'PROXY_ALPHA' resolves to environment variable SECRET_PROXY_ALPHA."""
from __future__ import annotations

import os


def get_secret(ref: str | None) -> str | None:
    if not ref:
        return None
    return os.environ.get(f"SECRET_{ref.upper()}")
