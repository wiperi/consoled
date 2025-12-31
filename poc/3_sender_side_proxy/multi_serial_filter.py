#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Multi-serial proxy with string filtering.

为多个串口同时提供过滤代理服务：
- 每个串口有独立的 PTY 和过滤器
- 使用单个 selector 监控所有 fd（epoll 在 Linux 上）
- 支持配置文件或命令行指定多个串口

架构:
    Serial1 <--> PTY1 (user1 connects here)
    Serial2 <--> PTY2 (user2 connects here)
    ...
    All managed by one event loop with epoll
"""

import os
import sys
import time
import json
import termios
import tty
import fcntl
import selectors
import signal
import argparse
from dataclasses import dataclass, field
from typing import Dict, Optional

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


class StringFilter:
    """高效字符串过滤器，使用 KMP 状态机。"""
    
    def __init__(self, pattern: bytes, timeout: float = 0.1):
        if not pattern:
            raise ValueError("Pattern cannot be empty")
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
    
    def get_timeout_remaining(self) -> float:
        if not self.buffer or self.last_data_time <= 0:
            return -1.0
        remaining = self.timeout - (time.monotonic() - self.last_data_time)
        return max(0.0, remaining)


@dataclass
class SerialChannel:
    """表示一个串口通道的所有状态。"""
    name: str                      # 通道名称，如 "console-1"
    serial_dev: str                # 串口设备路径
    baud: int                      # 波特率
    filter_pattern: bytes          # 过滤的字符串
    filter_timeout: float = 0.1    # 过滤超时
    
    # 运行时状态（初始化后填充）
    ser_fd: int = -1
    pty_master: int = -1
    pty_slave: int = -1
    pty_slave_name: str = ""
    string_filter: Optional[StringFilter] = None
    
    def open(self) -> None:
        """打开串口和 PTY。"""
        # 创建 PTY
        self.pty_master, self.pty_slave = os.openpty()
        self.pty_slave_name = os.ttyname(self.pty_slave)
        
        # 打开串口
        self.ser_fd = os.open(self.serial_dev, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        
        # 配置
        configure_serial(self.ser_fd, self.baud)
        set_raw_noecho(self.pty_master)
        set_raw_noecho(self.pty_slave)
        set_nonblocking(self.pty_master)
        set_nonblocking(self.ser_fd)
        
        # 创建过滤器
        self.string_filter = StringFilter(self.filter_pattern, self.filter_timeout)
    
    def close(self) -> None:
        """关闭所有 fd。"""
        for fd in (self.ser_fd, self.pty_master, self.pty_slave):
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self.ser_fd = self.pty_master = self.pty_slave = -1


class MultiSerialProxy:
    """
    管理多个串口通道的代理服务。
    
    使用单个 epoll/select 监控所有 fd，高效处理多路 I/O。
    """
    
    def __init__(self, channels: list[SerialChannel], verbose: bool = False):
        self.channels = channels
        self.verbose = verbose
        self.selector = selectors.DefaultSelector()
        self.stop = False
        
        # fd -> channel 的映射
        self.fd_to_channel: Dict[int, SerialChannel] = {}
        # fd -> 类型 ("ser" 或 "pty")
        self.fd_type: Dict[int, str] = {}
    
    def _log(self, channel: SerialChannel, msg: str) -> None:
        if self.verbose:
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] [{channel.name}] {msg}")
            sys.stdout.flush()
    
    def start(self) -> None:
        """启动所有通道。"""
        print(f"[proxy] Starting {len(self.channels)} channels...")
        print(f"[proxy] Using selector: {type(self.selector).__name__}")
        
        for ch in self.channels:
            try:
                ch.open()
                
                # 注册到 selector
                self.selector.register(ch.ser_fd, selectors.EVENT_READ)
                self.selector.register(ch.pty_master, selectors.EVENT_READ)
                
                # 建立映射
                self.fd_to_channel[ch.ser_fd] = ch
                self.fd_to_channel[ch.pty_master] = ch
                self.fd_type[ch.ser_fd] = "ser"
                self.fd_type[ch.pty_master] = "pty"
                
                print(f"  [{ch.name}] {ch.serial_dev} @ {ch.baud} -> {ch.pty_slave_name}")
                print(f"           Filter: {ch.filter_pattern!r}, timeout: {ch.filter_timeout}s")
                
            except Exception as e:
                print(f"  [{ch.name}] FAILED: {e}", file=sys.stderr)
                ch.close()
        
        print(f"[proxy] Ready. Total fds monitored: {len(self.fd_to_channel)}")
        sys.stdout.flush()
    
    def _get_min_timeout(self) -> float:
        """获取所有通道中最小的超时剩余时间。"""
        min_timeout = 0.5
        for ch in self.channels:
            if ch.string_filter:
                remaining = ch.string_filter.get_timeout_remaining()
                if remaining >= 0:
                    min_timeout = min(min_timeout, remaining + 0.01)
        return min_timeout
    
    def _check_all_timeouts(self) -> None:
        """检查所有通道的超时。"""
        for ch in self.channels:
            if ch.string_filter and ch.pty_master >= 0:
                timeout_data = ch.string_filter.check_timeout()
                if timeout_data:
                    self._log(ch, f"TIMEOUT flush: {timeout_data!r}")
                    try:
                        os.write(ch.pty_master, timeout_data)
                    except OSError:
                        pass
    
    def run(self) -> None:
        """主事件循环。"""
        while not self.stop:
            timeout = self._get_min_timeout()
            events = self.selector.select(timeout=timeout)
            
            # 检查超时
            self._check_all_timeouts()
            
            for key, _ in events:
                fd = key.fd
                ch = self.fd_to_channel.get(fd)
                if not ch:
                    continue
                
                fd_type = self.fd_type.get(fd)
                
                if fd_type == "ser":
                    # 串口 -> PTY（需要过滤）
                    self._handle_serial_read(ch)
                elif fd_type == "pty":
                    # PTY -> 串口（直接转发）
                    self._handle_pty_read(ch)
    
    def _handle_serial_read(self, ch: SerialChannel) -> None:
        """处理串口读取，过滤后转发到 PTY。"""
        try:
            data = os.read(ch.ser_fd, 4096)
        except BlockingIOError:
            return
        if not data:
            return
        
        self._log(ch, f"SER->PTY raw: {len(data)} bytes")
        
        filtered = ch.string_filter.process(data)
        
        if filtered != data:
            self._log(ch, f"SER->PTY filtered: {len(filtered)} bytes")
        
        if filtered:
            try:
                os.write(ch.pty_master, filtered)
            except OSError as e:
                self._log(ch, f"PTY write error: {e}")
    
    def _handle_pty_read(self, ch: SerialChannel) -> None:
        """处理 PTY 读取，直接转发到串口。"""
        try:
            data = os.read(ch.pty_master, 4096)
        except BlockingIOError:
            return
        if not data:
            return
        
        self._log(ch, f"PTY->SER: {len(data)} bytes")
        
        try:
            os.write(ch.ser_fd, data)
        except OSError as e:
            self._log(ch, f"SER write error: {e}")
    
    def shutdown(self) -> None:
        """关闭所有通道。"""
        print("\n[proxy] Shutting down...")
        
        for ch in self.channels:
            # 刷新过滤器缓冲区
            if ch.string_filter and ch.pty_master >= 0:
                remaining = ch.string_filter.flush()
                if remaining:
                    try:
                        os.write(ch.pty_master, remaining)
                    except OSError:
                        pass
            ch.close()
        
        self.selector.close()
        print("[proxy] Done.")


def load_config(config_path: str) -> list[SerialChannel]:
    """
    从 JSON 配置文件加载通道配置。
    
    配置格式:
    {
        "channels": [
            {
                "name": "console-1",
                "device": "/dev/C0-1",
                "baud": 9600,
                "filter": "password",
                "timeout": 0.1
            },
            ...
        ]
    }
    """
    with open(config_path, 'r') as f:
        config = json.load(f)
    
    channels = []
    for ch_cfg in config.get("channels", []):
        channels.append(SerialChannel(
            name=ch_cfg.get("name", ch_cfg["device"]),
            serial_dev=ch_cfg["device"],
            baud=ch_cfg.get("baud", 9600),
            filter_pattern=ch_cfg.get("filter", "").encode('utf-8'),
            filter_timeout=ch_cfg.get("timeout", 0.1),
        ))
    
    return channels


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Multi-serial proxy with string filtering",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # 单个串口
  %(prog)s -d /dev/C0-1 -b 9600 -f "password"
  
  # 多个串口（命令行）
  %(prog)s -d /dev/C0-1 -d /dev/C0-2 -b 9600 -f "password"
  
  # 使用配置文件
  %(prog)s -c config.json
        """
    )
    parser.add_argument("-d", "--device", action="append", dest="devices",
                        help="Serial device (can specify multiple)")
    parser.add_argument("-b", "--baud", type=int, default=9600,
                        help="Baud rate (default: 9600)")
    parser.add_argument("-f", "--filter", default="",
                        help="String to filter out")
    parser.add_argument("-t", "--timeout", type=float, default=0.1,
                        help="Filter timeout in seconds (default: 0.1)")
    parser.add_argument("-c", "--config", type=str,
                        help="JSON config file for multiple channels")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Verbose output")
    
    args = parser.parse_args()
    
    # 加载通道配置
    if args.config:
        channels = load_config(args.config)
    elif args.devices:
        channels = []
        for i, dev in enumerate(args.devices):
            channels.append(SerialChannel(
                name=f"ch-{i}",
                serial_dev=dev,
                baud=args.baud,
                filter_pattern=args.filter.encode('utf-8') if args.filter else b"",
                filter_timeout=args.timeout,
            ))
    else:
        parser.error("Must specify -d/--device or -c/--config")
    
    if not channels:
        print("No channels configured", file=sys.stderr)
        return 1
    
    # 过滤掉没有 pattern 的通道（或者允许空 pattern 表示不过滤）
    for ch in channels:
        if not ch.filter_pattern:
            ch.filter_pattern = b"\xff\xff\xff\xff"  # 不可能匹配的模式
    
    proxy = MultiSerialProxy(channels, verbose=args.verbose)
    
    def handle_signal(sig, frame):
        proxy.stop = True
    
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    
    try:
        proxy.start()
        proxy.run()
    finally:
        proxy.shutdown()
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
