#!/usr/bin/env python3
"""
Console Monitor Service

统一的 Console Monitor 服务，根据 CONFIG_DB 配置自动运行 DCE 或 DTE 模式。

DCE (Console Server 侧):
- 为每个串口创建 PTY 代理
- 过滤心跳帧，更新链路状态到 STATE_DB

DTE (SONiC Switch 侧):
- 定期发送心跳帧
- 监听 CONFIG_DB 配置变化

使用方法:
    console-monitor-dce              # 启动 DCE 服务
    console-monitor-dte [tty] [baud] # 启动 DTE 服务
"""

import os
import re
import sys
import time
import fcntl
import termios
import tty
import signal
import argparse
import logging
import threading
import select
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional, Callable, Dict

from swsscommon.swsscommon import (
    DBConnector,
    Table,
    ConfigDBConnector,
)

# ============================================================
# 日志配置
# ============================================================

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger("console-monitor")


# ============================================================
# 全局常量
# ============================================================

# 超时配置
HEARTBEAT_INTERVAL = 2.0      # DTE 心跳发送间隔（秒）
HEARTBEAT_TIMEOUT = 6.0      # DCE 心跳超时（秒）

# 波特率映射
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

# Redis 表名
CONSOLE_PORT_TABLE = "CONSOLE_PORT"
CONSOLE_SWITCH_TABLE = "CONSOLE_SWITCH"

# 默认波特率
DEFAULT_BAUD = 9600

# 内核命令行路径
PROC_CMDLINE = "/proc/cmdline"


# ============================================================
# 帧协议常量和类
# ============================================================

class SpecialChar(IntEnum):
    """特殊字符定义"""
    SOF = 0x05  # Start of Frame
    EOF = 0x00  # End of Frame
    DLE = 0x10  # Data Link Escape


# 可转义字符集合
ESCAPABLE_CHARS = frozenset({SpecialChar.SOF, SpecialChar.EOF, SpecialChar.DLE})


class FrameType(IntEnum):
    """帧类型定义"""
    HEARTBEAT = 0x01


# 协议版本
PROTOCOL_VERSION = 0x01

# 帧头帧尾长度
SOF_LEN = 3
EOF_LEN = 3

# Buffer 大小限制
MAX_FRAME_BUFFER_SIZE = 64

# 帧头帧尾序列
SOF_SEQUENCE = bytes([SpecialChar.SOF] * SOF_LEN)
EOF_SEQUENCE = bytes([SpecialChar.EOF] * EOF_LEN)

def log_binary_data(data: bytes, direction: str) -> None:
    """
    以二进制和可读形式输出数据到终端
    
    Args:
        data: 要输出的字节数据
        direction: 数据流向（如 "Serial→PTY", "PTY→Serial"）
    """ 
    hex_str = data.hex(' ', 1)  # 每字节用空格分隔
    # 将不可打印字符替换为 <HEX>
    readable = ''.join(chr(b) if 32 <= b < 127 else f"<0x{b:02x}>" for b in data)
    log.debug(f"[{direction} ({len(data)} bytes):\n  HEX: {hex_str}\n  ASCII: {readable}\n")



def crc16_modbus(data: bytes) -> int:
    """CRC-16/MODBUS 算法"""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def escape_data(data: bytes) -> bytes:
    """对数据进行转义"""
    result = bytearray()
    for byte in data:
        if byte in ESCAPABLE_CHARS:
            result.append(SpecialChar.DLE)
        result.append(byte)
    return bytes(result)


def unescape_data(data: bytes) -> bytes:
    """对数据进行去转义"""
    result = bytearray()
    i = 0
    while i < len(data):
        if data[i] == SpecialChar.DLE and i + 1 < len(data) and data[i + 1] in ESCAPABLE_CHARS:
            result.append(data[i + 1])
            i += 2
        else:
            result.append(data[i])
            i += 1
    return bytes(result)


@dataclass
class Frame:
    """帧数据结构"""
    version: int = PROTOCOL_VERSION
    seq: int = 0
    flag: int = 0x00
    frame_type: int = FrameType.HEARTBEAT
    payload: bytes = b""
    
    def build(self) -> bytes:
        """构造完整的帧二进制序列"""
        content = bytes([
            self.version,
            self.seq & 0xFF,
            self.flag,
            self.frame_type,
            len(self.payload),
        ]) + self.payload
        
        crc = crc16_modbus(content)
        crc_bytes = bytes([crc >> 8, crc & 0xFF])
        
        content_with_crc = content + crc_bytes
        escaped_content = escape_data(content_with_crc)
        
        return SOF_SEQUENCE + escaped_content + EOF_SEQUENCE
    
    @classmethod
    def parse(cls, buffer: bytes) -> Optional['Frame']:
        """从 buffer 解析帧"""
        unescaped = unescape_data(buffer)
        
        if len(unescaped) < 7:
            return None
        
        content = unescaped[:-2]
        crc_bytes = unescaped[-2:]
        
        expected_crc = crc16_modbus(content)
        received_crc = (crc_bytes[0] << 8) | crc_bytes[1]
        
        if expected_crc != received_crc:
            return None
        
        if len(content) < 5:
            return None
        
        version = content[0]
        seq = content[1]
        flag = content[2]
        frame_type = content[3]
        length = content[4]
        payload = content[5:5 + length] if length > 0 else b""
        
        return cls(
            version=version,
            seq=seq,
            flag=flag,
            frame_type=frame_type,
            payload=payload,
        )
    
    @classmethod
    def create_heartbeat(cls, seq: int = 0) -> 'Frame':
        """创建心跳帧"""
        return cls(
            version=PROTOCOL_VERSION,
            seq=seq,
            flag=0x00,
            frame_type=FrameType.HEARTBEAT,
            payload=b"",
        )
    
    def is_heartbeat(self) -> bool:
        """判断是否为心跳帧"""
        return self.frame_type == FrameType.HEARTBEAT


# 回调函数类型
FrameCallback = Callable[[Frame], None]
UserDataCallback = Callable[[bytes], None]


class FrameFilter:
    """帧过滤器：从字节流中识别帧和用户数据"""
    
    def __init__(
        self,
        on_frame: Optional[FrameCallback] = None,
        on_user_data: Optional[UserDataCallback] = None,
    ):
        self._on_frame = on_frame
        self._on_user_data = on_user_data
        self._buffer = bytearray()
        self._escape_next = False
        self._in_frame = False
    
    def process(self, data: bytes) -> None:
        """处理输入的字节流"""

        log_binary_data(data, "Received")

        for byte in data:
            if self._escape_next:

                self._buffer.append(byte)
                self._escape_next = False
                if len(self._buffer) >= MAX_FRAME_BUFFER_SIZE:
                    self._flush_buffer()

            elif byte == SpecialChar.DLE:

                self._buffer.append(byte)
                self._escape_next = True

            elif byte == SpecialChar.SOF:

                if not self._in_frame:
                    self._flush_as_user_data()
                else:
                    self._discard_buffer()
                self._in_frame = True

            elif byte == SpecialChar.EOF:

                self._try_parse_frame()
                self._in_frame = False

            else:
                
                self._buffer.append(byte)
                if len(self._buffer) >= MAX_FRAME_BUFFER_SIZE:
                    self._flush_buffer()
    
    def on_timeout(self) -> None:
        """超时回调"""
        if not self._in_frame:
            self._flush_as_user_data()
        else:
            self._discard_buffer()
        self._in_frame = False
    
    def flush(self) -> bytes:
        """刷新 buffer，返回剩余数据"""
        result = bytes(self._buffer)
        self._buffer.clear()
        self._escape_next = False
        self._in_frame = False
        return result
    
    def has_pending_data(self) -> bool:
        """检查是否有待处理的数据"""
        return len(self._buffer) > 0
    
    @property
    def in_frame(self) -> bool:
        """检查当前是否在帧内"""
        return self._in_frame
    
    def _flush_as_user_data(self) -> None:
        """将 buffer 作为用户数据发送"""
        if self._buffer and self._on_user_data:
            log_binary_data(self._buffer, 'User Data')
            self._on_user_data(bytes(self._buffer))
        self._buffer.clear()
        self._escape_next = False
    
    def _discard_buffer(self) -> None:
        """丢弃 buffer"""
        self._buffer.clear()
        self._escape_next = False
    
    def _flush_buffer(self) -> None:
        """根据是否在帧内决定如何处理 buffer 溢出"""
        if not self._in_frame:
            self._flush_as_user_data()
        else:
            self._discard_buffer()
        self._in_frame = False
    
    def _try_parse_frame(self) -> None:
        """尝试将 buffer 解析为帧"""
        if not self._buffer:
            self._escape_next = False
            return

        log_binary_data(self._buffer, 'Frame Data')
        
        frame = Frame.parse(bytes(self._buffer))
        self._buffer.clear()
        self._escape_next = False
        
        if frame is not None and self._on_frame:
            # 解析成功，回调帧
            self._on_frame(frame)

        # 解析失败，这不应该发生在正常情况下
        # 因为 SOF...EOF 之间的数据如果不是有效帧，
        # 说明数据被损坏了，丢弃即可



# ============================================================
# 工具函数
# ============================================================

def get_pty_symlink_prefix() -> str:
    """从 udevprefix.conf 读取 PTY 符号链接前缀"""
    try:
        from sonic_py_common import device_info
        platform_path, _ = device_info.get_paths_to_platform_and_hwsku_dirs()
        config_file = os.path.join(platform_path, "udevprefix.conf")
        
        if os.path.exists(config_file):
            with open(config_file, 'r') as f:
                prefix = f.readline().rstrip()
                return f"/dev/V{prefix}"
    except Exception as e:
        log.warning(f"Failed to read udevprefix.conf: {e}")
    
    return "/dev/VC0-"


def set_nonblocking(fd: int) -> None:
    """设置文件描述符为非阻塞模式"""
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)


def configure_serial(fd: int, baud: int) -> None:
    """配置串口参数"""
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
    """配置 PTY 为 raw 模式"""
    tty.setraw(fd, when=termios.TCSANOW)
    attrs = termios.tcgetattr(fd)
    attrs[3] &= ~(termios.ECHO | termios.ECHONL)
    termios.tcsetattr(fd, termios.TCSANOW, attrs)


def parse_proc_cmdline() -> tuple[str, int]:
    """
    从 /proc/cmdline 解析串口配置
    
    Returns:
        (tty_name, baud)
    
    Raises:
        ValueError: 未找到有效的 console 参数
    """
    try:
        with open(PROC_CMDLINE, 'r') as f:
            cmdline = f.read().strip()
    except Exception as e:
        raise ValueError(f"Failed to read {PROC_CMDLINE}: {e}")
    
    pattern = r'console=([a-zA-Z0-9]+)(?:,([0-9]+))?'
    matches = re.findall(pattern, cmdline)
    
    if not matches:
        raise ValueError(f"No console= parameter found in {PROC_CMDLINE}")
    
    tty_name, baud_str = matches[-1]
    baud = int(baud_str) if baud_str else DEFAULT_BAUD
    
    log.info(f"Parsed from /proc/cmdline: tty={tty_name}, baud={baud}")
    return (tty_name, baud)


# ============================================================
# DCE 串口代理
# ============================================================

class SerialProxy:
    """
    串口代理：创建 PTY 并转发串口数据
    
    使用 select 实现多路复用，在独立线程中运行。
    每个 proxy 拥有独立的 Redis 连接，避免多线程竞争。
    """
    
    def __init__(self, link_id: str, device: str, baud: int, pty_symlink_prefix: str):
        self.link_id = link_id
        self.device = device
        self.baud = baud
        self.pty_symlink_prefix = pty_symlink_prefix
        
        # 每个 proxy 创建独立的 Redis 连接，避免多线程竞争
        self.state_db: Optional[DBConnector] = None
        self.state_table: Optional[Table] = None
        
        self.ser_fd: int = -1
        self.pty_master: int = -1
        self.pty_slave: int = -1
        self.pty_name: str = ""
        self.pty_symlink: str = ""
        self.filter: Optional[FrameFilter] = None
        self.running: bool = False
        
        # 状态跟踪
        self._current_oper_state: Optional[str] = None
        self._last_heartbeat_time: float = 0.0
        self._last_data_activity: float = 0.0
        self._last_serial_data_time: float = 0.0  # 用于 filter 超时检测
        
        # 线程
        self._thread: Optional[threading.Thread] = None
        
        # 用于唤醒 select 的管道
        self._wake_r: int = -1
        self._wake_w: int = -1
    
    def start(self) -> bool:
        """启动代理"""
        try:
            # 创建独立的 Redis 连接（每个 proxy 一个，避免多线程竞争）
            self.state_db = DBConnector("STATE_DB", 0)
            self.state_table = Table(self.state_db, CONSOLE_PORT_TABLE)
            
            # 创建唤醒管道
            self._wake_r, self._wake_w = os.pipe()
            set_nonblocking(self._wake_r)
            
            # 创建 PTY
            self.pty_master, self.pty_slave = os.openpty()
            self.pty_name = os.ttyname(self.pty_slave)
            
            # 打开串口
            self.ser_fd = os.open(self.device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            
            # 配置串口和 PTY
            configure_serial(self.ser_fd, self.baud)
            configure_pty(self.pty_master)
            configure_pty(self.pty_slave)
            set_nonblocking(self.pty_master)
            set_nonblocking(self.ser_fd)
            
            # 创建帧过滤器
            self.filter = FrameFilter(
                on_frame=self._on_frame_received,
                on_user_data=self._on_user_data_received,
            )
            
            # 创建符号链接
            self._create_symlink()
            
            self.running = True
            self._last_heartbeat_time = time.monotonic()
            self._last_data_activity = time.monotonic()
            
            # 启动工作线程
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()
            
            log.info(f"[{self.link_id}] Started: {self.device} -> {self.pty_name} ({self.pty_symlink})")
            return True
            
        except Exception as e:
            log.error(f"[{self.link_id}] Failed to start: {e}")
            self.stop()
            return False
    
    def stop(self) -> None:
        """停止代理"""
        self.running = False
        
        # 唤醒 select 循环
        if self._wake_w >= 0:
            try:
                os.write(self._wake_w, b'x')
            except:
                pass
        
        # 等待线程结束
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        
        # 清理 STATE_DB 状态
        self._cleanup_state()
        
        # 删除符号链接
        self._remove_symlink()
        
        # 刷新剩余数据
        if self.filter and self.pty_master >= 0:
            remaining = self.filter.flush()
            if remaining:
                try:
                    os.write(self.pty_master, remaining)
                except:
                    pass
        
        # 关闭文件描述符
        for fd in (self._wake_r, self._wake_w, self.ser_fd, self.pty_master, self.pty_slave):
            if fd >= 0:
                try:
                    os.close(fd)
                except:
                    pass
        
        self._wake_r = self._wake_w = -1
        self.ser_fd = self.pty_master = self.pty_slave = -1
        
        log.info(f"[{self.link_id}] Stopped")
    
    def _run_loop(self) -> None:
        """工作线程主循环"""
        filter_timeout = self._calculate_filter_timeout(self.baud)

        while self.running:
            try:
                # 计算 select 超时时间
                now = time.monotonic()
                time_since_heartbeat = now - self._last_heartbeat_time
                select_timeout = max(0.1, HEARTBEAT_TIMEOUT - time_since_heartbeat)

                # 如果 filter 有待处理数据，需要考虑 filter 超时
                if self.filter and self.filter.has_pending_data():
                    time_since_serial = now - self._last_serial_data_time
                    remaining_filter_timeout = filter_timeout - time_since_serial
                    if remaining_filter_timeout > 0:
                        select_timeout = min(select_timeout, remaining_filter_timeout)
                    else:
                        # filter 超时已到，立即处理
                        select_timeout = 0

                # 使用 select 监听串口、PTY 和唤醒管道
                readable, _, _ = select.select(
                    [self.ser_fd, self.pty_master, self._wake_r],
                    [], [],
                    select_timeout
                )

                if not self.running:
                    break

                serial_data_received = False
                for fd in readable:
                    if fd == self.ser_fd:
                        self._on_serial_read()
                        serial_data_received = True
                    elif fd == self.pty_master:
                        self._on_pty_read()
                    elif fd == self._wake_r:
                        # 清空唤醒管道
                        try:
                            os.read(self._wake_r, 1024)
                        except:
                            pass

                # 检查心跳超时
                self._check_heartbeat_timeout()

                # 检查 filter 超时：只有当没有收到新串口数据且超时时间已到时才触发
                if self.filter and self.filter.has_pending_data() and not serial_data_received:
                    now = time.monotonic()
                    if now - self._last_serial_data_time >= filter_timeout:
                        self.filter.on_timeout()

            except Exception as e:
                if self.running:
                    log.error(f"[{self.link_id}] Loop error: {e}")
                    time.sleep(0.1)
    
    def _on_serial_read(self) -> None:
        """串口数据读取回调"""
        if not self.running or not self.filter:
            return
        try:
            data = os.read(self.ser_fd, 4096)
            if data:
                now = time.monotonic()
                self._last_data_activity = now
                self._last_serial_data_time = now
                self.filter.process(data)
        except (BlockingIOError, OSError):
            pass
    
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
    
    def _on_frame_received(self, frame: Frame) -> None:
        """帧接收回调"""
        if frame.is_heartbeat():
            self._last_heartbeat_time = time.monotonic()
            self._update_state("Up")
            log.debug(f"[{self.link_id}] Heartbeat received (seq={frame.seq})")
        else:
            log.warning(f"[{self.link_id}] Unknown frame type: {frame.frame_type}")
    
    def _on_user_data_received(self, data: bytes) -> None:
        """用户数据回调"""
        if self.pty_master >= 0:
            try:
                os.write(self.pty_master, data)
            except OSError:
                pass
    
    def _check_heartbeat_timeout(self) -> None:
        """检查心跳超时"""
        now = time.monotonic()
        time_since_heartbeat = now - self._last_heartbeat_time
        
        if time_since_heartbeat >= HEARTBEAT_TIMEOUT:
            # 检查是否有数据活动
            time_since_data = now - self._last_data_activity
            if time_since_data < HEARTBEAT_TIMEOUT:
                # 有数据活动，重置心跳时间继续等待
                log.debug(f"[{self.link_id}] Heartbeat timeout but data activity detected")
                self._last_heartbeat_time = now
                return
            
            # 既没有心跳也没有数据活动，无法判断故障原因（链路层/系统层/软件层）
            self._update_state("Unknown")
            self._last_heartbeat_time = now  # 重置以避免持续触发
    
    def _update_state(self, oper_state: str) -> None:
        """更新 Redis 状态（仅在状态变化时更新）"""
        if oper_state == self._current_oper_state:
            return
        
        self._current_oper_state = oper_state
        timestamp = str(int(time.time()))
        
        try:
            self.state_table.set(
                self.link_id,
                [("oper_state", oper_state), ("last_state_change", timestamp)]
            )
            log.info(f"[{self.link_id}] State: {oper_state}")
        except Exception as e:
            log.error(f"[{self.link_id}] Failed to update state: {e}")
    
    def _cleanup_state(self) -> None:
        """清理 STATE_DB 状态"""
        try:
            # 只删除 console-monitor 管理的字段
            self.state_table.hdel(self.link_id, "oper_state")
            self.state_table.hdel(self.link_id, "last_state_change")
            log.info(f"[{self.link_id}] STATE_DB cleaned up")
        except Exception as e:
            log.error(f"[{self.link_id}] Failed to cleanup STATE_DB: {e}")
    
    def _create_symlink(self) -> None:
        """创建 PTY 符号链接"""
        self.pty_symlink = f"{self.pty_symlink_prefix}{self.link_id}"
        try:
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
    
    @staticmethod
    def _calculate_filter_timeout(baud: int, multiplier: int = 3) -> float:
        """根据波特率计算帧过滤超时时间"""
        char_time = 10.0 / baud
        return char_time * MAX_FRAME_BUFFER_SIZE * multiplier



# ============================================================
# DCE 服务
# ============================================================

class DCEService:
    """
    DCE 侧服务：管理多个串口代理
    
    使用 ConfigDBConnector 的 subscribe/listen 模式监听 CONFIG_DB 变化，
    符合 SONiC 守护进程规范。
    每个 SerialProxy 拥有独立的 Redis 连接，避免多线程竞争。
    """
    
    def __init__(self):
        self.config_db: Optional[ConfigDBConnector] = None
        
        self.proxies: Dict[str, SerialProxy] = {}
        self.running: bool = False
        self.pty_symlink_prefix: str = ""
    
    def start(self) -> bool:
        """启动服务"""
        try:
            # 连接 CONFIG_DB（使用 ConfigDBConnector）
            self.config_db = ConfigDBConnector()
            self.config_db.connect(wait_for_init=True, retry_on=True)
            log.info("ConfigDB connected")
            
            # 读取 PTY 符号链接前缀
            self.pty_symlink_prefix = get_pty_symlink_prefix()
            log.info(f"PTY symlink prefix: {self.pty_symlink_prefix}")
            
            self.running = True
            log.info("DCE service initialized")
            return True
            
        except Exception as e:
            log.error(f"Failed to start DCE service: {e}")
            return False
    
    def register_callbacks(self) -> None:
        """注册配置变更回调"""
        
        def make_callback(func):
            """创建符合 ConfigDBConnector 格式的回调包装器"""
            def callback(table, key, data):
                if data is None:
                    op = "DEL"
                    data = {}
                else:
                    op = "SET"
                return func(key, op, data)
            return callback
        
        # 订阅 CONSOLE_PORT 表变更
        self.config_db.subscribe(CONSOLE_PORT_TABLE, 
                                  make_callback(self.console_port_handler))
        
        # 订阅 CONSOLE_SWITCH 表变更（用于检查功能开关）
        self.config_db.subscribe(CONSOLE_SWITCH_TABLE,
                                  make_callback(self.console_switch_handler))
        
        log.info("Callbacks registered")
    
    def run(self) -> None:
        """主循环：使用 ConfigDBConnector.listen() 监听配置变更"""
        try:
            # listen() 会阻塞，并在收到配置变更时调用注册的回调
            # init_data_handler 会在 listen 开始时被调用，传入所有订阅表的初始数据
            self.config_db.listen(init_data_handler=self._load_initial_config)
        except KeyboardInterrupt:
            log.info("Received keyboard interrupt")
        except Exception as e:
            if self.running:
                log.error(f"DCE listen error: {e}")
    
    def stop(self) -> None:
        """停止服务"""
        self.running = False
        
        # 停止所有代理
        for proxy in self.proxies.values():
            proxy.stop()
        self.proxies.clear()
        
        log.info("DCE service stopped")
    
    def _load_initial_config(self, init_data: dict) -> None:
        """
        加载初始配置
        
        Args:
            init_data: 包含所有订阅表初始数据的字典
                       格式: {table_name: {key: {field: value, ...}, ...}, ...}
        """
        log.info(f"Loading initial config: {list(init_data.keys())}")
        
        # 执行初始同步
        self._sync()
    
    def console_port_handler(self, key: str, op: str, data: dict) -> None:
        """
        CONSOLE_PORT 表变更处理器
        
        Args:
            key: 表项的 key（如 "1", "2" 等端口号）
            op: 操作类型 "SET" 或 "DEL"
            data: 表项数据
        """
        log.info(f"CONSOLE_PORT change: key={key}, op={op}, data={data}")
        self._sync()
    
    def console_switch_handler(self, key: str, op: str, data: dict) -> None:
        """
        CONSOLE_SWITCH 表变更处理器
        
        Args:
            key: 表项的 key
            op: 操作类型 "SET" 或 "DEL"
            data: 表项数据
        """
        log.info(f"CONSOLE_SWITCH change: key={key}, op={op}, data={data}")
        self._sync()
    
    def _check_feature_enabled(self) -> bool:
        """检查 console switch 功能是否启用"""
        try:
            entry = self.config_db.get_entry(CONSOLE_SWITCH_TABLE, "console_mgmt")
            if entry:
                enabled = entry.get("enabled", "")
                if enabled == "yes":
                    return True
            log.warning("Console switch feature is disabled")
            return False
        except Exception as e:
            log.error(f"Failed to check feature status: {e}")
            return False
    
    def _get_all_configs(self) -> Dict[str, dict]:
        """获取所有串口配置"""
        configs = {}
        try:
            table_data = self.config_db.get_table(CONSOLE_PORT_TABLE)
            for key, entry in table_data.items():
                # ConfigDBConnector 返回的 key 可能是 tuple
                key_str = str(key) if not isinstance(key, str) else key
                configs[key_str] = {
                    "baud": int(entry.get("baud_rate", 9600)),
                    "device": f"/dev/C0-{key_str}",
                }
        except Exception as e:
            log.error(f"Failed to get configs: {e}")
        return configs
    
    def _sync(self) -> None:
        """同步配置和代理"""
        # 检查功能是否启用
        if not self._check_feature_enabled():
            # 功能未启用，停止所有代理
            if self.proxies:
                log.info("Feature disabled, stopping all proxies")
                for proxy in self.proxies.values():
                    proxy.stop()
                self.proxies.clear()
            return
        
        # 获取配置
        redis_configs = self._get_all_configs()
        redis_ids = set(redis_configs.keys())
        current_ids = set(self.proxies.keys())
        
        # 删除不在配置中的代理
        for link_id in current_ids - redis_ids:
            self.proxies[link_id].stop()
            del self.proxies[link_id]
        
        # 添加新的代理
        for link_id in redis_ids - current_ids:
            cfg = redis_configs[link_id]
            proxy = SerialProxy(
                link_id, cfg["device"], cfg["baud"], self.pty_symlink_prefix
            )
            if proxy.start():
                self.proxies[link_id] = proxy
        
        # 更新配置变化的代理
        for link_id in redis_ids & current_ids:
            cfg = redis_configs[link_id]
            proxy = self.proxies[link_id]
            if proxy.baud != cfg["baud"]:
                proxy.stop()
                new_proxy = SerialProxy(
                    link_id, cfg["device"], cfg["baud"], self.pty_symlink_prefix
                )
                if new_proxy.start():
                    self.proxies[link_id] = new_proxy
        
        log.info(f"Sync complete: {len(self.proxies)} proxies active")


# ============================================================
# DTE 服务
# ============================================================

class DTEService:
    """
    DTE 侧服务：发送心跳帧
    
    使用 ConfigDBConnector 的 subscribe/listen 模式监听 CONFIG_DB 变化，
    符合 SONiC 守护进程规范。
    """
    
    def __init__(self, tty_name: str, baud: int):
        self.tty_name = tty_name
        self.baud = baud
        self.device_path = f"/dev/{tty_name}"
        
        self.config_db: Optional[ConfigDBConnector] = None
        
        self.ser_fd: int = -1
        self.running: bool = False
        self.enabled: bool = False
        self.seq: int = 0
        
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._heartbeat_stop: threading.Event = threading.Event()
    
    def start(self) -> bool:
        """启动服务"""
        try:
            # 打开串口
            self.ser_fd = os.open(self.device_path, os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK)
            
            # 连接 CONFIG_DB（使用 ConfigDBConnector）
            self.config_db = ConfigDBConnector()
            self.config_db.connect(wait_for_init=True, retry_on=True)
            log.info("ConfigDB connected")
            
            self.running = True
            log.info(f"DTE service initialized: {self.device_path}")
            return True
            
        except Exception as e:
            log.error(f"Failed to start DTE service: {e}")
            return False
    
    def register_callbacks(self) -> None:
        """注册配置变更回调"""
        
        def make_callback(func):
            """创建符合 ConfigDBConnector 格式的回调包装器"""
            def callback(table, key, data):
                if data is None:
                    op = "DEL"
                    data = {}
                else:
                    op = "SET"
                return func(key, op, data)
            return callback
        
        # 订阅 CONSOLE_SWITCH 表变更
        self.config_db.subscribe(CONSOLE_SWITCH_TABLE,
                                  make_callback(self.console_switch_handler))
        
        log.info("Callbacks registered")
    
    def run(self) -> None:
        """主循环：使用 ConfigDBConnector.listen() 监听配置变更"""
        try:
            # listen() 会阻塞，并在收到配置变更时调用注册的回调
            self.config_db.listen(init_data_handler=self._load_initial_config)
        except KeyboardInterrupt:
            log.info("Received keyboard interrupt")
        except Exception as e:
            if self.running:
                log.error(f"DTE listen error: {e}")
    
    def stop(self) -> None:
        """停止服务"""
        self.running = False
        self._stop_heartbeat()
        
        if self.ser_fd >= 0:
            try:
                os.close(self.ser_fd)
            except:
                pass
            self.ser_fd = -1
        
        log.info("DTE service stopped")
    
    def _load_initial_config(self, init_data: dict) -> None:
        """
        加载初始配置
        
        Args:
            init_data: 包含所有订阅表初始数据的字典
        """
        log.info(f"Loading initial config: {list(init_data.keys())}")
        
        # 检查初始 enabled 状态
        self.enabled = self._check_enabled()
        log.info(f"Initial enabled state: {self.enabled}")
        
        # 如果启用，启动心跳线程
        if self.enabled:
            self._start_heartbeat()
    
    def console_switch_handler(self, key: str, op: str, data: dict) -> None:
        """
        CONSOLE_SWITCH 表变更处理器
        
        Args:
            key: 表项的 key
            op: 操作类型 "SET" 或 "DEL"
            data: 表项数据
        """
        log.info(f"CONSOLE_SWITCH change: key={key}, op={op}, data={data}")
        
        # 检查 enabled 状态
        new_enabled = self._check_enabled()
        if new_enabled != self.enabled:
            log.info(f"Enabled state changed: {self.enabled} -> {new_enabled}")
            self.enabled = new_enabled
            
            if self.enabled:
                self._start_heartbeat()
            else:
                self._stop_heartbeat()
    
    def _check_enabled(self) -> bool:
        """检查 controlled_device 的 enabled 字段"""
        try:
            entry = self.config_db.get_entry(CONSOLE_SWITCH_TABLE, "controlled_device")
            if entry:
                return entry.get("enabled", "") == "yes"
            return False
        except Exception as e:
            log.warning(f"Failed to check enabled status: {e}")
            return False
    
    def _start_heartbeat(self) -> None:
        """启动心跳线程"""
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return
        
        self._heartbeat_stop.clear()
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()
        log.info("Heartbeat thread started")
    
    def _stop_heartbeat(self) -> None:
        """停止心跳线程"""
        self._heartbeat_stop.set()
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=2.0)
        self._heartbeat_thread = None
        log.info("Heartbeat thread stopped")
    
    def _heartbeat_loop(self) -> None:
        """心跳发送循环"""
        while not self._heartbeat_stop.is_set():
            self._send_heartbeat()
            self._heartbeat_stop.wait(HEARTBEAT_INTERVAL)
    
    def _send_heartbeat(self) -> None:
        """发送心跳帧"""
        if self.ser_fd < 0:
            return
        
        frame = Frame.create_heartbeat(self.seq)
        frame_bytes = frame.build()
        
        try:
            os.write(self.ser_fd, frame_bytes)
            log.debug(f"Sent heartbeat (seq={self.seq})")
            log_binary_data(frame_bytes, "DTE→Serial")
            self.seq = (self.seq + 1) % 256
        except Exception as e:
            log.error(f"Failed to send heartbeat: {e}")


# ============================================================
# 主程序入口
# ============================================================

def signal_handler(signum, frame):
    """全局信号处理器"""
    log.info(f"Received signal {signum}")
    raise SystemExit(0)


def run_dce() -> int:
    """DCE 服务入口"""
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGHUP, signal_handler)
    
    service = DCEService()
    
    if not service.start():
        return 1
    
    try:
        service.register_callbacks()
        service.run()
    except SystemExit:
        pass
    finally:
        service.stop()
    
    return 0


def run_dte() -> int:
    """DTE 服务入口"""
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGHUP, signal_handler)
    
    parser = argparse.ArgumentParser(description='Console Monitor DTE Service')
    parser.add_argument('tty_name', nargs='?', default=None, help='TTY device name')
    parser.add_argument('baud', nargs='?', type=int, default=None, help='Baud rate')
    args = parser.parse_args()
    
    # 确定 TTY 和波特率
    if args.tty_name:
        tty_name = args.tty_name
        baud = args.baud if args.baud else DEFAULT_BAUD
        log.info(f"Using command line args: tty={tty_name}, baud={baud}")
    else:
        try:
            tty_name, baud = parse_proc_cmdline()
        except ValueError as e:
            log.error(f"Failed to get serial config: {e}")
            return 1
    
    service = DTEService(tty_name, baud)
    
    if not service.start():
        return 1
    
    try:
        service.register_callbacks()
        service.run()
    except SystemExit:
        pass
    finally:
        service.stop()
    
    return 0


def main():
    """
    统一入口：根据命令行参数决定运行模式
    
    Usage:
        console-monitor dce    # 运行 DCE 服务
        console-monitor dte    # 运行 DTE 服务
    """
    if len(sys.argv) < 2:
        print("Usage: console-monitor <dce|dte> [args...]")
        print("  dce: Run DCE (Console Server) service")
        print("  dte: Run DTE (SONiC Switch) service")
        sys.exit(1)
    
    mode = sys.argv[1].lower()
    sys.argv = sys.argv[1:]  # 移除 mode 参数，让后续 argparse 处理
    
    if mode == "dce":
        sys.exit(run_dce())
    elif mode == "dte":
        sys.exit(run_dte())
    else:
        print(f"Unknown mode: {mode}")
        print("Use 'dce' or 'dte'")
        sys.exit(1)


if __name__ == "__main__":
    main()
