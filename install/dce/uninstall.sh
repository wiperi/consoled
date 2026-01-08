#!/bin/bash
# uninstall.sh
# 
# 卸载 console-monitor 服务的脚本
# 需要 root 权限运行

set -e

echo "Uninstalling console-monitor service..."

# 1. 停止服务
echo "  Stopping service..."
sudo systemctl stop console-monitor-dce.service 2>/dev/null || true

# 2. 禁用服务
echo "  Disabling service..."
sudo systemctl disable console-monitor-dce.service 2>/dev/null || true

# 3. 删除服务单元文件
echo "  Removing service unit..."
sudo rm -f /etc/systemd/system/console-monitor-dce.service

# 4. 删除 Python 包和可执行 wrapper
echo "  Removing Python package and wrapper..."
sudo rm -rf /usr/lib/python3/dist-packages/console_monitor
sudo rm -f /usr/local/bin/console-monitor-dce

# 5. 清理 PTY 符号链接（如果服务异常退出未清理）
echo "  Cleaning up PTY symlinks..."
sudo rm -f /dev/VC0-* 2>/dev/null || true

# 6. 重新加载 systemd
echo "  Reloading systemd..."
sudo systemctl daemon-reload

echo ""
echo "Uninstallation complete!"