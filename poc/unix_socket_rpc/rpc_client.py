#!/usr/bin/env python3
"""
Unix Socket RPC Client - 调用远程方法
"""
import socket
import json
import sys


SOCKET_PATH = "/tmp/consoled_rpc.sock"


class RPCClient:
    """简单的RPC客户端"""
    
    def __init__(self, socket_path):
        self.socket_path = socket_path
        
    def call(self, method, **params):
        """调用远程方法"""
        request = {
            'method': method,
            'params': params
        }
        
        client_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        
        try:
            client_socket.connect(self.socket_path)
            
            # 发送请求
            request_json = json.dumps(request)
            client_socket.send(request_json.encode('utf-8'))
            print(f"[RPC Client] 调用: {request_json}")
            
            # 接收响应
            response_data = client_socket.recv(4096).decode('utf-8')
            response = json.loads(response_data)
            
            print(f"[RPC Client] 响应: {response_data}")
            
            if response.get('error'):
                print(f"[RPC Client] 错误: {response['error']}")
                return None
            
            return response.get('result')
            
        except FileNotFoundError:
            print(f"[RPC Client] 错误: RPC服务器未运行 (找不到 {self.socket_path})")
            return None
        except Exception as e:
            print(f"[RPC Client] 错误: {e}")
            return None
        finally:
            client_socket.close()


def demo():
    """演示RPC调用"""
    client = RPCClient(SOCKET_PATH)
    
    print("=" * 50)
    print("演示 1: 加法运算")
    print("=" * 50)
    result = client.call('add', a=10, b=20)
    print(f"结果: {result}\n")
    
    print("=" * 50)
    print("演示 2: 乘法运算")
    print("=" * 50)
    result = client.call('multiply', a=7, b=8)
    print(f"结果: {result}\n")
    
    print("=" * 50)
    print("演示 3: 获取信息")
    print("=" * 50)
    result = client.call('get_info', name='Alice')
    print(f"结果: {result}\n")
    
    print("=" * 50)
    print("演示 4: 列出文件")
    print("=" * 50)
    result = client.call('list_files', directory='/tmp')
    print(f"结果: {result[:5] if result else None}... (只显示前5个)\n")
    
    print("=" * 50)
    print("演示 5: 调用不存在的方法")
    print("=" * 50)
    result = client.call('nonexistent_method')
    print(f"结果: {result}\n")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == 'demo':
        demo()
    else:
        print("用法:")
        print("  python3 rpc_client.py demo  - 运行演示")
        print("\n或者在代码中使用:")
        print("  client = RPCClient(SOCKET_PATH)")
        print("  result = client.call('add', a=10, b=20)")
