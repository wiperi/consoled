# Copilot Instructions for consoled

## Project Overview

**consoled** is a SONiC Console Monitor solution that provides link operational state detection (Oper Up/Down) between Console Server (DCE) and SONiC Switch (DTE) via serial ports. The system uses heartbeat frames for connectivity detection without interfering with normal console operations.

---

## Project Structure

```
consoled/
├── console_monitor/          # Core module
│   ├── dce.py               # DCE side service (Console Server)
│   ├── dte.py               # DTE side service (SONiC Switch)
│   ├── frame.py             # Frame protocol implementation
│   ├── serial_proxy.py      # Serial port proxy with PTY
│   ├── db_util.py           # Redis database utilities
│   ├── util.py              # Common utilities
│   └── constants.py         # Global constants
│
├── command_archive/          # CLI command tools
│   ├── consutil/            # Console utility commands (show, clear, connect)
│   ├── connect/             # Connection command
│   └── config/              # Configuration command
│
├── install/                  # Installation scripts
│   ├── dce/                 # DCE side installation (systemd service)
│   └── dte/                 # DTE side installation (systemd template service)
│
├── docs/                     # Documentation
│   ├── Console-Monitor-HLD-CN.md    # High Level Design (Chinese)
│   └── SONiC-Console-Switch-High-Level-Design.md
│
├── tests/                    # Unit tests (pytest)
└── poc/                      # Proof of concept experiments
```

---

## HLD Document Outline

The main design document is located at `docs/Console-Monitor-HLD-CN.md`. Its structure:

| Section | Content |
|---------|---------|
| **术语与缩写** | Terminology: DCE, DTE, Heartbeat, PTY, Proxy, TTY |
| **1. 功能概述** | Feature requirements and design goals |
| **2. 设计概述** | Architecture overview, DTE/DCE side design |
| **3. 详细设计** | Frame structure (3.1), DTE service (3.2), DCE service (3.3) |
| **4. 数据库更改** | Redis CONFIG_DB and STATE_DB schema |
| **5. CLI** | Command line interface design |
| **6. 流程图** | Flow diagrams |
| **7. 参考资料** | References |

### Key Design Points in HLD

- **Frame Protocol (Section 3.1)**: SOF/EOF delimiters, DLE escaping, CRC16-MODBUS
- **DTE Service (Section 3.2)**: Heartbeat sender, Redis keyspace notification
- **DCE Service (Section 3.3)**: Serial proxy, PTY bridge, frame filtering

---

## Technical Stack

- **Language**: Python 3.8+
- **Async**: asyncio
- **Database**: Redis (CONFIG_DB=4, STATE_DB=6)
- **Serial**: termios, PTY (os.openpty)
- **CLI**: Click
- **Testing**: pytest

---

## Coding Conventions

- Use **Chinese comments** for core logic explanations
- Use **type hints** for function signatures
- Use `dataclass` for data structures, `IntEnum` for enumerations
- Naming: `PascalCase` for classes, `snake_case` for functions, `UPPER_SNAKE_CASE` for constants
- Private methods: prefix with `_`

---

## Important Workflow Rules

### 🔴 Documentation Synchronization (MANDATORY)

**Every time you modify feature code, you MUST synchronize the changes to the HLD document:**

1. Update the relevant section in `docs/Console-Monitor-HLD-CN.md`
2. Ensure code implementation matches the HLD description
3. Update diagrams if architecture changes

### 🔴 Git Commit Convention (MANDATORY)

All commits MUST follow the **Conventional Commits** standard:

```
<type>(<scope>): <description>

[optional body]

[optional footer(s)]
```

Unless specified by user. Do not add optional body and optional footers.

**Types:**
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation only changes
- `refactor`: Code refactoring
- `test`: Adding or updating tests
- `chore`: Maintenance tasks

**Examples:**
```bash
feat(frame): add CRC16-MODBUS validation

docs(hld): update frame protocol section 3.1

fix(dce): resolve PTY symlink race condition

refactor(serial_proxy): simplify heartbeat timeout logic

feat(dte): implement Redis keyspace notification

docs(hld): sync heartbeat interval change to section 3.2
```

**When modifying features, commit pattern:**
```bash
# First commit: code change
git commit -m "feat(frame): add new frame type for status report"

# Second commit: documentation sync
git commit -m "docs(hld): add status report frame type to section 3.1.6"
```

---

## Common Development Tasks

### Adding a New Frame Type
1. Add type to `FrameType` enum in `frame.py`
2. Handle the new type in `FrameFilter`
3. **Update HLD section 3.1.6** (Frame Type Definition)
4. Commit with conventional format

### Modifying Serial Configuration
1. Update `constants.py` (e.g., `BAUD_MAP`, timeouts)
2. Adjust logic in `serial_proxy.py`
3. **Update HLD section 3.3** if behavior changes
4. Commit with conventional format

### Adding CLI Commands
1. Add command in `command_archive/consutil/main.py`
2. Use Click decorators
3. **Update HLD section 5** (CLI)
4. Commit with conventional format
