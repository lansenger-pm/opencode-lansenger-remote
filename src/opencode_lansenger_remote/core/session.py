"""Session management — in-memory with async idle-timeout eviction."""

from __future__ import annotations

import asyncio
import uuid
import time
from typing import Optional

from .types import Session, Config, load_config


_sessions: dict[str, Session] = {}
_cleanup_task: Optional[asyncio.Task] = None


async def init_session_manager(config: Config = load_config()) -> None:
    """Start async periodic cleanup that evicts idle sessions."""
    global _cleanup_task
    _cleanup_task = asyncio.create_task(_cleanup_loop(config))
    print(f"Session manager initialized (cleanup every {config.cleanup_interval_ms / 1000}s)")


async def _cleanup_loop(config: Config) -> None:
    """Async cleanup loop — evicts idle sessions."""
    while True:
        await asyncio.sleep(config.cleanup_interval_ms / 1000)
        now = time.time() * 1000
        expired = [
            tid for tid, s in _sessions.items()
            if now - s.last_activity > config.session_idle_timeout_ms
        ]
        for tid in expired:
            _sessions.pop(tid, None)
            print(f"Session expired: {tid}")


async def shutdown_session_manager() -> None:
    """Cancel the cleanup task for graceful shutdown."""
    global _cleanup_task
    if _cleanup_task:
        _cleanup_task.cancel()
        try:
            await _cleanup_task
        except asyncio.CancelledError:
            pass
        _cleanup_task = None


def get_or_create_session(thread_id: str, platform: str = "lansenger") -> Session:
    """Return existing session (touching lastActivity) or create new one."""
    existing = _sessions.get(thread_id)
    if existing:
        existing.last_activity = time.time() * 1000
        return existing

    session = Session(
        id=str(uuid.uuid4()),
        platform=platform,
        thread_id=thread_id,
        last_activity=time.time() * 1000,
    )
    _sessions[thread_id] = session
    return session


def get_session(thread_id: str) -> Optional[Session]:
    """Return session without touching lastActivity."""
    return _sessions.get(thread_id)