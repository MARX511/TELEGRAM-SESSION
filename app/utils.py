from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def mask_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) <= 4:
        return "*" * len(digits)
    return "+" + digits[:2] + "*" * (len(digits) - 4) + digits[-2:]


def _default(o: Any):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, Path):
        return str(o)
    if hasattr(o, "value"):
        return o.value
    return str(o)


def dumps(obj: Any) -> str:
    return json.dumps(obj, default=_default, ensure_ascii=False, sort_keys=True)


def loads(s: str | None) -> Any:
    if not s:
        return None
    return json.loads(s)
