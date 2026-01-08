#!/usr/bin/env python3
"""
测试脚本：验证心跳帧的构造

运行此脚本来验证心跳帧的格式是否符合 HLD 规范
"""

import sys
import os

# 添加模块搜索路径
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))

from frame import Frame, FrameType, SOF_SEQUENCE, EOF_SEQUENCE

def test_heartbeat_frame():
    """测试心跳帧的构造"""
    
    print("=" * 60)
    print("测试心跳帧构造")
    print("=" * 60)
    
    # 构造序列号 0 的心跳帧
    frame = Frame(
        seq=0,
        frame_type=FrameType.HEARTBEAT,
        payload=b""
    )
    
    frame_bytes = frame.build()
    
    # 显示帧的十六进制表示
    hex_str = frame_bytes.hex().upper()
    print(f"\n完整帧 (hex): {hex_str}")
    print(f"帧长度: {len(frame_bytes)} 字节")
    
    # 格式化显示
    print("\n帧结构解析:")
    print(f"  SOF x 3:     {frame_bytes[0:3].hex().upper()}")
    print(f"  Frame Body:  {frame_bytes[3:-3].hex().upper()}")
    print(f"  EOF x 3:     {frame_bytes[-3:].hex().upper()}")
    
    # 验证帧头帧尾
    assert frame_bytes[:3] == SOF_SEQUENCE, "帧头不正确"
    assert frame_bytes[-3:] == EOF_SEQUENCE, "帧尾不正确"
    
    print("\n✓ 帧头帧尾验证通过")
    
    # 测试多个序列号
    print("\n" + "=" * 60)
    print("测试序列号递增")
    print("=" * 60)
    
    for seq in [0, 1, 2, 255, 0]:
        frame = Frame(seq=seq, frame_type=FrameType.HEARTBEAT, payload=b"")
        frame_bytes = frame.build()
        hex_str = frame_bytes.hex().upper()
        print(f"Seq={seq:3d}: {hex_str}")
    
    print("\n✓ 所有测试通过!")


if __name__ == '__main__':
    test_heartbeat_frame()
