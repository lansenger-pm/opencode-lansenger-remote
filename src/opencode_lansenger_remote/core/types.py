"""Core types, configuration, and constants for OpenCode Lansenger Remote."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional, List


# ── Emoji vocabulary ──────────────────────────────────────────────────
EMOJI = {
    "SUCCESS": "✅",
    "ERROR": "❌",
    "LOADING": "⏳",
    "THINKING": "🤔",
    "APPROVAL": "📝",
    "FILES": "📄",
    "CODE": "🔧",
    "START": "🚀",
    "EXPIRED": "💤",
    "WARNING": "⚠️",
    "QUESTION": "💬",
}


# ── Data types ────────────────────────────────────────────────────────
@dataclass
class FileChange:
    path: str
    additions: int
    deletions: int


@dataclass
class ApprovalRequest:
    id: str
    type: str  # "file_edit" | "shell_command"
    description: str
    files: Optional[List[FileChange]] = None
    command: Optional[str] = None
    created_at: float = 0.0
    expires_at: float = 0.0


@dataclass
class Session:
    id: str
    thread_id: str
    platform: str = "lansenger"
    created_at: float = 0.0
    last_activity: float = 0.0
    opencode_session_id: Optional[str] = None
    pending_approvals: List[ApprovalRequest] = field(default_factory=list)


@dataclass
class MessageContext:
    platform: str = "lansenger"
    thread_id: str = ""
    user_id: str = ""
    message_id: Optional[str] = None


# ── Configuration ─────────────────────────────────────────────────────
DEFAULT_API_GATEWAY_URL = "https://apigw.lx.qianxin.com"

RECONNECT_BACKOFF = [2, 5, 10, 30, 60]  # seconds


@dataclass
class Config:
    # Lansenger credentials
    lansenger_app_id: str = ""
    lansenger_app_secret: str = ""
    lansenger_api_gateway_url: str = DEFAULT_API_GATEWAY_URL

    # OpenCode config
    opencode_server_url: str = "http://localhost:4096"
    opencode_model: str = ""  # 指定 opencode 使用的模型，如 gpt-4, claude-sonnet 等

    # Timeout config
    session_idle_timeout_ms: int = 1800000  # 30 min
    cleanup_interval_ms: int = 300000  # 5 min
    approval_timeout_ms: int = 300000  # 5 min
    request_timeout_minutes: int = 30


def load_config() -> Config:
    """Load config from environment variables and .env file."""
    config = Config()

    # Check .env file
    env_path = os.path.expanduser("~/.opencode-lansenger-remote/.env")
    env_vars: dict[str, str] = {}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    env_vars[key.strip()] = val.strip()

    # Lansenger credentials (env file < env vars)
    config.lansenger_app_id = os.getenv(
        "LANSENGER_APP_ID", env_vars.get("LANSENGER_APP_ID", "")
    )
    config.lansenger_app_secret = os.getenv(
        "LANSENGER_APP_SECRET", env_vars.get("LANSENGER_APP_SECRET", "")
    )
    config.lansenger_api_gateway_url = os.getenv(
        "LANSENGER_API_GATEWAY_URL",
        env_vars.get("LANSENGER_API_GATEWAY_URL", DEFAULT_API_GATEWAY_URL),
    )

    # OpenCode config
    config.opencode_server_url = os.getenv(
        "OPENCODE_SERVER_URL",
        env_vars.get("OPENCODE_SERVER_URL", "http://localhost:4096"),
    )

    # Timeout config
    config.session_idle_timeout_ms = int(
        os.getenv("SESSION_IDLE_TIMEOUT_MS", env_vars.get("SESSION_IDLE_TIMEOUT_MS", "1800000"))
    )
    config.cleanup_interval_ms = int(
        os.getenv("CLEANUP_INTERVAL_MS", env_vars.get("CLEANUP_INTERVAL_MS", "300000"))
    )
    config.approval_timeout_ms = int(
        os.getenv("APPROVAL_TIMEOUT_MS", env_vars.get("APPROVAL_TIMEOUT_MS", "300000"))
    )
    config.request_timeout_minutes = int(
        os.getenv("OPENCODE_REQUEST_TIMEOUT_MINUTES",
                  env_vars.get("OPENCODE_REQUEST_TIMEOUT_MINUTES", "30"))
    )
    config.opencode_model = os.getenv(
        "OPENCODE_MODEL", env_vars.get("OPENCODE_MODEL", "")
    )

    return config


# ── API endpoints ─────────────────────────────────────────────────────
API_ENDPOINTS = {
    "auth": {
        "app_token": "/v1/apptoken/create",
    },
    "websocket": {
        "endpoint": "/v1/ws/endpoint/create",
    },
    "smart_bot": {
        "private_message": "/v1/bot/messages/create",
        "group_message": "/v1/messages/group/create",
    },
    "medias": {
        "upload": "/v1/medias/create",
        "fetch": "/v1/medias/{media_id}/fetch",
    },
    "message": {
        "revoke": "/v1/messages/revoke",
        "dynamic_update": "/v1/messages/dynamic/update",
    },
    "groups": {
        "fetch": "/v2/groups/fetch",
    },
}