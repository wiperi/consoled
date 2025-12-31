#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Async Multi-Serial Proxy with Redis Integration.

功能：
- 使用 asyncio 单线程事件循环
- 监听 Redis keyspace 事件，动态增删串口通道
- 每个串口独立的字符串过滤器
- 支持热插拔：运行时添加/删除串口

架构：
    ┌─────────────────────────────────────────────┐
    │            asyncio event loop               │
    │                                             │
    │  ┌─────────┐ ┌─────────┐ ┌───────────────┐ │
    │  │Serial 1 │ │Serial 2 │ │ Redis Pub/Sub │ │
    │  │ (async) │ │ (async) │ │  (keyspace)   │ │
    │  └────┬────┘ └────┬────┘ └───────┬───────┘ │
    │       │           │              │          │
    │       └───────────┴──────────────┘          │
    │                   │                         │
    │           ChannelManager                    │
    │        (动态增删 channels)                  │
    └─────────────────────────────────────────────┘
"""

import os
import sys
import asyncio
import signal
import json
import termios
import tty
import fcntl
import time
import argparse
from dataclasses import dataclass, field
from typing import Dict, Optional, Set
import logging

# Redis async client
try:
    import redis.asyncio as aioredis
except ImportError:
    print("Please install redis: pip install redis", file=sys.stderr)
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# ============================================================
# 串口配置
# ============================================================

BAUD_MAP = {
    1200: termios.B1200,
    2400: termios.B2400,
    4800: termios.B4800,
    9600: termios.B9600,
    19200: termios.B19200,
    38400: termios.B38400,
    57600: termios.B57600,
    115200: termios.B115200,
}


def set_nonblocking(fd: int) -> None:
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)


def set_raw_noecho(fd: int) -> None:
    tty.setraw(fd, when=termios.TCSANOW)
    attrs = termios.tcgetattr(fd)
    attrs[3] &= ~(termios.ECHO | termios.ECHONL)
    termios.tcsetattr(fd, termios.TCSANOW, attrs)


def configure_serial(fd: int, baud: int) -> None:
    attrs = termios.tcgetattr(fd)
    attrs[0] &= ~(termios.IGNBRK | termios.BRKINT | termios.PARMRK |
                  termios.ISTRIP | termios.INLCR | termios.IGNCR |
                  termios.ICRNL | termios.IXON)
    attrs[1] &= ~termios.OPOST
    attrs[2] &= ~(termios.CSIZE | termios.PARENB)
    attrs[2] |= (termios.CS8 | termios.CREAD | termios.CLOCAL)
    attrs[3] &= ~(termios.ECHO | termios.ECHONL | termios.ICANON |
                  termios.ISIG | termios.IEXTEN)
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 0

    if baud not in BAUD_MAP:
        raise ValueError(f"Unsupported baud {baud}")

    speed = BAUD_MAP[baud]
    attrs[4] = speed
    attrs[5] = speed
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    termios.tcflush(fd, termios.TCIOFLUSH)


# ============================================================
# 字符串过滤器 (KMP)
# ============================================================

class StringFilter:
    """高效字符串过滤器，使用 KMP 状态机。"""
    
    def __init__(self, pattern: bytes, timeout: float = 0.1):
        if not pattern:
            # 空模式：不过滤任何内容
            pattern = b"\xff\xff\xff\xff"  # 不可能匹配
        self.pattern = pattern
        self.pattern_len = len(pattern)
        self.timeout = timeout
        self.failure = self._compute_failure(pattern)
        self.match_pos = 0
        self.buffer = bytearray()
        self.last_data_time: float = 0.0
    
    @staticmethod
    def _compute_failure(pattern: bytes) -> list:
        n = len(pattern)
        failure = [0] * n
        j = 0
        for i in range(1, n):
            while j > 0 and pattern[i] != pattern[j]:
                j = failure[j - 1]
            if pattern[i] == pattern[j]:
                j += 1
            failure[i] = j
        return failure
    
    def process(self, data: bytes) -> bytes:
        output = bytearray()
        self.last_data_time = time.monotonic()
        
        for byte in data:
            while self.match_pos > 0 and byte != self.pattern[self.match_pos]:
                fail_len = self.match_pos - self.failure[self.match_pos - 1]
                output.extend(self.buffer[:fail_len])
                self.buffer = self.buffer[fail_len:]
                self.match_pos = self.failure[self.match_pos - 1]
            
            if byte == self.pattern[self.match_pos]:
                self.buffer.append(byte)
                self.match_pos += 1
                if self.match_pos == self.pattern_len:
                    self.buffer.clear()
                    self.match_pos = 0
            else:
                output.append(byte)
        
        return bytes(output)
    
    def check_timeout(self) -> bytes:
        if self.buffer and self.last_data_time > 0:
            if time.monotonic() - self.last_data_time >= self.timeout:
                result = bytes(self.buffer)
                self.buffer.clear()
                self.match_pos = 0
                self.last_data_time = 0.0
                return result
        return b""
    
    def flush(self) -> bytes:
        result = bytes(self.buffer)
        self.buffer.clear()
        self.match_pos = 0
        return result


# ============================================================
# 异步串口通道
# ============================================================

@dataclass
class ChannelConfig:
    """通道配置（从 Redis 读取）"""
    name: str
    device: str
    baud: int = 9600
    filter_pattern: str = ""
    filter_timeout: float = 0.1
    enabled: bool = True


class AsyncSerialChannel:
    """
    异步串口通道。
    
    使用 asyncio 的 add_reader/add_writer 将 fd 集成到事件循环。
    """
    
    def __init__(self, config: ChannelConfig, loop: asyncio.AbstractEventLoop):
        self.config = config
        self.loop = loop
        self.name = config.name
        
        self.ser_fd: int = -1
        self.pty_master: int = -1
        self.pty_slave: int = -1
        self.pty_slave_name: str = ""
        
        self.string_filter: Optional[StringFilter] = None
        self.running: bool = False
        self._timeout_handle: Optional[asyncio.TimerHandle] = None
    
    async def start(self) -> bool:
        """启动通道。"""
        try:
            # 创建 PTY
            self.pty_master, self.pty_slave = os.openpty()
            self.pty_slave_name = os.ttyname(self.pty_slave)
            
            # 打开串口
            self.ser_fd = os.open(
                self.config.device, 
                os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK
            )
            
            # 配置
            configure_serial(self.ser_fd, self.config.baud)
            set_raw_noecho(self.pty_master)
            set_raw_noecho(self.pty_slave)
            set_nonblocking(self.pty_master)
            set_nonblocking(self.ser_fd)
            
            # 创建过滤器
            pattern = self.config.filter_pattern.encode('utf-8') if self.config.filter_pattern else b""
            self.string_filter = StringFilter(pattern, self.config.filter_timeout)
            
            # 注册到事件循环
            self.loop.add_reader(self.ser_fd, self._on_serial_readable)
            self.loop.add_reader(self.pty_master, self._on_pty_readable)
            
            self.running = True
            self._schedule_timeout_check()
            
            logger.info(f"[{self.name}] Started: {self.config.device} -> {self.pty_slave_name}")
            return True
            
        except Exception as e:
            logger.error(f"[{self.name}] Failed to start: {e}")
            await self.stop()
            return False
    
    async def stop(self) -> None:
        """停止通道。"""
        self.running = False
        
        # 取消超时检查
        if self._timeout_handle:
            self._timeout_handle.cancel()
            self._timeout_handle = None
        
        # 从事件循环移除
        if self.ser_fd >= 0:
            try:
                self.loop.remove_reader(self.ser_fd)
            except:
                pass
        if self.pty_master >= 0:
            try:
                self.loop.remove_reader(self.pty_master)
            except:
                pass
        
        # 刷新过滤器
        if self.string_filter and self.pty_master >= 0:
            remaining = self.string_filter.flush()
            if remaining:
                try:
                    os.write(self.pty_master, remaining)
                except:
                    pass
        
        # 关闭 fd
        for fd in (self.ser_fd, self.pty_master, self.pty_slave):
            if fd >= 0:
                try:
                    os.close(fd)
                except:
                    pass
        
        self.ser_fd = self.pty_master = self.pty_slave = -1
        logger.info(f"[{self.name}] Stopped")
    
    def _on_serial_readable(self) -> None:
        """串口可读回调（事件循环调用）。"""
        if not self.running:
            return
        
        try:
            data = os.read(self.ser_fd, 4096)
        except BlockingIOError:
            return
        except OSError as e:
            logger.warning(f"[{self.name}] Serial read error: {e}")
            return
        
        if not data:
            return
        
        # 过滤
        filtered = self.string_filter.process(data)
        
        if filtered:
            try:
                os.write(self.pty_master, filtered)
            except OSError as e:
                logger.warning(f"[{self.name}] PTY write error: {e}")
    
    def _on_pty_readable(self) -> None:
        """PTY 可读回调（用户输入）。"""
        if not self.running:
            return
        
        try:
            data = os.read(self.pty_master, 4096)
        except BlockingIOError:
            return
        except OSError as e:
            logger.warning(f"[{self.name}] PTY read error: {e}")
            return
        
        if not data:
            return
        
        try:
            os.write(self.ser_fd, data)
        except OSError as e:
            logger.warning(f"[{self.name}] Serial write error: {e}")
    
    def _schedule_timeout_check(self) -> None:
        """调度超时检查。"""
        if not self.running:
            return
        
        # 检查超时
        if self.string_filter:
            timeout_data = self.string_filter.check_timeout()
            if timeout_data and self.pty_master >= 0:
                try:
                    os.write(self.pty_master, timeout_data)
                except:
                    pass
        
        # 下次检查
        self._timeout_handle = self.loop.call_later(
            0.05,  # 50ms 检查一次
            self._schedule_timeout_check
        )


# ============================================================
# 通道管理器
# ============================================================

class ChannelManager:
    """
    管理所有串口通道，支持动态增删。
    """
    
    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.channels: Dict[str, AsyncSerialChannel] = {}
    
    async def add_channel(self, config: ChannelConfig) -> bool:
        """添加新通道。"""
        if config.name in self.channels:
            logger.warning(f"Channel {config.name} already exists, updating...")
            await self.remove_channel(config.name)
        
        channel = AsyncSerialChannel(config, self.loop)
        if await channel.start():
            self.channels[config.name] = channel
            return True
        return False
    
    async def remove_channel(self, name: str) -> bool:
        """移除通道。"""
        if name not in self.channels:
            logger.warning(f"Channel {name} not found")
            return False
        
        channel = self.channels.pop(name)
        await channel.stop()
        return True
    
    async def update_channel(self, config: ChannelConfig) -> bool:
        """更新通道配置（重启通道）。"""
        await self.remove_channel(config.name)
        return await self.add_channel(config)
    
    async def shutdown(self) -> None:
        """关闭所有通道。"""
        logger.info("Shutting down all channels...")
        for name in list(self.channels.keys()):
            await self.remove_channel(name)
    
    def get_channel_info(self) -> Dict[str, str]:
        """获取所有通道信息。"""
        return {
            name: ch.pty_slave_name 
            for name, ch in self.channels.items()
        }


# ============================================================
# Redis 监听器
# ============================================================

class RedisConfigWatcher:
    """
    监听 Redis 配置变更，动态管理通道。
    
    使用 Redis Keyspace Notifications:
    - 监听 CONSOLE_PORT|* 键的变更
    - SET/HSET -> 添加/更新通道
    - DEL -> 删除通道
    """
    
    def __init__(
        self, 
        channel_manager: ChannelManager,
        redis_host: str = "localhost",
        redis_port: int = 6379,
        redis_db: int = 4,
        key_pattern: str = "CONSOLE_PORT|*"
    ):
        self.channel_manager = channel_manager
        self.redis_host = redis_host
        self.redis_port = redis_port
        self.redis_db = redis_db
        self.key_pattern = key_pattern
        
        self.redis: Optional[aioredis.Redis] = None
        self.pubsub: Optional[aioredis.client.PubSub] = None
        self.running: bool = False
    
    async def start(self) -> None:
        """启动 Redis 监听。"""
        # 连接 Redis
        self.redis = aioredis.Redis(
            host=self.redis_host,
            port=self.redis_port,
            db=self.redis_db,
            decode_responses=True
        )
        
        # 测试连接
        await self.redis.ping()
        logger.info(f"Connected to Redis at {self.redis_host}:{self.redis_port} db={self.redis_db}")
        
        # 加载现有配置
        await self._load_existing_channels()
        
        # 订阅 keyspace 事件
        # 需要 Redis 配置: CONFIG SET notify-keyspace-events KEA
        self.pubsub = self.redis.pubsub()
        
        # 订阅模式：__keyspace@{db}__:CONSOLE_PORT|*
        pattern = f"__keyspace@{self.redis_db}__:{self.key_pattern}"
        await self.pubsub.psubscribe(pattern)
        logger.info(f"Subscribed to keyspace events: {pattern}")
        
        self.running = True
    
    async def _load_existing_channels(self) -> None:
        """加载 Redis 中已存在的通道配置。"""
        keys = await self.redis.keys(self.key_pattern)
        logger.info(f"Found {len(keys)} existing channel configs")
        
        for key in keys:
            config = await self._parse_channel_config(key)
            if config and config.enabled:
                await self.channel_manager.add_channel(config)
    
    async def _parse_channel_config(self, key: str) -> Optional[ChannelConfig]:
        """
        从 Redis 解析通道配置。
        
        支持两种格式：
        1. Hash: HGETALL key -> {device, baud, filter, ...}
        2. String (JSON): GET key -> {"device": ..., "baud": ...}
        """
        try:
            # 尝试作为 Hash 读取
            key_type = await self.redis.type(key)
            
            if key_type == "hash":
                data = await self.redis.hgetall(key)
                if not data:
                    return None
                
                # 从 key 提取名称: CONSOLE_PORT|console-1 -> console-1
                name = key.split("|", 1)[-1] if "|" in key else key
                
                return ChannelConfig(
                    name=name,
                    device=data.get("device", data.get("dev_path", "")),
                    baud=int(data.get("baud", data.get("baud_rate", 9600))),
                    filter_pattern=data.get("filter", ""),
                    filter_timeout=float(data.get("filter_timeout", 0.1)),
                    enabled=data.get("enabled", "1") in ("1", "true", "True", True),
                )
            
            elif key_type == "string":
                raw = await self.redis.get(key)
                if not raw:
                    return None
                
                data = json.loads(raw)
                name = key.split("|", 1)[-1] if "|" in key else key
                
                return ChannelConfig(
                    name=name,
                    device=data.get("device", ""),
                    baud=data.get("baud", 9600),
                    filter_pattern=data.get("filter", ""),
                    filter_timeout=data.get("filter_timeout", 0.1),
                    enabled=data.get("enabled", True),
                )
            
            else:
                logger.warning(f"Unknown key type for {key}: {key_type}")
                return None
                
        except Exception as e:
            logger.error(f"Failed to parse config for {key}: {e}")
            return None
    
    async def run(self) -> None:
        """运行事件监听循环。"""
        logger.info("Starting Redis event listener...")
        
        while self.running:
            try:
                message = await self.pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0
                )
                
                if message is None:
                    continue
                
                await self._handle_keyspace_event(message)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Redis listener error: {e}")
                await asyncio.sleep(1)
    
    async def _handle_keyspace_event(self, message: dict) -> None:
        """处理 keyspace 事件。"""
        # message 格式:
        # {
        #   'type': 'pmessage',
        #   'pattern': '__keyspace@4__:CONSOLE_PORT|*',
        #   'channel': '__keyspace@4__:CONSOLE_PORT|console-1',
        #   'data': 'set' / 'hset' / 'del' / 'expired'
        # }
        
        event_type = message.get("data", "")
        channel = message.get("channel", "")
        
        # 从 channel 提取 key: __keyspace@4__:CONSOLE_PORT|xxx -> CONSOLE_PORT|xxx
        key = channel.split(":", 1)[-1] if ":" in channel else ""
        if not key:
            return
        
        name = key.split("|", 1)[-1] if "|" in key else key
        
        logger.info(f"Redis event: {event_type} on {key}")
        
        if event_type in ("set", "hset", "hmset"):
            # 添加或更新
            config = await self._parse_channel_config(key)
            if config:
                if config.enabled:
                    await self.channel_manager.update_channel(config)
                else:
                    await self.channel_manager.remove_channel(name)
        
        elif event_type in ("del", "expired"):
            # 删除
            await self.channel_manager.remove_channel(name)
    
    async def stop(self) -> None:
        """停止监听。"""
        self.running = False
        
        if self.pubsub:
            await self.pubsub.unsubscribe()
            await self.pubsub.close()
        
        if self.redis:
            await self.redis.close()
        
        logger.info("Redis watcher stopped")


# ============================================================
# 主程序
# ============================================================

class AsyncMultiSerialProxy:
    """异步多串口代理主程序。"""
    
    def __init__(
        self,
        redis_host: str = "localhost",
        redis_port: int = 6379,
        redis_db: int = 4,
        key_pattern: str = "CONSOLE_PORT|*"
    ):
        self.loop: asyncio.AbstractEventLoop = None
        self.channel_manager: ChannelManager = None
        self.redis_watcher: RedisConfigWatcher = None
        
        self.redis_host = redis_host
        self.redis_port = redis_port
        self.redis_db = redis_db
        self.key_pattern = key_pattern
        
        self._shutdown_event = asyncio.Event()
    
    async def run(self) -> None:
        """运行代理服务。"""
        self.loop = asyncio.get_running_loop()
        
        # 创建管理器
        self.channel_manager = ChannelManager(self.loop)
        self.redis_watcher = RedisConfigWatcher(
            self.channel_manager,
            redis_host=self.redis_host,
            redis_port=self.redis_port,
            redis_db=self.redis_db,
            key_pattern=self.key_pattern,
        )
        
        # 信号处理
        for sig in (signal.SIGINT, signal.SIGTERM):
            self.loop.add_signal_handler(sig, self._signal_handler)
        
        try:
            # 启动 Redis 监听
            await self.redis_watcher.start()
            
            # 打印状态
            logger.info("Proxy is running. Channels:")
            for name, pty in self.channel_manager.get_channel_info().items():
                logger.info(f"  {name} -> {pty}")
            
            # 同时运行 Redis 事件循环和等待关闭
            await asyncio.gather(
                self.redis_watcher.run(),
                self._shutdown_event.wait(),
            )
            
        finally:
            await self._shutdown()
    
    def _signal_handler(self) -> None:
        """信号处理。"""
        logger.info("Received shutdown signal")
        self._shutdown_event.set()
        self.redis_watcher.running = False
    
    async def _shutdown(self) -> None:
        """关闭服务。"""
        await self.redis_watcher.stop()
        await self.channel_manager.shutdown()
        logger.info("Proxy shutdown complete")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Async Multi-Serial Proxy with Redis Integration"
    )
    parser.add_argument("--redis-host", default="localhost",
                        help="Redis host (default: localhost)")
    parser.add_argument("--redis-port", type=int, default=6379,
                        help="Redis port (default: 6379)")
    parser.add_argument("--redis-db", type=int, default=4,
                        help="Redis database (default: 4)")
    parser.add_argument("--key-pattern", default="CONSOLE_PORT|*",
                        help="Redis key pattern to watch (default: CONSOLE_PORT|*)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Verbose logging")
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    proxy = AsyncMultiSerialProxy(
        redis_host=args.redis_host,
        redis_port=args.redis_port,
        redis_db=args.redis_db,
        key_pattern=args.key_pattern,
    )
    
    try:
        asyncio.run(proxy.run())
    except KeyboardInterrupt:
        pass
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
