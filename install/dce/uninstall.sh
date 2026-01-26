#!/bin/bash
# uninstall.sh
# 
# 卸载 console-monitor DCE 服务的脚本
# 需要 root 权限运行

set -e

echo "Uninstalling console-monitor DCE services..."

# 1. 停止服务
echo "  Stopping services..."
sudo systemctl stop console-monitor-dce.service 2>/dev/null || true
sudo systemctl stop console-monitor-ptyhub.service 2>/dev/null || true

# 2. 禁用服务
echo "  Disabling services..."
sudo systemctl disable console-monitor-dce.service 2>/dev/null || true
sudo systemctl disable console-monitor-ptyhub.service 2>/dev/null || true

# 3. 删除服务单元文件
echo "  Removing service units..."
sudo rm -f /etc/systemd/system/console-monitor-dce.service
sudo rm -f /etc/systemd/system/console-monitor-ptyhub.service

# 4. 删除 console-monitor 可执行文件
echo "  Removing console-monitor executable..."
sudo rm -f /usr/bin/console-monitor

# 5. 清理 PTY 符号链接（如果服务异常退出未清理）
echo "  Cleaning up PTY symlinks..."
sudo rm -f /dev/*-PTS 2>/dev/null || true
sudo rm -f /dev/*-PTM 2>/dev/null || true

# 6. 重新加载 systemd
echo "  Reloading systemd..."
sudo systemctl daemon-reload

echo ""
echo "Uninstallation complete!"