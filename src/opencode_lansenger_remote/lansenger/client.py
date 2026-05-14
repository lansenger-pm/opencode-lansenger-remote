"""Lansenger HTTP API client — token management, sending messages, media upload."""

from __future__ import annotations

import json
import os
import stat
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

import httpx

from ..core.types import Config, API_ENDPOINTS, DEFAULT_API_GATEWAY_URL


class LansengerClient:
    """HTTP client for Lansenger personal bot API."""

    def __init__(self, config: Config):
        self._app_id = config.lansenger_app_id
        self._app_secret = config.lansenger_app_secret
        self._api_gateway_url = config.lansenger_api_gateway_url

        self._http_client: Optional[httpx.AsyncClient] = None

        # Token cache (3-level: memory → file → HTTP)
        self._app_token: Optional[str] = None
        self._token_expiry: float = 0
        self._token_file = Path.home() / ".opencode-lansenger-remote" / "lansenger_token.json"

        # Message dedup
        self._seen_messages: set[str] = set()

    async def _ensure_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client

    # ── Token management (3-level cache) ──────────────────────────────
    async def get_app_token(self) -> Optional[str]:
        """Get app access token with 3-level cache: memory → file → HTTP."""
        # Level 1: memory cache
        if self._app_token and datetime.now().timestamp() < self._token_expiry:
            return self._app_token

        # Level 2: persisted file cache
        persisted = self._load_persisted_token()
        if persisted and datetime.now().timestamp() < persisted["expires_at"]:
            self._app_token = persisted["app_token"]
            self._token_expiry = persisted["expires_at"]
            print(f"[Lansenger] Loaded persisted token (expires in {int(persisted['expires_at'] - datetime.now().timestamp())}s)")
            return self._app_token

        # Level 3: HTTP request
        client = await self._ensure_http_client()
        try:
            url = f"{self._api_gateway_url}{API_ENDPOINTS['auth']['app_token']}"
            params = {
                "grant_type": "client_credential",
                "appid": self._app_id,
                "secret": self._app_secret,
            }
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            if data.get("errCode") != 0:
                print(f"[Lansenger] Token error: {data.get('errMsg')}")
                return None

            self._app_token = data.get("data", {}).get("appToken")
            expires_in = data.get("data", {}).get("expiresIn", 7200)
            self._token_expiry = datetime.now().timestamp() + expires_in - 300
            self._persist_token(self._app_token, self._token_expiry + 300)
            print(f"[Lansenger] Got new token (expires in {expires_in}s)")
            return self._app_token
        except Exception as e:
            print(f"[Lansenger] Error getting token: {e}")
            return None

    def _load_persisted_token(self) -> Optional[dict]:
        try:
            if not self._token_file.exists():
                return None
            data = json.loads(self._token_file.read_text())
            if "app_token" in data and "expires_at" in data:
                return data
        except Exception:
            pass
        return None

    def _persist_token(self, app_token: str, expires_at: float) -> None:
        try:
            self._token_file.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(self._token_file.parent, stat.S_IRWXU)
            self._token_file.write_text(json.dumps({
                "app_token": app_token,
                "expires_at": expires_at,
            }, indent=2))
            os.chmod(self._token_file, stat.S_IRUSR | stat.S_IWUSR)
        except Exception as e:
            print(f"[Lansenger] Failed to persist token: {e}")

    # ── WS endpoint ───────────────────────────────────────────────────
    async def get_ws_url(self) -> Optional[str]:
        """Get WebSocket URL from Lansenger API."""
        client = await self._ensure_http_client()
        try:
            url = f"{self._api_gateway_url}{API_ENDPOINTS['websocket']['endpoint']}"
            response = await client.post(
                url,
                json={"appId": self._app_id, "secret": self._app_secret},
            )
            response.raise_for_status()
            data = response.json()

            if data.get("errCode") == 0:
                ws_url = data.get("data", {}).get("wsEndpoint")
                print(f"[Lansenger] Got WS URL: {ws_url[:50] if ws_url else None}")
                return ws_url
            else:
                print(f"[Lansenger] WS endpoint error: {data.get('errMsg')}")
                return None
        except Exception as e:
            print(f"[Lansenger] Error getting WS URL: {e}")
            return None

    # ── Send messages ─────────────────────────────────────────────────
    async def send_text(self, chat_id: str, content: str) -> Optional[str]:
        """Send plain text message to a user (DM)."""
        token = await self.get_app_token()
        if not token:
            print("[Lansenger] send_text: no token available")
            return None

        client = await self._ensure_http_client()
        try:
            url = f"{self._api_gateway_url}{API_ENDPOINTS['smart_bot']['private_message']}?app_token={token}"
            payload = {
                "userIdList": [chat_id],
                "msgType": "text",
                "msgData": {"text": {"content": content}},
            }
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()

            if data.get("errCode") != 0:
                print(f"[Lansenger] send_text error: errCode={data.get('errCode')}, errMsg={data.get('errMsg')}")
                return None

            msg_id = data.get("data", {}).get("msgId")
            print(f"[Lansenger] send_text OK: chat_id={chat_id}, msgId={msg_id}")
            return msg_id
        except httpx.HTTPStatusError as e:
            print(f"[Lansenger] send_text HTTP error: {e.response.status_code} — {e.response.text[:200]}")
            return None
        except Exception as e:
            print(f"[Lansenger] send_text error: {type(e).__name__}: {e}")
            return None

    async def send_format_text(self, chat_id: str, content: str) -> Optional[str]:
        """Send Markdown-formatted message to a user (DM)."""
        token = await self.get_app_token()
        if not token:
            print("[Lansenger] send_format_text: no token available")
            return None

        client = await self._ensure_http_client()
        try:
            url = f"{self._api_gateway_url}{API_ENDPOINTS['smart_bot']['private_message']}?app_token={token}"
            payload = {
                "userIdList": [chat_id],
                "msgType": "formatText",
                "msgData": {"formatText": {"formatType": 1, "text": content}},
            }
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()

            if data.get("errCode") != 0:
                print(f"[Lansenger] send_format_text error: errCode={data.get('errCode')}, errMsg={data.get('errMsg')}")
                return None

            msg_id = data.get("data", {}).get("msgId")
            print(f"[Lansenger] send_format_text OK: chat_id={chat_id}, msgId={msg_id}")
            return msg_id
        except httpx.HTTPStatusError as e:
            print(f"[Lansenger] send_format_text HTTP error: {e.response.status_code} — {e.response.text[:200]}")
            return None
        except Exception as e:
            print(f"[Lansenger] send_format_text error: {type(e).__name__}: {e}")
            return None

    # ── Typing indicator ──────────────────────────────────────────────
    async def send_typing(self, chat_id: str) -> None:
        """Send a brief 'thinking' message as typing indicator."""
        await self.send_text(chat_id, "⏳ 思考中... 🤔")

    # ── Send file ────────────────────────────────────────────────────
    async def send_file(
        self, chat_id: str, file_path: str,
        content: str = "", media_type: int = 3,
    ) -> Optional[str]:
        """Upload a file and send it as an attachment to a user (DM).

        media_type: 1=video, 2=image, 3=file/document.
        """
        media_id = await self.upload_media(file_path, media_type)
        if not media_id:
            print(f"[Lansenger] send_file: upload failed for {file_path}")
            return None

        token = await self.get_app_token()
        if not token:
            return None

        filename = os.path.basename(file_path)
        filesize = os.path.getsize(file_path)
        caption = content or filename

        client = await self._ensure_http_client()
        try:
            url = f"{self._api_gateway_url}{API_ENDPOINTS['smart_bot']['private_message']}?app_token={token}"
            payload = {
                "userIdList": [chat_id],
                "msgType": "text",
                "msgData": {
                    "text": {
                        "content": caption,
                        "attachmentList": [{
                            "mediaId": media_id,
                            "fileName": filename,
                            "fileSize": filesize,
                        }],
                    },
                },
            }
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()

            if data.get("errCode") != 0:
                print(f"[Lansenger] send_file error: errCode={data.get('errCode')}, errMsg={data.get('errMsg')}")
                return None

            msg_id = data.get("data", {}).get("msgId")
            print(f"[Lansenger] send_file OK: {filename} → {msg_id}")
            return msg_id
        except httpx.HTTPStatusError as e:
            print(f"[Lansenger] send_file HTTP error: {e.response.status_code} — {e.response.text[:200]}")
            return None
        except Exception as e:
            print(f"[Lansenger] send_file error: {type(e).__name__}: {e}")
            return None

    # ── Send appArticles ─────────────────────────────────────────────
    async def send_app_articles(
        self, chat_id: str, title: str, content: str,
        author: str = "OpenCode", cover_url: str = "",
    ) -> Optional[str]:
        """Send appArticles (rich text card) to a user (DM).

        appArticles is supported in bot private chat (4.6.12) and
        displays as a clickable card with title + content preview.
        """
        token = await self.get_app_token()
        if not token:
            return None

        client = await self._ensure_http_client()
        try:
            url = f"{self._api_gateway_url}{API_ENDPOINTS['smart_bot']['private_message']}?app_token={token}"
            payload = {
                "userIdList": [chat_id],
                "msgType": "appArticles",
                "msgData": {
                    "appArticles": {
                        "title": title,
                        "author": author,
                        "content": content,
                        "coverUrl": cover_url,
                    },
                },
            }
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()

            if data.get("errCode") != 0:
                print(f"[Lansenger] send_app_articles error: errCode={data.get('errCode')}, errMsg={data.get('errMsg')}")
                return None

            msg_id = data.get("data", {}).get("msgId")
            print(f"[Lansenger] send_app_articles OK: {title} → {msg_id}")
            return msg_id
        except httpx.HTTPStatusError as e:
            print(f"[Lansenger] send_app_articles HTTP error: {e.response.status_code} — {e.response.text[:200]}")
            return None
        except Exception as e:
            print(f"[Lansenger] send_app_articles error: {type(e).__name__}: {e}")
            return None

    # ── Media upload ──────────────────────────────────────────────────
    async def upload_media(self, file_path: str, media_type: int = 3) -> Optional[str]:
        """Upload a file to Lansenger. Returns mediaId or None.
        media_type: 1=video, 2=image, 3=file/document.
        """
        token = await self.get_app_token()
        if not token:
            return None

        client = await self._ensure_http_client()
        try:
            url = f"{self._api_gateway_url}{API_ENDPOINTS['medias']['upload']}?type={media_type}&app_token={token}"

            with open(file_path, "rb") as f:
                file_content = f.read()
            filename = os.path.basename(file_path)

            files = {"media": (filename, file_content)}
            response = await client.post(url, files=files)
            response.raise_for_status()
            data = response.json()

            if data.get("errCode") != 0:
                print(f"[Lansenger] Upload error: {data.get('errMsg')}")
                return None

            media_id = data.get("data", {}).get("mediaId")
            print(f"[Lansenger] Media uploaded: {filename} → {media_id}")
            return media_id
        except Exception as e:
            print(f"[Lansenger] Error uploading media: {e}")
            return None

    # ── Cleanup ───────────────────────────────────────────────────────
    async def close(self) -> None:
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None