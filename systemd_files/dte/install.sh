#!/bin/bash
# install.sh
# 
# 安装 console-monitor-dte 服务的脚本
# 需要 root 权限运行

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(cd "${SCRIPT_DIR}/../../src" && pwd)"

echo "Installing console-monitor-dte service..."

# 1. 安装服务模板
echo "  Installing service template..."
sudo cp "${SCRIPT_DIR}/console-monitor-dte@.service" /lib/systemd/system/

# 2. 安装 generator
echo "  Installing generator..."
sudo cp "${SCRIPT_DIR}/console-monitor-dte-generator" /lib/systemd/system-generators/
sudo chmod +x /lib/systemd/system-generators/console-monitor-dte-generator

# 3. 安装 Python 模块和脚本
echo "  Installing Python modules..."
sudo cp "${SRC_DIR}/frame.py" /usr/local/lib/
sudo cp "${SRC_DIR}/console-monitor-dte.py" /usr/local/bin/console-monitor-dte
sudo chmod +x /usr/local/bin/console-monitor-dte

# 4. 重新加载 systemd
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
