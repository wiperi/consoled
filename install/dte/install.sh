#!/bin/bash
# install.sh
# 
# 安装 console-monitor-dte 服务的脚本
# 需要 root 权限运行

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(cd "${SCRIPT_DIR}/../../src" && pwd)"

echo "Installing console-monitor-dte service..."

# 1. 安装 Python 包到系统路径
echo "  Installing Python package..."
sudo mkdir -p /usr/lib/python3/dist-packages
sudo cp -r "${SCRIPT_DIR}/../../console_monitor" /usr/lib/python3/dist-packages/console_monitor
sudo find /usr/lib/python3/dist-packages/console_monitor -name "*.pyc" -delete
sudo find /usr/lib/python3/dist-packages/console_monitor -name "__pycache__" -type d -delete

# 2. 安装可执行 wrapper
echo "  Installing executable wrapper..."
sudo cp "${SCRIPT_DIR}/console-monitor-dte" /usr/local/bin/console-monitor-dte
sudo chmod +x /usr/local/bin/console-monitor-dte

# 3. 安装服务模板
echo "  Installing service template..."
sudo cp "${SCRIPT_DIR}/console-monitor-dte@.service" /lib/systemd/system/

# 4. 安装 generator
echo "  Installing generator..."
sudo cp "${SCRIPT_DIR}/console-monitor-dte-generator" /lib/systemd/system-generators/
sudo chmod +x /lib/systemd/system-generators/console-monitor-dte-generator

# 5. 重新加载 systemd
echo "  Reloading systemd..."
sudo systemctl daemon-reload

echo ""
echo "Installation complete!"
echo ""
echo "Usage:"
echo "  - The service will auto-start on boot based on kernel cmdline 'console=' parameters"
echo "  - To manually start for a specific tty:"
echo "      sudo systemctl start console-monitor-dte@ttyS0.service"
echo "  - To check status:"
echo "      sudo systemctl status console-monitor-dte@ttyS0.service"
echo "  - To view logs:"
echo "      sudo journalctl -u console-monitor-dte@ttyS0.service -f"
echo ""
echo "To test the generator manually:"
echo "  sudo /lib/systemd/system-generators/console-monitor-dte-generator /tmp/test-gen '' ''"
echo "  ls -la /tmp/test-gen/"
