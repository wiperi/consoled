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
import subprocess
import termios
import tty
import fcntl
import time
import logging
from typing import Optional, Any

import redis.asyncio as aioredis
from sonic_py_common import device_info

from frame import Frame, FrameFilter

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
FILTER_TIMEOUT = 0.5       # 帧过滤超时（秒）
HEARTBEAT_TIMEOUT = 15.0   # 心跳超时（秒）

BAUD_MAP = {
    1200: termios.B1200, 2400: termios.B2400, 4800: termios.B4800,
    9600: termios.B9600, 19200: termios.B19200, 38400: termios.B38400,
    57600: termios.B57600, 115200: termios.B115200,
}


def get_pty_symlink_prefix() -> str:
    """从 udevprefix.conf 读取 PTY 符号链接前缀
    
    物理串口: /dev/C0-1, /dev/C0-2, ...
    符号链接: /dev/VC0-1, /dev/VC0-2, ... (V = Virtual)
    """
    try:
        platform_path, _ = device_info.get_paths_to_platform_and_hwsku_dirs()
        config_file = os.path.join(platform_path, "udevprefix.conf")
        
        if os.path.exists(config_file):
            with open(config_file, 'r') as f:
                prefix = f.readline().rstrip()
                # 添加 V 前缀表示 Virtual，避免与物理串口冲突
                return f"/dev/V{prefix}"
    except Exception as e:
        log.warning(f"Failed to read udevprefix.conf: {e}")
    
    # 默认前缀
    return "/dev/VC0-"


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
# 串口代理
# ============================================================

class SerialProxy:
    def __init__(self, link_id: str, device: str, baud: int, 
                 loop: asyncio.AbstractEventLoop,
                 db: 'DbUtil',
                 pty_symlink_prefix: str):
        self.link_id = link_id
        self.device = device
        self.baud = baud
        self.loop = loop
        self.db = db
        self.pty_symlink_prefix = pty_symlink_prefix

        self.ser_fd: int = -1
        self.pty_master: int = -1
        self.pty_slave: int = -1
        self.pty_name: str = ""
        self.pty_symlink: str = ""
        self.filter: Optional[FrameFilter] = None
        self.running: bool = False
        self._timeout_handle: Optional[asyncio.TimerHandle] = None
        self._heartbeat_handle: Optional[asyncio.TimerHandle] = None

    async def start(self) -> bool:
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

            # 创建帧过滤器，设置回调
            self.filter = FrameFilter(
                on_frame=self._on_frame_received,
                on_user_data=self._on_user_data_received,
            )

            # 注册到事件循环
            self.loop.add_reader(self.ser_fd, self._on_serial_read)
            self.loop.add_reader(self.pty_master, self._on_pty_read)

            self.running = True

            # 创建符号链接
            self._create_symlink()

            # 启动心跳超时定时器
            self._reset_heartbeat_timer()

            log.info(f"[{self.link_id}] Started: {self.device} -> {self.pty_name} ({self.pty_symlink})")
            return True

        except Exception as e:
            log.error(f"[{self.link_id}] Failed: {e}")
            await self.stop()
            return False

    async def stop(self) -> None:
        self.running = False

        # 清理 STATE_DB 状态
        await self.db.cleanup_state(self.link_id)

        # 删除符号链接
        self._remove_symlink()

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
        """串口数据读取回调"""
        if not self.running or not self.filter:
            return
        try:
            data = os.read(self.ser_fd, 4096)
            if data:
                # 取消旧的超时定时器
                if self._timeout_handle:
                    self._timeout_handle.cancel()
                    self._timeout_handle = None

                # 处理数据，FrameFilter 会通过回调分发帧和用户数据
                self.filter.process(data)

                # 如果 buffer 非空，设置新的超时定时器
                if self.filter.has_pending_data():
                    self._timeout_handle = self.loop.call_later(
                        FILTER_TIMEOUT,
                        self._on_timeout
                    )
        except (BlockingIOError, OSError):
            pass

    def _on_frame_received(self, frame: Frame) -> None:
        """帧接收回调：收到有效帧时触发"""
        if frame.is_heartbeat():
            # 心跳帧：重置定时器，更新状态为 up
            self._reset_heartbeat_timer()
            self._update_state("up")
            log.debug(f"[{self.link_id}] Heartbeat frame received (seq={frame.seq})")
        else:
            # 其他类型帧：目前只支持心跳，记录日志
            log.warning(f"[{self.link_id}] Unknown frame type: {frame.frame_type}")

    def _on_user_data_received(self, data: bytes) -> None:
        """用户数据回调：收到非帧数据时转发到 PTY"""
        if self.pty_master >= 0:
            try:
                os.write(self.pty_master, data)
            except OSError:
                pass

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
        """超时回调：将 buffer 作为用户数据处理"""
        self._timeout_handle = None
        if not self.running or not self.filter:
            return
        # 通知 FrameFilter 超时，它会通过 on_user_data 回调发送数据
        self.filter.on_timeout()
        log.debug(f"[{self.link_id}] Filter timeout triggered")

    def _on_pty_read(self) -> None:
        if not self.running:
            return
        try:
            data = os.read(self.pty_master, 4096)
            if data:
                os.write(self.ser_fd, data)
        except (BlockingIOError, OSError):
            pass

    def _create_symlink(self) -> None:
        """创建 PTY 符号链接"""
        self.pty_symlink = f"{self.pty_symlink_prefix}{self.link_id}"
        try:
            # 确保目标不存在
            if os.path.islink(self.pty_symlink) or os.path.exists(self.pty_symlink):
                os.unlink(self.pty_symlink)
            os.symlink(self.pty_name, self.pty_symlink)
            log.info(f"[{self.link_id}] Symlink: {self.pty_symlink} -> {self.pty_name}")
        except Exception as e:
            log.error(f"[{self.link_id}] Failed to create symlink: {e}")
            self.pty_symlink = ""

    def _remove_symlink(self) -> None:
        """删除 PTY 符号链接"""
        if self.pty_symlink:
            try:
                if os.path.islink(self.pty_symlink):
                    os.unlink(self.pty_symlink)
                    log.info(f"[{self.link_id}] Symlink removed: {self.pty_symlink}")
            except Exception as e:
                log.error(f"[{self.link_id}] Failed to remove symlink: {e}")
            self.pty_symlink = ""


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
