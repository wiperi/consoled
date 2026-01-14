"""
常量定义

包含 console-monitor 使用的全局常量。

注意：Redis 连接信息（db_id, socket_path, separator）由 SonicDBConfig 
从 /var/run/redis/sonic-db/database_config.json 动态获取，不在此处硬编码。
"""

import termios

# 超时配置
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
