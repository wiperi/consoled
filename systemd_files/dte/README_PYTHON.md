# Console Monitor DTE - Python 实现

## 概述

Python 实现的 DTE 侧（SONiC Switch）心跳守护进程，周期性发送心跳帧到串口。

## 文件清单

- **src/console-monitor-dte.py** - 主程序（安装后位于 `/usr/local/bin/console-monitor-dte`）
- **src/frame.py** - 帧协议实现（安装后位于 `/usr/local/lib/frame.py`）
- **systemd_files/dte/console-monitor-dte@.service** - systemd 服务模板
- **systemd_files/dte/console-monitor-dte-generator** - systemd generator
- **systemd_files/dte/install.sh** - 安装脚本
- **systemd_files/dte/uninstall.sh** - 卸载脚本

## 主要特性

1. **符合 HLD 3.1 帧规范**
   - 完整实现帧结构（SOF×3 + 帧内容 + CRC16 + EOF×3）
   - CRC-16/MODBUS 校验
   - 转义机制支持
   - 序列号 0-255 循环递增

2. **复用 frame.py 组件**
   - 使用统一的 Frame 类构造心跳帧
   - 与 DCE 侧使用相同的帧协议实现

3. **5 秒发送周期**
   - 固定间隔发送心跳帧
   - 无 payload（心跳帧类型）

## 安装

```bash
cd /home/admin/consoled/systemd_files/dte
sudo ./install.sh
```

安装脚本会：
1. 复制服务模板到 `/lib/systemd/system/`
2. 复制 generator 到 `/lib/systemd/system-generators/`
3. 复制 Python 模块和脚本到系统目录
4. 重新加载 systemd 配置

## 使用

### 自动启动

服务会根据内核命令行中的 `console=` 参数自动启动（通过 systemd generator）

### 手动管理

```bash
# 启动特定串口的服务
sudo systemctl start console-monitor-dte@ttyS0.service

# 停止服务
sudo systemctl stop console-monitor-dte@ttyS0.service

# 查看状态
sudo systemctl status console-monitor-dte@ttyS0.service

# 查看日志
sudo journalctl -u console-monitor-dte@ttyS0.service -f
```

## 测试

运行测试脚本验证帧构造：

```bash
cd /home/admin/consoled
python3 tests/test_heartbeat_sender.py
```

输出示例：
```
============================================================
测试心跳帧构造
============================================================

完整帧 (hex): 0101011001000010010050181B1B1B
帧长度: 15 字节

帧结构解析:
  SOF x 3:     010101
  Frame Body:  100100001001005018
  EOF x 3:     1B1B1B

✓ 帧头帧尾验证通过
```

## 心跳帧格式

### 序列号 0 的心跳帧示例

```
01 01 01 10 01 00 00 10 01 00 50 18 1B 1B 1B
└──┬──┘ └────────┬─────────┘ └──┬─┘ └──┬──┘
   │            │              │      └── EOF x 3
   │            │              └── CRC16 (0x5018)
   │            └── 转义后的帧内容
   └── SOF x 3
```

转义前的帧内容：
```
01 00 00 01 00 50 18
│  │  │  │  │  └──┬─┘
│  │  │  │  │     └── CRC16 (大端序)
│  │  │  │  └── Length: 0
│  │  │  └── Type: HEARTBEAT (0x01, 需要转义)
│  │  └── Flag: 0x00
│  └── Seq: 0x00
└── Version: 0x01 (需要转义)
```

注意：Version (0x01) 和 Type (0x01) 都需要转义成 `10 01`

## 与 Bash 版本的对比

| 特性 | Bash 版本 | Python 版本 |
|------|-----------|-------------|
| 依赖 | bash, coreutils | python3 |
| 代码复用 | 独立实现 | 复用 frame.py |
| 可维护性 | 较低 | 高 |
| 性能 | 低（每次计算 CRC） | 高（优化的字节操作） |
| 日志 | 基础 | 结构化日志 |
| 错误处理 | 基础 | 完善 |

## 开发者说明

### 修改心跳间隔

编辑 `src/console-monitor-dte.py`：

```python
# 心跳发送间隔（秒）
HEARTBEAT_INTERVAL = 5  # 修改此值
```

### 调试模式

```bash
# 直接运行脚本（不通过 systemd）
python3 /usr/local/bin/console-monitor-dte ttyS0
```

### 模块路径

脚本会自动搜索以下路径：
- `/usr/local/lib/` （生产环境）
- 脚本所在目录（开发环境）

## 卸载

```bash
cd /home/admin/consoled/systemd_files/dte
sudo ./uninstall.sh
```
