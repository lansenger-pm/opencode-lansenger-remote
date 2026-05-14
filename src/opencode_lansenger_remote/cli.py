"""CLI entry point for opencode-lansenger-remote."""

from __future__ import annotations

import asyncio
import sys
import signal

from .core.types import load_config
from .lansenger.bot import LansengerBot


def main() -> None:
    """Main CLI entry point."""
    config = load_config()

    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  OpenCode Lansenger Remote 🌠")
    print("  通过蓝信个人机器人远程控制 OpenCode")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    if not config.lansenger_app_id or not config.lansenger_app_secret:
        print("❌ 蓝信凭证未配置！")
        print("\n请创建 ~/.opencode-lansenger-remote/.env 文件：")
        print("")
        print("  LANSENGER_APP_ID=your_app_id")
        print("  LANSENGER_APP_SECRET=your_app_secret")
        print("")
        print("或设置环境变量 LANSENGER_APP_ID 和 LANSENGER_APP_SECRET")
        sys.exit(1)

    bot = LansengerBot(config)

    # Handle graceful shutdown
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def shutdown_handler():
        print("\n🛑 Received shutdown signal...")
        loop.call_soon_threadsafe(lambda: asyncio.ensure_future(bot.stop()))

    signal.signal(signal.SIGINT, lambda *_: shutdown_handler())
    signal.signal(signal.SIGTERM, lambda *_: shutdown_handler())

    try:
        loop.run_until_complete(bot.start())
    except KeyboardInterrupt:
        loop.run_until_complete(bot.stop())
    finally:
        loop.close()


if __name__ == "__main__":
    main()