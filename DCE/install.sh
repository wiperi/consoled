#!/bin/bash
# install.sh
# 
# 安装 console-monitor 服务的脚本
# 需要 root 权限运行

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Installing console-monitor service..."

# 1. 安装 daemon 脚本
echo "  Installing daemon script..."
sudo cp "${SCRIPT_DIR}/console-monitor.py" /usr/local/bin/console-monitor.py
sudo chmod +x /usr/local/bin/console-monitor.py

# 2. 安装服务单元文件
echo "  Installing service unit..."
sudo cp "${SCRIPT_DIR}/console-monitor.service" /etc/systemd/system/

# 3. 重新加载 systemd
echo "  Reloading systemd..."
sudo systemctl daemon-reload

# 4. 启用开机自启
echo "  Enabling service..."
sudo systemctl enable console-monitor.service

echo ""
echo "Installation complete!"
echo ""
echo "Usage:"
echo "  - To start the service:"
echo "      sudo systemctl start console-monitor.service"
echo "  - To check status:"
echo "      sudo systemctl status console-monitor.service"
echo "  - To view logs:"
echo "      sudo journalctl -u console-monitor.service -f"
echo "  - To restart the service:"
echo "      sudo systemctl restart console-monitor.service"
echo ""