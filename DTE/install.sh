#!/bin/bash
# install.sh
# 
# 安装 console-heartbeat 服务的脚本
# 需要 root 权限运行

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Installing console-heartbeat service..."

# 1. 安装服务模板
echo "  Installing service template..."
sudo cp "${SCRIPT_DIR}/console-heartbeat@.service" /lib/systemd/system/

# 2. 安装 generator
echo "  Installing generator..."
sudo cp "${SCRIPT_DIR}/console-heartbeat-generator" /lib/systemd/system-generators/
sudo chmod +x /lib/systemd/system-generators/console-heartbeat-generator

# 3. 安装 daemon 脚本
echo "  Installing daemon script..."
sudo cp "${SCRIPT_DIR}/console-heartbeat-daemon" /usr/local/bin/
sudo chmod +x /usr/local/bin/console-heartbeat-daemon

# 4. 重新加载 systemd
echo "  Reloading systemd..."
sudo systemctl daemon-reload

echo ""
echo "Installation complete!"
echo ""
echo "Usage:"
echo "  - The service will auto-start on boot based on kernel cmdline 'console=' parameters"
echo "  - To manually start for a specific tty:"
echo "      sudo systemctl start console-heartbeat@ttyS0.service"
echo "  - To check status:"
echo "      sudo systemctl status console-heartbeat@ttyS0.service"
echo "  - To view logs:"
echo "      sudo journalctl -u console-heartbeat@ttyS0.service -f"
echo ""
echo "To test the generator manually:"
echo "  sudo /lib/systemd/system-generators/console-heartbeat-generator /tmp/test-gen '' ''"
echo "  ls -la /tmp/test-gen/"
