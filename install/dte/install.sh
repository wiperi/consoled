#!/bin/bash
# install.sh
# 
# 安装 console-monitor-dte 服务的脚本
# 需要 root 权限运行
#
# 单文件设计：无需安装 Python 包，所有依赖内联在可执行文件中

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Installing console-monitor-dte service..."

# 1. 安装可执行文件（单文件，包含所有依赖）
echo "  Installing executable..."
sudo cp "${SCRIPT_DIR}/console-monitor-dte" /usr/local/bin/console-monitor-dte
sudo chmod +x /usr/local/bin/console-monitor-dte

# 2. 安装服务文件
echo "  Installing service file..."
sudo cp "${SCRIPT_DIR}/console-monitor-dte.service" /lib/systemd/system/

# 3. 重新加载 systemd
echo "  Reloading systemd..."
sudo systemctl daemon-reload

# 4. 启用并启动服务
echo "  Enabling and starting service..."
sudo systemctl enable console-monitor-dte.service
sudo systemctl start console-monitor-dte.service

echo ""
echo "Installation complete!"
echo ""
echo "Usage:"
echo "  - The service reads serial port configuration from /proc/cmdline automatically"
echo "  - To check status:"
echo "      sudo systemctl status console-monitor-dte.service"
echo "  - To view logs:"
echo "      sudo journalctl -u console-monitor-dte.service -f"
echo "  - To restart:"
echo "      sudo systemctl restart console-monitor-dte.service"

