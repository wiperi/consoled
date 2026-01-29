#!/bin/bash
# install.sh
# 
# 安装 console-monitor DTE 服务的脚本
# 需要 root 权限运行
#
# 安装统一的 console-monitor 命令到 /usr/local/bin/console-monitor
# 通过 `console-monitor dte` 参数启动 DTE 模式

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Installing console-monitor DTE service..."

# 1. 安装 console-monitor 可执行文件
echo "  Installing console-monitor executable..."
sudo cp "${SCRIPT_DIR}/../../console_monitor/console-monitor" /usr/local/bin/console-monitor
sudo chmod +x /usr/local/bin/console-monitor

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
echo "  - Manual run:"
echo "      console-monitor dte"

