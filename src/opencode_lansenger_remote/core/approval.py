"""Approval workflow — Promise-based with timeout auto-reject."""

from __future__ import annotations

import uuid
import time
import asyncio
from typing import Optional, List

from .types import Session, ApprovalRequest, FileChange, Config, load_config


# Maps request_id → (resolve_callback, reject_callback)
_approval_callbacks: dict[str, tuple] = {}


def create_approval_request(
    session: Session,
    type_: str,
    description: str,
    files: Optional[List[FileChange]] = None,
    command: Optional[str] = None,
) -> ApprovalRequest:
    config = load_config()
    now = time.time() * 1000
    request = ApprovalRequest(
        id=str(uuid.uuid4()),
        type=type_,
        description=description,
        files=files,
        command=command,
        created_at=now,
        expires_at=now + config.approval_timeout_ms,
    )
    session.pending_approvals.append(request)
    return request


def get_pending_approval(session: Session, request_id: Optional[str] = None) -> Optional[ApprovalRequest]:
    if request_id:
        return next((r for r in session.pending_approvals if r.id == request_id), None)
    return session.pending_approvals[0] if session.pending_approvals else None


def resolve_approval(session: Session, request_id: str, approved: bool) -> dict:
    idx = next((i for i, r in enumerate(session.pending_approvals) if r.id == request_id), -1)
    if idx == -1:
        return {"success": False, "error": "Approval request not found"}

    request = session.pending_approvals[idx]
    if time.time() * 1000 > request.expires_at:
        session.pending_approvals.pop(idx)
        return {"success": False, "error": "Approval request expired", "request": request}

    session.pending_approvals.pop(idx)

    # Resolve the asyncio Future if present
    future = _approval_callbacks.pop(request_id, None)
    if future and not future.done():
        future.set_result(approved)

    return {"success": True, "request": request}


async def wait_for_approval(request: ApprovalRequest) -> bool:
    """Returns True if approved, False if rejected/timeout."""
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    _approval_callbacks[request.id] = future

    # Auto-reject on timeout
    time_until_expiry = (request.expires_at - time.time() * 1000) / 1000
    if time_until_expiry > 0:
        loop.call_later(time_until_expiry, _auto_reject, request.id)

    return await future


def _auto_reject(request_id: str) -> None:
    future = _approval_callbacks.pop(request_id, None)
    if future and not future.done():
        future.set_result(False)  # Auto-reject


def cancel_all_approvals(session: Session) -> None:
    for request in session.pending_approvals:
        future = _approval_callbacks.pop(request.id, None)
        if future and not future.done():
            future.set_exception(Exception("Session ended"))
    session.pending_approvals.clear()


def format_approval_message(request: ApprovalRequest) -> str:
    lines = []
    if request.type == "file_edit":
        lines.append("📝 需要审批：编辑文件")
        lines.append("")
        lines.append("📄 变更：")
        if request.files:
            for f in request.files:
                lines.append(f"• {f.path} (+{f.additions}, -{f.deletions})")
    else:
        lines.append("📝 需要审批：执行命令")
        lines.append("")
        lines.append(f"🔧 `{request.command}`")

    lines.append("")
    lines.append("/approve — 批准变更")
    lines.append("/reject — 拒绝变更")
    lines.append("/diff — 查看变更详情")
    remaining = max(0, (request.expires_at - time.time() * 1000) / 60000)
    lines.append(f"⏱️ {remaining:.0f} 分钟后自动拒绝")
    return "\n".join(lines)