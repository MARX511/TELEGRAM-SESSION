"""At-rest encryption for session files (§28/§29). Fernet (AES-128-CBC + HMAC) with a key from the environment.
Encrypted files carry the '.enc' suffix; plaintext is only materialised in a private temp file during a check."""
from __future__ import annotations

import base64
import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

ENC_SUFFIX = ".enc"


def generate_key() -> str:
    return Fernet.generate_key().decode()


class SessionCrypto:
    def __init__(self, key: str | None):
        self._fernet = Fernet(key.encode() if isinstance(key, str) else key) if key else None

    @property
    def enabled(self) -> bool:
        return self._fernet is not None

    def encrypt_file(self, path: Path, remove_plain: bool = True) -> Path:
        if not self.enabled:
            raise RuntimeError("SESSION_FILE_ENCRYPTION_KEY is not configured")
        if path.suffix == ENC_SUFFIX:
            return path
        data = path.read_bytes()
        out = path.with_name(path.name + ENC_SUFFIX)
        out.write_bytes(self._fernet.encrypt(data))
        os.chmod(out, 0o600)
        if remove_plain:
            path.unlink()
        return out

    def decrypt_bytes(self, path: Path) -> bytes:
        if not self.enabled:
            raise RuntimeError("SESSION_FILE_ENCRYPTION_KEY is not configured")
        try:
            return self._fernet.decrypt(path.read_bytes())
        except InvalidToken as exc:
            raise ValueError("session file cannot be decrypted with the configured key") from exc

    def decrypt_to_temp(self, path: Path) -> Path:
        """Materialise plaintext in a 0600 temp file. Caller must delete it (see SessionService)."""
        data = self.decrypt_bytes(path)
        fd, tmp = tempfile.mkstemp(prefix="tgsess_", suffix=".session")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o600)
        return Path(tmp)

    @staticmethod
    def is_encrypted(path: Path) -> bool:
        return path.name.endswith(ENC_SUFFIX)

    @staticmethod
    def plain_name(path: Path) -> str:
        return path.name[: -len(ENC_SUFFIX)] if path.name.endswith(ENC_SUFFIX) else path.name


def key_fingerprint(key: str | None) -> str | None:
    if not key:
        return None
    import hashlib

    return hashlib.sha256(base64.urlsafe_b64decode(key)).hexdigest()[:12]
