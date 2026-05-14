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


def get_or_create_session(thread_id: str, platform: str = "lansenger") -> Session:
    """Return existing session (touching lastActivity) or create new one."""
    existing = _sessions.get(thread_id)
    if existing:
        existing.last_activity = time.time() * 1000
        return existing

    now = time.time() * 1000
    session = Session(
        id=str(uuid.uuid4()),
        thread_id=thread_id,
        platform=platform,
        created_at=now,
        last_activity=now,
    )
    _sessions[thread_id] = session
    print(f"Session created: {thread_id}")
    return session


def get_session(thread_id: str) -> Optional[Session]:
    return _sessions.get(thread_id)


def update_session(thread_id: str, **kwargs) -> Optional[Session]:
    session = _sessions.get(thread_id)
    if not session:
        return None
    for k, v in kwargs.items():
        setattr(session, k, v)
    session.last_activity = time.time() * 1000
    return session


def delete_session(thread_id: str) -> bool:
    return _sessions.pop(thread_id, None) is not None


def get_all_sessions() -> list[Session]:
    return list(_sessions.values())


def get_session_count() -> int:
    return len(_sessions)


async def stop_session_manager() -> None:
    if _cleanup_task:
        _cleanup_task.cancel()
        try:
            await _cleanup_task
        except asyncio.CancelledError:
            pass