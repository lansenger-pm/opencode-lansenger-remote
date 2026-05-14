"""Notification formatting and message templates."""

from __future__ import annotations

from typing import Optional, List

from .types import EMOJI, FileChange


def format_notification(
    type_: str,
    title: Optional[str] = None,
    details: Optional[str] = None,
    files: Optional[List[FileChange]] = None,
    actions: Optional[List[str]] = None,
) -> str:
    lines = []

    emoji_map = {
        "success": EMOJI["SUCCESS"],
        "error": EMOJI["ERROR"],
        "loading": EMOJI["LOADING"],
        "input_needed": EMOJI["QUESTION"],
        "expired": EMOJI["EXPIRED"],
        "started": EMOJI["START"],
    }
    emoji = emoji_map.get(type_, "")
    default_title = {
        "success": "完成",
        "error": "出错",
        "loading": "思考中...",
        "input_needed": "问题",
        "expired": "会话过期",
        "started": "就绪",
    }
    lines.append(f"{emoji} {title or default_title.get(type_, '')}")

    if details or files or actions:
        lines.append("")

    if files and len(files) > 0:
        lines.append(f"📄 {len(files)} 个文件变更：")
        for f in files[:5]:
            lines.append(f"• {f.path} (+{f.additions}, -{f.deletions})")
        if len(files) > 5:
            lines.append(f"• ... 还有 {len(files) - 5} 个")

    if details:
        lines.append(details)

    if actions:
        lines.append("")
        lines.append(" • ".join(actions))

    return "\n".join(lines)


def split_message(text: str, max_length: int = 4000) -> list[str]:
    if len(text) <= max_length:
        return [text]

    messages = []
    remaining = text
    while remaining:
        if len(remaining) <= max_length:
            messages.append(remaining)
            break

        # Find good break point
        break_point = remaining.rfind("\n", 0, max_length)
        if break_point < max_length * 0.5:
            break_point = remaining.rfind(" ", 0, max_length)
        if break_point < max_length * 0.5:
            break_point = max_length

        messages.append(remaining[:break_point])
        remaining = remaining[break_point:].strip()

    if len(messages) > 1:
        for i in range(len(messages) - 1):
            messages[i] += "\n\n... (续)"

    return messages


# ── Pre-built templates ───────────────────────────────────────────────
def bot_started() -> str:
    return format_notification(
        "started",
        title="OpenCode 远程控制就绪 🌠",
        actions=[
            "💬 发送 prompt 开始编码",
            "/help — 命令列表",
            "/status — 连接状态",
        ],
    )


def session_expired() -> str:
    return format_notification(
        "expired",
        title="会话过期（30 分钟无活动）",
        actions=["💬 发送新消息重新开始"],
    )


def task_completed(files: Optional[List[FileChange]] = None) -> str:
    return format_notification(
        "success",
        files=files or [],
        actions=["💬 回复继续", "/files — 查看变更"],
    )


def task_failed(error: str) -> str:
    return format_notification(
        "error",
        title=error[:50],
        details="任务失败。OpenCode 仍在运行。",
        actions=["💬 重试", "/reset — 重置会话"],
    )


def needs_input(question: str, options: Optional[List[str]] = None) -> str:
    details = None
    actions = None
    if options:
        details = "\n".join(f"{i + 1}. {o}" for i, o in enumerate(options))
        actions = ["回复编号选择"]
    return format_notification("input_needed", title=question, details=details, actions=actions)


def opencode_offline() -> str:
    return format_notification(
        "error",
        title="OpenCode 离线",
        details="无法连接 OpenCode 服务。",
        actions=["🔄 /retry — 重试", "/status — 诊断"],
    )


def thinking() -> str:
    return format_notification("loading", title="思考中... 🤔")


def approved() -> str:
    return format_notification("success", title="已批准 — 变更已应用 ✅")


def rejected() -> str:
    return format_notification("success", title="已拒绝 — 变更已丢弃 ❌")


def approval_timeout() -> str:
    return format_notification(
        "error",
        title="审批超时（5 分钟）",
        details="变更已自动拒绝。",
    )