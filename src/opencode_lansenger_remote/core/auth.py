"""Authorization — first-claimer model with JSON persistence."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Optional

AUTH_DIR = Path.home() / ".opencode-lansenger-remote"
AUTH_FILE = AUTH_DIR / "auth.json"


class AuthState:
    lansenger_owner: Optional[str] = None

    def to_dict(self) -> dict:
        return {"lansenger_owner": self.lansenger_owner}

    def from_dict(self, d: dict) -> None:
        self.lansenger_owner = d.get("lansenger_owner")


_auth = AuthState()


def _ensure_dir() -> None:
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(AUTH_DIR, stat.S_IRWXU)


def _load_auth() -> None:
    try:
        if AUTH_FILE.exists():
            data = json.loads(AUTH_FILE.read_text())
            _auth.from_dict(data)
    except Exception as e:
        print(f"Failed to load auth state, starting fresh: {e}")


def _save_auth() -> None:
    try:
        _ensure_dir()
        AUTH_FILE.write_text(json.dumps(_auth.to_dict(), indent=2))
        os.chmod(AUTH_FILE, stat.S_IRUSR | stat.S_IWUSR)
    except Exception as e:
        print(f"Failed to save auth state: {e}")


# Initialize on module load
_load_auth()


def isAuthorized(user_id: str) -> bool:
    return _auth.lansenger_owner == user_id


def has_owner() -> bool:
    return _auth.lansenger_owner is not None


def claim_ownership(user_id: str) -> dict:
    """Returns {success: bool, message: str}."""
    if _auth.lansenger_owner:
        if _auth.lansenger_owner == user_id:
            return {"success": True, "message": "already_owner"}
        return {"success": False, "message": "already_claimed"}
    _auth.lansenger_owner = user_id
    _save_auth()
    return {"success": True, "message": "claimed"}


def get_owner() -> Optional[str]:
    return _auth.lansenger_owner