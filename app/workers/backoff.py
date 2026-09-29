from __future__ import annotations

import random


def compute_backoff(attempt: int, base: float, cap: float, jitter: bool = True) -> float:
    """Exponential backoff for OUR retries (network/server errors). Server-defined waits (FloodWait) are used
    verbatim instead and never shortened."""
    delay = min(cap, base * (2 ** max(0, attempt - 1)))
    if jitter:
        delay *= 0.5 + random.random() / 2
    return round(delay, 3)
