"""
工具函数

包含串口配置、PTY 配置等工具函数。
"""

import os
import fcntl
import termios
import tty
import logging

from sonic_py_common import device_info
from .constants import BAUD_MAP

log = logging.getLogger(__name__)


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


def set_nonblocking(fd: int) -> None:
    """设置文件描述符为非阻塞模式"""
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)


def configure_serial(fd: int, baud: int) -> None:
    """配置串口参数（波特率、数据位、停止位等）"""
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
