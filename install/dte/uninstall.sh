#!/bin/bash
# uninstall.sh
# 
# 卸载 console-monitor-dte 服务的脚本
# 需要 root 权限运行

set -e

echo "Uninstalling console-monitor-dte service..."

# 1. 停止服务
echo "  Stopping service..."
sudo systemctl stop console-monitor-dte.service 2>/dev/null || true

# 2. 禁用服务
echo "  Disabling service..."
sudo systemctl disable console-monitor-dte.service 2>/dev/null || true

# 3. 删除服务文件
echo "  Removing service file..."
sudo rm -f /lib/systemd/system/console-monitor-dte.service

# 4. 删除可执行文件
echo "  Removing executable..."
sudo rm -f /usr/local/bin/console-monitor-dte

# 5. 重新加载 systemd
echo "  Reloading systemd..."
sudo systemctl daemon-reload

echo ""
echo "Uninstall complete!"

echo ""
echo "Uninstallation complete!"
