"""OpenCode SDK client — interact via HTTP server or CLI subprocess."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
from typing import Optional, Any, List

import httpx

from ..core.types import Config


def _find_opencode_db() -> Optional[str]:
    """Find OpenCode database path."""
    candidates = [
        os.path.expanduser("~/.local/share/opencode/opencode.db"),
        os.path.expanduser("~/Library/Application Support/ai.opencode.desktop/opencode.db"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def list_opencode_projects() -> List[dict]:
    """List projects from OpenCode database, sorted by last used."""
    db_path = _find_opencode_db()
    if not db_path:
        return []

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, worktree, name, icon_color, time_updated FROM project "
            "WHERE id != 'global' ORDER BY time_updated DESC"
        ).fetchall()
        conn.close()

        results = []
        for row in rows:
            worktree = row["worktree"]
            dirname = os.path.basename(worktree) if worktree else "?"
            name = row["name"] or dirname
            results.append({
                "id": row["id"],
                "path": worktree,
                "name": name,
                "icon_color": row["icon_color"] or "orange",
            })
        return results
    except Exception:
        return []


class OpenCodeClient:
    """Client for OpenCode — HTTP server mode (opencode serve) preferred, CLI fallback."""

    def __init__(self, config: Config):
        self._config = config
        self._server_url = config.opencode_server_url
        self._initialized = False
        self._verified = False
        self._opencode_path: Optional[str] = None
        self._http_available: bool = False
        self._workdir: str = os.getcwd()
        self._server_auth: Optional[httpx.BasicAuth] = None
        # Pre-configure auth from env vars if available
        pw = os.getenv("OPENCODE_SERVER_PASSWORD", "")
        if pw:
            self._server_auth = httpx.BasicAuth(
                os.getenv("OPENCODE_SERVER_USERNAME", "opencode"), pw
            )

    async def init(self) -> None:
        """Verify OpenCode installation and probe HTTP server availability."""
        self._opencode_path = shutil.which("opencode")
        if not self._opencode_path:
            raise RuntimeError(
                "OpenCode not found in PATH. Install it first:\n"
                "  npm install -g @opencode-ai/opencode\n\n"
                "Then verify:\n  opencode --version"
            )

        try:
            result = subprocess.run(
                [self._opencode_path, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                print(f"✅ OpenCode found: {result.stdout.strip()}")
            else:
                print(f"⚠️ OpenCode version check failed: {result.stderr}")
        except Exception as e:
            print(f"⚠️ Could not check OpenCode version: {e}")

        self._http_available = await self._probe_http()
        if self._http_available:
            print(f"✅ OpenCode server detected at {self._server_url}")
        else:
            print("ℹ️ OpenCode server not running — will use CLI mode")
            print("   Tip: run `opencode serve` for better performance (persistent sessions)")

        self._verified = True
        self._initialized = True

    async def _probe_http(self) -> bool:
        """Check if OpenCode server is reachable (opencode serve or TUI server)."""
        # Try the configured server URL first
        urls_to_try = [(self._server_url, self._server_auth)]
        # Also try to auto-discover desktop app server
        desktop_url, desktop_auth = self._discover_desktop_server()
        if desktop_url:
            urls_to_try.append((desktop_url, desktop_auth))

        for url, auth in urls_to_try:
            try:
                client = httpx.AsyncClient(timeout=5.0, auth=auth)
                try:
                    response = await client.get(f"{url}/global/health")
                    if response.status_code == 200:
                        data = response.json()
                        if data.get("healthy"):
                            version = data.get("version", "unknown")
                            print(f"   Server version: {version}")
                            # Update server URL/auth if discovered
                            if url != self._server_url:
                                self._server_url = url
                                self._server_auth = auth
                                print(f"   Discovered server at {url}")
                            await client.aclose()
                            return True
                except Exception:
                    pass
                await client.aclose()
            except Exception:
                pass
        return False

    def _discover_desktop_server(self) -> tuple[Optional[str], Optional[httpx.BasicAuth]]:
        """Auto-discover OpenCode desktop app server by scanning known port patterns."""
        import subprocess
        try:
            result = subprocess.run(
                ["lsof", "-iTCP", "-sTCP:LISTEN", "-P", "-n"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                if "OpenCode" in line and "LISTEN" in line:
                    # Extract port from line like: ... TCP 127.0.0.1:61864 (LISTEN)
                    parts = line.split(":")
                    if len(parts) >= 2:
                        port_str = parts[-1].split()[0]
                        try:
                            port = int(port_str)
                            url = f"http://localhost:{port}"
                            auth = httpx.BasicAuth(
                                os.getenv("OPENCODE_SERVER_USERNAME", "opencode"),
                                os.getenv("OPENCODE_SERVER_PASSWORD", ""),
                            )
                            return url, auth
                        except ValueError:
                            pass
        except Exception:
            pass
        return None, None

    async def check_connection(self) -> bool:
        """Check if OpenCode is available."""
        if self._http_available:
            return await self._probe_http()
        return bool(self._opencode_path)

    async def create_session(
        self,
        thread_id: str,
        title: str = "Lansenger remote session",
    ) -> Optional[dict]:
        """Create an OpenCode session. HTTP mode creates a real session; CLI mode is placeholder."""
        if self._http_available:
            try:
                timeout_s = self._config.request_timeout_minutes * 60
                client = httpx.AsyncClient(timeout=timeout_s, auth=self._server_auth)
                response = await client.post(
                    f"{self._server_url}/session",
                    json={"title": title},
                )
                await client.aclose()

                if response.status_code in (200, 201):
                    data = response.json()
                    session_id = data.get("id", "")
                    print(f"✅ Created OpenCode session: {session_id}")
                    return {"sessionId": session_id, "mode": "http"}
                else:
                    print(f"⚠️ HTTP session creation failed: HTTP {response.status_code}")
            except Exception as e:
                print(f"⚠️ HTTP session creation error: {e}")

        return {"sessionId": thread_id, "mode": "cli"}

    async def send_message(
        self,
        session: dict,
        message: str,
    ) -> str:
        """Send a prompt to OpenCode and return the response text."""
        mode = session.get("mode", "cli")

        if mode == "http" and self._http_available:
            result = await self._send_via_http(session, message)
            if result is not None:
                return result

        return await self._send_via_cli(message)

    async def _send_via_http(self, session: dict, message: str) -> Optional[str]:
        """Send prompt via OpenCode HTTP server API.

        Uses POST /session/:id/message with parts format per OpenCode SDK spec.
        Response format: { info: Message, parts: Part[] }
        """
        session_id = session.get("sessionId", "")
        timeout_s = self._config.request_timeout_minutes * 60

        try:
            client = httpx.AsyncClient(timeout=timeout_s, auth=self._server_auth)
            try:
                response = await client.post(
                    f"{self._server_url}/session/{session_id}/message",
                    json={
                        "parts": [{"type": "text", "text": message}],
                    },
                    timeout=timeout_s,
                )

                if response.status_code in (200, 201):
                    data = response.json()

                    # Extract text from parts array
                    parts = data.get("parts", [])
                    text_parts = [
                        p.get("text", "") for p in parts
                        if p.get("type") == "text" and p.get("text")
                    ]

                    # Also check info.content as fallback
                    info_text = data.get("info", {}).get("content", "")

                    response_text = "\n".join(text_parts) if text_parts else info_text

                    await client.aclose()
                    return response_text or "我收到了你的消息但没有回复内容。"

                error_body = response.text[:200]
                print(f"⚠️ HTTP message error: HTTP {response.status_code} — {error_body}")
                await client.aclose()
            except httpx.TimeoutException:
                await client.aclose()
                return "⏱️ OpenCode 响应超时。任务可能仍在运行。"
            except Exception as e:
                print(f"⚠️ HTTP message error: {e}")
                await client.aclose()
        except Exception:
            pass

        return None

    async def _send_via_cli(self, message: str) -> str:
        """Send prompt via `opencode run` CLI (non-interactive mode).

        Uses -c flag for working directory and positional message argument.
        """
        try:
            cmd = [
                self._opencode_path or "opencode",
                "run",
                "-c", self._workdir,
                message,
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            timeout_s = self._config.request_timeout_minutes * 60
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
            except asyncio.TimeoutError:
                proc.kill()
                return "⏱️ OpenCode 响应超时。"

            if proc.returncode == 0:
                output = stdout.decode("utf-8", errors="replace").strip()
                return output or "✅ OpenCode 执行完成（无文本输出）"
            else:
                error_msg = stderr.decode("utf-8", errors="replace").strip()[:200]
                return f"❌ OpenCode 错误: {error_msg}"

        except FileNotFoundError:
            return "❌ OpenCode 未安装。请先运行: npm install -g opencode-ai"
        except Exception as e:
            return f"❌ 执行 OpenCode 时出错: {e}"

    def current_workdir(self) -> str:
        """Return current working directory for OpenCode."""
        return self._workdir

    def set_workdir(self, path: str) -> None:
        """Set working directory for OpenCode CLI mode."""
        self._workdir = os.path.abspath(path)

    async def get_project_info(self) -> Optional[dict]:
        """Get project info from HTTP server (if available)."""
        if not self._http_available:
            return None
        try:
            client = httpx.AsyncClient(timeout=10.0, auth=self._server_auth)
            response = await client.get(f"{self._server_url}/project/current")
            await client.aclose()
            if response.status_code == 200:
                return response.json()
        except Exception:
            pass
        return None

    async def close(self) -> None:
        """Cleanup."""
        self._initialized = False