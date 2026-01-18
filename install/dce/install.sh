#!/bin/bash
# install.sh
# 
# 安装 console-monitor DCE 服务的脚本
# 需要 root 权限运行
#
# 安装统一的 console-monitor 命令到 /usr/bin/console-monitor
# 通过 `console-monitor dce` 参数启动 DCE 模式

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Installing console-monitor DCE service..."

# 1. 安装 console-monitor 可执行文件
echo "  Installing console-monitor executable..."
sudo cp "${SCRIPT_DIR}/../../console_monitor/console-monitor" /usr/bin/console-monitor
sudo chmod +x /usr/bin/console-monitor

# 2. 安装服务单元文件
echo "  Installing service unit..."
sudo cp "${SCRIPT_DIR}/console-monitor-dce.service" /etc/systemd/system/

# 3. 重新加载 systemd
echo "  Reloading systemd..."
sudo systemctl daemon-reload

# 4. 启用开机自启
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
echo "  - Manual run:"
echo "      console-monitor dce"
echo ""