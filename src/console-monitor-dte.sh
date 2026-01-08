#!/bin/bash
# console-heartbeat-daemon
# 
# 守护进程脚本，每隔5秒向指定串口发送心跳帧
# 
# 用法: console-heartbeat-daemon <TTY_NAME> <TTY_ESCAPED_NAME>
#   例如: console-heartbeat-daemon ttyS0 ttyS0
#
# 放置位置: /usr/local/bin/console-heartbeat-daemon

set -e

TTY_NAME="$1"
TTY_ESCAPED="$2"

# 设备路径
DEVICE="/dev/${TTY_NAME}"

# 从环境文件读取配置（如果存在）
CONF_FILE="/run/console-heartbeat/${TTY_NAME}.conf"
if [[ -f "$CONF_FILE" ]]; then
    source "$CONF_FILE"
fi

# 默认波特率
BAUDRATE="${BAUDRATE:-9600}"

# 检查设备是否存在
if [[ ! -c "$DEVICE" ]]; then
    echo "Error: Device $DEVICE does not exist or is not a character device"
    exit 1
fi

echo "Starting console-heartbeat-daemon on $DEVICE"

# 帧协议常量
SOF="\x01"          # Start of Frame
EOF_MARKER="\x1B"   # End of Frame
DLE="\x10"          # Data Link Escape

VERSION="\x01"      # 协议版本
FLAG="\x00"         # 标志位
TYPE_HEARTBEAT="\x01"  # 心跳帧类型
LENGTH="\x00"       # Payload长度（心跳帧无payload）

# 序列号（0-255循环）
SEQ=0

# CRC16-MODBUS 计算函数
# 参数: 十六进制字符串（如 "01000001"）
# 返回: 大端序CRC16（2字节十六进制字符串）
calc_crc16() {
    local data="$1"
    local crc=65535  # 0xFFFF
    
    # 将十六进制字符串转换为字节数组
    local bytes=()
    local i=0
    while [ $i -lt ${#data} ]; do
        bytes+=("0x${data:$i:2}")
        i=$((i + 2))
    done
    
    # CRC-16/MODBUS 算法
    for byte in "${bytes[@]}"; do
        byte=$((byte & 0xFF))
        crc=$((crc ^ byte))
        
        for bit in {0..7}; do
            if [ $((crc & 1)) -eq 1 ]; then
                crc=$(((crc >> 1) ^ 0xA001))  # 0xA001 是 0x8005 的反射
            else
                crc=$((crc >> 1))
            fi
        done
    done
    
    # 返回大端序（高字节在前）
    printf "%02X%02X" $((crc >> 8)) $((crc & 0xFF))
}

# 转义函数
# 将帧内容中的特殊字符进行转义
escape_frame_content() {
    local input="$1"
    local output=""
    
    local i=0
    while [ $i -lt ${#input} ]; do
        local byte="${input:$i:2}"
        case "$byte" in
            "01")  # SOF
                output+="1001"
                ;;
            "1B")  # EOF
                output+="101B"
                ;;
            "10")  # DLE
                output+="1010"
                ;;
            *)
                output+="$byte"
                ;;
        esac
        i=$((i + 2))
    done
    
    echo "$output"
}

# 构造心跳帧
build_heartbeat_frame() {
    local seq_hex=$(printf "%02X" $SEQ)
    
    # 帧内容（转义前）：Version + Seq + Flag + Type + Length
    local frame_content="01${seq_hex}000100"
    
    # 计算CRC16
    local crc=$(calc_crc16 "$frame_content")
    
    # 将CRC16附加到帧内容
    frame_content="${frame_content}${crc}"
    
    # 对整个帧内容进行转义
    local escaped_content=$(escape_frame_content "$frame_content")
    
    # 构造完整帧：SOF x 3 + 转义后的内容 + EOF x 3
    local frame="010101${escaped_content}1B1B1B"
    
    echo "$frame"
}

# 主循环：每5秒发送一次心跳帧
while true; do
    # 构造心跳帧
    FRAME_HEX=$(build_heartbeat_frame)
    
    # 将十六进制字符串转换为二进制并发送到设备
    echo -ne "$(echo "$FRAME_HEX" | sed 's/\(..\)/\\x\1/g')" > "$DEVICE" 2>/dev/null || {
        echo "Warning: Failed to write to $DEVICE"
    }
    
    # 记录日志
    echo "$(date '+%Y-%m-%d %H:%M:%S') - Sent heartbeat frame (seq=$SEQ) to $DEVICE: $FRAME_HEX"
    
    # 序列号递增（0-255循环）
    SEQ=$(((SEQ + 1) % 256))
    
    # 等待5秒
    sleep 5
done
