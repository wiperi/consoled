# Console Monitor DTE Service

Console Monitor DTE 侧服务，用于向 DCE 发送心跳帧以检测链路连通性。

## 功能

- 从 `/proc/cmdline` 自动读取串口配置（`console=<TTYNAME>,<BAUD>`）
- 也支持命令行参数指定串口
- 监听 Redis CONFIG_DB 中的 `enabled` 配置
- 当 `enabled=yes` 时，每 5 秒发送心跳帧到串口
- 通过 Redis keyspace notification 实时响应配置变更

## 组件说明

### 1. console-monitor-dte
单文件 Python 可执行脚本，包含所有依赖代码：

- 帧协议实现（Frame、FrameType）
- 串口配置工具（configure_serial）
- 心跳发送逻辑

### 2. console-monitor-dte.service
Systemd 服务文件，系统启动后自动运行服务。

## 安装

```bash
cd /home/admin/consoled/install/dte
chmod +x install.sh
sudo ./install.sh
```

## 卸载

```bash
cd /home/admin/consoled/install/dte
chmod +x uninstall.sh
sudo ./uninstall.sh
```

## Kernel 命令行参数格式

在 GRUB 或 bootloader 中添加参数：

```
console=ttyS0,9600
console=ttyS1,115200
```

服务会自动解析最后一个 `console=` 参数作为主控制台。

## 手动操作

```bash
# 查看服务状态
sudo systemctl status console-monitor-dte.service

# 查看日志
sudo journalctl -u console-monitor-dte.service -f

# 重启服务
sudo systemctl restart console-monitor-dte.service

# 停止服务
sudo systemctl stop console-monitor-dte.service

# 启动服务
sudo systemctl start console-monitor-dte.service
```

## 手动运行（调试用）

```bash
# 自动从 /proc/cmdline 读取配置
python3 /usr/local/bin/console-monitor-dte

# 指定串口参数
python3 /usr/local/bin/console-monitor-dte ttyS0 9600
```

## 文件位置

安装后的文件位置：

| 文件 | 位置 |
|------|------|
| 可执行文件 | `/usr/local/bin/console-monitor-dte` |
| 服务文件 | `/lib/systemd/system/console-monitor-dte.service` |

## 工作原理

```
系统启动
    │
    ▼
┌─────────────────────────────────────┐
│  console-monitor-dte.service        │
│  由 systemd 启动                    │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│  console-monitor-dte                │
│  读取 /proc/cmdline                 │
│  解析 console=ttyS0,9600            │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│  连接 Redis CONFIG_DB               │
│  检查 enabled 状态                  │
│  订阅 keyspace notification         │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│  如果 enabled=yes                   │
│  每 5 秒发送心跳帧到串口            │
└─────────────────────────────────────┘
```

## 调试

```bash
# 查看 systemd 日志
journalctl -u console-monitor-dte.service -f

# 查看 kernel 命令行
cat /proc/cmdline

# 检查串口设备
ls -la /dev/ttyS*

# 检查 Redis 配置
redis-cli -n 4 HGETALL "CONSOLE_SWITCH|controlled_device"

# 监听串口（在另一个终端）
cat /dev/ttyS0
# 或使用 picocom
picocom -b 9600 /dev/ttyS0
```
