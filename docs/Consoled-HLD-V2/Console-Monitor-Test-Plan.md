# Console Monitor Test Plan

## 1. 测试环境

### 1.1 硬件要求

| 设备 | 数量 | 说明 |
|------|------|------|
| Console Server (DCE) | 1 | 运行 console-monitor 服务 |
| SONiC Switch (DTE) | 1+ | 运行 console-heartbeat 服务 |
| 串口线 | 1+ | 连接 DCE 和 DTE |

### 1.2 软件要求

- DCE: SONiC with console-monitor.service installed
- DTE: SONiC with console-heartbeat@.service installed
- Redis: CONFIG_DB and STATE_DB available
- CLI: `show line` command available

### 1.3 测试前置条件

1. CONFIG_DB 中已配置 `CONSOLE_SWITCH|console_mgmt` enabled="yes"
2. CONFIG_DB 中已配置 `CONSOLE_PORT|<link_id>` 条目
3. 物理串口设备存在 (e.g., `/dev/C0-1`)

---

## 2. 功能测试用例

### 2.1 服务启动测试

#### TC-2.1.1 正常启动

| 项目 | 内容 |
|------|------|
| **前置条件** | CONFIG_DB 配置正确，console_mgmt enabled="yes" |
| **测试步骤** | 1. `sudo systemctl start console-monitor` |
| **预期结果** | 服务启动成功，日志显示 "Connected to Redis"，PTY symlink 创建成功 |
| **验证方法** | `systemctl status console-monitor`<br>`ls -la /dev/VC0-*` |

#### TC-2.1.2 功能禁用时启动

| 项目 | 内容 |
|------|------|
| **前置条件** | CONFIG_DB 中 `CONSOLE_SWITCH|console_mgmt` enabled="no" |
| **测试步骤** | 1. `sudo systemctl start console-monitor` |
| **预期结果** | 服务立即退出，日志显示 "Console feature is disabled" |
| **验证方法** | `journalctl -u console-monitor --no-pager -n 20` |

#### TC-2.1.3 无配置时启动

| 项目 | 内容 |
|------|------|
| **前置条件** | CONFIG_DB 中无 CONSOLE_PORT 条目 |
| **测试步骤** | 1. 启动 console-monitor |
| **预期结果** | 服务启动成功但无 proxy 实例，等待配置变更 |
| **验证方法** | 日志显示 "No console ports configured" |

---

### 2.2 心跳检测测试

#### TC-2.2.1 心跳正常接收 - 状态 UP

| 项目 | 内容 |
|------|------|
| **前置条件** | DCE 和 DTE 串口物理连接正常，DTE 心跳服务运行中 |
| **测试步骤** | 1. 启动 console-monitor<br>2. 等待 5-10 秒 |
| **预期结果** | STATE_DB 中 oper_state="up"，last_heartbeat 有时间戳 |
| **验证方法** | `redis-cli -n 6 HGETALL "CONSOLE_PORT\|1"` |

#### TC-2.2.2 心跳超时 - 状态 DOWN

| 项目 | 内容 |
|------|------|
| **前置条件** | 串口连接正常，DTE 心跳服务已停止 |
| **测试步骤** | 1. 停止 DTE 心跳服务<br>2. 等待 15+ 秒 |
| **预期结果** | STATE_DB 中 oper_state="down" |
| **验证方法** | `redis-cli -n 6 HGET "CONSOLE_PORT\|1" oper_state` |

#### TC-2.2.3 物理断开 - 状态 DOWN

| 项目 | 内容 |
|------|------|
| **前置条件** | 正常运行中，oper_state="up" |
| **测试步骤** | 1. 拔掉串口线<br>2. 等待 15+ 秒 |
| **预期结果** | STATE_DB 中 oper_state 变为 "down" |
| **验证方法** | `show line` 显示 Oper Status = down |

#### TC-2.2.4 物理重连 - 状态恢复 UP

| 项目 | 内容 |
|------|------|
| **前置条件** | 串口已断开，oper_state="down" |
| **测试步骤** | 1. 重新插入串口线<br>2. 等待 5-10 秒 |
| **预期结果** | STATE_DB 中 oper_state 恢复为 "up" |
| **验证方法** | `show line` 显示 Oper Status = up |

---

### 2.3 心跳过滤测试

#### TC-2.3.1 心跳帧完全过滤

| 项目 | 内容 |
|------|------|
| **前置条件** | console-monitor 运行，用户通过 PTY 连接 |
| **测试步骤** | 1. `picocom /dev/VC0-1`<br>2. 观察终端输出 |
| **预期结果** | 用户终端不显示心跳字节 (0x8D 0x90 0x8F 0x9D) |
| **验证方法** | 使用 hexdump 监控 PTY 输出，确认无心跳字节 |

#### TC-2.3.2 普通数据透传

| 项目 | 内容 |
|------|------|
| **前置条件** | console-monitor 运行，用户通过 PTY 连接 |
| **测试步骤** | 1. 在 DTE 执行 `echo "test message"`<br>2. 观察 DCE 终端 |
| **预期结果** | "test message" 完整显示在用户终端 |
| **验证方法** | 目视确认输出正确 |

#### TC-2.3.3 心跳与数据混合

| 项目 | 内容 |
|------|------|
| **前置条件** | console-monitor 运行 |
| **测试步骤** | 1. DTE 快速输出大量文本（如 `dmesg`）<br>2. 同时心跳持续发送 |
| **预期结果** | 文本完整透传，心跳被过滤，无数据丢失或乱序 |
| **验证方法** | 对比 DTE 输出和 DCE 接收（去除心跳后应完全一致） |

---

### 2.4 PTY Symlink 测试

#### TC-2.4.1 Symlink 创建

| 项目 | 内容 |
|------|------|
| **前置条件** | console-monitor 未运行 |
| **测试步骤** | 1. 启动 console-monitor |
| **预期结果** | `/dev/VC0-<link_id>` symlink 被创建，指向 `/dev/pts/X` |
| **验证方法** | `ls -la /dev/VC0-*` |

#### TC-2.4.2 Symlink 删除

| 项目 | 内容 |
|------|------|
| **前置条件** | console-monitor 运行中，symlink 存在 |
| **测试步骤** | 1. `sudo systemctl stop console-monitor` |
| **预期结果** | `/dev/VC0-<link_id>` symlink 被删除 |
| **验证方法** | `ls -la /dev/VC0-*` 返回 "No such file" |

#### TC-2.4.3 用户通过 Symlink 连接

| 项目 | 内容 |
|------|------|
| **前置条件** | console-monitor 运行，symlink 存在 |
| **测试步骤** | 1. `picocom /dev/VC0-1 -b 9600` |
| **预期结果** | 成功建立交互式会话 |
| **验证方法** | 能正常输入输出，与直连串口体验一致 |

---

### 2.5 服务关闭测试

#### TC-2.5.1 正常关闭 - STATE_DB 清理

| 项目 | 内容 |
|------|------|
| **前置条件** | console-monitor 运行，STATE_DB 有 oper_state 和 last_heartbeat |
| **测试步骤** | 1. `sudo systemctl stop console-monitor` |
| **预期结果** | STATE_DB 中 oper_state 和 last_heartbeat 字段被删除 |
| **验证方法** | `redis-cli -n 6 HGETALL "CONSOLE_PORT\|1"` 不含这两个字段 |

#### TC-2.5.2 正常关闭 - consutil 字段保留

| 项目 | 内容 |
|------|------|
| **前置条件** | consutil 已连接某端口，STATE_DB 有 state/pid/start_time |
| **测试步骤** | 1. `sudo systemctl stop console-monitor` |
| **预期结果** | STATE_DB 中 state、pid、start_time 字段保留 |
| **验证方法** | `redis-cli -n 6 HGETALL "CONSOLE_PORT\|1"` 包含这些字段 |

#### TC-2.5.3 SIGINT 关闭 (Ctrl+C)

| 项目 | 内容 |
|------|------|
| **前置条件** | 前台运行 console-monitor |
| **测试步骤** | 1. 按 Ctrl+C |
| **预期结果** | 服务正常退出，STATE_DB 清理完成，symlink 删除 |
| **验证方法** | 检查 STATE_DB 和 /dev/VC0-* |

---

### 2.6 动态配置测试

#### TC-2.6.1 运行时添加端口

| 项目 | 内容 |
|------|------|
| **前置条件** | console-monitor 运行，仅配置 port 1 |
| **测试步骤** | 1. `redis-cli -n 4 HSET "CONSOLE_PORT\|2" remote_device "Terminal2" baud_rate "9600"` |
| **预期结果** | 自动创建新的 proxy 实例和 symlink |
| **验证方法** | `ls /dev/VC0-2` 存在 |

#### TC-2.6.2 运行时删除端口

| 项目 | 内容 |
|------|------|
| **前置条件** | console-monitor 运行，配置有 port 1 和 port 2 |
| **测试步骤** | 1. `redis-cli -n 4 DEL "CONSOLE_PORT\|2"` |
| **预期结果** | 对应 proxy 停止，symlink 删除，STATE_DB 清理 |
| **验证方法** | `/dev/VC0-2` 不存在 |

---

### 2.7 CLI 测试

#### TC-2.7.1 show line 显示 Oper Status

| 项目 | 内容 |
|------|------|
| **前置条件** | console-monitor 运行，有心跳 |
| **测试步骤** | 1. `show line` |
| **预期结果** | 输出包含 "Oper Status" 和 "Last Heartbeat" 列 |
| **验证方法** | 目视确认输出格式正确 |

---

## 3. 异常测试用例

### 3.1 错误恢复测试

#### TC-3.1.1 Redis 断开重连

| 项目 | 内容 |
|------|------|
| **前置条件** | console-monitor 运行正常 |
| **测试步骤** | 1. 重启 Redis<br>2. 等待 console-monitor 重连 |
| **预期结果** | 服务自动重连或优雅退出 |
| **验证方法** | 查看日志和服务状态 |

#### TC-3.1.2 串口设备消失

| 项目 | 内容 |
|------|------|
| **前置条件** | console-monitor 运行，监控 USB 串口 |
| **测试步骤** | 1. 拔掉 USB 串口适配器 |
| **预期结果** | 对应 proxy 停止，日志记录错误，其他端口不受影响 |
| **验证方法** | 其他端口仍正常工作 |

#### TC-3.1.3 DTE 重启恢复

| 项目 | 内容 |
|------|------|
| **前置条件** | 正常运行，oper_state="up" |
| **测试步骤** | 1. 重启 DTE 设备<br>2. 等待 DTE 启动完成 |
| **预期结果** | 重启期间 oper_state 变为 "down"，启动后恢复 "up" |
| **验证方法** | 监控 `show line` 输出变化 |

---

## 4. 性能测试用例

### 4.1 数据吞吐测试

#### TC-4.1.1 高速数据传输

| 项目 | 内容 |
|------|------|
| **前置条件** | 串口配置为 115200 baud |
| **测试步骤** | 1. DTE 连续输出大文件内容<br>2. DCE 通过 PTY 接收 |
| **预期结果** | 数据完整无丢失，延迟可接受 |
| **验证方法** | 对比发送和接收文件的 MD5 |

### 4.2 多端口测试

#### TC-4.2.1 多端口并发

| 项目 | 内容 |
|------|------|
| **前置条件** | 配置 8+ 个串口 |
| **测试步骤** | 1. 所有端口同时有数据传输<br>2. 运行 30 分钟 |
| **预期结果** | CPU/内存使用正常，无数据丢失 |
| **验证方法** | 监控系统资源，检查数据完整性 |

---

## 5. 长期稳定性测试

### 5.1 长时间运行

#### TC-5.1.1 7x24 运行测试

| 项目 | 内容 |
|------|------|
| **前置条件** | 正常配置环境 |
| **测试步骤** | 1. 运行 7 天<br>2. 期间模拟正常使用 |
| **预期结果** | 服务稳定，无内存泄漏，状态检测准确 |
| **验证方法** | 定期检查服务状态和系统资源 |

---

## 6. 测试命令速查

```bash
# 服务管理
sudo systemctl start console-monitor
sudo systemctl stop console-monitor
sudo systemctl status console-monitor
journalctl -u console-monitor -f

# STATE_DB 检查
redis-cli -n 6 KEYS "CONSOLE_PORT|*"
redis-cli -n 6 HGETALL "CONSOLE_PORT|1"
redis-cli -n 6 HGET "CONSOLE_PORT|1" oper_state
redis-cli -n 6 HGET "CONSOLE_PORT|1" last_heartbeat

# CONFIG_DB 检查
redis-cli -n 4 KEYS "CONSOLE_PORT|*"
redis-cli -n 4 HGETALL "CONSOLE_PORT|1"
redis-cli -n 4 HGET "CONSOLE_SWITCH|console_mgmt" enabled

# Symlink 检查
ls -la /dev/VC0-*
readlink /dev/VC0-1

# CLI 验证
show line

# PTY 连接测试
picocom /dev/VC0-1 -b 9600
```

---

## 7. 测试结果记录表

| 用例 ID | 测试项 | 结果 | 测试人 | 日期 | 备注 |
|---------|--------|------|--------|------|------|
| TC-2.1.1 | 正常启动 | | | | |
| TC-2.1.2 | 功能禁用启动 | | | | |
| TC-2.2.1 | 心跳正常接收 | | | | |
| TC-2.2.2 | 心跳超时 | | | | |
| TC-2.3.1 | 心跳帧过滤 | | | | |
| ... | ... | | | | |
