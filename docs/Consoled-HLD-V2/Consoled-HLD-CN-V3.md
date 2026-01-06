# SONiC Console Monitor

## High Level Design Document

### Revision 1.0

---

## 目录

- [术语与缩写](#术语与缩写)
- [1. 功能概述](#1-功能概述)
  - [1.1 功能需求](#11-功能需求)
  - [1.2 设计目标](#12-设计目标)
- [2. 设计概述](#2-设计概述)
  - [2.1 架构](#21-架构)
  - [2.2 DTE 侧](#22-dte-侧)
  - [2.3 DCE 侧](#23-dce-侧)
- [3. 详细设计](#3-详细设计)
  - [3.1 心跳帧设计](#31-心跳帧设计)
  - [3.2 DTE 侧服务](#32-dte-侧服务)
  - [3.3 DCE 侧 Console Monitor 服务](#33-dce-侧-console-monitor-服务)
- [4. 数据库更改](#4-数据库更改)
- [5. CLI](#5-cli)
- [6. 流程图](#6-流程图)
- [7. 参考资料](#7-参考资料)

---

## 术语与缩写

| 术语 | 定义 |
|------|------|
| DCE | Data Communications Equipment - Console Server 侧 |
| DTE | Data Terminal Equipment - SONiC Switch（被管理设备）侧 |
| Heartbeat | 用于验证链路连通性的周期性信号 |
| Oper | 运行状态（Up/Down） |
| PTY | Pseudo Terminal - 虚拟终端接口 |
| Proxy | 处理串口通信的中间代理进程 |
| TTY | Teletypewriter - 终端设备接口 |

---

## 1. 功能概述

在数据中心网络中，Console Server（DCE）通过串口直连多台 SONiC Switch（DTE），用于故障时的带外管理与控制台接入。consoled 服务提供链路 Oper 状态探测功能。

### 1.1 功能需求

    连通性检测（Heartbeat）
        判断 DCE ↔ DTE 串口链路是否可用（Oper Up/Down）

    非侵入式（Non-Interference）
        不影响正常 Console 运维，包括远程设备冷重启和系统重装

    高可用与持久化（HA & Persistence）
        进程/系统重启后可恢复状态
        对端重启后可自动恢复探测

### 1.2 设计目标

| 目标 | 描述 |
|------|------|
| 可靠性 | 准确的链路状态检测，最小化误报/漏报 |
| 非侵入 | 对正常控制台操作零影响 |
| 低开销 | 最小化资源消耗和用户侧延迟 |
| 自动恢复 | 任意一侧重启后自动恢复 |

---

## 2. 设计概述

### 2.1 架构

```mermaid
flowchart LR
  subgraph DCE["DCE (Console Server)"]
    proxy_dce["proxy"]
    picocom["picocom (user)"]
    pty_master["pty_master"]
    pty_slave["pty_slave"]
    TTY_DCE["/dev/tty_dce (physical serial)"]
  end

  subgraph DTE["DTE (SONiC Switch)"]
    serial_getty["serial-getty@tty_dte.service"]
    hb_sender["console-heartbeat.service"]
    TTY_DTE["/dev/tty_dte (physical serial)"]
  end

  %% DTE side: services attached to the physical serial
  serial_getty <-- read/write --> TTY_DTE
  hb_sender -- heartbeat --> TTY_DTE

  %% physical link
  TTY_DCE <-- serial link --> TTY_DTE

  %% DCE side: proxy owns serial, filters RX, bridges to PTY for user tools
  TTY_DCE <-- read/write --> proxy_dce
  proxy_dce -- filter heartbeat and forward --> pty_master
  pty_master -- forward --> proxy_dce
  pty_master <-- PTY pair --> pty_slave
  picocom <-- interactive session --> pty_slave
```

设计核心：将 DCE 侧直接的"用户 ↔ 串口"访问模式转变为"用户 ↔ Proxy ↔ 串口"模式。

### 2.2 DTE 侧

DTE 周期性向串口发送特定格式的心跳帧。

    单向数据流
        DTE → DCE 方向，保证 DTE 重启阶段不会收到 DCE 侧协议干扰数据

    碰撞风险
        正常数据流中可能包含心跳帧格式的数据，导致误判
        通过心跳帧设计降低碰撞概率

### 2.3 DCE 侧

在物理串口和用户应用之间创建 Proxy，负责心跳帧检测、过滤和链路状态维护。

    独占权
        唯一持有物理串口文件描述符（`/dev/ttyUSBx`）的进程

    PTY 创建
        为上层应用创建伪终端对

    PTY 符号链接
        创建固定符号链接（如 `/dev/VC0-1`）指向动态 PTY slave（如 `/dev/pts/3`）
        上层应用（consutil、picocom）使用稳定的设备路径

    心跳过滤
        识别心跳帧，更新状态，并丢弃心跳数据

    数据透传
        非心跳数据透明转发到虚拟串口

---

## 3. 详细设计

### 3.1 心跳帧设计

#### 3.1.1 设计原则

    可靠检测
        可从任意字节流中区分心跳帧
        避免 read() 分块截断导致误判

    低碰撞率
        避免将用户正常输出误判为心跳帧

    可扩展性
        支持版本控制和未来功能扩展

#### 3.1.2 特殊字符定义

| 字符 | 值 (Hex) | 名称 | 描述 |
|------|----------|------|------|
| SOF | 0x01 | Start of Frame | 帧起始标志 |
| EOF | 0x04 | End of Frame | 帧结束标志 |
| DLE | 0x10 | Data Link Escape | 转义字符 |

#### 3.1.3 转义规则

当帧内容（SOF 和 EOF 之间）包含特殊字符时，需要进行转义：

| 原始字节 | 转义后 |
|----------|--------|
| 0x01 | 0x10 0x01 |
| 0x04 | 0x10 0x04 |
| 0x10 | 0x10 0x10 |

#### 3.1.4 帧格式

```
+-----+--------+-----+------+------+--------+---------+-------+-----+
| SOF | Version| Seq | Flag | Type | Length | Payload | CRC16 | EOF |
+-----+--------+-----+------+------+--------+---------+-------+-----+
| 1B  |   1B   | 1B  |  1B  |  1B  |   1B   |   N B   |  2B   | 1B  |
+-----+--------+-----+------+------+--------+---------+-------+-----+
```

| 字段 | 大小 | 描述 |
|------|------|------|
| SOF | 1 字节 | 帧起始，固定 0x01 |
| Version | 1 字节 | 协议版本，当前为 0x01 |
| Seq | 1 字节 | 序列号，0x00-0xFF 循环递增 |
| Flag | 1 字节 | 标志位，保留字段，当前为 0x00 |
| Type | 1 字节 | 帧类型 |
| Length | 1 字节 | Payload 长度（0-255） |
| Payload | N 字节 | 可选数据载荷 |
| CRC16 | 2 字节 | 校验和，大端序（高字节在前） |
| EOF | 1 字节 | 帧结束，固定 0x04 |

**CRC16 计算：**

    算法
        CRC-16/MODBUS（多项式 0x8005，初始值 0xFFFF，反射输入/输出）

    计算范围
        从 Version 到 Payload（不包括 SOF、CRC16、EOF）

    字节序
        大端序（高字节在前，低字节在后）

#### 3.1.5 帧类型定义

| Type | 值 (Hex) | 描述 |
|------|----------|------|
| HEARTBEAT | 0x01 | 心跳帧 |
| 保留 | 0x02-0xFF | 未来扩展 |

#### 3.1.6 心跳帧示例

心跳帧无 Payload，最小帧长度为 9 字节：

```
01 01 00 00 01 00 XX XX 04
│  │  │  │  │  │  └──┴── CRC16 (计算值)
│  │  │  │  │  └──────── Length: 0 (无 payload)
│  │  │  │  └─────────── Type: HEARTBEAT (0x01)
│  │  │  └────────────── Flag: 0x00
│  │  └───────────────── Seq: 0x00 (序列号)
│  └──────────────────── Version: 0x01
└─────────────────────── SOF
```

**CRC16 计算示例：**

    输入数据（Version 到 Length）
        01 00 00 01 00

    CRC16 结果
        使用 CRC-16/MODBUS 算法计算

---

### 3.2 DTE 侧服务

#### 3.2.1 服务: `console-heartbeat@<DEVICE_NAME>.service`

DTE 侧服务以固定 5 秒间隔周期性发送心跳帧。

    发送周期
        固定 5 秒

    服务实例
        使用 systemd 模板单元按串口生成

    自动激活
        由 systemd generator 根据内核命令行参数创建

#### 3.2.2 服务启动与管理

DTE 侧服务使用 systemd generator 根据内核命令行参数中的串口配置自动创建 `console-heartbeat@.service` 实例。

Generator 读取这些参数，在 `/run/systemd/generator/` 下创建对应的 wants 链接，使每个服务实例无需手动配置即可周期性发送心跳帧。

```mermaid
flowchart TD
  A["Bootloader 启动 Linux 内核"] --> B["内核解析命令行"]
  B --> C["/proc/cmdline 可用"]
  C --> D["systemd (PID 1) 启动"]
  D --> E["systemd 加载单元并运行 generators"]
  E --> F["console-heartbeat-generator 运行"]
  F --> G["generator 读取 /proc/cmdline"]
  G --> H["generator 发现 console=DEVICE,9600"]
  H --> I["generator 在 /run/systemd/generator/multi-user.target.wants 下创建 wants 链接"]
  I --> J["systemd 构建依赖图"]
  J --> K["multi-user.target 启动"]
  K --> L["console-heartbeat@DEVICE.service 被拉起"]
  L --> M["console-heartbeat@.service 模板被实例化"]
  M --> N["ExecStart 运行 console-heartbeat-daemon"]
  N --> O["daemon 打开 /dev/DEVICE"]
  O --> P["每 5s 向 /dev/DEVICE 写入心跳"]
```

---

### 3.3 DCE 侧 Console Monitor 服务

#### 3.3.1 服务: `console-monitor.service`

拓扑：

![Console Monitor Structure](ConsoleMonitorStructure.png)

每条链路有独立的 Proxy 实例，负责串口读写与状态维护。

#### 3.3.2 超时判定

超时周期默认 15 秒。如果在此期间未收到心跳，链路状态判定为 Down。

#### 3.3.3 心跳帧检测与过滤

为应对 read() 调用可能返回部分心跳帧的情况，实现了滑动缓冲区机制（类似 KMP 算法）：

算法：

    Buffer 大小
        `heartbeat_length - 1` 字节

    数据读取时
        将新数据追加到滑动缓冲区末尾

    模式匹配
        检测到完整心跳 → 更新心跳计时器，清空 buffer
        前缀匹配失败或 buffer 满 → 清空 buffer，透传数据
        1 秒内未匹配成功 → 透传 buffer 内容，防止数据阻塞

状态图：

```mermaid
flowchart TD
    A["读取数据"] --> B["追加到 Buffer"]
    B --> C{"检测到心跳?"}
    C -->|是| D["更新计时器, 清空 Buffer"]
    C -->|否| E{"前缀匹配失败或 Buffer 满?"}
    E -->|是| F["清空 Buffer, 透传数据"]
    E -->|否| G{"超时 1s?"}
    G -->|是| H["透传 Buffer 内容"]
    G -->|否| I["等待更多数据"]
```

#### 3.3.4 Oper 状态判定

每条链路维护独立状态。收到心跳时，Proxy 更新心跳计时器并将 oper 状态设为 UP。每 15 秒执行定时检查，如果最近 15 秒内未收到心跳，oper 状态设为 DOWN。状态变更写入 STATE_DB。

STATE_DB 条目：

    Key: `CONSOLE_PORT|<link_id>`
    Field: `oper_state`, Value: `up` / `down`
    Field: `last_heartbeat`, Value: `<timestamp>`

#### 3.3.5 服务启动与初始化

console-monitor 服务按以下顺序启动：

    1. 等待依赖
        在 `config-setup.service` 完成将 config.json 加载到 CONFIG_DB 后启动

    2. 连接 Redis
        建立到 CONFIG_DB 和 STATE_DB 的连接

    3. 检查 Console 功能
        验证 CONFIG_DB 中 `CONSOLE_SWITCH|console_mgmt` 的 `enabled` 字段是否为 `"yes"`
        如禁用则立即退出

    4. 读取 PTY 符号链接前缀
        从 `<platform_path>/udevprefix.conf` 读取设备前缀（如 `C0-`）
        构造虚拟设备前缀 `/dev/V<prefix>`（如 `/dev/VC0-`）

    5. 初始化 Proxy 实例
        为 CONFIG_DB 中的每个串口配置：
        - 打开物理串口（如 `/dev/C0-1`）
        - 创建 PTY 对（master/slave，如 `/dev/pts/X`）
        - 创建符号链接（如 `/dev/VC0-1` → `/dev/pts/3`）
        - 配置串口和 PTY 为 raw 模式
        - 将文件描述符注册到 asyncio 事件循环
        - 启动心跳超时定时器（15 秒）

    6. 订阅配置变更
        监听 CONFIG_DB keyspace 事件以动态重配置

    7. 进入主循环
        处理串口数据，过滤心跳，更新 STATE_DB

    8. 初始状态
        15 秒内无心跳，`oper_state` 设为 `down`
        收到首个心跳后，`oper_state` 变为 `up`，记录 `last_heartbeat` 时间戳

#### 3.3.6 动态配置变更

    监听 CONFIG_DB 配置变更事件
    动态添加、删除或重启链路的 Proxy 实例

#### 3.3.7 服务关闭与清理

当 console-monitor 服务收到关闭信号（SIGINT/SIGTERM）时，每个 proxy 执行清理：

    STATE_DB 清理
        仅删除 `oper_state` 和 `last_heartbeat` 字段
        保留 consutil 管理的 `state`、`pid`、`start_time` 字段

    PTY 符号链接
        删除符号链接（如 `/dev/VC0-1`）

    Buffer 刷新
        如 filter buffer 非空，刷新到 PTY

---

## 4. 数据库更改

### 4.1 STATE_DB

表: CONSOLE_PORT_TABLE

| Key 格式 | Field | Value | 描述 |
|----------|-------|-------|------|
| `CONSOLE_PORT|<link_id>` | `oper_state` | `up` / `down` | 链路运行状态 |
| `CONSOLE_PORT|<link_id>` | `last_heartbeat` | `<timestamp>` | 最后心跳接收时间 |

---

## 5. CLI

`show line` 命令增加链路 Oper 状态显示：

```
admin@sonic:~$ show line
```

输出：

```
  Line    Baud    Flow Control    PID    Start Time      Device    Oper Status          Last Heartbeat
------  ------  --------------  -----  ------------  ----------  -------------  ----------------------
     1    9600        Disabled      -             -   Terminal1             up  12/31/2025 10:11:29 PM
     2    9600        Disabled      -             -   Terminal2           down                       -
```

新增列：

| 列名 | 描述 |
|------|------|
| Oper Status | 控制台链路当前运行状态 |
| Last Heartbeat | 最近一次心跳接收的时间戳 |

---

## 6. 流程图

### 6.1 心跳帧检测与过滤流程

```mermaid
sequenceDiagram
    participant Serial as 串口
    participant Proxy
    participant Buffer
    participant PTY
    participant Timer as Buffer 刷新定时器
    participant HB as 心跳定时器
    participant Redis as STATE_DB

    Note over Serial,Redis: Case 1: 完整匹配
    Serial->>Proxy: read() 返回字节
    Proxy->>Buffer: 追加字节
    Proxy->>Proxy: 检查模式匹配
    Proxy-->>Proxy: 检测到完整匹配
    Proxy->>HB: 重置心跳定时器 (15s)
    Proxy->>Redis: 更新 oper_state=UP, last_heartbeat=now
    Proxy->>Buffer: 清空 buffer
    Note over Buffer: 心跳字节被丢弃

    Note over Serial,Redis: Case 2: 前缀匹配（部分）
    Serial->>Proxy: read() 返回字节
    Proxy->>Buffer: 追加字节
    Proxy->>Proxy: 检查模式匹配
    Proxy-->>Proxy: 前缀匹配（等待更多数据）
    Proxy->>Timer: 启动超时定时器 (1s)
    Note over Proxy: 等待下次 read...

    Note over Serial,Redis: Case 2a: 前缀匹配完成
    Serial->>Proxy: read() 返回剩余字节
    Proxy->>Timer: 取消超时定时器
    Proxy->>Buffer: 追加字节
    Proxy->>Proxy: 检查模式匹配
    Proxy-->>Proxy: 检测到完整匹配
    Proxy->>HB: 重置心跳定时器 (15s)
    Proxy->>Redis: 更新 oper_state=UP, last_heartbeat=now
    Proxy->>Buffer: 清空 buffer

    Note over Serial,Redis: Case 2b: 匹配完成前超时
    Timer-->>Proxy: 超时触发 (1s)
    Proxy->>PTY: 刷新 buffer 内容
    Proxy->>Buffer: 清空 buffer 并重置匹配位置

    Note over Serial,Redis: Case 3: 前缀不匹配
    Serial->>Proxy: read() 返回字节
    Proxy->>Buffer: 追加字节
    Proxy->>Proxy: 检查模式匹配
    Proxy-->>Proxy: 在位置 N 前缀不匹配
    Proxy->>PTY: 输出不匹配的前缀字节
    Proxy->>Buffer: 移除已输出字节，保留剩余
    Proxy->>Proxy: 继续匹配

    Note over Serial,Redis: Case 4: 心跳超时
    HB-->>HB: 15s 无重置
    HB->>Redis: 更新 oper_state=DOWN
    Note over Redis: last_heartbeat 保持不变
```

---

## 7. 参考资料

1. [SONiC Console Switch High Level Design](https://github.com/sonic-net/SONiC/blob/master/doc/console/SONiC-Console-Switch-High-Level-Design.md#scope)
2. [Systemd Generator Man Page](https://www.freedesktop.org/software/systemd/man/systemd.generator.html)
3. [Systemd Getty Generator Source Code](https://github.com/systemd/systemd/blob/main/src/getty-generator/getty-generator.c)
4. [Getty Explanation](https://0pointer.de/blog/projects/serial-console.html)
5. [ASCII Code](https://www.ascii-code.com/)
