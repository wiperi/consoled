# Console Monitor 测试计划

## 1. 概述

本文档定义 console_monitor 项目的完整测试策略，涵盖单元测试、集成测试和端到端测试。

### 1.1 项目模块概览

| 模块 | 功能 | 测试状态 |
|------|------|---------|
| `frame.py` | 帧协议：构建、解析、CRC、转义、过滤 | ✅ 已完成 |
| `util.py` | 工具函数：串口配置、PTY 配置 | ❌ 待测试 |
| `constants.py` | 常量定义 | ❌ 待测试 |
| `db_util.py` | Redis 数据库操作封装 | ❌ 待测试 |
| `serial_proxy.py` | DCE 侧串口代理 | ❌ 待测试 |
| `dce.py` | DCE 侧主服务（ProxyManager） | ❌ 待测试 |
| `dte.py` | DTE 侧心跳服务 | ❌ 待测试 |

---

## 2. 测试分层策略

```
┌─────────────────────────────────────────────────────────────┐
│                    端到端测试 (E2E)                         │
│    完整 DTE ↔ DCE 心跳通信，Redis 状态更新验证              │
├─────────────────────────────────────────────────────────────┤
│                    集成测试 (Integration)                   │
│    模块间交互：SerialProxy + DbUtil + FrameFilter          │
├─────────────────────────────────────────────────────────────┤
│                    单元测试 (Unit)                          │
│    各模块独立功能测试，使用 Mock 隔离外部依赖               │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 单元测试计划

### 3.1 `frame.py` - 帧协议测试 ✅

**文件**: `tests/test_frame.py`

| 测试类 | 测试用例 | 描述 |
|--------|---------|------|
| `TestCRC16` | `test_crc16_empty_data` | 测试空数据 CRC 计算 |
| | `test_crc16_known_value` | 测试 "123456789" 的 CRC-16/MODBUS 值应为 0x4B37 |
| | `test_crc16_single_byte` | 测试单字节数据 CRC 计算 |
| `TestEscaping` | `test_escape_no_special_chars` | 测试不含特殊字符的数据 |
| | `test_escape_sof` | 测试 SOF 字符转义 |
| | `test_escape_eof` | 测试 EOF 字符转义 |
| | `test_escape_dle` | 测试 DLE 字符转义 |
| | `test_escape_mixed` | 测试混合数据转义 |
| | `test_unescape_roundtrip` | 测试转义和去转义往返 |
| | `test_unescape_no_escape` | 测试不含转义的数据 |
| `TestFrame` | `test_create_heartbeat` | 测试创建心跳帧 |
| | `test_build_heartbeat` | 测试构建心跳帧 |
| | `test_build_and_parse_heartbeat` | 测试心跳帧的构建和解析 |
| | `test_build_and_parse_with_payload` | 测试带 payload 的帧 |
| | `test_build_and_parse_with_special_chars_in_payload` | 测试 payload 包含特殊字符 |
| | `test_parse_invalid_buffer_too_short` | 测试解析过短的 buffer |
| | `test_parse_invalid_crc` | 测试 CRC 错误的帧 |
| | `test_seq_wrapping` | 测试序列号回绕 |
| `TestFrameFilter` | `test_process_heartbeat_frame` | 测试处理心跳帧 |
| | `test_process_user_data_only` | 测试只有用户数据（遇到 SOF 后刷新） |
| | `test_process_user_data_with_timeout` | 测试用户数据超时刷新 |
| | `test_process_mixed_data` | 测试混合数据流 |
| | `test_process_multiple_frames` | 测试连续多个帧 |
| | `test_process_frame_byte_by_byte` | 测试逐字节处理帧 |
| | `test_flush` | 测试 flush 方法 |
| | `test_invalid_frame_discarded` | 测试无效帧被丢弃 |
| | `test_in_frame_property` | 测试 in_frame 属性 |
| | `test_timeout_in_frame_discards_data` | 测试在帧内超时时丢弃数据 |
| | `test_timeout_not_in_frame_sends_user_data` | 测试不在帧内超时时发送用户数据 |
| | `test_sof_in_frame_discards_previous_data` | 测试在帧内收到 SOF 时丢弃之前的数据 |
| | `test_sof_not_in_frame_sends_user_data` | 测试不在帧内收到 SOF 时发送用户数据 |
| `TestBuildHeartbeatFrame` | `test_build_heartbeat_frame` | 测试 build_heartbeat_frame 便捷函数 |

**测试覆盖功能**:
- CRC16-MODBUS 计算
- 特殊字符转义/去转义 (SOF, EOF, DLE)
- Frame 类：创建、构建、解析心跳帧
- FrameFilter 类：帧过滤、用户数据分离、超时处理

---

### 3.2 `util.py` - 工具函数测试

**文件**: `tests/test_util.py`

| 测试类 | 测试用例 | 描述 |
|--------|---------|------|
| `TestGetPtySymlinkPrefix` | `test_default_prefix` | 配置文件不存在时返回默认值 `/dev/VC0-` |
| | `test_read_from_config` | 从 udevprefix.conf 读取前缀 |
| | `test_handles_read_error` | 读取失败时返回默认值 |
| `TestSetNonblocking` | `test_set_nonblocking` | 验证 O_NONBLOCK 标志被设置 |
| `TestConfigureSerial` | `test_configure_baud_9600` | 配置 9600 波特率 |
| | `test_configure_baud_115200` | 配置 115200 波特率 |
| | `test_invalid_baud_uses_default` | 无效波特率使用默认值 |
| | `test_raw_mode_settings` | 验证 raw 模式参数 |
| `TestConfigurePty` | `test_configure_pty_raw_mode` | PTY 配置为 raw 模式 |
| | `test_echo_disabled` | 回显被禁用 |

**Mock 需求**:
- `os.open`, `fcntl.fcntl`, `termios.tcgetattr`, `termios.tcsetattr`
- `device_info.get_paths_to_platform_and_hwsku_dirs`

---

### 3.3 `constants.py` - 常量测试

**文件**: `tests/test_constants.py`

| 测试类 | 测试用例 | 描述 |
|--------|---------|------|
| `TestBaudMap` | `test_all_standard_bauds_exist` | 验证所有标准波特率都有映射 |
| | `test_baud_values_are_termios_constants` | 验证值是 termios 常量 |
| `TestRedisConfig` | `test_redis_defaults` | 验证 Redis 默认配置值 |
| `TestTimeouts` | `test_timeout_values_positive` | 超时值为正数 |

---

### 3.4 `db_util.py` - 数据库工具测试

**文件**: `tests/test_db_util.py`

| 测试类 | 测试用例 | 描述 |
|--------|---------|------|
| `TestDbUtilConnect` | `test_connect_success` | 成功连接 Redis |
| | `test_connect_failure` | 连接失败处理 |
| | `test_connect_sets_decode_responses` | decode_responses=True |
| `TestDbUtilClose` | `test_close_all_connections` | 关闭所有连接 |
| | `test_close_with_pubsub` | 关闭包含 pubsub 的连接 |
| | `test_close_idempotent` | 多次调用 close 安全 |
| `TestCheckConsoleFeatureEnabled` | `test_enabled_yes` | enabled=yes 返回 True |
| | `test_enabled_no` | enabled=no 返回 False |
| | `test_enabled_missing` | 字段缺失返回 False |
| | `test_redis_error` | Redis 错误返回 False |
| `TestSubscribeConfigChanges` | `test_subscribe_pattern` | 订阅正确的 keyspace pattern |
| `TestGetConfigEvent` | `test_get_message_returns_event` | 返回配置变更事件 |
| | `test_get_message_timeout` | 超时返回 None |
| `TestGetAllConfigs` | `test_get_multiple_configs` | 获取多个串口配置 |
| | `test_parse_baud_rate` | 正确解析波特率 |
| | `test_default_baud_rate` | 缺失时使用默认波特率 |
| | `test_empty_configs` | 无配置返回空字典 |
| `TestUpdateState` | `test_update_state_up` | 更新状态为 up，包含时间戳 |
| | `test_update_state_down` | 更新状态为 down |
| | `test_update_state_redis_error` | Redis 错误不抛异常 |
| `TestCleanupState` | `test_cleanup_removes_fields` | 清理指定字段 |
| | `test_cleanup_preserves_other_fields` | 保留其他字段 |

**Mock 需求**:
- `redis.asyncio.Redis` - 使用 `fakeredis` 或 `unittest.mock.AsyncMock`

---

### 3.5 `serial_proxy.py` - 串口代理测试

**文件**: `tests/test_serial_proxy.py`

| 测试类 | 测试用例 | 描述 |
|--------|---------|------|
| `TestSerialProxyInit` | `test_init_sets_attributes` | 初始化设置正确属性 |
| | `test_init_fd_defaults` | 文件描述符初始为 -1 |
| `TestSerialProxyStart` | `test_start_opens_pty` | 创建 PTY 对 |
| | `test_start_opens_serial` | 打开串口设备 |
| | `test_start_creates_symlink` | 创建符号链接 |
| | `test_start_configures_serial` | 配置串口参数 |
| | `test_start_registers_readers` | 注册事件循环读取器 |
| | `test_start_initializes_filter` | 初始化 FrameFilter |
| | `test_start_failure_cleanup` | 启动失败时清理资源 |
| `TestSerialProxyStop` | `test_stop_closes_fds` | 关闭所有文件描述符 |
| | `test_stop_removes_symlink` | 删除符号链接 |
| | `test_stop_removes_readers` | 移除事件循环读取器 |
| | `test_stop_flushes_filter` | 刷新过滤器缓冲区 |
| | `test_stop_cleans_state_db` | 清理 STATE_DB |
| `TestOnSerialRead` | `test_heartbeat_frame_received` | 收到心跳帧触发回调 |
| | `test_user_data_forwarded_to_pty` | 用户数据转发到 PTY |
| | `test_mixed_data_processed` | 混合数据正确处理 |
| | `test_read_error_handled` | 读取错误不崩溃 |
| `TestOnFrameReceived` | `test_heartbeat_resets_timer` | 心跳帧重置定时器 |
| | `test_heartbeat_updates_state_up` | 心跳帧更新状态为 up |
| | `test_unknown_frame_logged` | 未知帧类型记录日志 |
| `TestOnUserDataReceived` | `test_data_written_to_pty` | 数据写入 PTY |
| | `test_write_error_handled` | 写入错误不崩溃 |
| `TestHeartbeatTimer` | `test_reset_heartbeat_timer` | 重置定时器 |
| | `test_heartbeat_timeout_updates_down` | 超时更新状态为 down |
| `TestFilterTimeout` | `test_timeout_triggers_user_data` | 超时触发用户数据发送 |
| `TestOnPtyRead` | `test_pty_data_forwarded_to_serial` | PTY 数据转发到串口 |
| `TestSymlink` | `test_create_symlink` | 创建符号链接 |
| | `test_create_symlink_overwrites_existing` | 覆盖已存在链接 |
| | `test_remove_symlink` | 删除符号链接 |

**Mock 需求**:
- `os.openpty`, `os.open`, `os.read`, `os.write`, `os.close`, `os.symlink`, `os.unlink`
- `asyncio.AbstractEventLoop`
- `DbUtil`

---

### 3.6 `dce.py` - DCE 主服务测试

**文件**: `tests/test_dce.py`

| 测试类 | 测试用例 | 描述 |
|--------|---------|------|
| `TestProxyManagerInit` | `test_init_defaults` | 初始化默认值 |
| `TestProxyManagerStart` | `test_start_connects_db` | 连接数据库 |
| | `test_start_checks_feature_enabled` | 检查功能是否启用 |
| | `test_start_exits_if_disabled` | 功能禁用时退出 |
| | `test_start_reads_pty_prefix` | 读取 PTY 前缀 |
| | `test_start_syncs_proxies` | 初始同步代理 |
| | `test_start_subscribes_events` | 订阅配置事件 |
| `TestProxyManagerSync` | `test_sync_adds_new_proxies` | 添加新代理 |
| | `test_sync_removes_deleted_proxies` | 移除已删除代理 |
| | `test_sync_updates_changed_proxies` | 更新配置变化的代理 |
| | `test_sync_ignores_unchanged_proxies` | 不影响未变化的代理 |
| `TestProxyManagerRun` | `test_run_processes_events` | 处理配置事件 |
| | `test_run_syncs_on_event` | 收到事件后同步 |
| | `test_run_stops_on_signal` | 收到信号后停止 |
| `TestProxyManagerStop` | `test_stop_all_proxies` | 停止所有代理 |
| | `test_stop_closes_db` | 关闭数据库连接 |
| | `test_stop_clears_proxies` | 清空代理字典 |

**Mock 需求**:
- `DbUtil`
- `SerialProxy`
- `get_pty_symlink_prefix`

---

### 3.7 `dte.py` - DTE 心跳服务测试

**文件**: `tests/test_dte.py`

| 测试类 | 测试用例 | 描述 |
|--------|---------|------|
| `TestReadConfigFile` | `test_read_baudrate` | 读取波特率 |
| | `test_file_not_found` | 文件不存在返回 None |
| | `test_invalid_format` | 格式错误返回 None |
| | `test_missing_baudrate` | 缺少 BAUDRATE 行返回 None |
| `TestParseArgs` | `test_parse_tty_only` | 只指定 tty |
| | `test_parse_tty_and_baud` | 指定 tty 和 baud |
| | `test_default_baud_from_config` | 从配置文件读取默认波特率 |
| | `test_default_baud_fallback` | 使用内置默认波特率 |
| | `test_missing_tty_error` | 缺少 tty 参数报错 |
| `TestDTEHeartbeatInit` | `test_init_sets_attributes` | 初始化设置正确属性 |
| | `test_init_defaults` | 默认值正确 |
| `TestDTEHeartbeatStart` | `test_start_opens_serial` | 打开串口 |
| | `test_start_configures_serial` | 配置串口参数 |
| | `test_start_connects_redis` | 连接 Redis |
| | `test_start_checks_enabled` | 检查 enabled 状态 |
| | `test_start_subscribes_keyspace` | 订阅 keyspace notification |
| | `test_start_starts_heartbeat_if_enabled` | enabled 时启动心跳 |
| | `test_start_no_heartbeat_if_disabled` | disabled 时不启动心跳 |
| | `test_start_failure_cleanup` | 启动失败时清理 |
| `TestDTEHeartbeatStop` | `test_stop_cancels_tasks` | 取消所有任务 |
| | `test_stop_closes_pubsub` | 关闭 pubsub |
| | `test_stop_closes_redis` | 关闭 Redis 连接 |
| | `test_stop_closes_serial` | 关闭串口 |
| `TestConnectRedis` | `test_connect_success` | 成功连接 |
| | `test_connect_failure` | 连接失败抛异常 |
| `TestCheckEnabled` | `test_enabled_yes` | enabled=yes 返回 True |
| | `test_enabled_no` | enabled=no 返回 False |
| | `test_enabled_missing` | 字段缺失返回 False |
| | `test_no_redis_returns_false` | 无 Redis 连接返回 False |
| `TestSubscribeLoop` | `test_subscribe_correct_channel` | 订阅正确的 channel |
| | `test_responds_to_state_change` | 响应状态变化 |
| | `test_starts_heartbeat_on_enable` | 启用时启动心跳 |
| | `test_stops_heartbeat_on_disable` | 禁用时停止心跳 |
| `TestHeartbeatLoop` | `test_sends_heartbeat_periodically` | 周期性发送心跳 |
| | `test_heartbeat_interval` | 验证发送间隔 |
| | `test_stops_when_disabled` | 禁用时停止 |
| | `test_stops_when_not_running` | 服务停止时停止 |
| `TestSendHeartbeat` | `test_builds_correct_frame` | 构建正确的心跳帧 |
| | `test_writes_to_serial` | 写入串口 |
| | `test_seq_increments` | 序列号递增 |
| | `test_seq_wraps_at_256` | 序列号在 256 处回绕 |
| | `test_write_error_handled` | 写入错误不崩溃 |

**Mock 需求**:
- `os.open`, `os.write`, `os.close`
- `redis.asyncio.Redis`, `redis.asyncio.PubSub`
- `argparse.ArgumentParser`

---

## 4. 集成测试计划

**文件**: `tests/test_integration.py`

### 4.1 SerialProxy + FrameFilter 集成

| 测试用例 | 描述 |
|---------|------|
| `test_heartbeat_frame_updates_state` | 心跳帧触发状态更新为 up |
| `test_heartbeat_timeout_updates_state_down` | 心跳超时触发状态更新为 down |
| `test_user_data_passthrough` | 用户数据正确透传 |
| `test_mixed_traffic` | 混合流量正确处理 |

### 4.2 ProxyManager + DbUtil 集成

| 测试用例 | 描述 |
|---------|------|
| `test_config_change_triggers_sync` | 配置变更触发同步 |
| `test_add_config_creates_proxy` | 添加配置创建代理 |
| `test_remove_config_stops_proxy` | 删除配置停止代理 |
| `test_update_config_restarts_proxy` | 更新配置重启代理 |

### 4.3 DTE Redis 集成

| 测试用例 | 描述 |
|---------|------|
| `test_keyspace_notification_triggers_heartbeat` | keyspace 通知触发心跳 |
| `test_enable_disable_toggle` | enabled 状态切换 |

---

## 5. 端到端测试计划

**文件**: `tests/test_e2e.py`

### 5.1 测试环境

使用虚拟串口对 (`socat`) 模拟 DTE ↔ DCE 连接：

```bash
# 创建虚拟串口对
socat -d -d pty,raw,echo=0,link=/tmp/dte_serial pty,raw,echo=0,link=/tmp/dce_serial
```

### 5.2 完整链路测试

| 测试用例 | 描述 | 验证点 |
|---------|------|--------|
| `test_dte_sends_heartbeat` | DTE 发送心跳帧 | DCE 侧收到帧 |
| `test_dce_filters_heartbeat` | DCE 过滤心跳 | PTY 不收到心跳数据 |
| `test_dce_updates_state_up` | DCE 更新状态 | STATE_DB 中 oper_state=up |
| `test_heartbeat_timeout` | 心跳超时 | STATE_DB 中 oper_state=down |
| `test_user_data_passthrough` | 用户数据透传 | PTY 收到正确数据 |
| `test_dte_restart_recovery` | DTE 重启恢复 | DCE 状态自动恢复 |
| `test_dce_restart_recovery` | DCE 重启恢复 | 重启后继续检测 |
| `test_enable_disable_cycle` | enabled 状态切换 | 心跳正确启停 |
| `test_concurrent_user_session` | 并发用户会话 | 用户操作不受影响 |
| `test_high_frequency_heartbeat` | 高频心跳 | 系统稳定 |

### 5.3 边界条件测试

| 测试用例 | 描述 |
|---------|------|
| `test_frame_in_user_data` | 用户数据包含帧格式数据 |
| `test_partial_frame_timeout` | 不完整帧超时处理 |
| `test_rapid_enable_disable` | 快速切换 enabled 状态 |
| `test_redis_disconnect_recovery` | Redis 断连恢复 |
| `test_serial_disconnect_recovery` | 串口断开恢复 |

---

## 6. 性能测试

| 测试用例 | 描述 | 指标 |
|---------|------|------|
| `test_throughput` | 数据吞吐量 | ≥ 115200 bps |
| `test_latency` | 数据延迟 | ≤ 10ms |
| `test_cpu_usage` | CPU 使用率 | ≤ 5% (idle) |
| `test_memory_usage` | 内存使用 | ≤ 50MB |
| `test_long_running_stability` | 长时间运行稳定性 | 24h 无内存泄漏 |

---

## 7. 测试依赖

### 7.1 Python 包

```
pytest>=7.0.0
pytest-asyncio>=0.21.0
pytest-cov>=4.0.0
pytest-mock>=3.10.0
fakeredis>=2.10.0
```

### 7.2 系统工具

- `socat` - 创建虚拟串口对
- `redis-server` - Redis 服务 (集成测试)

---

## 8. 测试执行

### 8.1 运行所有测试

```bash
cd /home/admin/consoled
pytest tests/ -v --cov=console_monitor --cov-report=html
```

### 8.2 运行特定模块测试

```bash
# 单元测试
pytest tests/test_util.py -v
pytest tests/test_db_util.py -v
pytest tests/test_serial_proxy.py -v

# 集成测试
pytest tests/test_integration.py -v

# 端到端测试
pytest tests/test_e2e.py -v
```

### 8.3 CI/CD 集成

```yaml
# .github/workflows/test.yml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    services:
      redis:
        image: redis:7
        ports:
          - 6379:6379
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v4
        with:
          python-version: '3.10'
      - run: pip install -r requirements-test.txt
      - run: pytest tests/ -v --cov=console_monitor
```

---

## 9. 测试优先级

| 优先级 | 模块 | 原因 |
|--------|------|------|
| P0 (最高) | `db_util.py` | 核心数据库操作，影响所有功能 |
| P0 | `serial_proxy.py` | DCE 核心逻辑 |
| P0 | `dte.py` | DTE 核心逻辑 |
| P1 | `dce.py` | 服务管理逻辑 |
| P1 | 集成测试 | 验证模块交互 |
| P2 | `util.py` | 工具函数，较稳定 |
| P2 | `constants.py` | 静态常量 |
| P2 | 端到端测试 | 完整链路验证 |
| P3 | 性能测试 | 优化阶段 |

---

## 10. 测试覆盖率目标

| 类型 | 目标覆盖率 |
|------|-----------|
| 行覆盖率 | ≥ 80% |
| 分支覆盖率 | ≥ 70% |
| 核心模块 (`serial_proxy.py`, `dte.py`) | ≥ 90% |
