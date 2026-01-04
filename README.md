```sh
admin@bjw3-can-720dt-9:~/consoled$ tree -L 2
.
├── DCE/                              # Console Server side components
│   ├── console-monitor.py            # Console Monitor daemon: PTY bridge, heartbeat filtering, state management
│   ├── console-monitor.service       # systemd service unit for console-monitor
│   ├── install.sh                    # Installation script as systemd service
│   └── uninstall.sh                  # Uninstallation script
│
├── DTE/                              # SONiC Switch side components
│   ├── console-heartbeat-daemon      # Daemon script: sends heartbeat frames every 5 seconds
│   ├── console-heartbeat-generator   # systemd generator: auto-creates service instances from kernel cmdline
│   ├── console-heartbeat@.service    # systemd template unit for heartbeat sender
│   ├── install.sh                    # Installation script as systemd service
│   ├── uninstall.sh                  # Uninstallation script
│   └── README.md                     # DTE-specific documentation
│
├── docs/                             # Documentation
│   ├── Consoled-HLD-EN.md            # HLD document
│   └── ConsoleMonitorStructure.png
│
└── README.md
```
- [HLD Document](docs/Console-Monitor-HLD.md) is here.
- SONiC Console Server将同时安装DCE和DTE组件。
- SONiC Switch设备安装DTE组件。
- 为了便于调试，开发中暂时使用“hello”作为heartbeat内容