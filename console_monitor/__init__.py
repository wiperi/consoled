"""
Console Monitor Package

提供 DCE 和 DTE 两种服务模式：
- DCE: Console Server 侧，创建 PTY 代理，过滤心跳帧
- DTE: SONiC Switch 侧，发送心跳帧
"""

from .console_monitor import (
    # 帧协议
    Frame,
    FrameFilter,
    FrameType,
    SpecialChar,
    PROTOCOL_VERSION,
    SOF_SEQUENCE,
    EOF_SEQUENCE,
    crc16_modbus,
    escape_data,
    unescape_data,
    
    # 服务
    DCEService,
    DTEService,
    SerialProxy,
    
    # 常量
    HEARTBEAT_INTERVAL,
    HEARTBEAT_TIMEOUT,
    MAX_FRAME_BUFFER_SIZE,
    
    # 入口函数
    run_dce,
    run_dte,
    main,
)

__all__ = [
    # 帧协议
    "Frame",
    "FrameFilter",
    "FrameType",
    "SpecialChar",
    "PROTOCOL_VERSION",
    "SOF_SEQUENCE",
    "EOF_SEQUENCE",
    "crc16_modbus",
    "escape_data",
    "unescape_data",
    
    # 服务
    "DCEService",
    "DTEService",
    "SerialProxy",
    
    # 常量
    "HEARTBEAT_INTERVAL",
    "HEARTBEAT_TIMEOUT",
    "MAX_FRAME_BUFFER_SIZE",
    
    # 入口函数
    "run_dce",
    "run_dte",
    "main",
]
