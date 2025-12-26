# Unix Socket 进程间通信 POC

这个 POC 演示了如何使用 Unix Domain Socket 实现进程间通信（IPC）。

## 文件说明

### 1. 基础示例 - Echo 服务
- **server.py** - 简单的 echo 服务器，接收消息并返回
- **client.py** - 客户端，发送消息并接收响应

### 2. RPC 示例 - 远程过程调用
- **rpc_server.py** - RPC 服务器，支持 JSON 格式的方法调用
- **rpc_client.py** - RPC 客户端，调用远程方法

## 使用方法

### 运行基础 Echo 服务

**终端 1 - 启动服务器:**
```bash
python3 server.py
```

**终端 2 - 运行客户端:**
```bash
# 交互模式
python3 client.py

# 或直接发送消息
python3 client.py "Hello, Server!"
```

### 运行 RPC 服务

**终端 1 - 启动 RPC 服务器:**
```bash
python3 rpc_server.py
```

**终端 2 - 运行 RPC 客户端演示:**
```bash
python3 rpc_client.py demo
```

## Unix Socket 特点

### 优势
1. **性能高** - 不需要网络协议栈，直接在内核中传输数据
2. **安全性好** - 只能在本地访问，可以使用文件系统权限控制
3. **简单可靠** - 类似 TCP socket 的 API，但更简单

### vs TCP Socket
- Unix Socket: `AF_UNIX` + 文件路径
- TCP Socket: `AF_INET` + IP地址 + 端口号

### vs 其他 IPC 方式
- **管道(Pipe)**: 单向，适合父子进程
- **消息队列**: 需要序列化，较复杂
- **共享内存**: 需要同步机制，复杂
- **Unix Socket**: 双向，简单，可靠

## 代码示例

### 服务器端
```python
import socket
import os

SOCKET_PATH = "/tmp/my_app.sock"

# 创建 Unix socket
server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
server.bind(SOCKET_PATH)
server.listen(5)

# 接受连接
client, _ = server.accept()
data = client.recv(1024)
client.send(b"Response")
```

### 客户端
```python
import socket

SOCKET_PATH = "/tmp/my_app.sock"

# 连接到服务器
client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
client.connect(SOCKET_PATH)

# 发送和接收
client.send(b"Hello")
response = client.recv(1024)
```

## 注意事项

1. **清理 Socket 文件** - 服务器关闭后要删除 socket 文件
2. **权限控制** - 可以使用 `os.chmod()` 设置 socket 文件权限
3. **错误处理** - 处理连接断开、超时等异常情况
4. **缓冲区大小** - 根据实际需求调整 `recv()` 的缓冲区大小

## 扩展功能

可以在此基础上实现：
- 异步 I/O (使用 `asyncio`)
- 多客户端并发处理 (使用线程池或进程池)
- 双向流式通信
- 认证和加密
- 协议设计和版本管理
