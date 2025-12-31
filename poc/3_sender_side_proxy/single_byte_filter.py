#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sender-side proxy:
- Creates a PTY for user (picocom connects to PTY slave)
- Bridges SERIAL <-> PTY
- Filters out byte 'h' (0x68) only in SERIAL -> USER direction
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

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("serial_dev", help="e.g. /dev/C0-1 or /dev/ttyS0")
    parser.add_argument("baud", nargs="?", type=int, default=115200, help="e.g. 9600 (default 115200)")
    args = parser.parse_args()

    serial_dev = args.serial_dev
    baud = args.baud

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

    drop = b"h"  # 只过滤 ASCII 'h' (0x68)

    try:
        while not stop:
            events = sel.select(timeout=0.5)
            for key, _mask in events:
                if key.data == "ser":
                    try:
                        data = os.read(ser_fd, 4096)
                    except BlockingIOError:
                        continue
                    if not data:
                        continue

                    dump_bytes("SER->PROXY (raw)", data)

                    filtered = data.replace(drop, b"")
                    if filtered != data:
                        dump_bytes("SER->PROXY (after filter)", filtered)

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
        for fd in (ser_fd, pty_master, pty_slave):
            try:
                os.close(fd)
            except OSError:
                pass

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
