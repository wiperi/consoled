# Console Heartbeat Service

一个类似 `serial-getty@.service` 的 systemd 服务模板，从 kernel 命令行参数读取串口配置，并定期向串口发送 "hello"。

## 组件说明

### 1. console-heartbeat@.service
Systemd 服务模板文件，使用 `%I` 和 `%i` 作为实例参数占位符。

- `%I` - 未转义的实例名 (如 `ttyS0`)
- `%i` - 转义后的实例名 (如 `ttyS0`)

### 2. console-heartbeat-generator
Systemd generator 脚本，在系统启动早期运行：

1. 读取 `/proc/cmdline` 获取 kernel 参数
2. 解析所有 `console=ttyXXX,BAUDRATE` 格式的参数
3. 为每个串口创建配置文件和服务实例链接

### 3. console-heartbeat-daemon
实际的守护进程脚本：

1. 读取配置文件获取波特率
2. 配置串口参数 (使用 stty)
3. 每 5 秒向串口发送 "hello"

## 安装

```bash
cd /home/admin/consoled/systemd
chmod +x install.sh
sudo ./install.sh
```

## Kernel 命令行参数格式

在 GRUB 或 bootloader 中添加参数：

```
console=ttyS0,9600
console=ttyS1,115200n8
```

## 手动操作

```bash
# 手动启动服务
sudo systemctl start console-heartbeat@ttyS0.service

# 查看状态
sudo systemctl status console-heartbeat@ttyS0.service

# 停止服务
sudo systemctl stop console-heartbeat@ttyS0.service

# 查看日志
sudo journalctl -u console-heartbeat@ttyS0.service -f

# 设置开机启动（手动）
sudo systemctl enable console-heartbeat@ttyS0.service
```

## 测试 Generator

```bash
# 手动运行 generator 测试
sudo mkdir -p /tmp/test-gen
sudo /lib/systemd/system-generators/console-heartbeat-generator /tmp/test-gen '' ''

# 查看生成的文件
ls -la /tmp/test-gen/
cat /run/console-heartbeat/*.conf
```

## 文件位置

安装后的文件位置：

| 文件 | 位置 |
|------|------|
| 服务模板 | `/lib/systemd/system/console-heartbeat@.service` |
| Generator | `/lib/systemd/system-generators/console-heartbeat-generator` |
| Daemon 脚本 | `/usr/local/bin/console-heartbeat-daemon` |
| 运行时配置 | `/run/console-heartbeat/ttyXXX.conf` |

## 工作原理

```
系统启动
    │
    ▼
┌─────────────────────────────────────┐
│  console-heartbeat-generator        │
│  读取 /proc/cmdline                  │
│  解析 console=ttyS0,9600            │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│  创建配置文件                        │
│  /run/console-heartbeat/ttyS0.conf  │
│  BAUDRATE=9600                      │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│  创建服务链接                        │
│  multi-user.target.wants/           │
│  console-heartbeat@ttyS0.service    │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│  systemd 启动服务实例               │
│  console-heartbeat@ttyS0.service    │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│  console-heartbeat-daemon ttyS0     │
│  每 5 秒发送 "hello" 到 /dev/ttyS0  │
└─────────────────────────────────────┘
```

## 调试

```bash
# 查看 systemd 日志
journalctl -b | grep console-heartbeat

# 查看 kernel 命令行
cat /proc/cmdline

# 检查串口设备
ls -la /dev/ttyS*

# 监听串口（在另一个终端）
cat /dev/ttyS0
# 或使用 minicom/screen
screen /dev/ttyS0 9600
```
