#!/usr/bin/env python3
"""
Console Proxy Service

监听 Redis 配置，为每个串口创建过滤代理。
"""

import os
import time
import argparse
import asyncio
import signal
import logging
from typing import Optional

from .db_util import RedisDb
from .serial_proxy import SerialProxy
from .util import get_pty_symlink_prefix
from .constants import REDIS_DB, STATE_DB

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)


# DCE 专用常量
CONSOLE_PORT_TABLE = "CONSOLE_PORT"
CONSOLE_SWITCH_TABLE = "CONSOLE_SWITCH"
CONSOLE_PORT_PATTERN = "CONSOLE_PORT|*"
CONSOLE_SWITCH_PATTERN = "CONSOLE_SWITCH|*"


# ============================================================
# DCE 数据库操作封装
# ============================================================

class DceDbHelper:
    """DCE 专用数据库操作封装"""
    
    def __init__(self):
        self.config_db = RedisDb(REDIS_DB, "config_db")
        self.state_db = RedisDb(STATE_DB, "state_db")
    
    async def connect(self) -> None:
        """连接数据库"""
        await self.config_db.connect()
        await self.state_db.connect()
    
    async def close(self) -> None:
        """关闭数据库连接"""
        await self.config_db.close()
        await self.state_db.close()
    
    async def check_console_feature_enabled(self) -> bool:
        """检查 console switch 功能是否启用"""
        try:
            enabled = await self.config_db.hget(CONSOLE_SWITCH_TABLE, "console_mgmt", "enabled")
            if enabled == "yes":
                log.info("Console switch feature is enabled")
                return True
            else:
                log.warning(f"Console switch feature is disabled (enabled={enabled})")
                return False
        except Exception as e:
            log.error(f"Failed to check console switch feature status: {e}")
            return False
    
    async def subscribe_config_changes(self) -> None:
        """订阅配置变更事件（包括 CONSOLE_PORT 和 CONSOLE_SWITCH）"""
        await self.config_db.psubscribe(CONSOLE_PORT_PATTERN, CONSOLE_SWITCH_PATTERN)
    
    async def get_config_event(self) -> Optional[dict]:
        """获取配置变更事件（非阻塞）"""
        return await self.config_db.get_message()
    
    async def get_all_configs(self) -> dict[str, dict]:
        """获取所有串口配置"""
        keys = await self.config_db.keys(CONSOLE_PORT_PATTERN)
        configs: dict[str, dict] = {}
        
        for key in keys:
            link_id = key.split("|", 1)[-1]
            data = await self.config_db.hgetall(CONSOLE_PORT_TABLE, link_id)
            if data:
                configs[link_id] = {
                    "baud": int(data.get("baud_rate", 9600)),
                    "device": f"/dev/C0-{link_id}",
                }
        return configs
    
    async def update_state(self, link_id: str, oper_state: str) -> None:
        """更新串口状态（状态变化时更新 last_state_change）"""
        try:
            timestamp = int(time.time())
            await self.state_db.hset(
                CONSOLE_PORT_TABLE,
                link_id,
                {
                    "oper_state": oper_state,
                    "last_state_change": str(timestamp),
                }
            )
            log.info(f"[{link_id}] State: {oper_state}, state_change: {timestamp}")
        except Exception as e:
            log.error(f"[{link_id}] Failed to update state: {e}")
    
    async def cleanup_state(self, link_id: str) -> None:
        """清理 STATE_DB 状态"""
        try:
            # 只删除 console-monitor 管理的字段，保留 consutil 的字段
            await self.state_db.hdel(CONSOLE_PORT_TABLE, link_id, "oper_state", "last_state_change")
            log.info(f"[{link_id}] STATE_DB cleaned up (oper_state, last_state_change)")
        except Exception as e:
            log.error(f"[{link_id}] Failed to cleanup STATE_DB: {e}")


# ============================================================
# 代理管理
# ============================================================

class ProxyManager:
    def __init__(self):
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.db = DceDbHelper()
        self.proxies: dict[str, SerialProxy] = {}
        self.running: bool = False
        self.pty_symlink_prefix: str = ""

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()

        # 连接数据库
        await self.db.connect()

        # 读取 PTY 符号链接前缀
        self.pty_symlink_prefix = get_pty_symlink_prefix()
        log.info(f"PTY symlink prefix: {self.pty_symlink_prefix}")

        # 初始同步（会检查 console_feature_enabled）
        await self.sync()

        # 订阅配置变更事件（包括 CONSOLE_SWITCH 和 CONSOLE_PORT）
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

        # 检查 console switch 功能是否启用
        feature_enabled = await self.db.check_console_feature_enabled()
        
        if not feature_enabled:
            # 功能未启用，停止所有现有的 proxy
            if self.proxies:
                log.info("Console switch feature disabled, stopping all proxies...")
                await asyncio.gather(
                    *[proxy.stop() for proxy in self.proxies.values()],
                    return_exceptions=True
                )
                self.proxies.clear()
                log.info("All proxies stopped due to feature disabled")
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


def run():
    """Entry point for the console-monitor daemon"""
    parser = argparse.ArgumentParser(description='Console Monitor DCE Service')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Enable verbose output (binary data logging)')
    args = parser.parse_args()
    
    # 如果启用verbose，设置环境变量
    if args.verbose:
        os.environ['CONSOLE_MONITOR_VERBOSE'] = 'True'
    
    asyncio.run(main())


if __name__ == "__main__":
    run()
