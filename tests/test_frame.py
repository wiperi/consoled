#!/usr/bin/env python3
"""
Frame 和 FrameFilter 单元测试 (pytest)
"""

import sys
import os
import pytest

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from console_monitor.frame import (
    Frame, FrameFilter, FrameType,
    SpecialChar, PROTOCOL_VERSION,
    crc16_modbus, escape_data, unescape_data,
    build_heartbeat_frame,
    SOF_SEQUENCE, EOF_SEQUENCE,
)


# ============================================================
# CRC16 测试
# ============================================================

class TestCRC16:
    """CRC16 计算测试"""
    
    def test_crc16_empty_data(self):
        """测试空数据"""
        assert crc16_modbus(b"") == 0xFFFF
    
    def test_crc16_known_value(self):
        """测试 "123456789" 的 CRC-16/MODBUS 值应为 0x4B37"""
        assert crc16_modbus(b"123456789") == 0x4B37
    
    def test_crc16_single_byte(self):
        """测试单字节数据"""
        result = crc16_modbus(b"\x00")
        assert isinstance(result, int)
        assert result <= 0xFFFF


# ============================================================
# 转义/去转义测试
# ============================================================

class TestEscaping:
    """转义/去转义测试"""
    
    def test_escape_no_special_chars(self):
        """测试不含特殊字符的数据"""
        data = b"\x02\x03\x04"
        assert escape_data(data) == data
    
    def test_escape_sof(self):
        """测试 SOF 字符转义"""
        data = bytes([SpecialChar.SOF])
        expected = bytes([SpecialChar.DLE, SpecialChar.SOF])
        assert escape_data(data) == expected
    
    def test_escape_eof(self):
        """测试 EOF 字符转义"""
        data = bytes([SpecialChar.EOF])
        expected = bytes([SpecialChar.DLE, SpecialChar.EOF])
        assert escape_data(data) == expected
    
    def test_escape_dle(self):
        """测试 DLE 字符转义"""
        data = bytes([SpecialChar.DLE])
        expected = bytes([SpecialChar.DLE, SpecialChar.DLE])
        assert escape_data(data) == expected
    
    def test_escape_mixed(self):
        """测试混合数据转义"""
        data = bytes([0x02, SpecialChar.SOF, 0x03, SpecialChar.EOF, 0x04])
        expected = bytes([
            0x02,
            SpecialChar.DLE, SpecialChar.SOF,
            0x03,
            SpecialChar.DLE, SpecialChar.EOF,
            0x04
        ])
        assert escape_data(data) == expected
    
    def test_unescape_roundtrip(self):
        """测试转义和去转义往返"""
        original = bytes([0x00, SpecialChar.SOF, SpecialChar.EOF, SpecialChar.DLE, 0xFF])
        escaped = escape_data(original)
        unescaped = unescape_data(escaped)
        assert unescaped == original
    
    def test_unescape_no_escape(self):
        """测试不含转义的数据"""
        data = b"\x02\x03\x04"
        assert unescape_data(data) == data


# ============================================================
# Frame 类测试
# ============================================================

class TestFrame:
    """Frame 类测试"""
    
    def test_create_heartbeat(self):
        """测试创建心跳帧"""
        frame = Frame.create_heartbeat(seq=5)
        assert frame.version == PROTOCOL_VERSION
        assert frame.seq == 5
        assert frame.flag == 0x00
        assert frame.frame_type == FrameType.HEARTBEAT
        assert frame.payload == b""
        assert frame.is_heartbeat()
    
    def test_build_heartbeat(self):
        """测试构建心跳帧"""
        frame = Frame.create_heartbeat(seq=0)
        data = frame.build()
        
        # 检查帧头帧尾
        assert data.startswith(SOF_SEQUENCE)
        assert data.endswith(EOF_SEQUENCE)
        
        # 长度检查: SOF(3) + content + CRC(2-4) + EOF(3)
        # content: Version(1) + Seq(1) + Flag(1) + Type(1) + Length(1) = 5
        # 最小长度: 3 + 5 + 2 + 3 = 13
        assert len(data) >= 13
    
    def test_build_and_parse_heartbeat(self):
        """测试心跳帧的构建和解析"""
        original = Frame.create_heartbeat(seq=42)
        data = original.build()
        
        # 去除帧头帧尾
        buffer = data[3:-3]
        
        # 解析
        parsed = Frame.parse(buffer)
        
        assert parsed is not None
        assert parsed.version == original.version
        assert parsed.seq == original.seq
        assert parsed.flag == original.flag
        assert parsed.frame_type == original.frame_type
        assert parsed.payload == original.payload
    
    def test_build_and_parse_with_payload(self):
        """测试带 payload 的帧"""
        original = Frame(
            version=PROTOCOL_VERSION,
            seq=10,
            flag=0x00,
            frame_type=0x02,  # 假设的其他类型
            payload=b"test data",
        )
        data = original.build()
        
        # 去除帧头帧尾
        buffer = data[3:-3]
        
        # 解析
        parsed = Frame.parse(buffer)
        
        assert parsed is not None
        assert parsed.payload == b"test data"
    
    def test_build_and_parse_with_special_chars_in_payload(self):
        """测试 payload 包含特殊字符"""
        # payload 包含所有特殊字符
        original = Frame(
            version=PROTOCOL_VERSION,
            seq=0,
            flag=0x00,
            frame_type=0x02,
            payload=bytes([SpecialChar.SOF, SpecialChar.EOF, SpecialChar.DLE]),
        )
        data = original.build()
        
        # 去除帧头帧尾
        buffer = data[3:-3]
        
        # 解析
        parsed = Frame.parse(buffer)
        
        assert parsed is not None
        assert parsed.payload == original.payload
    
    def test_parse_invalid_buffer_too_short(self):
        """测试解析过短的 buffer"""
        result = Frame.parse(b"\x01\x02\x03")
        assert result is None
    
    def test_parse_invalid_crc(self):
        """测试 CRC 错误的帧"""
        original = Frame.create_heartbeat(seq=0)
        data = original.build()
        
        # 去除帧头帧尾
        buffer = bytearray(data[3:-3])
        
        # 破坏数据
        if len(buffer) > 2:
            buffer[0] ^= 0xFF
        
        # 解析应失败
        result = Frame.parse(bytes(buffer))
        assert result is None
    
    def test_seq_wrapping(self):
        """测试序列号回绕"""
        frame = Frame.create_heartbeat(seq=300)  # > 255
        data = frame.build()
        
        buffer = data[3:-3]
        parsed = Frame.parse(buffer)
        
        assert parsed is not None
        assert parsed.seq == 300 & 0xFF  # 应该是 44


# ============================================================
# FrameFilter 类测试
# ============================================================

class TestFrameFilter:
    """FrameFilter 类测试"""
    
    @pytest.fixture
    def filter_with_callbacks(self):
        """创建带回调的 FrameFilter fixture"""
        received_frames = []
        received_user_data = []
        
        def on_frame(frame):
            received_frames.append(frame)
        
        def on_user_data(data):
            received_user_data.append(data)
        
        frame_filter = FrameFilter(
            on_frame=on_frame,
            on_user_data=on_user_data,
        )
        
        return frame_filter, received_frames, received_user_data
    
    def test_process_heartbeat_frame(self, filter_with_callbacks):
        """测试处理心跳帧"""
        frame_filter, received_frames, received_user_data = filter_with_callbacks
        
        frame = Frame.create_heartbeat(seq=0)
        data = frame.build()
        
        frame_filter.process(data)
        
        assert len(received_frames) == 1
        assert len(received_user_data) == 0
        assert received_frames[0].is_heartbeat()
    
    def test_process_user_data_only(self, filter_with_callbacks):
        """测试只有用户数据（遇到 SOF 后刷新）"""
        frame_filter, received_frames, received_user_data = filter_with_callbacks
        
        # 发送普通数据，然后发送 SOF 触发刷新
        user_data = b"\x02\x03\x04"
        
        frame_filter.process(user_data)
        frame_filter.process(bytes([SpecialChar.SOF]))
        
        assert len(received_frames) == 0
        assert len(received_user_data) == 1
        assert received_user_data[0] == user_data
    
    def test_process_user_data_with_timeout(self, filter_with_callbacks):
        """测试用户数据超时刷新"""
        frame_filter, received_frames, received_user_data = filter_with_callbacks
        
        user_data = b"\x02\x03\x04"
        
        frame_filter.process(user_data)
        frame_filter.on_timeout()
        
        assert len(received_frames) == 0
        assert len(received_user_data) == 1
        assert received_user_data[0] == user_data
    
    def test_process_mixed_data(self, filter_with_callbacks):
        """测试混合数据流"""
        frame_filter, received_frames, received_user_data = filter_with_callbacks
        
        # 用户数据 + 心跳帧 + 用户数据
        user_data1 = b"\x02\x03"
        frame = Frame.create_heartbeat(seq=1)
        frame_data = frame.build()
        user_data2 = b"\x04\x06"  # 避免使用 0x05 (SOF) 和 0x00 (EOF) 特殊字符
        
        # 发送第一段用户数据
        frame_filter.process(user_data1)
        
        # 发送心跳帧（帧头的 SOF 会触发用户数据刷新）
        frame_filter.process(frame_data)
        
        # 发送第二段用户数据，然后超时刷新
        frame_filter.process(user_data2)
        frame_filter.on_timeout()
        
        # 验证结果
        assert len(received_frames) == 1
        assert len(received_user_data) == 2
        assert received_user_data[0] == user_data1
        assert received_user_data[1] == user_data2
    
    def test_process_multiple_frames(self, filter_with_callbacks):
        """测试连续多个帧"""
        frame_filter, received_frames, received_user_data = filter_with_callbacks
        
        for i in range(3):
            frame = Frame.create_heartbeat(seq=i)
            frame_filter.process(frame.build())
        
        assert len(received_frames) == 3
        for i, frame in enumerate(received_frames):
            assert frame.seq == i
    
    def test_process_frame_byte_by_byte(self, filter_with_callbacks):
        """测试逐字节处理帧"""
        frame_filter, received_frames, received_user_data = filter_with_callbacks
        
        frame = Frame.create_heartbeat(seq=5)
        data = frame.build()
        
        for byte in data:
            frame_filter.process(bytes([byte]))
        
        assert len(received_frames) == 1
        assert received_frames[0].seq == 5
    
    def test_flush(self, filter_with_callbacks):
        """测试 flush 方法"""
        frame_filter, received_frames, received_user_data = filter_with_callbacks
        
        frame_filter.process(b"\x02\x03")
        
        assert frame_filter.has_pending_data()
        remaining = frame_filter.flush()
        
        assert remaining == b"\x02\x03"
        assert not frame_filter.has_pending_data()
    
    def test_invalid_frame_discarded(self, filter_with_callbacks):
        """测试无效帧被丢弃"""
        frame_filter, received_frames, received_user_data = filter_with_callbacks
        
        # 构造一个无效的帧（错误的 CRC）
        invalid_data = SOF_SEQUENCE + b"\x01\x02\x03\x04\x05\xFF\xFF" + EOF_SEQUENCE
        
        frame_filter.process(invalid_data)
        
        # 无效帧应该被丢弃，不应该触发任何回调
        # SOF 会触发一次空的用户数据刷新（buffer 为空时不触发）
        assert len(received_frames) == 0
    
    def test_in_frame_property(self, filter_with_callbacks):
        """测试 in_frame 属性"""
        frame_filter, received_frames, received_user_data = filter_with_callbacks
        
        # 初始状态不在帧内
        assert not frame_filter.in_frame
        
        # 收到 SOF 后进入帧内
        frame_filter.process(SOF_SEQUENCE)
        assert frame_filter.in_frame
        
        # 收到 EOF 后退出帧内
        frame_filter.process(EOF_SEQUENCE)
        assert not frame_filter.in_frame
    
    def test_timeout_in_frame_discards_data(self, filter_with_callbacks):
        """测试在帧内超时时丢弃数据"""
        frame_filter, received_frames, received_user_data = filter_with_callbacks
        
        # 发送 SOF 和部分帧内容（不完整的帧）
        frame_filter.process(SOF_SEQUENCE + b"\x01\x02\x03")
        
        assert frame_filter.in_frame
        assert frame_filter.has_pending_data()
        
        # 超时 - 在帧内，应该丢弃而不是发送给用户
        frame_filter.on_timeout()
        
        # 验证没有用户数据被发送
        assert len(received_user_data) == 0
        assert not frame_filter.has_pending_data()
        assert not frame_filter.in_frame
    
    def test_timeout_not_in_frame_sends_user_data(self, filter_with_callbacks):
        """测试不在帧内超时时发送用户数据"""
        frame_filter, received_frames, received_user_data = filter_with_callbacks
        
        # 发送普通数据（没有 SOF）
        frame_filter.process(b"\x02\x03\x04")
        
        assert not frame_filter.in_frame
        assert frame_filter.has_pending_data()
        
        # 超时 - 不在帧内，应该发送给用户
        frame_filter.on_timeout()
        
        # 验证用户数据被发送
        assert len(received_user_data) == 1
        assert received_user_data[0] == b"\x02\x03\x04"
        assert not frame_filter.has_pending_data()
    
    def test_sof_in_frame_discards_previous_data(self, filter_with_callbacks):
        """测试在帧内收到 SOF 时丢弃之前的数据"""
        frame_filter, received_frames, received_user_data = filter_with_callbacks
        
        # 发送 SOF 和部分帧内容
        frame_filter.process(SOF_SEQUENCE + b"\x01\x02\x03")
        assert frame_filter.in_frame
        
        # 再次发送 SOF（之前的不完整帧应该被丢弃）
        frame_filter.process(SOF_SEQUENCE)
        
        # 仍然在帧内（新帧开始）
        assert frame_filter.in_frame
        # 之前的数据被丢弃，没有发送给用户
        assert len(received_user_data) == 0
        # buffer 已清空
        assert not frame_filter.has_pending_data()
    
    def test_sof_not_in_frame_sends_user_data(self, filter_with_callbacks):
        """测试不在帧内收到 SOF 时发送用户数据"""
        frame_filter, received_frames, received_user_data = filter_with_callbacks
        
        # 发送普通数据
        frame_filter.process(b"\x02\x03\x04")
        assert not frame_filter.in_frame
        
        # 发送 SOF（应该将之前的数据作为用户数据发送）
        frame_filter.process(SOF_SEQUENCE)
        
        # 现在在帧内
        assert frame_filter.in_frame
        # 之前的数据被发送给用户
        assert len(received_user_data) == 1
        assert received_user_data[0] == b"\x02\x03\x04"


# ============================================================
# 便捷函数测试
# ============================================================

class TestBuildHeartbeatFrame:
    """便捷函数测试"""
    
    def test_build_heartbeat_frame(self):
        """测试 build_heartbeat_frame 函数"""
        data = build_heartbeat_frame(seq=10)
        
        assert data.startswith(SOF_SEQUENCE)
        assert data.endswith(EOF_SEQUENCE)
        
        # 验证可以解析
        buffer = data[3:-3]
        frame = Frame.parse(buffer)
        
        assert frame is not None
        assert frame.seq == 10
        assert frame.is_heartbeat()
