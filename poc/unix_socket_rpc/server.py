#!/usr/bin/env python3
"""
Unix Socket Server - 接收客户端消息并响应
"""
import socket
import os
import sys


SOCKET_PATH = "/tmp/consoled_poc.sock"


def start_server():
    # 如果socket文件已存在，删除它
    if os.path.exists(SOCKET_PATH):
        os.remove(SOCKET_PATH)
    
    # 创建Unix socket
    server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    
    # 绑定socket到文件路径
    server_socket.bind(SOCKET_PATH)
    
    # 开始监听（最多5个pending连接）
    server_socket.listen(5)
    print(f"[Server] 监听中... Socket路径: {SOCKET_PATH}")
    
    try:
        while True:
            # 接受客户端连接
            client_socket, _ = server_socket.accept()
            print("[Server] 客户端已连接")
            
            try:
                while True:
                    # 接收数据（最多1024字节）
                    data = client_socket.recv(1024)
                    
                    if not data:
                        # 客户端关闭连接
                        print("[Server] 客户端断开连接")
                        break
                    
                    message = data.decode('utf-8')
                    print(f"[Server] 收到消息: {message}")
                    
                    # 发送响应
                    response = f"Echo: {message}"
                    client_socket.send(response.encode('utf-8'))
                    print(f"[Server] 发送响应: {response}")
                    
            except Exception as e:
                print(f"[Server] 处理客户端时出错: {e}")
            finally:
                client_socket.close()
                
    except KeyboardInterrupt:
        print("\n[Server] 服务器关闭")
    finally:
        server_socket.close()
        if os.path.exists(SOCKET_PATH):
            os.remove(SOCKET_PATH)


if __name__ == "__main__":
    start_server()
