# SONiC Console Monitor

## High Level Design Document

### Revision 1.0

---

## Table of Contents

- [Revision History](#revision-history)
- [Scope](#scope)
- [Definitions and Abbreviations](#definitions-and-abbreviations)
- [1. Feature Overview](#1-feature-overview)
  - [1.1 Functional Requirements](#11-functional-requirements)
  - [1.2 Design Goals](#12-design-goals)
- [2. Design Overview](#2-design-overview)
  - [2.1 Architecture](#21-architecture)
  - [2.2 DTE Side (Managed Device)](#22-dte-side-managed-device)
  - [2.3 DCE Side (Proxy Process)](#23-dce-side-proxy-process)
- [3. Detailed Design](#3-detailed-design)
  - [3.1 Heartbeat Frame Design](#31-heartbeat-frame-design)
  - [3.2 DTE Side Service](#32-dte-side-service)
  - [3.3 DCE Side Console Monitor Service](#33-dce-side-console-monitor-service)
- [4. Database Changes](#4-database-changes)
  - [4.1 STATE_DB](#41-state_db)
- [5. CLI](#5-cli)
- [6. Flow Diagrams](#6-flow-diagrams)
- [7. References](#7-references)

---

## Revision History

---

## Scope

This document describes the high-level design of the SONiC Console Monitor (consoled, console-daemon) feature. The consoled service provides link operational status detection for console connections between Console Servers (DCE) and SONiC Switches (DTE) in data center networks.

---

## Definitions and Abbreviations

| Term      | Definition                                                                 |
|-----------|----------------------------------------------------------------------------|
| DCE       | Data Communications Equipment - Console Server side                        |
| DTE       | Data Terminal Equipment - SONiC Switch (managed device) side              |
| Heartbeat | Periodic signal sent to verify link connectivity                          |
| Oper      | Operational status (Up/Down)                                              |
| PTY       | Pseudo Terminal - Virtual terminal interface                              |
| Proxy     | Intermediary process handling serial port communication                   |
| TTY       | Teletypewriter - Terminal device interface                                |

---

## 1. Feature Overview

In data center networks, Console Servers (C0/DCE) are directly connected to multiple SONiC Switches (DTE) via serial ports for out-of-band management and console access during failures. The consoled service provides link operational status detection with the following capabilities:

### 1.1 Functional Requirements

1. **Connectivity Detection (Heartbeat)**: Determine whether the C0 ↔ DTE serial link is available (Oper Up/Down)
2. **Non-Interference**: Must not affect normal console operations (emergency access has the highest priority)
3. **High Availability & Persistence**: State recovery after process/system restart; automatic detection recovery after remote device restart

### 1.2 Design Goals

| Goal                | Description                                                              |
|---------------------|--------------------------------------------------------------------------|
| Reliability         | Accurate link status detection with minimal false positives/negatives   |
| Non-intrusive       | Zero impact on normal console operations                                |
| Low Overhead        | Minimal resource consumption and bandwidth usage                        |
| Automatic Recovery  | Self-healing after restarts on either side                              |

---

## 2. Design Overview

### 2.1 Architecture

![Consoled Architecture](ConsoledArchitecture.png)

The core design transforms the direct "User ↔ Serial Port" access model into a "User ↔ Proxy ↔ Serial Port" model on the DCE side.

### 2.2 DTE Side (Managed Device)

The DTE periodically sends heartbeat frames with a specific format to the serial port.

**Key Characteristics:**

- **Unidirectional Data Flow**: DTE → DCE only, ensuring no interference from DCE-side protocol data during DTE reboot phase
- **Collision Risk Mitigation**: There is a small probability that normal data streams may contain heartbeat frame patterns, causing false detection. This risk is minimized through careful heartbeat frame design

### 2.3 DCE Side (Proxy Process)

The Proxy process on the DCE side serves as the intermediary between applications and the physical serial port.

**Responsibilities:**

| Function           | Description                                                                         |
|--------------------|-------------------------------------------------------------------------------------|
| Exclusive Access   | Sole process holding the physical serial port file descriptor (`/dev/ttyUSBx`)     |
| PTY Creation       | Creates a virtual serial port (pseudo-terminal) for upper-layer applications       |
| Flow Control       | Real-time scanning of serial port input stream                                      |
| Heartbeat Filtering| Identifies heartbeat frames, updates state, and discards them                       |
| Data Passthrough   | Transparently forwards non-heartbeat data to the virtual serial port               |

---

## 3. Detailed Design

### 3.1 Heartbeat Frame Design

#### 3.1.1 Design Principles

1. **Reliable Detection**: Must be distinguishable from arbitrary byte streams; avoid misdetection due to read() call fragmentation
2. **Low Collision Rate**: Minimize false positives where normal user output is mistakenly identified as heartbeat frames

#### 3.1.2 Frame Format

The heartbeat frame uses a specific sequence of non-printable characters, avoiding the common ASCII and UTF-8 character ranges to reduce collision probability.

**Heartbeat Byte Sequence:**

```
F4 9B 2D C7 8E A1 5F 93
```

| Byte Position | Value (Hex) |
|---------------|-------------|
| 0             | F4          |
| 1             | 9B          |
| 2             | 2D          |
| 3             | C7          |
| 4             | 8E          |
| 5             | A1          |
| 6             | 5F          |
| 7             | 93          |

---

### 3.2 DTE Side Service

#### 3.2.1 Service: `console-heartbeat@ttyS0.service`

**Configuration:**

| Parameter       | Default Value | Description                              |
|-----------------|---------------|------------------------------------------|
| Send Interval   | 5 seconds     | Heartbeat transmission period            |

#### 3.2.2 Write Operation Risks and Mitigations

| Risk                    | Description                                                        | Mitigation                           |
|-------------------------|--------------------------------------------------------------------|--------------------------------------|
| Partial Write (Non-blocking) | Kernel send buffer full may cause incomplete write           | Use blocking mode with proper handling |
| Signal Interruption     | Write call may be interrupted by signals during blocking mode     | Implement retry logic                |

#### 3.2.3 Service Startup and Management

- **Automatic Instance Generation**: Uses systemd generator to automatically create `console-monitor@.service` instances
- **Configuration Source**: Serial port list passed via kernel command line parameters
- **Service Location**: Generated wants links in `/run/systemd/generator/`
- **Function**: Periodically sends heartbeat frames to the specified serial port

---

### 3.3 DCE Side Console Monitor Service

#### 3.3.1 Service: `console-monitor.service`

**Topology:**

![Console Monitor Structure](ConsoleMonitorStructure.png)

Each link has an independent Proxy instance responsible for serial port read/write operations and state maintenance.

#### 3.3.2 Timeout Detection

| Parameter      | Default Value | Description                                           |
|----------------|---------------|-------------------------------------------------------|
| Timeout Period | 15 seconds    | Duration without heartbeat before declaring Oper Down |

#### 3.3.3 Heartbeat Frame Detection and Filtering

To handle cases where read() calls may return partial heartbeat frames, a sliding buffer mechanism (inspired by the KMP algorithm) is implemented:

**Algorithm:**

1. **Buffer Size**: `heartbeat_length - 1` bytes
2. **On Data Read**: Append new data to the sliding buffer tail
3. **Pattern Matching**:
   - If complete heartbeat detected → Update heartbeat timer, flush buffer
   - If prefix match fails OR buffer full → Clear buffer, passthrough to upper layer
   - If no match within 1 second → Passthrough buffer contents to prevent data blocking

**State Diagram:**

```
┌─────────────────┐
│   Read Data     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Append to Buffer│
└────────┬────────┘
         │
         ▼
┌─────────────────────┐     Yes    ┌──────────────────┐
│ Heartbeat Detected? │───────────►│ Update Timer     │
└─────────┬───────────┘            │ Flush Buffer     │
          │ No                     └──────────────────┘
          ▼
┌─────────────────────┐     Yes    ┌──────────────────┐
│ Prefix Match Failed │───────────►│ Clear Buffer     │
│ OR Buffer Full?     │            │ Passthrough Data │
└─────────┬───────────┘            └──────────────────┘
          │ No
          ▼
┌─────────────────────┐     Yes    ┌──────────────────┐
│ Timeout (1s)?       │───────────►│ Passthrough      │
└─────────┬───────────┘            │ Buffer Contents  │
          │ No                     └──────────────────┘
          ▼
┌─────────────────────┐
│ Wait for More Data  │
└─────────────────────┘
```

#### 3.3.4 Operational State Determination

Each link maintains independent state:

| Event                        | Action                                                    |
|------------------------------|-----------------------------------------------------------|
| Heartbeat Received           | Update heartbeat timer; Set Oper State = Up               |
| Scheduled Check (every 15s)  | If `now - last_heartbeat_time > timeout` → Oper State = Down |
| State Change                 | Write to STATE_DB                                         |

**STATE_DB Entry:**

- **Key**: `CONSOLED_PORT|<link_id>`
- **Field**: `oper_state`
- **Value**: `up` / `down`

#### 3.3.5 Service Startup and Initialization

| Phase              | Action                                                                     |
|--------------------|----------------------------------------------------------------------------|
| Startup Timing     | After `config-setup.service`                                               |
| Configuration      | Read CONFIG_DB, initialize Proxy instances for each serial port            |
| State Recovery     | If STATE_DB entry exists → Preserve; If not → Initialize as `down`         |
| Timer Init         | Set `last_heartbeat_time = now` to avoid false down/up transitions         |

#### 3.3.6 Dynamic Configuration Changes

- Monitor CONFIG_DB for consoled configuration change events
- Dynamically add, remove, or restart Proxy instances for links

---

## 4. Database Changes

### 4.1 STATE_DB

**Table: CONSOLE_PORT_TABLE**

| Key Format                 | Field            | Value              | Description                    |
|----------------------------|------------------|--------------------|--------------------------------|
| `CONSOLED_PORT|<link_id>`  | `oper_state`     | `up` / `down`      | Link operational status        |
| `CONSOLED_PORT|<link_id>`  | `last_heartbeat` | `<timestamp>`      | Last heartbeat reception time  |

---

## 5. CLI

The `show line` command is enhanced to display link operational status:

```
admin@sonic:~$ show line
```

**Output:**

| Line | Baud | Flow Control | PID  | Start Time   | Device    | Oper Status | Last Heartbeat   |
|------|------|--------------|------|--------------|-----------|-------------|------------------|
| 1    | 9600 | Disabled     | 1234 | Jan 15 10:23 | Terminal1 | UP          | Jan 15 14:32:18  |
| 2    | 9600 | Disabled     | 5678 | Jan 15 10:24 | Terminal2 | DOWN        | Jan 15 14:30:45  |

**New Columns:**

| Column          | Description                                          |
|-----------------|------------------------------------------------------|
| Oper Status     | Current operational status of the console link       |
| Last Heartbeat  | Timestamp of the most recent heartbeat reception     |

---

## 6. Flow Diagrams

### 6.1 Heartbeat Detection Flow

```
┌─────────┐                              ┌─────────┐
│   DTE   │                              │   DCE   │
│ (SONiC  │                              │(Console │
│ Switch) │                              │ Server) │
└────┬────┘                              └────┬────┘
     │                                        │
     │  ┌──────────────────────────────────┐  │
     │  │ Every 5 seconds                  │  │
     │  └──────────────────────────────────┘  │
     │                                        │
     │  Heartbeat Frame (8 bytes)             │
     │ ──────────────────────────────────────►│
     │                                        │
     │                                   ┌────┴────┐
     │                                   │ Proxy   │
     │                                   │ Process │
     │                                   └────┬────┘
     │                                        │
     │                              ┌─────────┴─────────┐
     │                              │ 1. Detect Frame   │
     │                              │ 2. Update Timer   │
     │                              │ 3. Update STATE_DB│
     │                              │ 4. Discard Frame  │
     │                              └───────────────────┘
     │                                        │
```

### 6.2 Timeout Detection Flow

```
┌─────────────────────────────────────────────────────────┐
│                   DCE Proxy Process                      │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  ┌────────────────────────────────────────────────────┐ │
│  │            Periodic Check (every 15s)              │ │
│  └────────────────────┬───────────────────────────────┘ │
│                       │                                  │
│                       ▼                                  │
│  ┌────────────────────────────────────────────────────┐ │
│  │     now - last_heartbeat_time > 15s ?              │ │
│  └────────────────────┬───────────────────────────────┘ │
│                       │                                  │
│            ┌──────────┴──────────┐                      │
│            │ Yes                 │ No                   │
│            ▼                     ▼                      │
│  ┌─────────────────┐   ┌─────────────────┐             │
│  │ oper_state=DOWN │   │ oper_state=UP   │             │
│  │ Update STATE_DB │   │ (no change)     │             │
│  └─────────────────┘   └─────────────────┘             │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

---

## 7. References

1. [SONiC Console Switch High Level Design](../SONiC-Console-Switch-High-Level-Design.md)
2. SONiC Architecture Documentation
3. Linux Serial Programming Guide
4. Systemd Generator Documentation
