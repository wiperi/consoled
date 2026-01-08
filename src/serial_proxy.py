"""
串口代理类

为每个串口创建 PTY 代理，实现数据过滤、心跳检测、状态管理等功能。
"""

import os
import asyncio
import logging
from typing import Optional

from frame import Frame, FrameFilter
from db_util import DbUtil
from util import set_nonblocking, configure_serial, configure_pty
from constants import FILTER_TIMEOUT, HEARTBEAT_TIMEOUT

log = logging.getLogger(__name__)


class SerialProxy:
    """串口代理：创建 PTY 并转发串口数据"""
    
    def __init__(self, link_id: str, device: str, baud: int, 
                 loop: asyncio.AbstractEventLoop,
                 db: DbUtil,
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
        """启动代理"""
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
        """停止代理"""
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
        """PTY 数据读取回调"""
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
