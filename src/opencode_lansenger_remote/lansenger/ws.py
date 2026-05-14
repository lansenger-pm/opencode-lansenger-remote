"""Lansenger WebSocket connection — long-connection with auto-reconnect."""

from __future__ import annotations

import asyncio
import json
from typing import Optional, Callable, Any

import websockets

from ..core.types import RECONNECT_BACKOFF
from .client import LansengerClient


class LansengerWS:
    """WebSocket client for Lansenger personal bot with auto-reconnect."""

    def __init__(
        self,
        http_client: LansengerClient,
        on_message: Callable[[dict], Any],
    ):
        self._http_client = http_client
        self._on_message_callback = on_message
        self._ws: Optional[Any] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        """Start WebSocket connection with auto-reconnect."""
        self._running = True
        ws_url = await self._http_client.get_ws_url()
        if not ws_url:
            print("[Lansenger WS] Failed to get WS URL, retrying in 5s...")
            await asyncio.sleep(5)
            ws_url = await self._http_client.get_ws_url()
            if not ws_url:
                print("[Lansenger WS] Still no WS URL. Giving up.")
                return

        self._ws_task = asyncio.create_task(self._run_ws(ws_url))
        print("[Lansenger WS] Started")

    async def stop(self) -> None:
        """Stop WebSocket connection."""
        self._running = False
        if self._ws_task:
            self._ws_task.cancel()
        print("[Lansenger WS] Stopped")

    async def _run_ws(self, ws_url: str) -> None:
        """Run WebSocket with exponential backoff reconnect."""
        backoff_idx = 0
        while self._running:
            try:
                print("[Lansenger WS] Connecting...")
                async with websockets.connect(
                    ws_url,
                    ping_interval=25,
                    ping_timeout=10,
                ) as ws:
                    self._ws = ws
                    backoff_idx = 0
                    print("[Lansenger WS] Connected ✅")

                    async for raw_message in ws:
                        await self._on_raw_message(raw_message)

            except asyncio.CancelledError:
                return
            except Exception as e:
                if not self._running:
                    return
                print(f"[Lansenger WS] Error: {e}")

            if not self._running:
                return

            delay = RECONNECT_BACKOFF[min(backoff_idx, len(RECONNECT_BACKOFF) - 1)]
            print(f"[Lansenger WS] Reconnecting in {delay}s...")
            await asyncio.sleep(delay)
            backoff_idx += 1

            # Refresh WS URL on reconnect
            new_url = await self._http_client.get_ws_url()
            if new_url:
                ws_url = new_url

    async def _on_raw_message(self, raw_message: str) -> None:
        """Parse and dispatch incoming WS message."""
        try:
            data = json.loads(raw_message)
        except json.JSONDecodeError:
            print("[Lansenger WS] Invalid JSON message")
            return

        events = data.get("events", [])
        for event_data in events:
            await self._on_message_callback(event_data)