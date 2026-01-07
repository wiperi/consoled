#!/usr/bin/env python3
"""
Frame Protocol Implementation

基于 HLD 3.1 节帧结构设计实现帧的构造和解析。

帧格式:
+----------+--------+-----+------+------+--------+---------+-------+----------+
| SOF x 3  | Version| Seq | Flag | Type | Length | Payload | CRC16 | EOF x 3  |
+----------+--------+-----+------+------+--------+---------+-------+----------+
|    3B    |   1B   | 1B  |  1B  |  1B  |   1B   |   N B   |  2B   |    3B    |
+----------+--------+-----+------+------+--------+---------+-------+----------+

特殊字符:
- SOF (0x01): 帧起始符
- EOF (0x1B): 帧结束符
- DLE (0x10): 转义字符

转义规则 (帧内容中):
- 0x01 -> 0x10 0x01
- 0x1B -> 0x10 0x1B
- 0x10 -> 0x10 0x10
"""

from dataclasses import dataclass
from enum import IntEnum
from typing import Optional, Callable


# ============================================================
# 常量定义
# ============================================================

class SpecialChar(IntEnum):
    """特殊字符定义"""
    SOF = 0x01  # Start of Frame
    EOF = 0x1B  # End of Frame
    DLE = 0x10  # Data Link Escape


class FrameType(IntEnum):
    """帧类型定义"""
    HEARTBEAT = 0x01
    # 0x02-0xFF 保留


# 协议版本
PROTOCOL_VERSION = 0x01

# 帧头帧尾长度
SOF_LEN = 3
EOF_LEN = 3

# Buffer 大小限制 (不含帧头帧尾)
MAX_FRAME_BUFFER_SIZE = 64

# 帧头帧尾序列
SOF_SEQUENCE = bytes([SpecialChar.SOF] * SOF_LEN)
EOF_SEQUENCE = bytes([SpecialChar.EOF] * EOF_LEN)


# ============================================================
# CRC16 计算
# ============================================================

def crc16_modbus(data: bytes) -> int:
    """
    CRC-16/MODBUS 算法
    
    多项式: 0x8005
    初始值: 0xFFFF
    反射输入/输出: True
    """
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001  # 0x8005 reflected
            else:
                crc >>= 1
    return crc


# ============================================================
# 转义处理
# ============================================================

def escape_data(data: bytes) -> bytes:
    """
    对数据进行转义
    
    0x01 (SOF) -> 0x10 0x01
    0x1B (EOF) -> 0x10 0x1B
    0x10 (DLE) -> 0x10 0x10
    """
    result = bytearray()
    for byte in data:
        if byte in (SpecialChar.SOF, SpecialChar.EOF, SpecialChar.DLE):
            result.append(SpecialChar.DLE)
        result.append(byte)
    return bytes(result)


def unescape_data(data: bytes) -> bytes:
    """
    对数据进行去转义
    
    0x10 0x01 -> 0x01
    0x10 0x1B -> 0x1B
    0x10 0x10 -> 0x10
    """
    result = bytearray()
    i = 0
    while i < len(data):
        if data[i] == SpecialChar.DLE and i + 1 < len(data):
            result.append(data[i + 1])
            i += 2
        else:
            result.append(data[i])
            i += 1
    return bytes(result)


# ============================================================
# Frame 类
# ============================================================

@dataclass
class Frame:
    """
    帧数据结构
    
    职责:
    1. 根据构造函数参数构造帧的二进制序列
    2. 尝试将一段二进制序列解析成帧
    """
    version: int = PROTOCOL_VERSION
    seq: int = 0
    flag: int = 0x00
    frame_type: int = FrameType.HEARTBEAT
    payload: bytes = b""
    
    def build(self) -> bytes:
        """
        构造完整的帧二进制序列
        
        流程:
        1. 构造帧头 (SOF x 3)
        2. 构造帧内容 (Version + Seq + Flag + Type + Length + Payload)
        3. 对整个帧内容进行转义
        4. 计算 CRC16 (基于转义后的内容)
        5. 对 CRC16 进行转义
        6. 构造帧尾 (EOF x 3)
        
        Returns:
            完整的帧二进制数据
        """
        # 构造帧内容 (不含 CRC，未转义)
        # Version(1) + Seq(1) + Flag(1) + Type(1) + Length(1) + Payload(N)
        content = bytes([
            self.version,
            self.seq & 0xFF,
            self.flag,
            self.frame_type,
            len(self.payload),  # 原始 payload 长度
        ]) + self.payload
        
        # 转义帧内容 (header + payload)
        escaped_content = escape_data(content)
        
        # 计算 CRC16 (基于转义后的内容)
        crc = crc16_modbus(escaped_content)
        crc_bytes = bytes([crc >> 8, crc & 0xFF])  # 大端序
        
        # 转义 CRC
        escaped_crc = escape_data(crc_bytes)
        
        # 组装完整帧
        return SOF_SEQUENCE + escaped_content + escaped_crc + EOF_SEQUENCE
    
    @classmethod
    def parse(cls, buffer: bytes) -> Optional['Frame']:
        """
        从 buffer 解析帧
        
        buffer 应该是去除帧头帧尾后的原始数据（包含转义字符）
        
        流程:
        1. 验证最小长度 (Version + Seq + Flag + Type + Length + CRC16 = 7 bytes)
        2. CRC16 校验 (使用 buffer 中的数据，不含最后 2-4 字节的 CRC)
        3. 校验通过后去转义提取数据
        
        Args:
            buffer: 去除帧头帧尾后的原始数据
            
        Returns:
            解析成功返回 Frame 对象，失败返回 None
        """
        # 最小长度检查: Version(1) + Seq(1) + Flag(1) + Type(1) + Length(1) + CRC(2) = 7
        # 但由于转义，CRC 可能变成 4 字节，所以最小是 7 字节
        if len(buffer) < 7:
            return None
        
        # 找到 CRC 的边界
        # CRC 是最后的 2 个字节（去转义后），但在 buffer 中可能是 2-4 字节
        # 需要从后向前解析
        
        # 策略: 尝试不同的 CRC 长度 (2, 3, 4 字节对应转义后的可能性)
        for crc_escaped_len in range(2, 5):
            if len(buffer) < 5 + crc_escaped_len:
                continue
            
            content_with_escape = buffer[:-crc_escaped_len]
            crc_escaped = buffer[-crc_escaped_len:]
            
            # 去转义 CRC
            crc_bytes = unescape_data(crc_escaped)
            if len(crc_bytes) != 2:
                continue
            
            # 计算期望的 CRC
            expected_crc = crc16_modbus(content_with_escape)
            received_crc = (crc_bytes[0] << 8) | crc_bytes[1]
            
            if expected_crc == received_crc:
                # CRC 校验通过，解析帧内容
                content = unescape_data(content_with_escape)
                
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
        
        return None
    
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


# ============================================================
# FrameFilter 类
# ============================================================

# 回调函数类型
FrameCallback = Callable[[Frame], None]
UserDataCallback = Callable[[bytes], None]


class FrameFilter:
    """
    帧过滤器
    
    职责:
    1. 接受字节流输入
    2. 从字节流中识别帧和用户数据
    3. 通过回调函数将帧和用户数据交给外部处理
    
    检测算法 (基于 HLD 3.3.3):
    - 收到 SOF 时: 将当前 buffer 作为用户数据发送，清空 buffer
    - 收到 EOF 时: 尝试解析 buffer 为帧
    - 收到 DLE 时: 下一个字节作为普通数据处理（转义）
    - 收到其他字节时: 追加到 buffer
    - buffer 溢出时: 将 buffer 作为用户数据发送
    - 超时时: 将 buffer 作为用户数据发送
    """
    
    def __init__(
        self,
        on_frame: Optional[FrameCallback] = None,
        on_user_data: Optional[UserDataCallback] = None,
    ):
        """
        Args:
            on_frame: 帧回调函数，当检测到有效帧时调用
            on_user_data: 用户数据回调函数，当有非帧数据时调用
        """
        self._on_frame = on_frame
        self._on_user_data = on_user_data
        self._buffer = bytearray()
        self._escape_next = False  # DLE 转义标志
    
    def process(self, data: bytes) -> None:
        """
        处理输入的字节流
        
        Args:
            data: 输入的字节数据
        """
        for byte in data:
            # 如果上一个字节是 DLE，当前字节作为普通数据处理
            if self._escape_next:
                self._buffer.append(byte)
                self._escape_next = False
                # 溢出保护
                if len(self._buffer) >= MAX_FRAME_BUFFER_SIZE:
                    self._flush_as_user_data()
            elif byte == SpecialChar.DLE:
                # 收到 DLE: 标记下一个字节需要转义
                self._buffer.append(byte)
                self._escape_next = True
            elif byte == SpecialChar.SOF:
                # 收到 SOF: 当前 buffer 是用户数据
                self._flush_as_user_data()
            elif byte == SpecialChar.EOF:
                # 收到 EOF: 尝试解析 buffer 为帧
                self._try_parse_frame()
            else:
                # 其他字节: 追加到 buffer
                self._buffer.append(byte)
                
                # 溢出保护
                if len(self._buffer) >= MAX_FRAME_BUFFER_SIZE:
                    self._flush_as_user_data()
    
    def on_timeout(self) -> None:
        """
        超时回调
        
        当一段时间内没有收到数据时调用，将 buffer 作为用户数据发送
        """
        self._flush_as_user_data()
    
    def flush(self) -> bytes:
        """
        刷新 buffer，返回剩余数据
        
        Returns:
            buffer 中的剩余数据
        """
        result = bytes(self._buffer)
        self._buffer.clear()
        self._escape_next = False
        return result
    
    def has_pending_data(self) -> bool:
        """检查是否有待处理的数据"""
        return len(self._buffer) > 0
    
    def _flush_as_user_data(self) -> None:
        """将 buffer 作为用户数据发送"""
        if self._buffer and self._on_user_data:
            self._on_user_data(bytes(self._buffer))
        self._buffer.clear()
        self._escape_next = False
    
    def _try_parse_frame(self) -> None:
        """尝试将 buffer 解析为帧"""
        if not self._buffer:
            self._escape_next = False
            return
        
        frame = Frame.parse(bytes(self._buffer))
        self._buffer.clear()
        self._escape_next = False
        
        if frame is not None:
            # 解析成功，回调帧
            if self._on_frame:
                self._on_frame(frame)
        else:
            # 解析失败，这不应该发生在正常情况下
            # 因为 SOF...EOF 之间的数据如果不是有效帧，
            # 说明数据被损坏了，丢弃即可
            pass


# ============================================================
# 便捷函数
# ============================================================

def build_heartbeat_frame(seq: int = 0) -> bytes:
    """构建心跳帧的便捷函数"""
    return Frame.create_heartbeat(seq).build()
