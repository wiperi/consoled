#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sender-side proxy:
- Creates a PTY for user (picocom connects to PTY slave)
- Bridges SERIAL <-> PTY
- Filters out a specific string in SERIAL -> USER direction
- Uses state machine for efficient prefix matching
- Immediately passes through data when prefix match fails
- Prints EVERY read chunk (raw + after-filter) with ASCII view + HEX dump
"""

import os
import sys
import time
import termios
import tty
import fcntl
import selectors
import signal
import argparse
from typing import Tuple

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
    # 设为 raw，避免行规整、信号、回显等干扰
    tty.setraw(fd, when=termios.TCSANOW)
    attrs = termios.tcgetattr(fd)
    attrs[3] &= ~(termios.ECHO | termios.ECHONL)  # lflag
    termios.tcsetattr(fd, termios.TCSANOW, attrs)

def configure_serial(fd: int, baud: int) -> None:
    attrs = termios.tcgetattr(fd)

    # iflag
    attrs[0] &= ~(termios.IGNBRK | termios.BRKINT | termios.PARMRK |
                  termios.ISTRIP | termios.INLCR | termios.IGNCR |
                  termios.ICRNL | termios.IXON)
    # oflag
    attrs[1] &= ~termios.OPOST
    # cflag: 8N1 + enable receiver + local
    attrs[2] &= ~(termios.CSIZE | termios.PARENB)
    attrs[2] |= (termios.CS8 | termios.CREAD | termios.CLOCAL)
    # lflag
    attrs[3] &= ~(termios.ECHO | termios.ECHONL | termios.ICANON |
                  termios.ISIG | termios.IEXTEN)

    # VMIN/VTIME：配合 non-blocking + select，这里设 0/0
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 0

    if baud not in BAUD_MAP:
        raise ValueError(f"Unsupported baud {baud}. Use one of: {sorted(BAUD_MAP.keys())}")

    speed = BAUD_MAP[baud]
    # 兼容写法：直接设置 ispeed/ospeed（避免 cfsetispeed/cfsetospeed 不存在）
    attrs[4] = speed  # ispeed
    attrs[5] = speed  # ospeed

    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    termios.tcflush(fd, termios.TCIOFLUSH)

def _ascii_view(b: bytes) -> str:
    # 可读视图：可打印 ASCII 原样显示，其它用 '.'
    return ''.join(chr(x) if 32 <= x <= 126 else '.' for x in b)

def dump_bytes(tag: str, b: bytes) -> None:
    ts = time.strftime("%H:%M:%S")
    hx = b.hex(' ')  # 打印全部字节（很长也照打）
    av = _ascii_view(b)
    print(f"[{ts}] {tag} len={len(b)}")
    print(f"  ascii: {av}")
    print(f"  hex  : {hx}")
    sys.stdout.flush()


class StringFilter:
    """
    高效字符串过滤器，使用状态机实现前缀匹配。
    
    工作原理：
    1. 维护一个匹配状态（已匹配的前缀长度）
    2. 每个字节到来时检查是否能继续匹配
    3. 如果匹配失败，立即输出已缓存的字节并重置状态
    4. 如果完全匹配，丢弃整个目标字符串
    5. 如果超时未收到新数据，立即透传缓存的部分匹配
    
    性能特点：
    - O(1) 每字节处理时间
    - 最小化缓冲延迟：失败时立即透传
    - 内存占用固定：只缓存最多 len(pattern) - 1 个字节
    - 超时机制：避免部分匹配数据长时间滞留
    """
    
    def __init__(self, pattern: bytes, timeout: float = 0.1):
        """
        Args:
            pattern: 要过滤的字节串
            timeout: 超时时间（秒），buffer 非空时超过此时间未收到新数据则透传
        """
        if not pattern:
            raise ValueError("Pattern cannot be empty")
        self.pattern = pattern
        self.pattern_len = len(pattern)
        self.timeout = timeout
        # 预计算失败函数（KMP风格），用于快速回退
        self.failure = self._compute_failure(pattern)
        # 当前匹配位置
        self.match_pos = 0
        # 缓存的部分匹配字节
        self.buffer = bytearray()
        # 上次收到数据的时间戳
        self.last_data_time: float = 0.0
    
    @staticmethod
    def _compute_failure(pattern: bytes) -> list:
        """
        计算 KMP 失败函数。
        failure[i] 表示 pattern[0:i+1] 的最长真前缀（也是后缀）的长度。
        """
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
        """
        处理输入数据，返回过滤后的输出。
        
        返回的数据：
        - 确定不是目标字符串一部分的字节立即输出
        - 完全匹配目标字符串时，该字符串被丢弃
        - 部分匹配的字节暂时缓存，等待后续数据
        """
        output = bytearray()
        
        # 更新时间戳
        self.last_data_time = time.monotonic()
        
        for byte in data:
            # 尝试匹配当前字节
            while self.match_pos > 0 and byte != self.pattern[self.match_pos]:
                # 匹配失败，使用失败函数回退
                # 输出不能匹配的部分
                fail_len = self.match_pos - self.failure[self.match_pos - 1]
                output.extend(self.buffer[:fail_len])
                self.buffer = self.buffer[fail_len:]
                self.match_pos = self.failure[self.match_pos - 1]
            
            if byte == self.pattern[self.match_pos]:
                # 匹配成功，继续
                self.buffer.append(byte)
                self.match_pos += 1
                
                if self.match_pos == self.pattern_len:
                    # 完全匹配，丢弃整个 pattern
                    self.buffer.clear()
                    self.match_pos = 0
            else:
                # 完全不匹配，直接输出
                output.append(byte)
        
        return bytes(output)
    
    def flush(self) -> bytes:
        """
        刷新缓冲区，返回所有缓存的字节。
        在连接关闭或需要强制输出时调用。
        """
        result = bytes(self.buffer)
        self.buffer.clear()
        self.match_pos = 0
        return result
    
    def reset(self) -> None:
        """重置过滤器状态。"""
        self.buffer.clear()
        self.match_pos = 0
    
    def check_timeout(self) -> bytes:
        """
        检查是否超时，如果超时则透传缓存的数据。
        
        Returns:
            超时时返回缓存的数据，否则返回空 bytes
        """
        if self.buffer and self.last_data_time > 0:
            elapsed = time.monotonic() - self.last_data_time
            if elapsed >= self.timeout:
                result = bytes(self.buffer)
                self.buffer.clear()
                self.match_pos = 0
                self.last_data_time = 0.0
                return result
        return b""
    
    def has_pending_data(self) -> bool:
        """检查是否有待处理的缓存数据。"""
        return len(self.buffer) > 0
    
    def get_timeout_remaining(self) -> float:
        """
        获取距离超时还剩多少时间。
        
        Returns:
            剩余时间（秒），如果没有缓存数据则返回 -1
        """
        if not self.buffer or self.last_data_time <= 0:
            return -1.0
        elapsed = time.monotonic() - self.last_data_time
        remaining = self.timeout - elapsed
        return max(0.0, remaining)

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("serial_dev", help="e.g. /dev/C0-1 or /dev/ttyS0")
    parser.add_argument("baud", nargs="?", type=int, default=115200, help="e.g. 9600 (default 115200)")
    parser.add_argument("-f", "--filter", default="hello", help="String to filter out (default: 'hello')")
    parser.add_argument("-t", "--timeout", type=float, default=1, help="Timeout in seconds for partial match (default: 1)")
    args = parser.parse_args()

    serial_dev = args.serial_dev
    baud = args.baud
    filter_pattern = args.filter.encode('utf-8')
    filter_timeout = args.timeout

    # 1) 创建 PTY
    pty_master, pty_slave = os.openpty()
    pty_slave_name = os.ttyname(pty_slave)

    # 2) 打开串口
    ser_fd = os.open(serial_dev, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)

    # 3) 配置 raw
    configure_serial(ser_fd, baud)
    set_raw_noecho(pty_master)
    set_raw_noecho(pty_slave)

    set_nonblocking(pty_master)
    set_nonblocking(ser_fd)

    print(f"[proxy] PTY for user: {pty_slave_name}")
    print(f"[proxy] Connect with: picocom -b {baud} {pty_slave_name}")
    print(f"[proxy] Filtering string: {repr(filter_pattern)}")
    print(f"[proxy] Match timeout: {filter_timeout}s")
    sys.stdout.flush()

    stop = False
    def _stop(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    sel = selectors.DefaultSelector()
    sel.register(ser_fd, selectors.EVENT_READ, data="ser")
    sel.register(pty_master, selectors.EVENT_READ, data="pty")

    # 创建字符串过滤器
    string_filter = StringFilter(filter_pattern, timeout=filter_timeout)

    try:
        while not stop:
            # 动态调整 select 超时：如果有缓存数据，使用剩余超时时间
            select_timeout = string_filter.get_timeout_remaining()
            if select_timeout < 0:
                select_timeout = 0.5  # 默认超时
            else:
                select_timeout = min(select_timeout + 0.01, 0.5)  # 稍微多等一点，避免频繁唤醒
            
            events = sel.select(timeout=select_timeout)
            
            # 检查超时，透传缓存数据
            timeout_data = string_filter.check_timeout()
            if timeout_data:
                dump_bytes("TIMEOUT (flushing buffer)", timeout_data)
                try:
                    os.write(pty_master, timeout_data)
                except OSError:
                    pass
            
            for key, _mask in events:
                if key.data == "ser":
                    try:
                        data = os.read(ser_fd, 4096)
                    except BlockingIOError:
                        continue
                    if not data:
                        continue

                    dump_bytes("SER->PROXY (raw)", data)

                    # 使用字符串过滤器处理数据
                    filtered = string_filter.process(data)
                    if filtered != data:
                        dump_bytes("SER->PROXY (after filter)", filtered)
                        # 显示当前缓冲区状态（部分匹配）
                        if string_filter.buffer:
                            print(f"  [buffered: {bytes(string_filter.buffer)!r}]")
                            sys.stdout.flush()

                    if filtered:
                        os.write(pty_master, filtered)

                elif key.data == "pty":
                    try:
                        data = os.read(pty_master, 4096)
                    except BlockingIOError:
                        continue
                    if not data:
                        continue

                    dump_bytes("PTY->PROXY (user input)", data)

                    os.write(ser_fd, data)

    finally:
        # 刷新过滤器缓冲区
        remaining = string_filter.flush()
        if remaining:
            dump_bytes("FLUSH (remaining buffer)", remaining)
            try:
                os.write(pty_master, remaining)
            except OSError:
                pass
        
        for fd in (ser_fd, pty_master, pty_slave):
            try:
                os.close(fd)
            except OSError:
                pass

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
