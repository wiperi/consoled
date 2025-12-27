#!/usr/bin/env python3
"""
虚拟串口监控程序
- 同时监听 /dev/s1, /dev/s2, /dev/s3
- 发送 hello，期望5秒内收到 world
- 支持 pause/resume 命令控制探测
"""

import asyncio
import sys
import os


class SerialProbe:
    def __init__(self, name: str, device_path: str):
        self.name = name
        self.device_path = device_path
        self.paused = False
        self.reader = None
        self.writer = None
        self.running = True
        self.pause_event = asyncio.Event()  # pause 信号事件
        self.pause_event.clear()  # 初始状态为未暂停

    async def open(self):
        """打开串口设备"""
        try:
            # 以非阻塞方式打开设备
            self.reader, self.writer = await asyncio.open_connection(limit=1024)
            # 使用文件描述符方式打开
            fd = os.open(self.device_path, os.O_RDWR | os.O_NONBLOCK)
            loop = asyncio.get_event_loop()
            self.reader = asyncio.StreamReader()
            protocol = asyncio.StreamReaderProtocol(self.reader)
            transport, _ = await loop.connect_read_pipe(
                lambda: protocol, os.fdopen(fd, "rb", buffering=0)
            )

            # 写入需要单独的文件描述符
            self.write_fd = os.open(self.device_path, os.O_WRONLY | os.O_NONBLOCK)
            print(f"[{self.name}] 已打开 {self.device_path}")
            return True
        except Exception as e:
            print(f"[{self.name}] 打开失败: {e}")
            return False

    async def open_device(self):
        """打开串口设备（简化版本）"""
        try:
            self.read_fd = os.open(self.device_path, os.O_RDONLY | os.O_NONBLOCK)
            self.write_fd = os.open(self.device_path, os.O_WRONLY | os.O_NONBLOCK)
            print(f"[{self.name}] 已打开 {self.device_path}")
            return True
        except Exception as e:
            print(f"[{self.name}] 打开失败: {e}")
            return False

    def send(self, data: str):
        """发送数据"""
        try:
            os.write(self.write_fd, (data + "\n").encode())
            return True
        except Exception as e:
            print(f"[{self.name}] 发送失败: {e}")
            return False

    async def read_with_timeout(self, timeout: float) -> str | None:
        """带超时和 pause 打断的读取"""
        loop = asyncio.get_event_loop()
        try:
            # 创建读取任务
            read_task = asyncio.create_task(
                asyncio.wait_for(
                    loop.run_in_executor(None, self._blocking_read),
                    timeout=timeout
                )
            )
            # 创建 pause 事件任务
            pause_task = asyncio.create_task(self.pause_event.wait())
            
            # 等待三个事件中的任何一个完成: 读取、超时、pause
            done, pending = await asyncio.wait(
                [read_task, pause_task],
                return_when=asyncio.FIRST_COMPLETED
            )
            
            # 取消所有待处理的任务
            for task in pending:
                task.cancel()
            for task in pending:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            
            # 检查哪个任务先完成
            if pause_task in done:
                # 被 pause 打断
                return None
            
            if read_task in done:
                # 读取任务完成
                try:
                    return read_task.result()
                except asyncio.TimeoutError:
                    return None
            
            return None
            
        except Exception as e:
            print(f"[{self.name}] 读取错误: {e}")
            return None

    def _blocking_read(self) -> str:
        """阻塞读取（在线程池中执行）"""
        import select

        while self.running and not self.paused:
            r, _, _ = select.select([self.read_fd], [], [], 0.1)
            if r:
                data = os.read(self.read_fd, 1024)
                return data.decode().strip()
        return ""

    async def probe_loop(self):
        """探测循环"""
        if not await self.open_device():
            return

        while self.running:
            # 如果处于暂停状态，等待 pause_event 被清除
            if self.paused:
                await asyncio.sleep(0.5)
                continue

            # 发送 hello
            print(f"[{self.name}] 发送: hello")
            self.send("hello")

            # 等待回复（会被 response、timeout 或 pause 打断）
            response = await self.read_with_timeout(5.0)

            # 检查是否被 pause 打断
            if self.paused:
                print(f"[{self.name}] ⏸️  等待被 pause 打断")
                continue

            if response is None:
                print(f"[{self.name}] ⚠️  超时: 5秒内未收到回复")
            elif "world" in response.lower():
                print(f"[{self.name}] ✅ 收到回复: {response}")
            else:
                print(f"[{self.name}] ❓ 收到非预期回复: {response}")

            # 间隔一段时间再次探测
            await asyncio.sleep(0.5)

    async def pause(self):
        """暂停探测（立刻打断当前等待）"""
        self.paused = True
        self.pause_event.set()  # 设置 pause 信号
        
        # 向 receiver 发送 pause_req
        print(f"[{self.name}] 发送: pause_req")
        self.send("pause_req")
        
        # 等待 1 秒内收到 pause_ack
        response = await self._wait_for_ack(1.0)
        
        if response and "pause_ack" in response.lower():
            print(f"[{self.name}] ✅ 收到 pause_ack")
        else:
            print(f"[{self.name}] ⚠️  等待 pause_ack 超时")
        
        print(f"[{self.name}] ⏸️  已暂停")
    
    async def _wait_for_ack(self, timeout: float) -> str | None:
        """等待 ack 回复"""
        loop = asyncio.get_event_loop()
        try:
            data = await asyncio.wait_for(
                loop.run_in_executor(None, self._blocking_read_once),
                timeout=timeout
            )
            return data
        except asyncio.TimeoutError:
            return None
    
    def _blocking_read_once(self) -> str:
        """阻塞读取一次（用于 ack 等待）"""
        import select
        r, _, _ = select.select([self.read_fd], [], [], 1.0)
        if r:
            data = os.read(self.read_fd, 1024)
            return data.decode().strip()
        return ""

    def resume(self):
        """恢复探测"""
        self.paused = False
        self.pause_event.clear()  # 清除 pause 信号
        print(f"[{self.name}] ▶️  已恢复")

    def stop(self):
        """停止探测"""
        self.running = False
        try:
            os.close(self.read_fd)
            os.close(self.write_fd)
        except:
            pass


class ConsoleMonitor:
    def __init__(self):
        self.probes: dict[str, SerialProbe] = {}
        self.running = True

    def add_probe(self, name: str, device_path: str):
        """添加串口探测器"""
        self.probes[name] = SerialProbe(name, device_path)

    async def stdin_handler(self):
        """处理用户输入"""
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        print("\n📋 命令帮助:")
        print("  pause <n>  - 暂停 sn 的探测 (如: pause 1)")
        print("  resume <n> - 恢复 sn 的探测 (如: resume 1)")
        print("  status     - 查看所有探测器状态")
        print("  quit       - 退出程序")
        print("-" * 40)

        while self.running:
            try:
                line = await reader.readline()
                if not line:
                    break

                cmd = line.decode().strip().lower()
                await self.handle_command(cmd)

            except Exception as e:
                print(f"输入错误: {e}")

    async def handle_command(self, cmd: str):
        """处理命令"""
        parts = cmd.split()
        if not parts:
            return

        action = parts[0]

        if action == "pause" and len(parts) >= 2:
            name = f"s{parts[1]}"
            if name in self.probes:
                await self.probes[name].pause()
            else:
                print(f"未找到探测器: {name}")

        elif action == "resume" and len(parts) >= 2:
            name = f"s{parts[1]}"
            if name in self.probes:
                self.probes[name].resume()
            else:
                print(f"未找到探测器: {name}")

        elif action == "status":
            print("\n📊 探测器状态:")
            for name, probe in self.probes.items():
                status = "⏸️  暂停" if probe.paused else "▶️  运行中"
                print(f"  {name}: {status}")
            print()

        elif action == "quit" or action == "exit":
            print("正在退出...")
            self.running = False
            for probe in self.probes.values():
                probe.stop()

        else:
            print(f"未知命令: {cmd}")

    async def run(self):
        """运行监控器"""
        print("=" * 40)
        print("🖥️  虚拟串口监控程序启动")
        print("=" * 40)

        # 启动所有探测任务
        tasks = []
        for probe in self.probes.values():
            tasks.append(asyncio.create_task(probe.probe_loop()))

        # 启动 stdin 处理
        tasks.append(asyncio.create_task(self.stdin_handler()))

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            print("程序已退出")


async def main():
    monitor = ConsoleMonitor()

    # 添加三个虚拟串口探测器
    monitor.add_probe("s1", "/dev/s1")
    # monitor.add_probe("s2", "/dev/s2")
    # monitor.add_probe("s3", "/dev/s3")

    await monitor.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，退出程序")
