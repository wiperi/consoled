# SONiC consoled (Serial Link State Detection)

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
  * [1.2 Design Overview](#12-design-overview)

    * [1.2.1 Basic Approach](#121-basic-approach)
    * [1.2.2 Container](#122-container)
* [2 Functionality](#2-functionality)

  * [2.1 Target Deployment Use Cases](#21-target-deployment-use-cases)
  * [2.2 Functional Description](#22-functional-description)
  * [2.3 Limitations](#23-limitations)
* [3 Design](#3-design)

  * [3.1 Architecture Overview](#31-architecture-overview)

    * [3.1.1 Startup](#311-startup)
    * [3.1.2 Sender Behavior](#312-sender-behavior)
    * [3.1.3 Sender-Side Coordination with consutil](#313-sender-side-coordination-with-consutil)
    * [3.1.4 Receiver Behavior](#314-receiver-behavior)
    * [3.1.5 Receiver-Side Exclusive Serial Ownership and PTY Handover](#315-receiver-side-exclusive-serial-ownership-and-pty-handover)
    * [3.1.6 State Machine](#316-state-machine)
  * [3.2 DB Changes](#32-db-changes)

    * [3.2.1 CONFIG_DB](#321-config-db)
    * [3.2.2 STATE_DB](#322-state-db)
  * [3.3 CLI](#33-cli)
* [4 Flow Diagrams](#4-flow-diagrams)

  * [4.1 Normal Heartbeat](#41-normal-heartbeat)
  * [4.2 User Attach and Handover](#42-user-attach-and-handover)
  * [4.3 Session End and Resume Probing](#43-session-end-and-resume-probing)
  * [4.4 Crash/Restart Recovery](#44-crashrestart-recovery)
* [9 Reference](#9-reference)

---

# List of Tables

* [Table 1: Abbreviations](#table-1-abbreviations)

---

# Revision

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

---

# 1 Feature Overview

consoled provides **link-state observability** for serial links (which lack a physical link status like Ethernet) and enables **safe interactive takeover** for emergency console access.

## 1.1 Requirements

### 1.1.1 Functional Requirements

1. **Per-link heartbeat detection**

   * Sender periodically transmits heartbeat frames on each serial link.
   * Receiver replies with ACK frames.
   * Sender classifies Oper state as Up/Down per link.

3. **Non-interference interactive access**

   * When an interactive console session attaches, consoled must pause probing deterministically.
   * Probing must not inject bytes into a live interactive session.

6. **Resilience**

   * consoled must recover after daemon crash or device reboot.
   * After reboot, consoled must resume probing and not cause any Oper state jitter.

### 1.1.2 Non-Functional Requirements

1. **Objservability**

   * Operation staff should be able to query per-link Oper status easily.

2. **Invisibility**

   * Except Oper state, other internal details should be hidden from users.

---

## 1.2 Design Overview

### 1.2.1 Basic Approach

* **Sender (C0)** 

  * manged by systemctl, start on boot, after config-setup.service load config.json to CONFIG_DB.
  * probes each DTE link independently using heartbeat frames.
  * avoid conflict with interactive sessions by coordinating with `consutil`.

* **Receiver (DTE)** runs consoled to:

  * respond to heartbeats while in probe mode,
  * pause probing and switch to **interactive bridge mode** on attach,

### 1.2.2 Container

  * No new containers are introduced.

---

# 2 Functionality

## 2.1 Target Deployment Use Cases

1. **OOB readiness monitoring**

   * Detect whether each serial link is usable before emergency operations.

2. **Emergency console access**

   * Operators attach interactive session with highest priority, regardless of probing.

3. **Automated monitoring/alerting**

   * Export Oper state into Redis for external monitoring/alerting.

## 2.2 Functional Description

* In normal operation, consoled runs heartbeats and updates Oper state.
* On interactive attach, probing is paused and the serial link is bridged to a PTY running agetty, allowing login and console operations.
* After detach, consoled restores serial device settings and resumes heartbeats.

## 2.3 Limitations

Probing mechanism depends on system status and correct configuration.

* Oper state is up if and only if serial link is connected and consoled service is running correctly on both ends.

* Oper state is down if and only if serial link is disconnected or consoled service is not running correctly on either end.

---

# 3 Design

## 3.1 Architecture Overview

![consoled Architecture](ConsoledArchitecture.png)

### 3.1.1 Startup

consoled runs as a systemd service managed by `systemctl`. It starts on boot after `config-setup.service` loads `config.json` into CONFIG_DB.

consoled determines whether to run as **Sender** or **Receiver** based on the `enabled` field in `CONSOLE_SWITCH:console_mgmt` table in CONFIG_DB.

### 3.1.2 Sender Behavior

For each Serial Port, check `CONSOLE_PORT|<link_id> state` in STATE_DB:

* If `busy`: subscribe to STATE_DB keyspace notifications for `CONSOLE_PORT|<link_id> state`, wait until state becomes `idle`
* If `idle`: start a probe cycle:
  1. Send `HEARTBEAT` frame
  2. Wait for `HEARTBEAT_ACK` frame
  3. Update `CONSOLE_PORT|<link_id> oper_status` in STATE_DB to `up` or `down` based on ACK reception
  4. Wait for next probe cycle

**Concurrent listeners:**

1. **Redis channel listener** (`CONSOLED_CTRL:<link_id>`):
   * On receiving `PAUSE_REQ`, send `PAUSE_REQ` frame to receiver and wait for `PAUSE_ACK` frame
   * Upon receiving ACK or timeout, publish `PAUSE_ACK` to the channel
   * This signal is consumed by `consutil` to invoke picocom and open an interactive session

2. **STATE_DB keyspace notification listener** (`CONSOLE_PORT|<link_id> state`):
   * When state changes to `idle`, send `RESUME_REQ` frame to receiver
   * Wait for `RESUME_ACK` frame
   * Upon receiving ACK, resume probe cycle

---

### 3.1.3 Sender-Side Coordination with consutil

**Problem:** Sender consoled and `consutil` may contend on the same serial device. `consutil` must not start an interactive program until consoled has paused probing.

**Solution:** Redis Pub/Sub + STATE_DB coordination

* consoled subscribes to Redis channel: `CONSOLED_CTRL:<link_id>`
* consutil publishes control messages to request/release exclusive access
* STATE_DB provides durable state confirmation
**Control flow:**

1. **consutil** after user input connect command, publishes `PAUSE_REQ` to `CONSOLED_CTRL:<link_id>` channel
2. **consoled** receives message:
    * Stops sending heartbeat frames
    * Publishes `PAUSE_ACK` to `CONSOLED_CTRL:<link_id>` channel
3. **consutil** waits for `PAUSE_ACK` on the same channel (timeout: 1s, on_timeout: assume consold is not functional, proceed anyway)
4. **consutil** opens physical serial device and starts picocom
5. On session end, **consutil** updates `CONSOLE_PORT|<link_id> state` to `"idle"` in STATE_DB
6. **consoled** monitors STATE_DB keyspace notifications for `CONSOLE_PORT|<link_id> state` changes
7. Upon detecting `state = "idle"`, **consoled** resumes probing mode

**Alternative IPC Schemes:**

> **Note:** The current design assumes Redis is available and performs well, with message delivery latency under 1 second.

Other candidates considered:
* **Unix Domain Sockets** - Direct socket communication without Redis dependency
* **Redis Streams** - Persistent message queue with better durability guarantees

**Alternative Sender Architecture (Multiplexing Approach):**

> Instead of pausing/resuming, consoled could multiplex heartbeat and interactive traffic on the same serial link using line-based framing.

Design:
1. consoled exclusively owns the physical TTY on both sender and receiver sides
2. Both sides create a PTY pair as a "virtual serial port"
3. `consutil` (sender) and `getty` service (receiver) connect only to the PTY slave
4. consoled performs line-based multiplexing:
   * Inspects each line's prefix to determine if it's a heartbeat frame
   * Forwards heartbeat frames internally, user data to/from PTY
5. No pause/resume protocol needed - heartbeat and interactive traffic coexist

**Trade-offs:**
| Aspect | Current (Pause/Resume) | Multiplexing |
|--------|------------------------|--------------|
| Complexity | Simpler framing | More complex parsing |
| Latency | Pause delay on attach | No attach delay |
| Reliability | Clean separation | Risk of frame corruption |

The feasibility still requires further proof-of-concept verification.

---

### 3.1.4 Receiver Behavior

The receiver exclusively owns the physical serial device (e.g., `/dev/ttyS0`). In probe mode, it listens for heartbeat frames and replies with ACK.

When an interactive connection request (PAUSE_REQ) is detected, the receiver consoled:

1. **Pauses probing** and stops replying to heartbeat frames
2. **Bridges the serial device** to a PTY running agetty, enabling login and console operations
3. **After disconnect (RESUME_REQ)**, consoled restores serial device settings and resumes heartbeat

**Alternative Receiver Approaches:**

> The goal is simply to determine link availability. The only information operators need is: "Can I manage the DTE via console?" The simplest approach would be for the sender to periodically send `\r` - if the link and system services are working, the operator would see the some prompt (e.g., `bjw3-can-7050c-5 login:`) echoed back.

Regardless of approach, receiving heartbeat at software layer implies:
* Hardware link is connected AND system service is running correctly

| Receiver Type | Heartbeat Received Means | Heartbeat Not Received Means |
|---------------|--------------------------|------------------------------|
| **getty as receiver** | Hardware link OK AND getty service OK | Hardware link down OR getty misconfigured |
| **consoled as receiver** | Hardware link OK AND consoled service OK | Hardware link down OR consoled misconfigured |

In either case, no heartbeat response means the console path is unusable for device management.


---

### 3.1.5 Receiver-Side Exclusive Serial Ownership and PTY Handover

#### Probe Mode

* consoled exclusively opens physical serial device
* consoled listens for heartbeat frames and replies with ACK

#### Interactive mode

On detecting an interactive attach request (from local operator or remote mechanism), receiver consoled:

1. **Pauses probing**

   * stops responding to/initiating probe traffic
2. **Creates a PTY**

   * `pty_master`, `pty_slave`
3. **Starts serial-getty service on PTY slave**

    * example: `systemctl start serial-getty@<pty_slave>.service`
    * serial-getty automatically invokes agetty with appropriate parameters for serial login
4. **Bridges physical serial ↔ PTY master using socat**

    * example: `socat /dev/ttyS0,raw,echo=0 <pty_master>,raw,echo=0`
    * full-duplex forwarding between physical TTY and PTY master
    * ensures the user interacts with a normal login experience via agetty on the PTY slave
5. **Session end**

   * After receive `RESUME_REQ` from C0 side. Shut down socat bridge and switch to probing mode.

---

### 3.1.6 State Machine

![Sender FSM](ConsoledSenderFSM.png)

![Receiver FSM](ConsoleReceiverFSM.png)

---

## 3.2 DB Changes

This section describes DB schema changes for consoled.

### 3.2.1 CONFIG_DB

No changes to CONFIG_DB are required. consoled reuses existing CONSOLE_PORT table entries.

### 3.2.2 STATE_DB

#### `CONSOLE_PORT|<link_id>`

Add one new field to the existing STATE_DB table:

* `oper_status = "up"/"down"`

    * `up (default)`: heartbeat ACKs are received within timeout
    * `down`: heartbeat timed out or link failure detected

---

## 3.3 CLI

To be determined.

---

# 4 Flow Diagrams

## 4.1 Normal Heartbeat

* Sender: send `HEARTBEAT`
* Receiver: reply `HEARTBEAT_ACK`
* Sender: update STATE_DB `CONSOLE_PORT|<link_id> oper_status=up`
* Timeout: `CONSOLE_PORT|<link_id> oper_status=down`

## 4.2 User Attach and Handover

1. User execute `connect line <line_id>` on C0 (Sender)
2. consutil publishes `PAUSE_REQ` to `CONSOLED_CTRL:<link_id>` channel
3. sender consoled receives `PAUSE_REQ` message
4. sender consoled sends `PAUSE_REQ` frame to receiver
5. receiver consoled receives `PAUSE_REQ` frame
6. receiver consoled initiates handover procedure: bridge console device to PTY
7. receiver consoled sends `PAUSE_ACK` frame to sender
8. sender consoled receives `PAUSE_ACK` frame
9. sender consoled publishes `PAUSE_ACK` to `CONSOLED_CTRL:<link_id>` channel
10. consutil receives `PAUSE_ACK`, opens serial device, starts picocom
11. User interacts with console via picocom

## 4.3 Session End and Resume Probing

1. User exits picocom on C0 (Sender)
2. consutil updates `CONSOLE_PORT|<link_id> state=idle` in STATE_DB
3. sender consoled detects state change via keyspace notification
4. sender consoled sends `RESUME_REQ` frame to receiver
5. receiver consoled receives `RESUME_REQ` frame
6. receiver consoled initiates resume procedure: unbridge console device and pty_master, restart probing (watching for HEARTBEAT)
7. receiver consoled sends `RESUME_ACK` frame to sender
8. sender consoled receives `RESUME_ACK` frame
9. sender consoled goes into `IDLE` mode and start next round of heartbeat probing

## 4.4 Crash/Restart Recovery

* systemd restarts consoled
* consoled go through initialization again:

  * read CONFIG_DB/STATE_DB
  * determine Sender/Receiver role
  * Create PTY pairs if needed
  * Ensure getty service is running on PTY slave
  * re-open serial and resume probing (start from `IDLE` mode)

---

# 9 Reference

* SONiC Console Switch High Level Design Document
* Linux PTY / agetty manuals (`man pty`, `man agetty`)
