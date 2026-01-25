#!/bin/bash
# install.sh
# 
# Install console-monitor DCE service
# Requires root privileges
#
# Installs the unified console-monitor command to /usr/bin/console-monitor
# DCE mode: `console-monitor dce` (main process, manages proxy services)
# Proxy mode: `console-monitor proxy <link_id>` (per-port process)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Installing console-monitor DCE service..."

# 1. Install console-monitor executable
echo "  Installing console-monitor executable..."
sudo cp "${SCRIPT_DIR}/../../console_monitor/console-monitor" /usr/bin/console-monitor
sudo chmod +x /usr/bin/console-monitor

# 2. Install service unit files
echo "  Installing service units..."
sudo cp "${SCRIPT_DIR}/console-monitor-dce.service" /etc/systemd/system/
sudo cp "${SCRIPT_DIR}/console-monitor-proxy@.service" /etc/systemd/system/

# 3. Reload systemd
echo "  Reloading systemd..."
sudo systemctl daemon-reload

# 4. Enable auto-start
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
echo "  - To view proxy logs for port 1:"
echo "      sudo journalctl -u console-monitor-proxy@1.service -f"
echo "  - To restart the service:"
echo "      sudo systemctl restart console-monitor-dce.service"
echo "  - Manual run (main process):"
echo "      console-monitor dce"
echo "  - Manual run (proxy for port 1):"
echo "      console-monitor proxy 1"
echo ""