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
    """List projects from OpenCode, sorted by last session activity (up to 9).

    Combines two sources:
    1. project table — registered projects with worktree
    2. session table — directories used with project_id='global' (not in project table)
    """
    db_path = _find_opencode_db()
    if not db_path:
        return []

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        rows = conn.execute(
            "SELECT directory as path, MAX(time_updated) as last_used "
            "FROM session "
            "GROUP BY directory "
            "ORDER BY last_used DESC "
            "LIMIT 9"
        ).fetchall()
        conn.close()

        results = []
        for row in rows:
            path = row["path"]
            dirname = os.path.basename(path) if path else "?"
            results.append({
                "id": "",
                "path": path,
                "name": dirname,
                "icon_color": "orange",
            })
        return results
    except Exception:
        return []


class OpenCodeClient:
    """Client for OpenCode — HTTP server mode (opencode serve) preferred, CLI fallback."""

    _WORKDIR_FILE = os.path.expanduser("~/.opencode-lansenger-remote/workdir.json")

    def __init__(self, config: Config):
        self._config = config
        self._server_url = config.opencode_server_url
        self._initialized = False
        self._verified = False
        self._opencode_path: Optional[str] = None
        self._http_available: bool = False
        self._workdir: str = self._load_persisted_workdir() or os.getcwd()
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

        self._verified = True
        self._initialized = True

    async def _probe_http(self, verbose: bool = False) -> bool:
        """Check if OpenCode server is reachable (opencode serve or TUI server)."""
        # Try the configured server URL first
        urls_to_try = [(self._server_url, self._server_auth)]
        # Also try to auto-discover desktop app server
        desktop_url, desktop_auth = self._discover_desktop_server()
        if desktop_url:
            urls_to_try.append((desktop_url, desktop_auth))

        for url, auth in urls_to_try:
            # Try with auth first, then without
            auth_options = [auth, None] if auth else [None]
            for option in auth_options:
                auth_label = f"opencode:{'***' if option else 'none'}" if option else "none"
                try:
                    client = httpx.AsyncClient(timeout=5.0, auth=option)
                    try:
                        response = await client.get(f"{url}/global/health")
                        if verbose:
                            print(f"   Probe {url} (auth={auth_label}): HTTP {response.status_code}")
                        if response.status_code == 200:
                            data = response.json()
                            if data.get("healthy"):
                                version = data.get("version", "unknown")
                                print(f"   Server version: {version}")
                                # Update server URL/auth if discovered
                                if url != self._server_url:
                                    self._server_url = url
                                    self._server_auth = option
                                    print(f"   Connected to server at {url}")
                                await client.aclose()
                                return True
                    except httpx.ConnectError as e:
                        if verbose:
                            print(f"   Probe {url} (auth={auth_label}): connection refused")
                    except httpx.TimeoutException:
                        if verbose:
                            print(f"   Probe {url} (auth={auth_label}): timeout")
                    except Exception as e:
                        if verbose:
                            print(f"   Probe {url} (auth={auth_label}): {type(e).__name__}: {e}")
                    await client.aclose()
                except Exception as e:
                    if verbose:
                        print(f"   Probe {url} (auth={auth_label}): client error: {type(e).__name__}: {e}")
        return False

    def _discover_desktop_server(self) -> tuple[Optional[str], Optional[httpx.BasicAuth]]:
        """Auto-discover OpenCode desktop app server URL and auth.

        Tries multiple approaches:
        1. Parse desktop app log files for 'server ready' URL
        2. Scan with lsof for OpenCode listening ports
        """
        import glob
        import re
        import subprocess

        # Approach 1: Parse desktop app logs for server URL
        log_dirs = [
            os.path.expanduser("~/Library/Application Support/ai.opencode.desktop/logs"),
        ]
        for log_base in log_dirs:
            try:
                if not os.path.isdir(log_base):
                    continue
                # Find most recent main.log
                log_files = sorted(glob.glob(os.path.join(log_base, "*", "main.log")), reverse=True)
                for log_path in log_files[:3]:  # Check 3 most recent
                    try:
                        with open(log_path, "r") as f:
                            content = f.read()
                        # Match: server ready { url: 'http://127.0.0.1:54496' }
                        m = re.search(r"server ready\s*\{[^}]*url:\s*'([^']+)'", content)
                        if m:
                            url = m.group(1)
                            auth = httpx.BasicAuth(
                                os.getenv("OPENCODE_SERVER_USERNAME", "opencode"),
                                os.getenv("OPENCODE_SERVER_PASSWORD", ""),
                            )
                            print(f"   Discovered server from logs: {url}")
                            return url, auth
                    except Exception:
                        continue
            except Exception:
                pass

        # Approach 2: lsof scan
        try:
            result = subprocess.run(
                ["lsof", "-iTCP", "-sTCP:LISTEN", "-P", "-n"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                if "LISTEN" in line and ("opencode" in line.lower() or "OpenCode" in line):
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

    async def reconnect(self) -> bool:
        """Re-probe OpenCode server: refresh port, auth, and availability."""
        # Re-read auth from env vars (OpenCode desktop may have restarted with new password)
        pw = os.getenv("OPENCODE_SERVER_PASSWORD", "")
        if pw:
            self._server_auth = httpx.BasicAuth(
                os.getenv("OPENCODE_SERVER_USERNAME", "opencode"), pw
            )
        self._http_available = await self._probe_http(verbose=True)
        if self._http_available:
            print(f"✅ Reconnected: {self._server_url}")
        else:
            print("❌ OpenCode server not found after reconnect")
        return self._http_available

    async def create_session(
        self,
        thread_id: str,
        title: str = "Lansenger remote session",
    ) -> Optional[dict]:
        """Create a new OpenCode session, or reuse an existing one for the workdir.

        Strategy (single GET /session call):
        1. Fetch all sessions, find those matching our workdir
        2. If a reusable non-active session exists, reuse it
        3. Otherwise create a new session with parentID from any workdir session
           so it inherits the correct directory/project context
        """
        if not self._http_available:
            return {"sessionId": thread_id, "mode": "cli"}

        workdir_sessions = await self._fetch_workdir_sessions()
        reuse = self._pick_reusable(workdir_sessions)

        if reuse:
            session_id = reuse["id"]
            print(f"✅ Reusing existing session: {session_id} (dir={reuse.get('directory')})")
            return {"sessionId": session_id, "mode": "http"}

        parent_id = workdir_sessions[0]["id"] if workdir_sessions else None
        if parent_id:
            print(f"✅ Will use parentID: {parent_id} ({len(workdir_sessions)} workdir candidates)")

        try:
            timeout_s = self._config.request_timeout_minutes * 60
            client = httpx.AsyncClient(timeout=timeout_s, auth=self._server_auth)
            body = {"title": title}
            if parent_id:
                body["parentID"] = parent_id
            response = await client.post(
                f"{self._server_url}/session",
                json=body,
            )
            await client.aclose()

            if response.status_code in (200, 201):
                data = response.json()
                session_id = data.get("id", "")
                directory = data.get("directory", "")
                print(f"✅ Created new OpenCode session: {session_id} (dir={directory}, parent={parent_id})")
                return {"sessionId": session_id, "mode": "http"}
            else:
                print(f"⚠️ HTTP session creation failed: HTTP {response.status_code}")
        except Exception as e:
            print(f"⚠️ HTTP session creation error: {e}")

        return {"sessionId": thread_id, "mode": "cli"}

    async def _fetch_workdir_sessions(self) -> list[dict]:
        """Fetch sessions matching current workdir (single API call)."""
        try:
            client = httpx.AsyncClient(timeout=10.0, auth=self._server_auth)
            response = await client.get(f"{self._server_url}/session")
            await client.aclose()
            if response.status_code != 200:
                return []

            sessions = response.json()
            workdir = self._workdir
            matching = [
                s for s in sessions
                if s.get("directory") == workdir and s.get("id")
            ]
            matching.sort(key=lambda s: s.get("time", {}).get("updated", 0), reverse=True)
            return matching
        except Exception as e:
            print(f"⚠️ Error fetching workdir sessions: {e}")
        return []

    def _pick_reusable(self, workdir_sessions: list[dict]) -> Optional[dict]:
        """Pick a session to reuse: the 2nd most recent (avoid desktop active)."""
        if len(workdir_sessions) > 1:
            return workdir_sessions[1]
        return None

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
        """Send prompt via OpenCode HTTP server API, then fetch full response.

        OpenCode returns multi-step responses: the POST returns only the first
        assistant step, but the full analysis is in subsequent messages.
        We need to: (1) send prompt, (2) wait for completion, (3) fetch all
        assistant text parts from the message list.
        """
        session_id = session.get("sessionId", "")
        timeout_s = self._config.request_timeout_minutes * 60

        try:
            client = httpx.AsyncClient(timeout=timeout_s, auth=self._server_auth)
            try:
                # Record current message count before sending
                pre_count = await self._get_message_count(client, session_id)

                # Send prompt
                response = await client.post(
                    f"{self._server_url}/session/{session_id}/message",
                    json={"parts": [{"type": "text", "text": message}]},
                    timeout=timeout_s,
                )

                if response.status_code not in (200, 201):
                    error_body = response.text[:200]
                    print(f"⚠️ HTTP message error: HTTP {response.status_code} — {error_body}")
                    await client.aclose()
                    return None

                # Wait for OpenCode to finish processing (poll message list)
                full_text = await self._poll_for_completion(client, session_id, pre_count, timeout_s)

                await client.aclose()
                return full_text or "我收到了你的消息但没有回复内容。"

            except httpx.TimeoutException:
                await client.aclose()
                return "⏱️ OpenCode 响应超时。任务可能仍在运行。"
            except Exception as e:
                print(f"⚠️ HTTP message error: {e}")
                await client.aclose()
        except Exception:
            pass

        return None

    async def join_desktop_session(self) -> Optional[dict]:
        """Find and reuse the desktop app's most active session for the workdir."""
        workdir_sessions = await self._fetch_workdir_sessions()
        if workdir_sessions:
            session = workdir_sessions[0]
            session_id = session["id"]
            print(f"✅ Joined desktop session: {session_id} (dir={session.get('directory')})")
            return {"sessionId": session_id, "mode": "http"}
        return None

    async def _get_message_count(self, client: httpx.AsyncClient, session_id: str) -> int:
        """Get current message count for a session."""
        try:
            response = await client.get(f"{self._server_url}/session/{session_id}/message")
            if response.status_code == 200:
                return len(response.json())
        except Exception:
            pass
        return 0

    async def _poll_for_completion(
        self, client: httpx.AsyncClient, session_id: str,
        pre_count: int, timeout_s: int,
    ) -> Optional[str]:
        """Poll message list until OpenCode finishes, then extract all text."""
        poll_interval = 2
        elapsed = 0

        while elapsed < timeout_s:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

            try:
                response = await client.get(f"{self._server_url}/session/{session_id}/message")
                if response.status_code != 200:
                    continue

                messages = response.json()
                if len(messages) <= pre_count:
                    continue

                # Check if the last assistant message has completed
                # (info.time.completed exists means it's done)
                new_messages = messages[pre_count:]
                last_assistant = None
                all_text_parts = []

                for msg in new_messages:
                    info = msg.get("info", {})
                    if info.get("role") != "assistant":
                        continue
                    last_assistant = msg

                    # Extract text parts from this message
                    for part in msg.get("parts", []):
                        if part.get("type") == "text" and part.get("text"):
                            all_text_parts.append(part["text"])

                if not last_assistant:
                    continue

                # Check if completed
                time_info = last_assistant.get("info", {}).get("time", {})
                if time_info.get("completed"):
                    print(f"✅ OpenCode completed after {elapsed}s, {len(all_text_parts)} text parts")
                    return "\n\n".join(all_text_parts) if all_text_parts else None

            except Exception as e:
                print(f"⚠️ Poll error: {e}")
                continue

        # Timeout — extract whatever text we have
        try:
            response = await client.get(f"{self._server_url}/session/{session_id}/message")
            if response.status_code == 200:
                messages = response.json()
                new_messages = messages[pre_count:]
                all_text_parts = []
                for msg in new_messages:
                    if msg.get("info", {}).get("role") != "assistant":
                        continue
                    for part in msg.get("parts", []):
                        if part.get("type") == "text" and part.get("text"):
                            all_text_parts.append(part["text"])
                if all_text_parts:
                    return "\n\n".join(all_text_parts)
        except Exception:
            pass

        return None

    async def _send_via_cli(self, message: str) -> str:
        """Send prompt via `opencode run` CLI (non-interactive mode).

        Uses -c flag for working directory, optional --model flag, and positional message argument.
        """
        try:
            cmd = [
                self._opencode_path or "opencode",
                "run",
                "-c", self._workdir,
            ]
            if self._config.opencode_model:
                cmd.extend(["--model", self._config.opencode_model])
            cmd.append(message)
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
        """Set working directory for OpenCode and persist it."""
        self._workdir = os.path.abspath(path)
        self._persist_workdir()

    def _load_persisted_workdir(self) -> Optional[str]:
        """Load persisted workdir from file."""
        try:
            if os.path.exists(self._WORKDIR_FILE):
                data = json.loads(open(self._WORKDIR_FILE).read())
                workdir = data.get("workdir", "")
                if workdir and os.path.isdir(workdir):
                    print(f"✅ Loaded persisted workdir: {workdir}")
                    return workdir
        except Exception:
            pass
        return None

    def _persist_workdir(self) -> None:
        """Persist current workdir to file."""
        try:
            os.makedirs(os.path.dirname(self._WORKDIR_FILE), exist_ok=True)
            with open(self._WORKDIR_FILE, "w") as f:
                json.dump({"workdir": self._workdir}, f)
            os.chmod(self._WORKDIR_FILE, 0o600)
        except Exception as e:
            print(f"⚠️ Failed to persist workdir: {e}")

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