"""
数据库工具类

封装 Redis 数据库操作，包括配置读取、状态更新、事件订阅等。
"""

import time
import logging
from typing import Optional

import redis.asyncio as aioredis

from .constants import REDIS_HOST, REDIS_PORT, REDIS_DB, STATE_DB, KEY_PATTERN

log = logging.getLogger(__name__)


class DbUtil:
    """Redis 数据库操作封装"""
    
    def __init__(self):
        self.config_db: Optional[aioredis.Redis] = None
        self.state_db: Optional[aioredis.Redis] = None
        self.pubsub: Optional[aioredis.PubSub] = None
    
    async def connect(self) -> None:
        """连接 Redis 数据库"""
        self.config_db = aioredis.Redis(
            host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB,
            decode_responses=True
        )
        await self.config_db.ping()  # type: ignore
        log.info(f"Connected to Redis config db={REDIS_DB}")
        
        self.state_db = aioredis.Redis(
            host=REDIS_HOST, port=REDIS_PORT, db=STATE_DB,
            decode_responses=True
        )
        await self.state_db.ping()  # type: ignore
        log.info(f"Connected to Redis state db={STATE_DB}")
    
    async def close(self) -> None:
        """关闭 Redis 连接"""
        if self.pubsub:
            await self.pubsub.unsubscribe()
            await self.pubsub.aclose()
        if self.config_db:
            await self.config_db.aclose()
        if self.state_db:
            await self.state_db.aclose()
    
    async def check_console_feature_enabled(self) -> bool:
        """检查 console switch 功能是否启用"""
        if not self.config_db:
            return False
        
        try:
            enabled = await self.config_db.hget("CONSOLE_SWITCH|console_mgmt", "enabled")  # type: ignore
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
        """订阅配置变更事件"""
        if not self.config_db:
            return
        self.pubsub = self.config_db.pubsub()
        pattern = f"__keyspace@{REDIS_DB}__:{KEY_PATTERN}"
        await self.pubsub.psubscribe(pattern)
        log.info(f"Subscribed: {pattern}")
    
    async def get_config_event(self) -> Optional[dict]:
        """获取配置变更事件（非阻塞）"""
        if not self.pubsub:
            return None
        return await self.pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
    
    async def get_all_configs(self) -> dict[str, dict]:
        """获取所有串口配置"""
        if not self.config_db:
            return {}
        
        keys: list[str] = await self.config_db.keys(KEY_PATTERN)
        configs: dict[str, dict] = {}
        
        for key in keys:
            link_id = key.split("|", 1)[-1]
            data = await self.config_db.hgetall(key)  # type: ignore
            if data:
                configs[link_id] = {
                    "baud": int(data.get("baud_rate", 9600)),
                    "device": f"/dev/C0-{link_id}",
                }
        return configs
    
    async def update_state(self, link_id: str, oper_state: str) -> None:
        """更新串口状态（只有 up 时更新 heartbeat）"""
        if not self.state_db:
            return
        
        key = f"CONSOLE_PORT|{link_id}"
        
        try:
            if oper_state == "up":
                timestamp = int(time.time())
                await self.state_db.hset(  # type: ignore
                    key,
                    mapping={
                        "oper_state": oper_state,
                        "last_heartbeat": str(timestamp),
                    }
                )
                log.info(f"[{link_id}] State: {oper_state}, heartbeat: {timestamp}")
            else:
                await self.state_db.hset(key, "oper_state", oper_state)  # type: ignore
                log.info(f"[{link_id}] State: {oper_state}")
        except Exception as e:
            log.error(f"[{link_id}] Failed to update state: {e}")
    
    async def cleanup_state(self, link_id: str) -> None:
        """清理 STATE_DB 状态"""
        if not self.state_db:
            return
        
        key = f"CONSOLE_PORT|{link_id}"
        
        try:
            # 只删除 console-monitor 管理的字段，保留 consutil 的字段
            await self.state_db.hdel(key, "oper_state", "last_heartbeat")  # type: ignore
            log.info(f"[{link_id}] STATE_DB cleaned up (oper_state, last_heartbeat)")
        except Exception as e:
            log.error(f"[{link_id}] Failed to cleanup STATE_DB: {e}")
