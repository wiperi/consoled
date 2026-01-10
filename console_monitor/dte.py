#!/usr/bin/env python3
"""
Console Monitor DTE (Data Terminal Equipment)

DTE 侧服务：
1. 读取 /proc/cmdline 解析 console=<TTYNAME>,<BAUD>
2. 检查 CONFIG_DB 中 CONSOLE_SWITCH|controlled_device 的 enabled 字段
3. 监听 Redis keyspace notification，动态响应 enabled 状态变化
4. 如果 enabled=yes，每 5 秒发送心跳帧到串口

放置位置: /usr/local/bin/console-monitor-dte
"""

import sys
import os
import re
import asyncio
import signal
import logging
from typing import Optional, Tuple

# 添加模块搜索路径
sys.path.insert(0, '/usr/local/lib')

from .frame import Frame, FrameType
from .util import set_nonblocking, configure_serial
from .constants import BAUD_MAP

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger(__name__)

# 心跳发送间隔（秒）
HEARTBEAT_INTERVAL = 5

# Redis 配置
REDIS_HOST = "localhost"
REDIS_PORT = 6379
CONFIG_DB = 4

# Redis key
CONSOLE_SWITCH_KEY = "CONSOLE_SWITCH|controlled_device"


def parse_cmdline() -> Optional[Tuple[str, int]]:
    """
    从 /proc/cmdline 解析 console 参数

    格式: console=<TTYNAME>,<BAUD>
    例如: console=ttyS0,9600

    Returns:
        (tty_name, baud) 或 None（解析失败时）
    """
    try:
        with open('/proc/cmdline', 'r') as f:
            cmdline = f.read().strip()

        log.info(f"Parsing /proc/cmdline: {cmdline}")

        # 匹配 console=<ttyname>,<baud> 或 console=<ttyname>
        # 格式可能是: console=ttyS0,9600 或 console=ttyS0,9600n8
        pattern = r'console=([a-zA-Z0-9]+),(\d+)'
        match = re.search(pattern, cmdline)

        if match:
            tty_name = match.group(1)
            baud = int(match.group(2))
            log.info(f"Parsed console: tty={tty_name}, baud={baud}")
            return (tty_name, baud)

        # 尝试匹配只有 tty 名称的情况
        pattern_simple = r'console=([a-zA-Z0-9]+)'
        match = re.search(pattern_simple, cmdline)
        if match:
            tty_name = match.group(1)
            baud = 9600  # 默认波特率
            log.info(f"Parsed console (default baud): tty={tty_name}, baud={baud}")
            return (tty_name, baud)

        log.warning("No console= parameter found in /proc/cmdline")
        return None

    except Exception as e:
        log.error(f"Failed to parse /proc/cmdline: {e}")
        return None


class DTEHeartbeat:
    """
    DTE 心跳服务

    功能：
    1. 监听 Redis keyspace notification
    2. 根据 enabled 状态发送心跳帧
    """

    def __init__(self, tty_name: str, baud: int):
        self.tty_name = tty_name
        self.baud = baud
        self.device_path = f"/dev/{tty_name}"

        self.ser_fd: int = -1
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.running: bool = False
        self.enabled: bool = False
        self.seq: int = 0  # 心跳序列号 (0-255 循环)

        self._heartbeat_task: Optional[asyncio.Task] = None
        self._subscribe_task: Optional[asyncio.Task] = None
        self._redis = None  # redis.asyncio.Redis for CONFIG_DB
        self._pubsub = None  # redis pubsub for keyspace notification

    async def start(self) -> bool:
        """启动服务"""
        try:
            self.loop = asyncio.get_running_loop()

            # 打开串口
            self.ser_fd = os.open(self.device_path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            configure_serial(self.ser_fd, self.baud)
            set_nonblocking(self.ser_fd)

            self.running = True

            # 连接 Redis
            await self._connect_redis()

            # 检查初始状态
            self.enabled = await self._check_enabled()
            log.info(f"Initial enabled state: {self.enabled}")

            # 启动 keyspace notification 监听
            self._subscribe_task = asyncio.create_task(self._subscribe_loop())

            # 如果初始状态为 enabled，启动心跳任务
            if self.enabled:
                self._start_heartbeat()

            log.info(f"DTE Heartbeat service started: {self.device_path}")
            return True

        except Exception as e:
            log.error(f"Failed to start DTE Heartbeat service: {e}", exc_info=True)
            await self.stop()
            return False

    async def stop(self) -> None:
        """停止服务"""
        self.running = False

        # 停止心跳任务
        self._stop_heartbeat()

        # 取消订阅任务
        if self._subscribe_task:
            self._subscribe_task.cancel()
            try:
                await self._subscribe_task
            except asyncio.CancelledError:
                pass
            self._subscribe_task = None

        # 关闭 pubsub
        if self._pubsub:
            await self._pubsub.close()
            self._pubsub = None

        # 关闭 Redis 连接
        if self._redis:
            await self._redis.aclose()
            self._redis = None

        # 关闭串口
        if self.ser_fd >= 0:
            try:
                os.close(self.ser_fd)
            except:
                pass
            self.ser_fd = -1

        log.info("DTE Heartbeat service stopped")

    async def _connect_redis(self) -> None:
        """连接 Redis CONFIG_DB"""
        try:
            import redis.asyncio as aioredis
            self._redis = aioredis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                db=CONFIG_DB,
                decode_responses=True
            )
            await self._redis.ping()
            log.info(f"Connected to Redis CONFIG_DB (db={CONFIG_DB})")
        except ImportError:
            log.error("redis.asyncio not available")
            raise
        except Exception as e:
            log.error(f"Failed to connect to Redis: {e}")
            raise

    async def _check_enabled(self) -> bool:
        """
        检查 CONFIG_DB 中 CONSOLE_SWITCH|controlled_device 的 enabled 字段

        Returns:
            True 如果 enabled=yes，False 否则
        """
        if not self._redis:
            return False

        try:
            enabled = await self._redis.hget(CONSOLE_SWITCH_KEY, "enabled")
            return enabled == "yes"
        except Exception as e:
            log.warning(f"Failed to check enabled status: {e}")
            return False

    async def _subscribe_loop(self) -> None:
        """监听 Redis keyspace notification"""
        try:
            import redis.asyncio as aioredis

            # 创建新的连接用于 pubsub
            pubsub_redis = aioredis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                db=CONFIG_DB,
                decode_responses=True
            )

            self._pubsub = pubsub_redis.pubsub()

            # 订阅 keyspace notification
            # 格式: __keyspace@<db>__:<key>
            channel = f"__keyspace@{CONFIG_DB}__:{CONSOLE_SWITCH_KEY}"
            await self._pubsub.subscribe(channel)
            log.info(f"Subscribed to keyspace notification: {channel}")

            while self.running:
                try:
                    message = await asyncio.wait_for(
                        self._pubsub.get_message(ignore_subscribe_messages=True),
                        timeout=1.0
                    )

                    if message:
                        log.debug(f"Received keyspace notification: {message}")
                        # 收到通知后重新检查 enabled 状态
                        new_enabled = await self._check_enabled()

                        if new_enabled != self.enabled:
                            log.info(f"Enabled state changed: {self.enabled} -> {new_enabled}")
                            self.enabled = new_enabled

                            if self.enabled:
                                self._start_heartbeat()
                            else:
                                self._stop_heartbeat()

                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    log.error(f"Error in subscribe loop: {e}")
                    await asyncio.sleep(1)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.error(f"Subscribe loop error: {e}")
        finally:
            if self._pubsub:
                await self._pubsub.close()
                self._pubsub = None
            log.info("Subscribe loop stopped")

    def _start_heartbeat(self) -> None:
        """启动心跳任务"""
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            log.info("Heartbeat task started")

    def _stop_heartbeat(self) -> None:
        """停止心跳任务"""
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            log.info("Heartbeat task stopped")

    async def _heartbeat_loop(self) -> None:
        """心跳发送循环：每 5 秒发送心跳帧"""
        log.info(f"Heartbeat loop started (interval={HEARTBEAT_INTERVAL}s)")

        while self.running and self.enabled:
            try:
                self._send_heartbeat()
                await asyncio.sleep(HEARTBEAT_INTERVAL)

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Heartbeat loop error: {e}")

        log.info("Heartbeat loop stopped")

    def _send_heartbeat(self) -> None:
        """发送一次心跳帧到串口"""
        if self.ser_fd < 0:
            return

        # 构造心跳帧
        frame = Frame(
            seq=self.seq,
            frame_type=FrameType.HEARTBEAT,
            payload=b""
        )

        frame_bytes = frame.build()

        try:
            os.write(self.ser_fd, frame_bytes)
            hex_str = frame_bytes.hex().upper()
            log.info(f"Sent heartbeat frame (seq={self.seq}): {hex_str}")

            # 序列号递增 (0-255 循环)
            self.seq = (self.seq + 1) % 256

        except Exception as e:
            log.error(f"Failed to send heartbeat: {e}")


async def async_main() -> int:
    """异步主函数"""
    # 解析 /proc/cmdline
    result = parse_cmdline()
    if not result:
        log.error("Failed to parse console configuration from /proc/cmdline")
        return 1

    tty_name, baud = result

    # 创建心跳服务
    service = DTEHeartbeat(tty_name, baud)

    # 设置信号处理
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def signal_handler():
        log.info("Received shutdown signal")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    # 启动服务
    if not await service.start():
        return 1

    # 等待停止信号
    await stop_event.wait()

    # 停止服务
    await service.stop()

    return 0


def main():
    """主函数"""
    try:
        exit_code = asyncio.run(async_main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        log.info("Interrupted")
        sys.exit(0)
    except Exception as e:
        log.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


def run():
    main()


if __name__ == '__main__':
    main()
