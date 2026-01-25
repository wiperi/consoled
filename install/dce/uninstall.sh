#!/bin/bash
# uninstall.sh
# 
# Uninstall console-monitor DCE service
# Requires root privileges

set -e

echo "Uninstalling console-monitor DCE service..."

# 1. Stop all proxy services
echo "  Stopping proxy services..."
for service in $(systemctl list-units --type=service --all | grep 'console-monitor-proxy@' | awk '{print $1}'); do
    sudo systemctl stop "$service" 2>/dev/null || true
done

# 2. Stop main DCE service
echo "  Stopping DCE service..."
sudo systemctl stop console-monitor-dce.service 2>/dev/null || true

# 3. Disable service
echo "  Disabling service..."
sudo systemctl disable console-monitor-dce.service 2>/dev/null || true

# 4. Remove service unit files
echo "  Removing service units..."
sudo rm -f /etc/systemd/system/console-monitor-dce.service
sudo rm -f /etc/systemd/system/console-monitor-proxy@.service

# 5. Remove console-monitor executable
echo "  Removing console-monitor executable..."
sudo rm -f /usr/bin/console-monitor

# 6. Cleanup PTY symlinks (if service exited abnormally)
echo "  Cleaning up PTY symlinks..."
sudo rm -f /dev/VC0-* 2>/dev/null || true
sudo rm -f /dev/C0-*-PTS 2>/dev/null || true

# 7. Reload systemd
echo "  Reloading systemd..."
sudo systemctl daemon-reload

echo ""
echo "Uninstallation complete!"