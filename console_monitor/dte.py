#!/usr/bin/env python3
"""
Console Monitor DTE (Data Terminal Equipment)

DTE 侧服务：
1. 读取 /proc/cmdline 解析 console=<TTYNAME>,<BAUD>
2. 打开串口，创建 PTY，创建符号链接 /dev/V<ttyname>
3. 在串口和 PTY 之间转发数据
4. 每隔 5 秒检查 STATE_DB 中 CONSOLE_SWITCH|console_mgmt 是否 enabled，如果 enabled 发送心跳帧

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
from .util import set_nonblocking, configure_serial, configure_pty
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
STATE_DB = 6


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


class DTEProxy:
    """
    DTE 代理
    
    功能：
    1. 打开串口并创建 PTY
    2. 在串口和 PTY 之间转发数据
    3. 定期检查 CONFIG_DB 并发送心跳
    """
    
    def __init__(self, tty_name: str, baud: int):
        self.tty_name = tty_name
        self.baud = baud
        self.device_path = f"/dev/{tty_name}"
        self.pty_symlink = f"/dev/V{tty_name}"
        
        self.ser_fd: int = -1
        self.pty_master: int = -1
        self.pty_slave: int = -1
        self.pty_name: str = ""
        
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.running: bool = False
        self.seq: int = 0  # 心跳序列号 (0-255 循环)
        
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._redis = None  # Optional redis.asyncio.Redis
    
    async def start(self) -> bool:
        """启动代理"""
        try:
            self.loop = asyncio.get_running_loop()
            
            # 创建 PTY
            self.pty_master, self.pty_slave = os.openpty()
            self.pty_name = os.ttyname(self.pty_slave)
            
            # 打开串口
            self.ser_fd = os.open(self.device_path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            
            # 配置串口和 PTY
            configure_serial(self.ser_fd, self.baud)
            configure_pty(self.pty_master)
            configure_pty(self.pty_slave)
            set_nonblocking(self.pty_master)
            set_nonblocking(self.ser_fd)
            
            # 创建符号链接
            self._create_symlink()
            
            # 注册到事件循环
            self.loop.add_reader(self.ser_fd, self._on_serial_read)
            self.loop.add_reader(self.pty_master, self._on_pty_read)
            
            self.running = True
            
            # 连接 Redis
            await self._connect_redis()
            
            # 启动心跳任务
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            
            log.info(f"DTE Proxy started: {self.device_path} <-> {self.pty_name} ({self.pty_symlink})")
            return True
            
        except Exception as e:
            log.error(f"Failed to start DTE Proxy: {e}", exc_info=True)
            await self.stop()
            return False
    
    async def stop(self) -> None:
        """停止代理"""
        self.running = False
        
        # 取消心跳任务
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None
        
        # 关闭 Redis 连接
        if self._redis:
            await self._redis.aclose()
            self._redis = None
        
        # 移除事件循环监听
        if self.loop:
            for fd in (self.ser_fd, self.pty_master):
                if fd >= 0:
                    try:
                        self.loop.remove_reader(fd)
                    except:
                        pass
        
        # 删除符号链接
        self._remove_symlink()
        
        # 关闭文件描述符
        for fd in (self.ser_fd, self.pty_master, self.pty_slave):
            if fd >= 0:
                try:
                    os.close(fd)
                except:
                    pass
        
        self.ser_fd = self.pty_master = self.pty_slave = -1
        log.info("DTE Proxy stopped")
    
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
            await self._redis.ping()  # type: ignore
            log.info(f"Connected to Redis CONFIG_DB (db={CONFIG_DB})")
        except ImportError:
            log.warning("redis.asyncio not available, heartbeat check disabled")
            self._redis = None
        except Exception as e:
            log.warning(f"Failed to connect to Redis: {e}, heartbeat check disabled")
            self._redis = None
    
    async def _check_agent_enabled(self) -> bool:
        """
        检查 STATE_DB 中 CONSOLE_SWITCH|console_mgmt 是否 enabled
        
        Returns:
            True 如果 enabled，False 否则
        """
        if not self._redis:
            # 如果没有 Redis 连接，默认不发送心跳
            return False
        
        try:
            enabled = await self._redis.hget("CONSOLE_SWITCH|console_mgmt", "enabled")  # type: ignore
            return enabled == "yes"
        except Exception as e:
            log.warning(f"Failed to check agent status: {e}")
            return False
    
    async def _heartbeat_loop(self) -> None:
        """心跳发送循环：每 5 秒检查一次并发送心跳"""
        log.info(f"Heartbeat loop started (interval={HEARTBEAT_INTERVAL}s)")

        while self.running:
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL)

                if not self.running:
                    break
                
                # 检查是否启用
                if await self._check_agent_enabled():
                    self._send_heartbeat()
                else:
                    log.debug("Agent not enabled, skipping heartbeat")
                    
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
    
    def _on_serial_read(self) -> None:
        """串口数据读取回调：转发到 PTY"""
        if not self.running:
            return
        try:
            data = os.read(self.ser_fd, 4096)
            if data and self.pty_master >= 0:
                os.write(self.pty_master, data)
        except (BlockingIOError, OSError):
            pass
    
    def _on_pty_read(self) -> None:
        """PTY 数据读取回调：转发到串口"""
        if not self.running:
            return
        try:
            data = os.read(self.pty_master, 4096)
            if data and self.ser_fd >= 0:
                os.write(self.ser_fd, data)
        except (BlockingIOError, OSError):
            pass
    
    def _create_symlink(self) -> None:
        """创建 PTY 符号链接"""
        try:
            # 确保目标不存在
            if os.path.islink(self.pty_symlink) or os.path.exists(self.pty_symlink):
                os.unlink(self.pty_symlink)
            os.symlink(self.pty_name, self.pty_symlink)
            log.info(f"Symlink: {self.pty_symlink} -> {self.pty_name}")
        except Exception as e:
            log.error(f"Failed to create symlink: {e}")
            self.pty_symlink = ""
    
    def _remove_symlink(self) -> None:
        """删除 PTY 符号链接"""
        if self.pty_symlink:
            try:
                if os.path.islink(self.pty_symlink):
                    os.unlink(self.pty_symlink)
                    log.info(f"Symlink removed: {self.pty_symlink}")
            except Exception as e:
                log.error(f"Failed to remove symlink: {e}")
            self.pty_symlink = ""


async def async_main() -> int:
    """异步主函数"""
    # 解析 /proc/cmdline
    result = parse_cmdline()
    if not result:
        log.error("Failed to parse console configuration from /proc/cmdline")
        return 1
    
    tty_name, baud = result
    
    # 创建代理
    proxy = DTEProxy(tty_name, baud)
    
    # 设置信号处理
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    
    def signal_handler():
        log.info("Received shutdown signal")
        stop_event.set()
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)
    
    # 启动代理
    if not await proxy.start():
        return 1
    
    # 等待停止信号
    await stop_event.wait()
    
    # 停止代理
    await proxy.stop()
    
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
