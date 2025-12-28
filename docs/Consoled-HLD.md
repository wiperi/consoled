# SONiC consoled (Serial Link State Detection & Interactive Console Handover)

# High Level Design Document

#### Revision 1.0

# Table of Contents

* [List of Tables](#list-of-tables)
* [Revision](#revision)
* [About this Manual](#about-this-manual)
* [Scope](#scope)
* [Definition/Abbreviation](#definitionabbreviation)

  * [Table 1: Abbreviations](#table-1-abbreviations)
* [1 Feature Overview](#1-feature-overview)

  * [1.1 Requirements](#11-requirements)

    * [1.1.1 Functional Requirements](#111-functional-requirements)
    * [1.1.2 Non-Functional Requirements](#112-non-functional-requirements)
    * [1.1.3 Configuration and Management Requirements](#113-configuration-and-management-requirements)
  * [1.2 Design Overview](#12-design-overview)

    * [1.2.1 Basic Approach](#121-basic-approach)
    * [1.2.2 Components](#122-components)
    * [1.2.3 Deployment Model](#123-deployment-model)
* [2 Functionality](#2-functionality)

  * [2.1 Target Deployment Use Cases](#21-target-deployment-use-cases)
  * [2.2 Functional Description](#22-functional-description)
  * [2.3 Limitations](#23-limitations)
* [3 Design](#3-design)

  * [3.1 Architecture Overview](#31-architecture-overview)

    * [3.1.1 Serial Link Model](#311-serial-link-model)
    * [3.1.2 Sender-Side Coordination with consutil](#312-sender-side-coordination-with-consutil)
    * [3.1.3 Receiver-Side Exclusive Serial Ownership and PTY Handover](#313-receiver-side-exclusive-serial-ownership-and-pty-handover)
    * [3.1.4 Pause/Resume Protocol and Deterministic Handover](#314-pauseresume-protocol-and-deterministic-handover)
    * [3.1.5 State Machine](#315-state-machine)
  * [3.2 DB Changes](#32-db-changes)

    * [3.2.1 CONFIG_DB](#321-config-db)
    * [3.2.2 STATE_DB](#322-state-db)
  * [3.3 CLI](#33-cli)

    * [3.3.1 consoled-control (optional)](#331-consoled-control-optional)
    * [3.3.2 consutil integration](#332-consutil-integration)
  * [3.4 Systemd and Service Layout](#34-systemd-and-service-layout)
  * [3.5 Security and Access Control](#35-security-and-access-control)
* [4 Flow Diagrams](#4-flow-diagrams)

  * [4.1 Normal Heartbeat](#41-normal-heartbeat)
  * [4.2 User Attach and Handover (Receiver side)](#42-user-attach-and-handover-receiver-side)
  * [4.3 Session End and Resume Probing](#43-session-end-and-resume-probing)
  * [4.4 Crash/Restart Recovery](#44-crashrestart-recovery)
* [5 Error Handling](#5-error-handling)
* [6 Serviceability and Debug](#6-serviceability-and-debug)
* [7 Warm Boot Support](#7-warm-boot-support)
* [8 Scalability](#8-scalability)
* [9 Reference](#9-reference)

---

# List of Tables

* [Table 1: Abbreviations](#table-1-abbreviations)

---

# Revision

| Rev |    Date    |    Authors    | Change Description                                |
| :-: | :--------: | :-----------: | ------------------------------------------------- |
| 0.1 | 2025-12-28 | consoled team | Initial draft                                     |
| 1.0 | 2025-12-28 | consoled team | Filled design details, DB/CLI, handover semantics |

---

# About this Manual

This document describes the functionality and high level design of **consoled**, a SONiC feature that provides:

* **Serial link liveness detection** using a heartbeat protocol (Oper status),
* **Non-interference interactive console access** with deterministic Pause/Resume handover,
* **Crash/reboot resilience** via state persistence and service supervision.

---

# Scope

This HLD covers the consoled design for a testbed topology where:

* **Sender** runs on a Console Server node (C0) and probes multiple DTE devices via independent serial links.
* **Receiver** runs on each DTE (SONiC switch) and owns the physical serial device for that link (e.g., `/dev/ttyUSB0`).
* Interactive console sessions must always have the highest priority and must be able to **preempt probing** without user-visible probe artifacts.

---

# Definition/Abbreviation

## Table 1: Abbreviations

| Term  | Meaning                                           |
| ----- | ------------------------------------------------- |
| C0    | Console Device / Console Server (Sender side)     |
| DTE   | SONiC Switch / managed device (Receiver side)     |
| Oper  | Operational status derived from heartbeats        |
| Admin | Desired status configured by user                 |
| PTY   | Pseudo-terminal (master/slave pair)               |
| UDS   | Unix Domain Socket                                |
| HA    | High availability via service supervision/restart |
| HUP   | Hangup event when a TTY/PTY closes                |

---

# 1 Feature Overview

consoled provides **link-state observability** for serial links (which lack a physical link status like Ethernet) and enables **safe interactive takeover** for emergency console access.

## 1.1 Requirements

### 1.1.1 Functional Requirements

1. **Per-link heartbeat detection**

   * Sender periodically transmits heartbeat frames on each serial link.
   * Receiver replies with ACK frames.
   * Sender classifies Oper state as Up/Down per link.

2. **Admin vs Oper separation**

   * Admin status is user-configured enable/disable.
   * Oper status reflects real connectivity based on heartbeats.

3. **Non-interference interactive access**

   * When an interactive console session attaches, consoled must pause probing deterministically.
   * Probing must not inject bytes into a live interactive session.

4. **Receiver-side exclusive physical serial ownership**

   * DTE-side consoled exclusively opens the physical serial device (e.g., `/dev/ttyUSB0`) during normal operation.

5. **Receiver-side PTY handover for login**

   * On interactive attach, receiver creates a PTY, starts `agetty` on the PTY slave, and bridges **physical serial ↔ PTY**.
   * On session end, receiver cleans up PTY/agetty and resumes probing.

6. **Resilience**

   * consoled must recover after daemon crash or device reboot.
   * After peer reboot, consoled must re-establish handshake and resume accurate Oper reporting.

### 1.1.2 Non-Functional Requirements

1. **Deterministic handover**

   * Handover must be well-defined, with explicit state transitions and acknowledgements.

2. **Bounded interference window**

   * The design must minimize and bound any residual “in-flight” bytes during mode switching.
   * Interactive sessions must not observe heartbeat frames or pause-ack frames.

3. **Serviceability**

   * Clear logs, counters, and state exposure for debugging.

### 1.1.3 Configuration and Management Requirements

1. Configurable per link:

   * enable/disable (Admin),
   * heartbeat interval and timeout policy,
   * serial parameters (baud, parity, flow control) where applicable.

2. State visibility:

   * Per link Admin/Oper and probe mode (running/paused/attached).
   * Session ownership (interactive vs probe).

---

## 1.2 Design Overview

### 1.2.1 Basic Approach

* **Sender (C0)** probes each DTE link independently using heartbeat frames.
* **Receiver (DTE)** runs consoled to:

  * respond to heartbeats while in probe mode,
  * pause probing and switch to **interactive bridge mode** on attach,
  * provide a local login path via `agetty` on a PTY slave,
  * restore serial configuration and resume probing after detach.

### 1.2.2 Components

* `consoled` daemon

  * runs on C0 (Sender role) and on DTE (Receiver role),
  * exposes a local control plane for coordination (recommended: UDS RPC).

* `consutil` CLI

  * user-facing tool to connect / attach a session,
  * coordinates with consoled to ensure deterministic takeover.

* `agetty` (Receiver side)

  * started by consoled on PTY slave to provide login prompt and session lifecycle.

### 1.2.3 Deployment Model

* C0: one sender process managing N links (ports).
* Each DTE: one receiver process managing one (or multiple) physical serial ports depending on platform.

---

# 2 Functionality

## 2.1 Target Deployment Use Cases

1. **OOB readiness monitoring**

   * Detect whether each serial OOB path is usable before emergency operations.

2. **Emergency console access**

   * Operators attach interactive session with highest priority, regardless of probing.

3. **Automated monitoring/alerting**

   * Export Oper state into Redis for external monitoring/alerting.

## 2.2 Functional Description

* In normal operation, consoled runs heartbeats and updates Oper state.
* On interactive attach, probing is paused and the serial link is bridged to a PTY running agetty, allowing login and console operations.
* After detach, consoled restores serial device settings and resumes heartbeats.

## 2.3 Limitations

* A single serial link supports **only one interactive session at a time** (enforced by consoled ownership).
* Accurate “instant” preemption is bounded by UART/driver buffering; the design uses deterministic handshake + drain/quiet-window to prevent user-visible artifacts.

---

# 3 Design

## 3.1 Architecture Overview

### 3.1.1 Serial Link Model

Each serial link is an independent probing domain:

* C0 ↔ DTE1
* C0 ↔ DTE2
* …
* C0 ↔ DTEn

Per link, consoled maintains:

* `admin_status` (enable/disable)
* `oper_status` (up/down/unknown)
* `probe_mode` (running/paused/attached)
* `owner` (daemon/interactive/none)
* timers, retry/backoff, and handshake epoch

---

### 3.1.2 Sender-Side Coordination with consutil

**Problem statement:** Sender consoled and `consutil` may contend on the same serial device if both open it directly. Additionally, `consutil` must not start an interactive program (e.g., picocom) until consoled has placed the link into a safe paused state.

**Recommended approach: UDS-based RPC (control plane)**

* consoled provides a per-host UDS endpoint, e.g.:

  * `/var/run/consoled/consoled.sock`
* consutil uses RPC to request exclusive interactive ownership of a line:

  * `AttachRequest(line, epoch)` → `AttachReady(line, epoch)` or error
  * `DetachNotify(line, epoch)`
  * `Query(line)` (state/owner/debug)

**Why UDS RPC**

* Strong request/response semantics, explicit timeouts, simple failure model.
* Avoids Redis pub/sub message loss and reduces control-plane latency/jitter.
* Enables deterministic “gate” before consutil opens any interactive channel.

**Alternative IPC options (supported but not preferred)**

1. **Redis-based coordination**

   * Suitable for state publication, weaker for strict handshake.
   * Requires epochs, durable queue/streams, retries, and idempotency to be safe.

2. **Signals + pidfile**

   * Fast but fragile (PID reuse, permission boundaries, poor payload semantics).

3. **File locks (flock)**

   * Good for mutual exclusion; insufficient alone for handshake and drain semantics.

**HLD decision**

* Control-plane handshake: **UDS RPC**
* State publication/monitoring: **Redis STATE_DB**

---

### 3.1.3 Receiver-Side Exclusive Serial Ownership and PTY Handover

Receiver-side design follows your required behavior:

#### Normal mode (Probe Mode)

* consoled **exclusively opens** physical serial device (e.g., `/dev/ttyUSB0`)
* consoled listens for heartbeat frames and replies with ACK
* consoled updates link status and keeps serial configuration consistent

#### Interactive mode (Attach / Bridge Mode)

On detecting an interactive attach request (from local operator or remote mechanism), receiver consoled:

1. **Pauses probing**

   * stops responding to/initiating probe traffic
2. **Creates a PTY**

   * `pty_master`, `pty_slave`
3. **Starts agetty on PTY slave**

   * example: `agetty -8 -L <pty_slave> <baud> vt102` (platform-specific flags may vary)
4. **Bridges physical serial ↔ PTY master**

   * full-duplex forwarding
   * ensures the user interacts with a normal login experience via agetty
5. **Session end detection**

   * detect PTY slave close/HUP or agetty exit
6. **Cleanup and restore**

   * terminate agetty (if still alive)
   * close and remove PTY
   * restore physical serial settings
   * resume probing

**Key property:** The physical serial device remains owned by consoled throughout, preventing multi-process read competition on DTE.

---

### 3.1.4 Pause/Resume Protocol and Deterministic Handover

To prevent the user from seeing in-flight heartbeat bytes, consoled uses an explicit **Pause handshake** plus **drain/quiet-window** gating.

**Pause handshake (conceptual)**

* `PAUSE_REQ(epoch)`
* `PAUSE_ACK(epoch)`

**Deterministic attach gate**

* Receiver enters `QUIESCING(epoch)`:

  * stop scheduling/providing probe traffic immediately
  * consume and discard any residual probe-related bytes
  * wait for a **quiet window** (e.g., no RX bytes for 200–500ms)
  * optionally flush kernel buffers (`tcflush(TCIFLUSH)` or `TCIOFLUSH`)
* Only after reaching `PAUSED_READY(epoch)` does the receiver create PTY/start agetty/enable bridging.

This ensures:

* Any “just-arrived” heartbeat or ACK is handled internally during QUIESCING.
* The interactive session sees a clean stream, not probe artifacts.

---

### 3.1.5 State Machine

Per link state machine (receiver side) at a high level:

* `PROBING`
* `QUIESCING` (pause requested; drain + quiet-window)
* `PAUSED_READY` (safe to attach)
* `ATTACHED` (PTY + agetty running; bridging active)
* `RESUMING` (cleanup + restore + optional resume handshake)
* back to `PROBING`

Sender side state machine is similar but focuses on:

* heartbeat scheduling
* timeout/backoff
* remote paused indication (if modeled)
* oper status classification

---

## 3.2 DB Changes

This section describes DB schema changes for consoled.

### 3.2.1 CONFIG_DB

Proposed tables (names can be adjusted to SONiC naming conventions):

#### `CONSOLED_GLOBAL|global`

* `enabled = "yes"/"no"`
* `default_interval_ms`
* `default_timeout_ms`

#### `CONSOLED_LINK|<link_id>`

* `admin_status = "up"/"down"` (or `enabled = yes/no`)
* `role = "sender"/"receiver"`
* `device = "/dev/ttyXXX"` (platform alias)
* `baud_rate`, `flow_control`, other serial params (optional)
* `interval_ms`, `timeout_ms`, `retry_policy` (optional)

### 3.2.2 STATE_DB

#### `CONSOLED_LINK|<link_id>`

* `admin_status = "up"/"down"`
* `oper_status = "up"/"down"/"unknown"`
* `probe_mode = "probing"/"paused"/"attached"/"quiescing"`
* `pause_reason = "interactive_session"/"admin_disable"/"error"/""`
* `owner = "daemon"/"interactive"/"none"`
* `epoch = <uint64>`
* `last_change_ts = <unix_ts>`
* `last_heartbeat_ts = <unix_ts>` (sender side)
* `error = <string>` (optional)

This mirrors the Console Switch pattern where STATE_DB contains “busy/idle”; here we generalize it to include probe and ownership semantics.

---

## 3.3 CLI

### 3.3.1 consoled-control (optional)

A minimal CLI for operators and debugging:

* `consoledctl show`
* `consoledctl pause <link>`
* `consoledctl resume <link>`
* `consoledctl debug dump <link>`

(If you want to keep surface area small, these can be internal-only and accessed via `consutil`.)

### 3.3.2 consutil integration

`consutil connect <target>` should be extended/implemented to coordinate with consoled before starting interactive I/O.

Suggested behavior:

1. `consutil connect <link>`
2. calls UDS RPC `AttachRequest(link)`
3. waits for `AttachReady(link)`
4. then starts the interactive program (sender side might use `picocom`; receiver side uses PTY+agetty model per requirement)
5. on exit, calls `DetachNotify(link)`

---

## 3.4 Systemd and Service Layout

* `consoled.service` on both C0 and DTE
* `Restart=always` with bounded restart bursts
* ensures recovery after crashes/reboots

Receiver-side agetty lifecycle:

* spawned and owned by consoled (recommended)
* consoled tracks PID and ensures cleanup on detach or daemon restart

On daemon restart:

* consoled reads STATE_DB/Config and reconciles:

  * if `ATTACHED` but no PTY/agetty exists → force cleanup and return to PROBING
  * if agetty exists but consoled restarted → reattach monitoring or terminate safely

---

## 3.5 Security and Access Control

* Physical serial device permissions should prevent arbitrary multi-process open.
* Receiver-side design already enforces single owner by keeping physical device open in consoled only.
* PTY slave permissions:

  * allow interactive login only for intended users/groups
  * agetty/login obey PAM and system policies

UDS RPC access:

* restrict socket filesystem permissions to trusted group (e.g., `sudo` or `admin`)

---

# 4 Flow Diagrams

(Shown as text sequences; can be converted to diagrams if desired.)

## 4.1 Normal Heartbeat

* Sender: send `HEARTBEAT(epoch, seq)`
* Receiver: reply `HEARTBEAT_ACK(epoch, seq)`
* Sender: update `oper_status=up`
* Timeout: `oper_status=down` with retry/backoff

## 4.2 User Attach and Handover (Receiver side)

1. attach requested (local/remote control path)
2. receiver consoled enters `QUIESCING`
3. stop probe replies/traffic
4. drain residual bytes + quiet window
5. set `PAUSED_READY`
6. create PTY
7. start agetty on PTY slave
8. enable bridging physical serial ↔ PTY
9. set `ATTACHED`

## 4.3 Session End and Resume Probing

1. PTY slave closes / agetty exits
2. consoled stops bridging
3. cleanup PTY + terminate agetty
4. restore serial settings
5. set `PROBING` and resume heartbeat replies

## 4.4 Crash/Restart Recovery

* systemd restarts consoled
* consoled reconciles state:

  * cleanup stale PTY/agetty
  * re-open serial and resume probing
  * sender re-handshakes epochs and resumes classification

---

# 5 Error Handling

* Serial open failures: mark `oper_status=unknown`, `probe_mode=paused`, publish `error`
* PTY creation failure: fail attach request, keep probing paused only if safe; otherwise resume probing
* agetty spawn failure: fail attach, cleanup PTY, resume probing
* Unexpected bytes / protocol desync: reset per-link epoch and re-sync
* Detach timeout: if agetty is unresponsive, send SIGTERM then SIGKILL with bounded timing; cleanup PTY

---

# 6 Serviceability and Debug

* Structured logs per link and per transition:

  * state transitions with epoch
  * attach/detach events with PID/user (where available)
  * heartbeat statistics (sent/acked/timeouts)
* Debug commands:

  * show link state
  * show current owner/mode
  * dump last error and last transition timestamp
* Techsupport integration: capture consoled logs and relevant STATE_DB keys

---

# 7 Warm Boot Support

* consoled persists logical state in STATE_DB and can reconstruct after warm reboot.
* After reboot, consoled always reconciles runtime artifacts (PTY/agetty) to avoid stale “busy” state.

---

# 8 Scalability

* Sender: scales linearly with number of links; each link can be handled by an event-driven loop.
* Receiver: per physical port one state machine; PTY/agetty only exist during active interactive sessions.
* Resource limits:

  * maximum concurrent interactive sessions equals number of ports (1 per port)
  * heartbeat polling interval should be tuned to avoid CPU spikes under N ports

---

# 9 Reference

* SONiC Console Switch High Level Design Document (format reference)
* Linux PTY / agetty manuals (`man pty`, `man agetty`)
* SONiC Redis DB patterns (CONFIG_DB / STATE_DB conventions)

---

## Next step (optional)

如果你希望把它做得更贴近可落地实现，我可以继续补齐两块通常在评审里会被追问的内容，并保持同样的 HLD 风格：

1. **Sender↔consutil 的 IPC 接口定义**（UDS 方法签名、错误码、幂等与超时策略）
2. **Receiver 的 PTY/agetty 细节**（如何检测 attach、如何判定会话结束、如何恢复串口 stty、以及 quiet-window/flush 的参数建议）

你只要告诉我：Sender 侧 interactive 连接是“直接打开物理串口跑 picocom”，还是也要走“PTY/broker”模型统一接入。
