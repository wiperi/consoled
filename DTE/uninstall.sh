#!/bin/bash
# uninstall.sh
# 
# 卸载 console-heartbeat 服务的脚本
# 需要 root 权限运行

set -e

echo "Uninstalling console-heartbeat service..."

# 1. 停止所有运行中的服务实例
echo "  Stopping running services..."
for service in $(systemctl list-units --type=service --all | grep 'console-heartbeat@' | awk '{print $1}'); do
    echo "    Stopping $service..."
    sudo systemctl stop "$service" 2>/dev/null || true
done

# 2. 禁用服务（如果有手动启用的）
echo "  Disabling services..."
sudo systemctl disable 'console-heartbeat@*.service' 2>/dev/null || true

# 3. 删除服务模板
echo "  Removing service template..."
sudo rm -f /lib/systemd/system/console-heartbeat@.service

# 4. 删除 generator
echo "  Removing generator..."
sudo rm -f /lib/systemd/system-generators/console-heartbeat-generator

# 5. 删除 daemon 脚本
echo "  Removing daemon script..."
sudo rm -f /usr/local/bin/console-heartbeat-daemon

# 6. 清理运行时配置目录
echo "  Cleaning up runtime files..."
sudo rm -rf /run/console-heartbeat

# 7. 清理 generator 生成的符号链接
echo "  Cleaning up generated symlinks..."
sudo rm -f /run/systemd/generator/multi-user.target.wants/console-heartbeat@*.service

# 8. 重新加载 systemd
echo "  Reloading systemd..."
sudo systemctl daemon-reload

echo ""
echo "Uninstallation complete!"
