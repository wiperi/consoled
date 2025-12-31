## 功能概述

在数据中心网络中，C0（Console Server, DCE）通过串口直连多台 DTE（SONiC Switch），用于故障时的带外管理与控制台接入。希望实现 consoled 来提供“链路 Oper 状态”探测，并满足：

连通性检测（Heartbeat）：判断 C0 ↔ DTE 串口链路是否可用（Oper Up/Down）。

非侵入式（Non-Interference）：不影响正常 Console 运维（救火优先级最高）。

高可用与持久化（HA & Persistence）：进程/系统重启后可恢复；对端重启可自动恢复探测。

## 设计概述

![ConsoledArchitecture](ConsoledArchitecture.png)

设计核心在于在DTE侧，将直接的“用户 <-> 串口”访问模式转变为“用户 <-> Proxy <-> 串口”模式。

DTE (被管理设备): 
    周期性向串口发送特定格式的心跳包（Heartbeat）。
    note：DTE -> DCE 的单向数据流，保证了在DTE重启阶段，不会收到任何来自DCE侧协议的干扰数据。
    风险：有一定的概率，正常数据流中包含心跳帧格式的数据，导致这部分正常数据被误判为心跳帧而丢弃。可以通过心跳帧设计来降低碰撞概率。

Proxy (DCE 侧代理进程):
    独占权: 唯一直接打开并持有物理串口文件描述符（/dev/ttyUSBx）的进程。

    伪终端 (PTY): 为上层应用（如 picocom, minicom）创建一个虚拟串口。

    流控与过滤: 实时扫描串口输入流。识别到心跳包时更新状态并将其丢弃；识别到非探测数据时透传给虚拟串口。

## 详细设计

### 心跳帧设计

    设计原则
        可从任意字节流中尽量可靠的区分心跳帧：避免心跳被read分块截断，导致误判
        第碰撞：尽量避免把用户正常输出误判为心跳帧

    心跳帧格式
        使用特定的不可打印字符串作为心跳包，避开了ascii字符集和UTF-8编码的常用字符范围，降低了碰撞概率。

        F4 9B 2D C7

### DTE侧

    发送周期
        默认5秒发送一次心跳包

    note - 风险：
        write部分写入
            非阻塞模式write，内核发送缓冲区满时，可能出现部分写入的情况
            阻塞模式写入：可能在write调用期间被信号中断，导致部分写入
        解决方案

    启动与重启
        使用systemd generator自动生成console-monitor@.service实例，通过内核命令行参数传递的串口列表，
        自动创建对应的systemd服务实例。

        在 /run/systemd/generator/... 里生成一个 wants 链接，从而“自动实例化” console-monitor@ttyS0.service。

        该服务负责向指定串口周期性发送心跳包。


### DCE侧设计

    超时判定
        默认15秒未收到心跳包，判定链路不可用（Oper Down）

    心跳帧检测与过滤
        为了应对read调用可能读取到部分心跳包的情况，设计了一个滑动缓冲区（sliding buffer），用于存储最近读取的字节流。

        buffer size = heartbea length

        每次从串口读取数据后，将数据追加到滑动缓冲区的末尾，并检查缓冲区中是否包含完整的心跳包。

        如果检测到心跳包，则更新心跳计时器，刷新缓冲区。

        如果前缀匹配失败，或缓冲区已满，则清空缓冲区，透传给上层应用。

        如果1s内没有匹配成功，则将缓冲区内容透传给上层应用，避免数据阻塞。

    Oper 状态判定（每条链路独立）
        每次收到心跳包，更新心跳计时器，Oper 状态置为 Up

        定时任务：每15s检查 now - last_heartbeat_time > timeout => oper_state = Down

        状态变更写入 STATE_DB, key: "CONSOLED_PORT|<link_id> ", field: "oper_status", value: "UP"/"DOWN"

    启动与重启
        在 config-setup.service之后启动 consoled 服务。读取 CONFIG_DB 中的配置，初始化各串口的 Proxy 实例。

        如果STATE_DB中已有链路状态，则不做修改，如果没有，则初始化为down状态。

        last_heartbeat_time 初始化为now，避免假down/up

        

    