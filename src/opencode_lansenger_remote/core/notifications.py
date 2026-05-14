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


def split_message(
    text: str, max_length: int = 4000, hard_limit: int = 6000,
) -> list[str]:
    """Split text into chunks respecting Markdown structure.

    Target chunk size is max_length (4000). Chunks may exceed this
    slightly to preserve Markdown integrity (e.g. keeping a code block
    together), but will never exceed hard_limit (6000).

    Never breaks inside:
    - Fenced code blocks (``` ... ```)
    - Inline code (`...`)
    - Bold/italic markers (**...**, *...*)
    - Links ([text](url))
    """
    if len(text) <= hard_limit:
        return [text]

    _SUFFIX = "\n\n... (续)"
    _SUFFIX_LEN = len(_SUFFIX)

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= hard_limit:
            chunks.append(remaining)
            break

        cut = _find_safe_cut(remaining, max_length - _SUFFIX_LEN, hard_limit - _SUFFIX_LEN)
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")

    if len(chunks) > 1:
        for i in range(len(chunks) - 1):
            chunks[i] += _SUFFIX

    return chunks


def _find_safe_cut(text: str, max_len: int, hard_limit: int) -> int:
    """Find a safe cut point that doesn't break Markdown.

    Tries to cut near max_len. If needed to preserve structure (e.g.
    closing a code block), may extend up to hard_limit. Never exceeds
    hard_limit.
    """

    # 1) Detect if we're inside a fenced code block at position max_len
    fence_count = 0
    pos = 0
    while pos < max_len:
        line_start = text.rfind("\n", 0, pos) + 1
        line_end = text.find("\n", pos)
        if line_end == -1:
            line_end = len(text)
        line = text[line_start:line_end]
        stripped = line.strip()
        if stripped.startswith("```"):
            fence_count += 1
        pos = line_end + 1

    inside_code_block = fence_count % 2 == 1

    # 2) If inside code block, extend cut to end of block (up to hard_limit)
    if inside_code_block:
        end_fence = text.find("\n```", max_len)
        if end_fence != -1:
            end_line = text.find("\n", end_fence + 1)
            if end_line == -1:
                end_line = len(text)
            if end_line <= hard_limit:
                return min(end_line, len(text))
        # End fence beyond hard_limit — force break at line boundary within hard_limit
        nl = text.rfind("\n", 0, hard_limit)
        return nl if nl > hard_limit * 0.3 else hard_limit

    # 3) Not inside code block — find best newline break near max_len
    best = text.rfind("\n\n", 0, max_len)
    if best > max_len * 0.3:
        return best

    best = text.rfind("\n", 0, max_len)
    if best > max_len * 0.3:
        chunk = text[:best]
        if _inline_markdown_balanced(chunk):
            return best

    # 4) Fallback: word boundary
    best = text.rfind(" ", 0, max_len)
    if best > max_len * 0.3:
        chunk = text[:best]
        if _inline_markdown_balanced(chunk):
            return best

    # 5) Hard cut at hard_limit
    return hard_limit


def _inline_markdown_balanced(chunk: str) -> bool:
    """Check that inline Markdown markers are balanced in chunk.

    Returns False if cutting here would break:
    - Backtick pairs (inline code)
    - Double-star pairs (bold)
    - Double-underscore pairs (bold)
    - Bracket-link pairs ([...](...))
    """
    # Backtick pairs (ignore fenced blocks)
    backticks = chunk.count("`") - chunk.count("```") * 3
    if backticks % 2 != 0:
        return False

    # Bold markers ** (count occurrences not inside code)
    if chunk.count("**") % 2 != 0:
        return False

    # Bold markers __
    if chunk.count("__") % 2 != 0:
        return False

    # Link pattern: [text](url) — opening [ without closing ]
    # Simple check: count standalone [ that aren't part of complete [...](...)
    import re
    open_brackets = len(re.findall(r"\[[^\]]*\]", chunk))
    all_brackets = chunk.count("[")
    if all_brackets > open_brackets:
        return False

    return True


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