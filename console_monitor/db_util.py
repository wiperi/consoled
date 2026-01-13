"""
数据库工具类

通用 Redis 数据库封装，提供基于 table|key 的访问接口。
上层应用指定 table 和 key 来访问数据，指定 key pattern 来订阅事件。
"""

import logging
from typing import Optional

import redis.asyncio as aioredis

from .constants import REDIS_HOST, REDIS_PORT

log = logging.getLogger(__name__)


class RedisDb:
    """
    通用 Redis 数据库封装
    
    一个实例对应一个 Redis db，提供基于 table|key 的访问接口。
    """
    
    def __init__(self, db_id: int, name: str = ""):
        """
        初始化 Redis 数据库封装
        
        Args:
            db_id: Redis 数据库编号（0-15）
            name: 数据库名称（用于日志）
        """
        self.db_id = db_id
        self.name = name or f"db{db_id}"
        self._redis: Optional[aioredis.Redis] = None
        self._pubsub: Optional[aioredis.PubSub] = None
    
    async def connect(self) -> None:
        """连接 Redis 数据库"""
        self._redis = aioredis.Redis(
            host=REDIS_HOST, port=REDIS_PORT, db=self.db_id,
            decode_responses=True
        )
        await self._redis.ping()  # type: ignore
        log.info(f"[{self.name}] Connected to Redis db={self.db_id}")
    
    async def close(self) -> None:
        """关闭 Redis 连接"""
        if self._pubsub:
            await self._pubsub.unsubscribe()
            await self._pubsub.aclose()
            self._pubsub = None
        if self._redis:
            await self._redis.aclose()
            self._redis = None
    
    # ============================================================
    # 基础 Hash 操作
    # ============================================================
    
    async def hget(self, table: str, key: str, field: str) -> Optional[str]:
        """
        获取 hash 表中指定字段的值
        
        Args:
            table: 表名（如 "CONSOLE_PORT"）
            key: 键名（如 "1"）
            field: 字段名
        
        Returns:
            字段值，不存在返回 None
        """
        if not self._redis:
            return None
        full_key = f"{table}|{key}"
        return await self._redis.hget(full_key, field)  # type: ignore
    
    async def hgetall(self, table: str, key: str) -> dict[str, str]:
        """
        获取 hash 表中所有字段
        
        Args:
            table: 表名
            key: 键名
        
        Returns:
            字段-值字典
        """
        if not self._redis:
            return {}
        full_key = f"{table}|{key}"
        return await self._redis.hgetall(full_key)  # type: ignore
    
    async def hset(self, table: str, key: str, mapping: dict[str, str]) -> None:
        """
        设置 hash 表中多个字段的值
        
        Args:
            table: 表名
            key: 键名
            mapping: 字段-值映射
        """
        if not self._redis:
            return
        full_key = f"{table}|{key}"
        await self._redis.hset(full_key, mapping=mapping)  # type: ignore
    
    async def hdel(self, table: str, key: str, *fields: str) -> None:
        """
        删除 hash 表中指定字段
        
        Args:
            table: 表名
            key: 键名
            fields: 要删除的字段名
        """
        if not self._redis:
            return
        full_key = f"{table}|{key}"
        await self._redis.hdel(full_key, *fields)  # type: ignore
    
    async def keys(self, pattern: str) -> list[str]:
        """
        获取匹配模式的所有键
        
        Args:
            pattern: 键模式（如 "CONSOLE_PORT|*"）
        
        Returns:
            匹配的键列表
        """
        if not self._redis:
            return []
        return await self._redis.keys(pattern)  # type: ignore
    
    # ============================================================
    # Pub/Sub 操作
    # ============================================================
    
    async def psubscribe(self, *patterns: str) -> None:
        """
        订阅匹配模式的键空间事件
        
        Args:
            patterns: 键模式列表（如 "CONSOLE_PORT|*"），会自动转换为 keyspace 模式
        """
        if not self._redis:
            return
        
        if not self._pubsub:
            self._pubsub = self._redis.pubsub()
        
        for pattern in patterns:
            keyspace_pattern = f"__keyspace@{self.db_id}__:{pattern}"
            await self._pubsub.psubscribe(keyspace_pattern)
            log.info(f"[{self.name}] Subscribed: {keyspace_pattern}")
    
    async def get_message(self, timeout: float = 1.0) -> Optional[dict]:
        """
        获取订阅消息（非阻塞）
        
        Args:
            timeout: 超时时间（秒）
        
        Returns:
            消息字典，无消息返回 None
        """
        if not self._pubsub:
            return None
        return await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=timeout)
