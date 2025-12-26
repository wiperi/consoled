#!/usr/bin/env python3
"""
Unix Socket Client - 向服务器发送消息并接收响应
"""
import socket
import sys
import time


SOCKET_PATH = "/tmp/consoled_poc.sock"


def send_message(message):
    # 创建Unix socket
    client_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    
    try:
        # 连接到服务器
        client_socket.connect(SOCKET_PATH)
        print(f"[Client] 已连接到服务器: {SOCKET_PATH}")
        
        # 发送消息
        client_socket.send(message.encode('utf-8'))
        print(f"[Client] 发送消息: {message}")
        
        # 接收响应
        response = client_socket.recv(1024).decode('utf-8')
        print(f"[Client] 收到响应: {response}")
        
        return response
        
    except FileNotFoundError:
        print(f"[Client] 错误: 服务器未运行 (找不到 {SOCKET_PATH})")
        return None
    except Exception as e:
        print(f"[Client] 错误: {e}")
        return None
    finally:
        client_socket.close()


def interactive_mode():
    """交互模式 - 持续发送消息"""
    print("[Client] 交互模式 (输入 'quit' 退出)")
    
    while True:
        message = input("输入消息: ").strip()
        
        if message.lower() == 'quit':
            print("[Client] 退出")
            break
        
        if not message:
            continue
            
        send_message(message)
        print()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # 命令行参数模式
        message = " ".join(sys.argv[1:])
        send_message(message)
    else:
        # 交互模式
        interactive_mode()
