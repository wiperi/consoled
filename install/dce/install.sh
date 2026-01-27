#!/bin/bash
# install.sh
# 
# 安装 console-monitor DCE 服务的脚本
# 需要 root 权限运行
#
# 安装内容：
# - console-monitor 可执行文件到 /usr/bin/console-monitor
# - console-monitor-ptyhub.service (PTY Hub 服务)
# - console-monitor-dce.service (DCE 服务)
# - console-monitor-proxy@.service (Proxy 模板服务，由 DCE 动态启动)
#
# 服务启动顺序: ptyhub -> dce -> proxy@<link_id> (由 dce 动态管理)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Installing console-monitor DCE services..."

# 1. 安装 console-monitor 可执行文件
echo "  Installing console-monitor executable..."
sudo cp "${SCRIPT_DIR}/../../console_monitor/console-monitor" /usr/bin/console-monitor
sudo chmod +x /usr/bin/console-monitor

# 2. 安装服务单元文件
echo "  Installing service units..."
sudo cp "${SCRIPT_DIR}/console-monitor-ptyhub.service" /etc/systemd/system/
sudo cp "${SCRIPT_DIR}/console-monitor-dce.service" /etc/systemd/system/
sudo cp "${SCRIPT_DIR}/console-monitor-proxy@.service" /etc/systemd/system/

# 3. 重新加载 systemd
echo "  Reloading systemd..."
sudo systemctl daemon-reload

# 4. 启用开机自启 (proxy 服务由 dce 动态管理，不需要 enable)
echo "  Enabling services..."
sudo systemctl enable console-monitor-ptyhub.service
sudo systemctl enable console-monitor-dce.service

echo ""
echo "Installation complete!"
echo ""
echo "Usage:"
echo "  - To start the services:"
echo "      sudo systemctl start console-monitor-ptyhub.service"
echo "      sudo systemctl start console-monitor-dce.service"
echo "  - To check status:"
echo "      sudo systemctl status console-monitor-ptyhub.service"
echo "      sudo systemctl status console-monitor-dce.service"
echo "      sudo systemctl status 'console-monitor-proxy@*'"
echo "  - To view logs:"
echo "      sudo journalctl -u console-monitor-ptyhub.service -f"
echo "      sudo journalctl -u console-monitor-dce.service -f"
echo "      sudo journalctl -u 'console-monitor-proxy@*' -f"
echo "  - To restart the services:"
echo "      sudo systemctl restart console-monitor-ptyhub.service"
echo "      sudo systemctl restart console-monitor-dce.service"
echo "  - Manual run:"
echo "      console-monitor ptyhub    # PTY Hub service"
echo "      console-monitor dce       # DCE service"
echo "      console-monitor proxy 1   # Proxy service for port 1"
echo ""