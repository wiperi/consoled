# consoled（基于串口的链路状态探测协议）

## 1. 项目背景（Background）与拓扑架构

在现有 Testbed 环境中，Console/OOB 管理链路由 Console Server（C0）通过串口（Serial）直连多台受控设备（SONiC Switch，DTE），用于在紧急故障场景下提供可靠的带外管理与控制台接入能力。

### 1.1 设备角色

- **C0（Console Device / Console Server）**  
    运行 SONiC 的控制台设备，为多台交换机提供 Console 接入能力，并作为链路状态探测的发起端与状态汇聚点（例如写入本机 Redis State DB/Config DB 供监控读取）。

- **DTE（SONiC Switch / 被管理交换机）**  
    运行 SONiC 的受控交换机设备，提供 Console/Serial 接口作为被探测链路的一端。每台交换机可视为一个独立的探测对象（一个 Serial Link）。

### 1.2 连接方式与协议方向

- **物理连接拓扑**：  
    C0 通过多条独立的串口线缆/串口通道分别连接到多台 DTE（SONiC Switch）。  
    表达为：C0 ↔ DTE1、C0 ↔ DTE2、…、C0 ↔ DTEn（多链路、多会话并行）。

- **协议角色**：
    - **Sender**：C0（发起心跳/探测报文）
    - **Receiver**：DTE（SONiC Switch）（接收并响应心跳/探测报文）

> **注**：本 PRD 假设每条 Serial Link 是一个相互独立的探测域；状态管理与非干扰控制应以"端口/链路"为粒度。

## 3. 核心目标（Core Objectives）

在 SONiC 环境中实现并部署 consoled，完成以下目标：

1. **状态定义与区分**
     - **Admin Status（期望状态）**：用户配置的 Enable/Disable。
     - **Oper Status（实际状态）**：串口链路实际连通性 Up/Down（由探测协议判定）。

2. **连通性检测（Heartbeat）**  
     通过心跳机制准确判断 C0 ↔ DTE 串口链路是否可用。

3. **非侵入式设计（Non-Interference）**  
     consoled 运行不得干扰正常 Console 运维操作（救火场景优先级最高）。

4. **高可用与持久化（HA & Persistence）**  
     支持进程重启、系统重启后状态自动恢复；对端重启后可自动重新握手并恢复探测。

## 4. 技术方案与难点攻克（Technical Solution & Challenges）

### 4.1 协议机制：心跳检测（Heartbeat Mechanism）

- **基本原理**：  
    C0 侧周期性向每个 DTE 的对应 Serial Link 发送特定心跳包；DTE 侧收到后返回应答。

- **判定逻辑（单链路）**：
    - 收到应答 → Oper Up
    - 超时未收到应答 → Oper Down

- **关键点**：
    - Sender 固定为 C0；Receiver 固定为 DTE（SONiC Switch）。
    - C0 作为状态汇聚点，对外输出每条链路的 Oper/Admin 状态（例如写入 C0 本机 Redis State DB/Config DB）。

### 4.2 难点一：工作流冲突处理（Interference Avoidance）

- **挑战**：  
    串口通常是独占/强约束资源。一旦运维人员进入 Console 会话，任何后台探测流量都可能干扰交互式输入输出，影响救援效率与正确性。

- **解决方案：互斥锁/暂停机制（Pause/Resume）**
    - 当检测到 Console User Session 建立（例如终端 attach/会话占用发生）时，consoled 必须**自动暂停探测**。
    - 当 Console 会话结束、资源释放后，consoled 自动恢复探测。

> **设计要求**：Pause/Resume 必须是确定性的，并且以"运维会话优先"为最高原则。

### 4.3 难点二：双端与进程重启的鲁棒性（Resilience）

- **挑战**：  
    C0 或 M0 可能重启；consoled 进程也可能崩溃、被升级重启或被 watchdog 拉起。协议必须避免"卡死在旧状态"或"需要人工介入恢复"。

- **解决方案**：
    1. **Daemon 管理**：  
         使用 systemd（或 supervisord）确保 consoled 异常退出后自动拉起。
    
    2. **握手重置与状态同步**：  
         任意一端重启后,协议应能自动重新建立探测会话（重新握手/重新同步），并在合理时间内恢复 Oper 状态判定。

## 5. 项目意义（Impact）

consoled 将补齐串口链路在"物理层缺乏 Link 感知"这一长期盲区，使 C0 ↔ M0 的 Console 链路状态具备接近以太网链路的可观测性与可靠性：

- 故障场景下，运维人员可准确获知 Console 链路是否可用，减少误判与无效操作。
- 提升带外管理路径的稳定性与可恢复性，缩短重大网络故障的恢复时间窗口。
- 为后续监控告警、自动化运维与容量/可靠性分析提供可信数据基础。
