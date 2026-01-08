#!/usr/bin/env python3
"""
Console Monitor DTE (Data Terminal Equipment)

每隔 5 秒向指定串口发送心跳帧

用法: console-monitor-dte.py <TTY_NAME>
  例如: console-monitor-dte.py ttyS0

放置位置: /usr/local/bin/console-monitor-dte
"""

import sys
import os
import time
import logging
import argparse
from pathlib import Path

# 添加模块搜索路径
sys.path.insert(0, '/usr/local/lib')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from frame import Frame, FrameType

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger(__name__)

# 心跳发送间隔（秒）
HEARTBEAT_INTERVAL = 5


class HeartbeatSender:
    """心跳帧发送器"""
    
    def __init__(self, device_path: str):
        """
        初始化心跳发送器
        
        Args:
            device_path: 串口设备路径，如 /dev/ttyS0
        """
        self.device_path = device_path
        self.device_fd = None
        self.seq = 0  # 序列号 (0-255 循环)
        
    def open_device(self) -> None:
        """打开串口设备"""
        device = Path(self.device_path)
        
        if not device.exists():
            raise FileNotFoundError(f"Device {self.device_path} does not exist")
        
        if not device.is_char_device():
            raise ValueError(f"Device {self.device_path} is not a character device")
        
        # 以二进制写模式打开设备
        self.device_fd = open(self.device_path, 'wb', buffering=0)
        log.info(f"Opened device: {self.device_path}")
    
    def close_device(self) -> None:
        """关闭串口设备"""
        if self.device_fd:
            self.device_fd.close()
            self.device_fd = None
            log.info(f"Closed device: {self.device_path}")
    
    def send_heartbeat(self) -> None:
        """发送一次心跳帧"""
        if not self.device_fd:
            raise RuntimeError("Device not opened")
        
        # 构造心跳帧（无 payload）
        frame = Frame(
            seq=self.seq,
            frame_type=FrameType.HEARTBEAT,
            payload=b""
        )
        
        # 构建帧的二进制数据
        frame_bytes = frame.build()
        
        try:
            # 写入设备
            self.device_fd.write(frame_bytes)
            self.device_fd.flush()
            
            # 记录日志（显示十六进制）
            hex_str = frame_bytes.hex().upper()
            log.info(f"Sent heartbeat frame (seq={self.seq}): {hex_str}")
            
            # 序列号递增（0-255 循环）
            self.seq = (self.seq + 1) % 256
            
        except Exception as e:
            log.error(f"Failed to write to {self.device_path}: {e}")
    
    def run(self) -> None:
        """主循环：每 5 秒发送一次心跳"""
        log.info(f"Starting heartbeat daemon on {self.device_path}")
        log.info(f"Heartbeat interval: {HEARTBEAT_INTERVAL}s")
        
        try:
            self.open_device()
            
            while True:
                self.send_heartbeat()
                time.sleep(HEARTBEAT_INTERVAL)
                
        except KeyboardInterrupt:
            log.info("Received interrupt signal, shutting down...")
        except Exception as e:
            log.error(f"Fatal error: {e}", exc_info=True)
            raise
        finally:
            self.close_device()


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description='Console Monitor DTE - Send heartbeat frames to serial device'
    )
    parser.add_argument(
        'tty_name',
        help='TTY device name (e.g., ttyS0)'
    )
    parser.add_argument(
        '--device-prefix',
        default='/dev',
        help='Device path prefix (default: /dev)'
    )
    
    args = parser.parse_args()
    
    # 构造设备路径
    device_path = f"{args.device_prefix}/{args.tty_name}"
    
    # 创建发送器并运行
    sender = HeartbeatSender(device_path)
    sender.run()

def run():
    main()

if __name__ == '__main__':
    main()
