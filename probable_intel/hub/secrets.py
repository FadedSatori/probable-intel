from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


class SecretManager:
    def __init__(self, apparatus_id: str = "", dotenv_path: Path | None = None) -> None:
        self._apparatus_id = apparatus_id
        _load_dotenv(dotenv_path or Path(".env"))

    def get(self, key: str, default: str = "") -> str:
        """Look up a secret by env var name."""
        return os.environ.get(key, default)

    def require(self, key: str) -> str:
        val = os.environ.get(key)
        if not val:
            raise EnvironmentError(
                f"required secret {key!r} is not set; add it to .env"
            )
        return val
