"""Lansenger bot — message handler, command routing, OpenCode integration."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Optional, Any

from ..core.types import Config, MessageContext, EMOJI
from ..core.session import get_or_create_session, init_session_manager
from ..core.auth import isAuthorized, has_owner, claim_ownership, get_owner
from ..core.approval import (
    create_approval_request,
    get_pending_approval,
    resolve_approval,
    wait_for_approval,
    format_approval_message,
    cancel_all_approvals,
)
from ..core.notifications import (
    bot_started,
    session_expired,
    task_completed,
    task_failed,
    needs_input,
    opencode_offline,
    thinking,
    approved,
    rejected,
    approval_timeout,
    split_message,
)
from ..opencode.client import OpenCodeClient, list_opencode_projects
from .client import LansengerClient
from .ws import LansengerWS


class LansengerBot:
    """Lansenger personal bot — receives WS messages, routes commands, integrates OpenCode."""

    def __init__(self, config: Config):
        self._config = config
        self._http_client = LansengerClient(config)
        self._opencode = OpenCodeClient(config)
        self._opencode_sessions: dict[str, Any] = {}
        self._ws: Optional[LansengerWS] = None

        # Dedup dict for inbound messages (ordered, keeps insertion order for truncation)
        self._seen_msg_ids: dict[str, bool] = {}
        # Project list cache for /projects + /cd <number>
        self._project_list: list = []

    async def start(self) -> None:
        """Initialize everything and start the bot."""
        # Check credentials
        if not self._config.lansenger_app_id or not self._config.lansenger_app_secret:
            print("\n❌ 蓝信凭证未配置！")
            print("请设置 LANSENGER_APP_ID 和 LANSENGER_APP_SECRET")
            print(f"配置文件：~/.opencode-lansenger-remote/.env")
            return

        # Initialize session manager
        await init_session_manager(self._config)

        # Initialize OpenCode
        print("🔧 正在初始化 OpenCode...")
        try:
            await self._opencode.init()
            print("✅ OpenCode 就绪")
        except Exception as e:
            print(f"❌ OpenCode 初始化失败: {e}")
            print("请确保 OpenCode 已安装: npm install -g @opencode-ai/opencode")

        # Start WS connection
        self._ws = LansengerWS(
            http_client=self._http_client,
            on_message=self._on_event,
        )
        await self._ws.start()

        # Show security status
        if not has_owner():
            print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print("  🔐 安全提示")
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print("")
            print("  机器人尚未绑定 owner！")
            print("  首位发送 /start 的用户将自动成为 owner。")
            print("")
            print("  👉 请在蓝信中发送 /start 认领机器人！")
            print("")
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        else:
            print("🔒 机器人已绑定 owner")

        # Keep running
        print("\n🚀 蓝信机器人已启动 🌠")
        print("   在蓝信中发送消息即可控制 OpenCode")

        # Wait forever (or until SIGINT)
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            await self.stop()

    async def stop(self) -> None:
        """Stop everything."""
        print("\n🛑 正在关闭...")
        if self._ws:
            await self._ws.stop()
        await self._http_client.close()
        await self._opencode.close()
        print("已关闭")

    # ── Inbound event processing ──────────────────────────────────────
    async def _on_event(self, event_data: dict) -> None:
        """Process a single WS event from Lansenger."""
        msg_data = event_data.get("data", {})
        msg_type = msg_data.get("msgType", "text")
        msg_id = event_data.get("id", "") or msg_data.get("messageId", "") or ""

        # Dedup
        if msg_id and msg_id in self._seen_msg_ids:
            return
        if msg_id:
            self._seen_msg_ids[msg_id] = True
        # Trim dedup dict (keep most recent 500)
        if len(self._seen_msg_ids) > 1000:
            excess = len(self._seen_msg_ids) - 500
            for key in list(self._seen_msg_ids.keys())[:excess]:
                self._seen_msg_ids.pop(key, None)

        # Extract text — handle both content/Content cases
        msg_data_content = msg_data.get("msgData", {})
        text = ""
        if isinstance(msg_data_content, dict):
            text_content = msg_data_content.get("text", {})
            if isinstance(text_content, dict):
                text = text_content.get("content", "") or text_content.get("Content", "")
            elif isinstance(text_content, str):
                text = text_content
        # Fallback: direct text field in data
        if not text:
            direct_text = msg_data.get("text", {})
            if isinstance(direct_text, dict):
                text = direct_text.get("content", "") or direct_text.get("Content", "")
            elif isinstance(direct_text, str):
                text = direct_text

        if not text.strip():
            print(f"[Lansenger] Empty message, msgType={msg_type}")
            return

        # Build context — FromStaffId (uppercase) fallback
        sender_id = msg_data.get("from", "") or msg_data.get("FromStaffId", "")
        chat_id = msg_data.get("conversationId") or msg_data.get("ConversationId") or sender_id

        ctx = MessageContext(
            platform="lansenger",
            thread_id=chat_id,
            user_id=sender_id,
            message_id=msg_id,
        )

        print(f"[Lansenger] 收到消息: {text[:50]} (from={sender_id}, id={msg_id[:16]})")

        # Route to handler
        try:
            await self._handle_message(ctx, text)
        except Exception as e:
            import traceback
            print(f"[Lansenger] 处理消息出错: {e}")
            traceback.print_exc()

    # ── Message routing ───────────────────────────────────────────────
    async def _handle_message(self, ctx: MessageContext, text: str) -> None:
        """Route message: command or prompt."""
        session = get_or_create_session(ctx.thread_id, "lansenger")

        if text.startswith("/"):
            await self._handle_command(ctx, text, session)
            return

        # Non-command: prompt → send to OpenCode
        # Auth check
        if not isAuthorized(ctx.user_id):
            if not has_owner():
                await self._reply(ctx.thread_id, "🔐 **需要授权**\n\n请先发送 /start 认领机器人。")
            else:
                await self._reply(ctx.thread_id, "🚫 **访问被拒绝**\n\n你不是此机器人的授权用户。")
            return

        # Check OpenCode connection
        if not await self._opencode.check_connection():
            await self._reply(ctx.thread_id, opencode_offline())
            return

        # Send typing indicator
        typing_msg_id = await self._http_client.send_text(ctx.thread_id, thinking())

        # Get or create OpenCode session
        opencode_session = self._opencode_sessions.get(ctx.thread_id)
        if not opencode_session:
            opencode_session = await self._opencode.create_session(
                ctx.thread_id, f"Lansenger chat {ctx.thread_id}"
            )
            if not opencode_session:
                await self._reply(ctx.thread_id, "❌ 无法创建 OpenCode 会话")
                return
            self._opencode_sessions[ctx.thread_id] = opencode_session
            session.opencode_session_id = opencode_session.get("sessionId")

        # Send to OpenCode
        try:
            response = await self._opencode.send_message(
                opencode_session, text
            )

            # Split long response
            messages = split_message(response)
            for msg in messages:
                await self._reply(ctx.thread_id, msg)
        except Exception as e:
            await self._reply(ctx.thread_id, task_failed(str(e)))

    # ── Command handling ──────────────────────────────────────────────
    async def _handle_command(self, ctx: MessageContext, text: str, session: Any) -> None:
        """Handle slash commands."""
        command = text.split()[0].lower()

        switch = {
            "/start": self._cmd_start,
            "/help": self._cmd_help,
            "/status": self._cmd_status,
            "/approve": self._cmd_approve,
            "/reject": self._cmd_reject,
            "/diff": self._cmd_diff,
            "/files": self._cmd_files,
            "/reset": self._cmd_reset,
            "/retry": self._cmd_retry,
            "/project": self._cmd_project,
            "/projects": self._cmd_projects,
            "/cd": self._cmd_cd,
            "/pwd": self._cmd_pwd,
        }

        handler = switch.get(command)
        if handler:
            await handler(ctx, session, text)
        else:
            await self._reply(
                ctx.thread_id,
                f"{EMOJI['WARNING']} 未知命令: {command}\n\n试试 /help",
            )

    async def _cmd_start(self, ctx: MessageContext, session: Any, raw_text: str = "") -> None:
        result = claim_ownership(ctx.user_id)
        if result["success"]:
            if result["message"] == "claimed":
                await self._reply(ctx.thread_id, bot_started() + f"\n\n🔑 你的 ID: `{ctx.user_id}`")
            else:
                await self._reply(ctx.thread_id, bot_started())
        else:
            await self._reply(ctx.thread_id, "🚫 **访问被拒绝**\n\n此机器人已被其他用户绑定。")

    async def _cmd_help(self, ctx: MessageContext, session: Any, raw_text: str = "") -> None:
        await self._reply(ctx.thread_id, """📖 命令列表

/start — 认领 owner & 启动
/status — 检查连接
/projects — 列出 OpenCode 项目（编号选择）
/project — 查看当前项目信息
/pwd — 查看当前工作目录
/cd <路径或编号> — 切换项目目录（如 /cd 1 或 /cd ~/my-project）
/reset — 重置会话
/approve — 批准待审批变更
/reject — 拒绝变更
/diff — 查看变更详情
/files — 列出变更文件
/retry — 重试 OpenCode 连接

💬 其他文本作为 prompt 发送给 OpenCode！""")

    async def _cmd_status(self, ctx: MessageContext, session: Any, raw_text: str = "") -> None:
        connected = await self._opencode.check_connection()
        oc_session = self._opencode_sessions.get(ctx.thread_id)
        idle_s = int((time.time() * 1000 - session.last_activity) / 1000)
        pending = len(session.pending_approvals)
        status = "✅" if connected else "❌"
        oc_id = (oc_session.get("sessionId", "")[:8] if oc_session else "none")
        await self._reply(ctx.thread_id,
            f"{status} 连接状态\n\n💬 会话: {oc_id}\n⏰ 空闲: {idle_s}s\n📝 待审批: {pending}")

    async def _cmd_approve(self, ctx: MessageContext, session: Any, raw_text: str = "") -> None:
        pending = get_pending_approval(session)
        if not pending:
            await self._reply(ctx.thread_id, "🤷 当前没有待审批的变更")
            return
        resolve_approval(session, pending.id, True)
        await self._reply(ctx.thread_id, approved())

    async def _cmd_reject(self, ctx: MessageContext, session: Any, raw_text: str = "") -> None:
        pending = get_pending_approval(session)
        if not pending:
            await self._reply(ctx.thread_id, "🤷 当前没有待拒绝的变更")
            return
        resolve_approval(session, pending.id, False)
        await self._reply(ctx.thread_id, rejected())

    async def _cmd_diff(self, ctx: MessageContext, session: Any, raw_text: str = "") -> None:
        pending = get_pending_approval(session)
        if not pending or not pending.files:
            await self._reply(ctx.thread_id, "📄 没有待查看的变更")
            return
        diff_lines = []
        for f in pending.files:
            diff_lines.append(f"--- a/{f.path}\n+++ b/{f.path}\n@@ +{f.additions} -{f.deletions} @@")
        messages = split_message("```diff\n" + "\n".join(diff_lines) + "\n```")
        for msg in messages:
            await self._reply(ctx.thread_id, msg)

    async def _cmd_files(self, ctx: MessageContext, session: Any, raw_text: str = "") -> None:
        pending = get_pending_approval(session)
        if not pending or not pending.files:
            await self._reply(ctx.thread_id, "📄 当前会话没有变更文件")
            return
        file_list = "\n".join(f"• {f.path} (+{f.additions}, -{f.deletions})" for f in pending.files)
        await self._reply(ctx.thread_id, f"📄 变更文件:\n{file_list}")

    async def _cmd_reset(self, ctx: MessageContext, session: Any, raw_text: str = "") -> None:
        cancel_all_approvals(session)
        session.opencode_session_id = None
        self._opencode_sessions.pop(ctx.thread_id, None)
        await self._reply(ctx.thread_id, "🔄 会话已重置，重新开始！")

    async def _cmd_retry(self, ctx: MessageContext, session: Any, raw_text: str = "") -> None:
        connected = await self._opencode.check_connection()
        if connected:
            await self._reply(ctx.thread_id, "✅ OpenCode 已上线！")
        else:
            await self._reply(ctx.thread_id, "❌ 仍然离线。请确认 OpenCode 是否在运行。")

    async def _cmd_project(self, ctx: MessageContext, session: Any, raw_text: str = "") -> None:
        """Show current project info (via HTTP or CLI)."""
        cwd = self._opencode.current_workdir()
        project_info = f"📁 当前工作目录: `{cwd}`"
        if self._opencode._http_available:
            try:
                info = await self._opencode.get_project_info()
                if info:
                    name = info.get("name", "unknown")
                    project_info += f"\n📂 项目名称: {name}"
            except Exception:
                pass
        await self._reply(ctx.thread_id, project_info)

    async def _cmd_projects(self, ctx: MessageContext, session: Any, raw_text: str = "") -> None:
        """List OpenCode projects and allow selection by number."""
        import os
        projects = list_opencode_projects()
        if not projects:
            await self._reply(ctx.thread_id, "❌ 未找到 OpenCode 项目记录\n\n请先在 OpenCode 客户端中打开一些项目。")
            return

        # Build numbered list
        lines = ["📂 OpenCode 项目列表：", ""]
        for i, p in enumerate(projects, 1):
            name = p["name"]
            path = p["path"]
            short_path = path.replace(os.path.expanduser("~"), "~")
            current = " ← 当前" if path == self._opencode.current_workdir() else ""
            lines.append(f"  {i}. **{name}** `{short_path}`{current}")
        lines.append("")
        lines.append("💡 输入 `/cd <编号>` 快速切换，如 `/cd 1`")

        # Store project list for quick selection
        self._project_list = projects

        await self._reply(ctx.thread_id, "\n".join(lines))

    async def _cmd_cd(self, ctx: MessageContext, session: Any, raw_text: str = "") -> None:
        """Switch working directory for OpenCode. Supports number index from /projects."""
        import os
        args = raw_text.split(maxsplit=1)
        if len(args) < 2:
            await self._reply(ctx.thread_id, "⚠️ 用法: `/cd <路径或编号>`\n\n例如:\n  `/cd ~/Documents/my-project`\n  `/cd 1`（先用 /projects 列出编号）")
            return
        target = args[1].strip()

        # If target is a number, look up from project list
        if target.isdigit() and hasattr(self, '_project_list') and self._project_list:
            idx = int(target) - 1
            if 0 <= idx < len(self._project_list):
                target_dir = self._project_list[idx]["path"]
            else:
                await self._reply(ctx.thread_id, f"❌ 编号 {target} 不存在（共 {len(self._project_list)} 个项目）\n\n请先用 /projects 查看列表")
                return
        else:
            target_dir = os.path.expanduser(target)

        if not os.path.isdir(target_dir):
            await self._reply(ctx.thread_id, f"❌ 目录不存在: `{target_dir}`")
            return
        self._opencode.set_workdir(target_dir)
        cancel_all_approvals(session)
        session.opencode_session_id = None
        self._opencode_sessions.pop(ctx.thread_id, None)
        short_path = target_dir.replace(os.path.expanduser("~"), "~")
        await self._reply(ctx.thread_id, f"✅ 已切换到: `{short_path}`\n\n💡 会话已重置，新 prompt 将在此目录下工作。")

    async def _cmd_pwd(self, ctx: MessageContext, session: Any, raw_text: str = "") -> None:
        """Show current working directory."""
        cwd = self._opencode.current_workdir()
        await self._reply(ctx.thread_id, f"📁 `{cwd}`")

    # ── Reply helper ──────────────────────────────────────────────────
    async def _reply(self, chat_id: str, text: str) -> Optional[str]:
        """Send a reply using formatText (Markdown) if content has formatting,
        otherwise plain text."""
        # Use formatText for content with Markdown indicators
        if any(marker in text for marker in ["**", "```", "•", "—", "##", "|"]):
            return await self._http_client.send_format_text(chat_id, text)
        return await self._http_client.send_text(chat_id, text)