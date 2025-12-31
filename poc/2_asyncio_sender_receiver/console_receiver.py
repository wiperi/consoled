#!/usr/bin/env python3
"""
虚拟串口接收器
- 同时监听 /dev/r1, /dev/r2, /dev/r3
- 收到 hello 回复 world
- 收到 pause_req 回复 pause_ack
"""

import asyncio
import os
import sys


class SerialReceiver:
    def __init__(self, name: str, device_path: str):
        self.name = name
        self.device_path = device_path
        self.running = True
        self.read_fd = -1
        self.write_fd = -1

    async def open_device(self) -> bool:
        """打开串口设备"""
        try:
            self.read_fd = os.open(self.device_path, os.O_RDONLY | os.O_NONBLOCK)
            self.write_fd = os.open(self.device_path, os.O_WRONLY | os.O_NONBLOCK)
            print(f"[{self.name}] 已打开 {self.device_path}")
            return True
        except Exception as e:
            print(f"[{self.name}] 打开失败: {e}")
            return False

    def send(self, data: str) -> bool:
        """发送数据"""
        try:
            os.write(self.write_fd, (data + "\n").encode())
            return True
        except Exception as e:
            print(f"[{self.name}] 发送失败: {e}")
            return False

    def _blocking_read(self) -> str:
        """阻塞读取（在线程池中执行）"""
        import select

        while self.running:
            r, _, _ = select.select([self.read_fd], [], [], 0.1)
            if r:
                data = os.read(self.read_fd, 1024)
                return data.decode().strip()
        return ""

    async def receive_loop(self):
        """接收循环"""
        if not await self.open_device():
            return

        loop = asyncio.get_event_loop()

        while self.running:
            try:
                # 等待接收数据
                data = await loop.run_in_executor(None, self._blocking_read)

                if not data:
                    continue

                print(f"[{self.name}] 收到: {data}")

                # 处理消息
                data_lower = data.lower()

                if "hello" in data_lower:
                    print(f"[{self.name}] 回复: world")
                    self.send("world")

                elif "pause_req" in data_lower:
                    print(f"[{self.name}] 回复: pause_ack")
                    self.send("pause_ack")

                else:
                    print(f"[{self.name}] 未知消息，忽略")

            except Exception as e:
                print(f"[{self.name}] 接收错误: {e}")

    def stop(self):
        """停止接收"""
        self.running = False
        try:
            if self.read_fd >= 0:
                os.close(self.read_fd)
            if self.write_fd >= 0:
                os.close(self.write_fd)
        except:
            pass


class ReceiverMonitor:
    def __init__(self):
        self.receivers: dict[str, SerialReceiver] = {}
        self.running = True

    def add_receiver(self, name: str, device_path: str):
        """添加串口接收器"""
        self.receivers[name] = SerialReceiver(name, device_path)

    async def stdin_handler(self):
        """处理用户输入"""
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        print("\n📋 命令帮助:")
        print("  status - 查看所有接收器状态")
        print("  quit   - 退出程序")
        print("-" * 40)

        while self.running:
            try:
                line = await reader.readline()
                if not line:
                    break

                cmd = line.decode().strip().lower()
                self.handle_command(cmd)

            except Exception as e:
                print(f"输入错误: {e}")

    def handle_command(self, cmd: str):
        """处理命令"""
        if not cmd:
            return

        if cmd == "status":
            print("\n📊 接收器状态:")
            for name, receiver in self.receivers.items():
                status = "▶️  运行中" if receiver.running else "⏹️  已停止"
                print(f"  {name}: {status}")
            print()

        elif cmd == "quit" or cmd == "exit":
            print("正在退出...")
            self.running = False
            for receiver in self.receivers.values():
                receiver.stop()

        else:
            print(f"未知命令: {cmd}")

    async def run(self):
        """运行监控器"""
        print("=" * 40)
        print("📡 虚拟串口接收器启动")
        print("=" * 40)

        # 启动所有接收任务
        tasks = []
        for receiver in self.receivers.values():
            tasks.append(asyncio.create_task(receiver.receive_loop()))

        # 启动 stdin 处理
        tasks.append(asyncio.create_task(self.stdin_handler()))

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            print("程序已退出")


async def main():
    monitor = ReceiverMonitor()

    # 添加三个虚拟串口接收器
    monitor.add_receiver("r1", "/dev/r1")
    monitor.add_receiver("r2", "/dev/r2")
    monitor.add_receiver("r3", "/dev/r3")

    await monitor.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，退出程序")
