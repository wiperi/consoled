#!/bin/bash
# install.sh
# 
# 安装 console-monitor 服务的脚本
# 需要 root 权限运行

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Installing console-monitor service..."

# 1. 安装 Python 包到系统路径
echo "  Installing Python package..."
sudo mkdir -p /usr/lib/python3/dist-packages
sudo cp -r "${SCRIPT_DIR}/../../console_monitor" /usr/lib/python3/dist-packages/console_monitor
sudo find /usr/lib/python3/dist-packages/console_monitor -name "*.pyc" -delete
sudo find /usr/lib/python3/dist-packages/console_monitor -name "__pycache__" -type d -delete

# 2. 安装可执行 wrapper
echo "  Installing executable wrapper..."
sudo cp "${SCRIPT_DIR}/console-monitor-dce" /usr/local/bin/console-monitor-dce
sudo chmod +x /usr/local/bin/console-monitor-dce

# 3. 安装服务单元文件
echo "  Installing service unit..."
sudo cp "${SCRIPT_DIR}/console-monitor-dce.service" /etc/systemd/system/

# 4. 重新加载 systemd
echo "  Reloading systemd..."
sudo systemctl daemon-reload

# 5. 启用开机自启
echo "  Enabling service..."
sudo systemctl enable console-monitor-dce.service

echo ""
echo "Installation complete!"
echo ""
echo "Usage:"
echo "  - To start the service:"
echo "      sudo systemctl start console-monitor-dce.service"
echo "  - To check status:"
echo "      sudo systemctl status console-monitor-dce.service"
echo "  - To view logs:"
echo "      sudo journalctl -u console-monitor-dce.service -f"
echo "  - To restart the service:"
echo "      sudo systemctl restart console-monitor-dce.service"
echo ""