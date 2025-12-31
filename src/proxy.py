#!/usr/bin/env python3
"""
Console Proxy Service

监听 Redis 配置，为每个串口创建过滤代理。
- 过滤字符串: "hello"
- 超时: 1s (buffer 非空时透传)
"""

import os
import sys
import asyncio
import signal
import termios
import tty
import fcntl
import time
import logging
from typing import Optional, Any

import redis.asyncio as aioredis

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# 配置
REDIS_HOST = "localhost"
REDIS_PORT = 6379
REDIS_DB = 4          # 配置数据库
STATE_DB = 6          # 状态数据库
KEY_PATTERN = "CONSOLE_PORT|*"
FILTER_PATTERN = b"hello"
FILTER_TIMEOUT = 1.0       # 过滤超时（秒）
HEARTBEAT_TIMEOUT = 15.0   # 心跳超时（秒）

BAUD_MAP = {
    1200: termios.B1200, 2400: termios.B2400, 4800: termios.B4800,
    9600: termios.B9600, 19200: termios.B19200, 38400: termios.B38400,
    57600: termios.B57600, 115200: termios.B115200,
}


# ============================================================
# 数据库工具类
# ============================================================

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


# ============================================================
# 串口配置
# ============================================================

def set_nonblocking(fd: int) -> None:
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)


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
    speed = BAUD_MAP.get(baud, termios.B9600)
    attrs[4] = attrs[5] = speed
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    termios.tcflush(fd, termios.TCIOFLUSH)


def configure_pty(fd: int) -> None:
    tty.setraw(fd, when=termios.TCSANOW)
    attrs = termios.tcgetattr(fd)
    attrs[3] &= ~(termios.ECHO | termios.ECHONL)
    termios.tcsetattr(fd, termios.TCSANOW, attrs)


# ============================================================
# 字符串过滤器 (KMP)
# ============================================================

class StringFilter:
    def __init__(self, pattern: bytes):
        self.pattern = pattern
        self.pattern_len = len(pattern)
        self.failure = self._compute_failure(pattern)
        self.match_pos = 0
        self.buffer = bytearray()

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

    def process(self, data: bytes) -> tuple[bytes, int]:
        """处理数据，返回 (过滤后的数据, pattern 匹配次数)"""
        output = bytearray()
        match_count = 0

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
                    match_count += 1  # 完整匹配一次
            else:
                output.append(byte)

        return bytes(output), match_count

    def flush(self) -> bytes:
        result = bytes(self.buffer)
        self.buffer.clear()
        self.match_pos = 0
        return result


# ============================================================
# 串口代理
# ============================================================

class SerialProxy:
    def __init__(self, link_id: str, device: str, baud: int, 
                 loop: asyncio.AbstractEventLoop,
                 db: 'DbUtil'):
        self.link_id = link_id
        self.device = device
        self.baud = baud
        self.loop = loop
        self.db = db

        self.ser_fd: int = -1
        self.pty_master: int = -1
        self.pty_slave: int = -1
        self.pty_name: str = ""
        self.filter: Optional[StringFilter] = None
        self.running: bool = False
        self._timeout_handle: Optional[asyncio.TimerHandle] = None
        self._heartbeat_handle: Optional[asyncio.TimerHandle] = None

    def start(self) -> bool:
        try:
            # PTY
            self.pty_master, self.pty_slave = os.openpty()
            self.pty_name = os.ttyname(self.pty_slave)

            # 串口
            self.ser_fd = os.open(self.device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)

            configure_serial(self.ser_fd, self.baud)
            configure_pty(self.pty_master)
            configure_pty(self.pty_slave)
            set_nonblocking(self.pty_master)
            set_nonblocking(self.ser_fd)

            self.filter = StringFilter(FILTER_PATTERN)

            # 注册到事件循环
            self.loop.add_reader(self.ser_fd, self._on_serial_read)
            self.loop.add_reader(self.pty_master, self._on_pty_read)

            self.running = True

            # 启动心跳超时定时器
            self._reset_heartbeat_timer()

            log.info(f"[{self.link_id}] Started: {self.device} -> {self.pty_name}")
            return True

        except Exception as e:
            log.error(f"[{self.link_id}] Failed: {e}")
            self.stop()
            return False

    def stop(self) -> None:
        self.running = False

        # 取消超时定时器
        if self._timeout_handle:
            self._timeout_handle.cancel()
            self._timeout_handle = None

        # 取消心跳定时器
        if self._heartbeat_handle:
            self._heartbeat_handle.cancel()
            self._heartbeat_handle = None

        for fd in (self.ser_fd, self.pty_master):
            if fd >= 0:
                try:
                    self.loop.remove_reader(fd)
                except:
                    pass

        if self.filter and self.pty_master >= 0:
            remaining = self.filter.flush()
            if remaining:
                try:
                    os.write(self.pty_master, remaining)
                except:
                    pass

        for fd in (self.ser_fd, self.pty_master, self.pty_slave):
            if fd >= 0:
                try:
                    os.close(fd)
                except:
                    pass

        self.ser_fd = self.pty_master = self.pty_slave = -1
        log.info(f"[{self.link_id}] Stopped")

    def _on_serial_read(self) -> None:
        if not self.running or not self.filter:
            return
        try:
            data = os.read(self.ser_fd, 4096)
            if data:
                # 取消旧的超时定时器
                if self._timeout_handle:
                    self._timeout_handle.cancel()
                    self._timeout_handle = None

                filtered, match_count = self.filter.process(data)

                # 如果匹配到 pattern，触发心跳
                if match_count > 0:
                    self._on_pattern_matched()

                if filtered:
                    os.write(self.pty_master, filtered)

                # 如果 buffer 非空，设置新的超时定时器
                if self.filter.buffer:
                    self._timeout_handle = self.loop.call_later(
                        FILTER_TIMEOUT,
                        self._on_timeout
                    )
        except (BlockingIOError, OSError):
            pass

    def _on_pattern_matched(self) -> None:
        """pattern 匹配成功，重置心跳定时器，更新状态为 up"""
        self._reset_heartbeat_timer()
        self._update_state("up")

    def _reset_heartbeat_timer(self) -> None:
        """重置心跳超时定时器"""
        if self._heartbeat_handle:
            self._heartbeat_handle.cancel()
        self._heartbeat_handle = self.loop.call_later(
            HEARTBEAT_TIMEOUT,
            self._on_heartbeat_timeout_triggered
        )

    def _on_heartbeat_timeout_triggered(self) -> None:
        """心跳超时回调"""
        self._heartbeat_handle = None
        if not self.running:
            return
        log.warning(f"[{self.link_id}] Heartbeat timeout")
        self._update_state("down")

    def _update_state(self, oper_state: str) -> None:
        """异步更新 Redis 状态（fire-and-forget）"""
        asyncio.create_task(self.db.update_state(self.link_id, oper_state))

    def _on_timeout(self) -> None:
        """超时回调：透传 buffer 中的数据"""
        self._timeout_handle = None
        if not self.running or not self.filter:
            return
        if self.filter.buffer and self.pty_master >= 0:
            data = self.filter.flush()
            if data:
                try:
                    os.write(self.pty_master, data)
                    log.debug(f"[{self.link_id}] Timeout flush: {data!r}")
                except OSError:
                    pass

    def _on_pty_read(self) -> None:
        if not self.running:
            return
        try:
            data = os.read(self.pty_master, 4096)
            if data:
                os.write(self.ser_fd, data)
        except (BlockingIOError, OSError):
            pass


# ============================================================
# 代理管理
# ============================================================

class ProxyManager:
    def __init__(self):
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.db = DbUtil()
        self.proxies: dict[str, SerialProxy] = {}
        self.running: bool = False

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()

        # 连接数据库
        await self.db.connect()

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
            self.proxies[link_id].stop()
            del self.proxies[link_id]

        # 添加新的 proxy
        for link_id in redis_ids - current_ids:
            cfg = redis_configs[link_id]
            proxy = SerialProxy(
                link_id, cfg["device"], cfg["baud"], self.loop,
                db=self.db
            )
            if proxy.start():
                self.proxies[link_id] = proxy

        # 更新已存在但配置变化的 proxy
        for link_id in redis_ids & current_ids:
            cfg = redis_configs[link_id]
            proxy = self.proxies[link_id]
            if proxy.baud != cfg["baud"]:
                proxy.stop()
                new_proxy = SerialProxy(
                    link_id, cfg["device"], cfg["baud"], self.loop,
                    db=self.db
                )
                if new_proxy.start():
                    self.proxies[link_id] = new_proxy

        log.info(f"Sync complete: {len(self.proxies)} proxies active")

    async def stop(self) -> None:
        self.running = False

        for proxy in self.proxies.values():
            proxy.stop()
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
