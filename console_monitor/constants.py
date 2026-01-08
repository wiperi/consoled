"""
常量定义

包含 console-monitor-dce 使用的全局常量。
"""

import termios

# Redis 配置
REDIS_HOST = "localhost"
REDIS_PORT = 6379
REDIS_DB = 4          # 配置数据库
STATE_DB = 6          # 状态数据库
KEY_PATTERN = "CONSOLE_PORT|*"

# 超时配置
FILTER_TIMEOUT = 0.5       # 帧过滤超时（秒）
HEARTBEAT_TIMEOUT = 15.0   # 心跳超时（秒）

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
