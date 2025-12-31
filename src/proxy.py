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
REDIS_DB = 4
KEY_PATTERN = "CONSOLE_PORT|*"
FILTER_PATTERN = b"hello"
FILTER_TIMEOUT = 1.0  # 秒

BAUD_MAP = {
    1200: termios.B1200, 2400: termios.B2400, 4800: termios.B4800,
    9600: termios.B9600, 19200: termios.B19200, 38400: termios.B38400,
    57600: termios.B57600, 115200: termios.B115200,
}


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
        self.last_data_time = 0.0

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
        """如果超时且 buffer 非空，返回 buffer 内容并清空"""
        if self.buffer and self.last_data_time > 0:
            if time.monotonic() - self.last_data_time >= FILTER_TIMEOUT:
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
# 串口代理
# ============================================================

class SerialProxy:
    def __init__(self, link_id: str, device: str, baud: int, loop: asyncio.AbstractEventLoop):
        self.link_id = link_id
        self.device = device
        self.baud = baud
        self.loop = loop

        self.ser_fd = -1
        self.pty_master = -1
        self.pty_slave = -1
        self.pty_name = ""
        self.filter = None
        self.running = False
        self._timeout_handle = None  # 独立超时定时器

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
        if not self.running:
            return
        try:
            data = os.read(self.ser_fd, 4096)
            if data:
                # 取消旧的超时定时器
                if self._timeout_handle:
                    self._timeout_handle.cancel()
                    self._timeout_handle = None

                filtered = self.filter.process(data)
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
        self.loop = None
        self.redis = None
        self.pubsub = None
        self.proxies: dict[str, SerialProxy] = {}  # link_id -> proxy
        self.running = False

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()

        # 连接 Redis
        self.redis = aioredis.Redis(
            host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB,
            decode_responses=True
        )
        await self.redis.ping()
        log.info(f"Connected to Redis db={REDIS_DB}")

        # 初始同步
        await self.sync()

        # 订阅 keyspace 事件
        self.pubsub = self.redis.pubsub()
        pattern = f"__keyspace@{REDIS_DB}__:{KEY_PATTERN}"
        await self.pubsub.psubscribe(pattern)
        log.info(f"Subscribed: {pattern}")

        self.running = True

    async def run(self) -> None:
        """主循环：监听 Redis 事件"""
        while self.running:
            msg = await self.pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if msg:
                log.info(f"Redis event: {msg.get('data')} on {msg.get('channel')}")
                await self.sync()

    async def sync(self) -> None:
        """同步 Redis 配置和实际 proxy"""
        # 获取 Redis 中的配置
        keys = await self.redis.keys(KEY_PATTERN)
        redis_configs = {}

        for key in keys:
            link_id = key.split("|", 1)[-1]
            data = await self.redis.hgetall(key)
            if data:
                redis_configs[link_id] = {
                    "baud": int(data.get("baud_rate", 9600)),
                    "device": f"/dev/C0-{link_id}",  # 根据 link_id 推导设备路径
                }

        redis_ids = set(redis_configs.keys())
        current_ids = set(self.proxies.keys())

        # 删除不在 Redis 中的 proxy
        for link_id in current_ids - redis_ids:
            self.proxies[link_id].stop()
            del self.proxies[link_id]

        # 添加新的 proxy
        for link_id in redis_ids - current_ids:
            cfg = redis_configs[link_id]
            proxy = SerialProxy(link_id, cfg["device"], cfg["baud"], self.loop)
            if proxy.start():
                self.proxies[link_id] = proxy

        # 更新已存在但配置变化的 proxy
        for link_id in redis_ids & current_ids:
            cfg = redis_configs[link_id]
            proxy = self.proxies[link_id]
            if proxy.baud != cfg["baud"]:
                proxy.stop()
                new_proxy = SerialProxy(link_id, cfg["device"], cfg["baud"], self.loop)
                if new_proxy.start():
                    self.proxies[link_id] = new_proxy

        log.info(f"Sync complete: {len(self.proxies)} proxies active")

    async def stop(self) -> None:
        self.running = False

        for proxy in self.proxies.values():
            proxy.stop()
        self.proxies.clear()

        if self.pubsub:
            await self.pubsub.unsubscribe()
            await self.pubsub.close()
        if self.redis:
            await self.redis.close()

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
