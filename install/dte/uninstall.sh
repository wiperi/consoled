#!/bin/bash
# uninstall.sh
# 
# 卸载 console-monitor-dte 服务的脚本
# 需要 root 权限运行

set -e

echo "Uninstalling console-monitor-dte service..."

# 1. 停止所有运行中的服务实例
echo "  Stopping running services..."
for service in $(systemctl list-units --type=service --all | grep 'console-monitor-dte@' | awk '{print $1}'); do
    echo "    Stopping $service..."
    sudo systemctl stop "$service" 2>/dev/null || true
done

# 2. 禁用服务（如果有手动启用的）
echo "  Disabling services..."
sudo systemctl disable 'console-monitor-dte@*.service' 2>/dev/null || true

# 3. 删除服务模板
echo "  Removing service template..."
sudo rm -f /lib/systemd/system/console-monitor-dte@.service

# 4. 删除 generator
echo "  Removing generator..."
sudo rm -f /lib/systemd/system-generators/console-monitor-dte-generator

# 5. 恢复系统默认的 getty generator
echo "  Restoring default systemd-getty-generator..."
sudo rm -f /etc/systemd/system-generators/systemd-getty-generator

# 6. 删除 daemon 脚本和 Python 包
echo "  Removing daemon script and Python package..."
sudo rm -f /usr/local/bin/console-monitor-dte
sudo rm -rf /usr/lib/python3/dist-packages/console_monitor

# 6. 清理运行时配置目录
echo "  Cleaning up runtime files..."
sudo rm -rf /run/console-monitor-dte

# 7. 清理 generator 生成的符号链接
echo "  Cleaning up generated symlinks..."
sudo rm -f /run/systemd/generator/multi-user.target.wants/console-monitor-dte@*.service

# 8. 重新加载 systemd
echo "  Reloading systemd..."
sudo systemctl daemon-reload

echo ""
echo "Uninstallation complete!"
