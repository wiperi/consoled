#!/usr/bin/env python3
"""
Unix Socket RPC Server - 实现简单的RPC功能
支持JSON格式的远程过程调用
"""
import socket
import os
import json


SOCKET_PATH = "/tmp/consoled_rpc.sock"


class RPCServer:
    """简单的RPC服务器"""
    
    def __init__(self, socket_path):
        self.socket_path = socket_path
        self.methods = {}
        
    def register_method(self, name, func):
        """注册可调用的方法"""
        self.methods[name] = func
        print(f"[RPC Server] 注册方法: {name}")
        
    def handle_request(self, request_data):
        """处理RPC请求"""
        try:
            request = json.loads(request_data)
            method_name = request.get('method')
            params = request.get('params', {})
            
            if method_name not in self.methods:
                return {
                    'error': f'Method not found: {method_name}',
                    'result': None
                }
            
            # 调用方法
            result = self.methods[method_name](**params)
            
            return {
                'result': result,
                'error': None
            }
            
        except json.JSONDecodeError:
            return {'error': 'Invalid JSON', 'result': None}
        except Exception as e:
            return {'error': str(e), 'result': None}
    
    def start(self):
        """启动RPC服务器"""
        if os.path.exists(self.socket_path):
            os.remove(self.socket_path)
        
        server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_socket.bind(self.socket_path)
        server_socket.listen(5)
        
        print(f"[RPC Server] 监听中... Socket路径: {self.socket_path}")
        print(f"[RPC Server] 可用方法: {list(self.methods.keys())}")
        
        try:
            while True:
                client_socket, _ = server_socket.accept()
                print("[RPC Server] 客户端已连接")
                
                try:
                    data = client_socket.recv(4096)
                    if data:
                        request_data = data.decode('utf-8')
                        print(f"[RPC Server] 收到请求: {request_data}")
                        
                        response = self.handle_request(request_data)
                        response_json = json.dumps(response)
                        
                        client_socket.send(response_json.encode('utf-8'))
                        print(f"[RPC Server] 发送响应: {response_json}")
                        
                except Exception as e:
                    print(f"[RPC Server] 处理请求时出错: {e}")
                finally:
                    client_socket.close()
                    
        except KeyboardInterrupt:
            print("\n[RPC Server] 服务器关闭")
        finally:
            server_socket.close()
            if os.path.exists(self.socket_path):
                os.remove(self.socket_path)


# ========== 示例RPC方法 ==========

def add(a, b):
    """加法"""
    return a + b


def multiply(a, b):
    """乘法"""
    return a * b


def get_info(name="Guest"):
    """获取信息"""
    return {
        'message': f'Hello, {name}!',
        'timestamp': __import__('time').time()
    }


def list_files(directory="/tmp"):
    """列出目录中的文件"""
    try:
        return os.listdir(directory)
    except Exception as e:
        return {'error': str(e)}


if __name__ == "__main__":
    server = RPCServer(SOCKET_PATH)
    
    # 注册方法
    server.register_method('add', add)
    server.register_method('multiply', multiply)
    server.register_method('get_info', get_info)
    server.register_method('list_files', list_files)
    
    # 启动服务器
    server.start()
