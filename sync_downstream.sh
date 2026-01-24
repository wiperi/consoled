#!/bin/bash
# sync_downstream.sh - 同步 consoled 项目文件到 downstream 子模块
# 用法: ./sync_downstream.sh

set -e

# 获取脚本所在目录作为项目根目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 颜色输出
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "=========================================="
echo "  Syncing consoled to downstream repos"
echo "=========================================="

# 同步函数 - 处理文件
sync_file() {
    local src="$1"
    local dst="$2"
    
    if [[ ! -f "$src" ]]; then
        echo -e "${YELLOW}[SKIP]${NC} Source file not found: $src"
        return 1
    fi
    
    # 创建目标目录
    sudo mkdir -p "$(dirname "$dst")"
    
    # 复制文件
    sudo cp -f "$src" "$dst"
    echo -e "${GREEN}[SYNC]${NC} $src -> $dst"
}

# 同步函数 - 处理目录
sync_dir() {
    local src="$1"
    local dst="$2"
    
    if [[ ! -d "$src" ]]; then
        echo -e "${YELLOW}[SKIP]${NC} Source directory not found: $src"
        return 1
    fi
    
    # 创建目标目录的父目录
    sudo mkdir -p "$(dirname "$dst")"
    
    # 使用 rsync 同步目录（如果有的话），否则用 cp
    if command -v rsync &> /dev/null; then
        sudo rsync -av --delete "$src/" "$dst/" > /dev/null
    else
        sudo rm -rf "$dst"
        sudo cp -rf "$src" "$dst"
    fi
    echo -e "${GREEN}[SYNC]${NC} $src/ -> $dst/"
}

echo ""
echo ">>> Syncing to sonic-host-services..."
sync_file \
    "console_monitor/console-monitor" \
    "downstream/sonic-host-services/scripts/console-monitor"

echo ""
echo ">>> Syncing to SONiC..."
sync_file \
    "docs/Console-Monitor-High-Level-Design.md" \
    "downstream/SONiC/doc/console/Console-Monitor-High-Level-Design.md"

echo ""
echo ">>> Syncing to sonic-mgmt..."
sync_file \
    "docs/Console-Monitor-Test-Plan.md" \
    "downstream/sonic-mgmt/docs/testplan/console/Console-Monitor-Test-Plan.md"

echo ""
echo ">>> Syncing to sonic-utilities..."
sync_dir \
    "commands/consutil" \
    "downstream/sonic-utilities/consutil"

sync_file \
    "commands/config/console.py" \
    "downstream/sonic-utilities/config/console.py"

echo ""
echo ">>> Syncing to local Python packages..."
sync_dir \
    "commands/consutil" \
    "/usr/local/lib/python3.11/dist-packages/consutil"

sync_file \
    "commands/config/console.py" \
    "/usr/local/lib/python3.11/dist-packages/config/console.py"

echo ""
echo "=========================================="
echo "  Sync completed!"
echo "=========================================="
