#!/usr/bin/env python3
"""
Console Proxy Service

监听 Redis 配置，为每个串口创建过滤代理。
"""

import sys
import asyncio
import signal
import logging
from typing import Optional

from db_util import DbUtil
from serial_proxy import SerialProxy
from util import get_pty_symlink_prefix

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)


# ============================================================
# 代理管理
# ============================================================

class ProxyManager:
    def __init__(self):
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.db = DbUtil()
        self.proxies: dict[str, SerialProxy] = {}
        self.running: bool = False
        self.pty_symlink_prefix: str = ""

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()

        # 连接数据库
        await self.db.connect()

        # 检查 console switch 功能是否启用
        if not await self.db.check_console_feature_enabled():
            log.error("Console switch feature is not enabled, exiting...")
            sys.exit(1)

        # 读取 PTY 符号链接前缀
        self.pty_symlink_prefix = get_pty_symlink_prefix()
        log.info(f"PTY symlink prefix: {self.pty_symlink_prefix}")

        # 初始同步
        await self.sync()

        # 订阅配置变更事件
        await self.db.subscribe_config_changes()

        self.running = True

    async def run(self) -> None:
        """主循环：监听 Redis 事件"""
        while self.running:
            msg = await self.db.get_config_event()
            if msg:
                log.info(f"Redis event: {msg.get('data')} on {msg.get('channel')}")
                await self.sync()

    async def sync(self) -> None:
        """同步 Redis 配置和实际 proxy"""
        if not self.loop:
            return

        # 获取 Redis 中的配置
        redis_configs = await self.db.get_all_configs()

        redis_ids = set(redis_configs.keys())
        current_ids = set(self.proxies.keys())

        # 删除不在 Redis 中的 proxy
        for link_id in current_ids - redis_ids:
            await self.proxies[link_id].stop()
            del self.proxies[link_id]

        # 添加新的 proxy
        for link_id in redis_ids - current_ids:
            cfg = redis_configs[link_id]
            proxy = SerialProxy(
                link_id, cfg["device"], cfg["baud"], self.loop,
                db=self.db,
                pty_symlink_prefix=self.pty_symlink_prefix
            )
            if await proxy.start():
                self.proxies[link_id] = proxy

        # 更新已存在但配置变化的 proxy
        for link_id in redis_ids & current_ids:
            cfg = redis_configs[link_id]
            proxy = self.proxies[link_id]
            if proxy.baud != cfg["baud"]:
                await proxy.stop()
                new_proxy = SerialProxy(
                    link_id, cfg["device"], cfg["baud"], self.loop,
                    db=self.db,
                    pty_symlink_prefix=self.pty_symlink_prefix
                )
                if await new_proxy.start():
                    self.proxies[link_id] = new_proxy

        log.info(f"Sync complete: {len(self.proxies)} proxies active")

    async def stop(self) -> None:
        self.running = False

        # 并发等待所有 proxy 停止
        if self.proxies:
            await asyncio.gather(
                *[proxy.stop() for proxy in self.proxies.values()],
                return_exceptions=True
            )
        
        self.proxies.clear()

        await self.db.close()

        log.info("Shutdown complete")


# ============================================================
# 主程序
# ============================================================

async def main() -> None:
    manager = ProxyManager()

    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def signal_handler():
        log.info("Received shutdown signal")
        manager.running = False
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    try:
        await manager.start()
        await manager.run()
    finally:
        await manager.stop()


if __name__ == "__main__":
    asyncio.run(main())
