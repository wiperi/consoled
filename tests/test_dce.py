#!/usr/bin/env python3
"""
DCE 主服务 (ProxyManager) 单元测试 (pytest)
"""

import sys
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Mock sonic_py_common 模块（仅在 SONiC 环境下存在）
sys.modules['sonic_py_common'] = MagicMock()
sys.modules['sonic_py_common.device_info'] = MagicMock()

from console_monitor.dce import ProxyManager


# ============================================================
# ProxyManager 初始化测试
# ============================================================

class TestProxyManagerInit:
    """ProxyManager 初始化测试"""

    def test_init_defaults(self):
        """测试初始化默认值"""
        manager = ProxyManager()

        assert manager.loop is None
        assert manager.proxies == {}
        assert manager.running is False
        assert manager.pty_symlink_prefix == ""
        assert manager.db is not None


# ============================================================
# ProxyManager Start 测试
# ============================================================

class TestProxyManagerStart:
    """ProxyManager 启动测试"""

    @pytest.fixture
    def manager(self):
        """创建 ProxyManager 实例"""
        return ProxyManager()

    @pytest.mark.asyncio
    async def test_start_connects_db(self, manager):
        """测试启动时连接数据库"""
        manager.db.connect = AsyncMock()
        manager.db.check_console_feature_enabled = AsyncMock(return_value=False)
        manager.db.get_all_configs = AsyncMock(return_value={})
        manager.db.subscribe_config_changes = AsyncMock()

        with patch('console_monitor.dce.get_pty_symlink_prefix', return_value='/dev/VC0-'):
            await manager.start()

        manager.db.connect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_checks_feature_enabled(self, manager):
        """测试启动时检查功能是否启用"""
        manager.db.connect = AsyncMock()
        manager.db.check_console_feature_enabled = AsyncMock(return_value=True)
        manager.db.get_all_configs = AsyncMock(return_value={})
        manager.db.subscribe_config_changes = AsyncMock()

        with patch('console_monitor.dce.get_pty_symlink_prefix', return_value='/dev/VC0-'):
            await manager.start()

        manager.db.check_console_feature_enabled.assert_awaited()

    @pytest.mark.asyncio
    async def test_start_reads_pty_prefix(self, manager):
        """测试启动时读取 PTY 前缀"""
        manager.db.connect = AsyncMock()
        manager.db.check_console_feature_enabled = AsyncMock(return_value=False)
        manager.db.get_all_configs = AsyncMock(return_value={})
        manager.db.subscribe_config_changes = AsyncMock()

        with patch('console_monitor.dce.get_pty_symlink_prefix', return_value='/dev/custom-') as mock_prefix:
            await manager.start()

        mock_prefix.assert_called_once()
        assert manager.pty_symlink_prefix == '/dev/custom-'

    @pytest.mark.asyncio
    async def test_start_syncs_proxies(self, manager):
        """测试启动时初始同步代理"""
        manager.db.connect = AsyncMock()
        manager.db.check_console_feature_enabled = AsyncMock(return_value=True)
        manager.db.get_all_configs = AsyncMock(return_value={})
        manager.db.subscribe_config_changes = AsyncMock()

        with patch('console_monitor.dce.get_pty_symlink_prefix', return_value='/dev/VC0-'):
            await manager.start()

        # sync 会调用 get_all_configs
        manager.db.get_all_configs.assert_awaited()

    @pytest.mark.asyncio
    async def test_start_subscribes_events(self, manager):
        """测试启动时订阅配置事件"""
        manager.db.connect = AsyncMock()
        manager.db.check_console_feature_enabled = AsyncMock(return_value=False)
        manager.db.get_all_configs = AsyncMock(return_value={})
        manager.db.subscribe_config_changes = AsyncMock()

        with patch('console_monitor.dce.get_pty_symlink_prefix', return_value='/dev/VC0-'):
            await manager.start()

        manager.db.subscribe_config_changes.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_sets_running_true(self, manager):
        """测试启动后 running 标志为 True"""
        manager.db.connect = AsyncMock()
        manager.db.check_console_feature_enabled = AsyncMock(return_value=False)
        manager.db.get_all_configs = AsyncMock(return_value={})
        manager.db.subscribe_config_changes = AsyncMock()

        with patch('console_monitor.dce.get_pty_symlink_prefix', return_value='/dev/VC0-'):
            await manager.start()

        assert manager.running is True


# ============================================================
# ProxyManager Sync 测试
# ============================================================

class TestProxyManagerSync:
    """ProxyManager 同步测试"""

    @pytest.fixture
    def manager(self):
        """创建已启动的 ProxyManager 实例"""
        m = ProxyManager()
        m.loop = MagicMock()
        m.pty_symlink_prefix = '/dev/VC0-'
        return m

    @pytest.mark.asyncio
    async def test_sync_returns_if_no_loop(self):
        """测试没有 loop 时直接返回"""
        manager = ProxyManager()
        manager.db.check_console_feature_enabled = AsyncMock()

        await manager.sync()

        # 没有 loop，不应该调用任何方法
        manager.db.check_console_feature_enabled.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_stops_all_when_feature_disabled(self, manager):
        """测试功能禁用时停止所有代理"""
        manager.db.check_console_feature_enabled = AsyncMock(return_value=False)

        # 创建模拟代理
        mock_proxy = AsyncMock()
        mock_proxy.stop = AsyncMock()
        manager.proxies = {'1': mock_proxy}

        await manager.sync()

        mock_proxy.stop.assert_awaited_once()
        assert manager.proxies == {}

    @pytest.mark.asyncio
    async def test_sync_adds_new_proxies(self, manager):
        """测试添加新代理"""
        manager.db.check_console_feature_enabled = AsyncMock(return_value=True)
        manager.db.get_all_configs = AsyncMock(return_value={
            '1': {'device': '/dev/C0-1', 'baud': 9600},
        })

        mock_proxy = MagicMock()
        mock_proxy.start = AsyncMock(return_value=True)

        with patch('console_monitor.dce.SerialProxy', return_value=mock_proxy):
            await manager.sync()

        assert '1' in manager.proxies
        mock_proxy.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_removes_deleted_proxies(self, manager):
        """测试移除已删除代理"""
        manager.db.check_console_feature_enabled = AsyncMock(return_value=True)
        manager.db.get_all_configs = AsyncMock(return_value={})

        # 添加一个现有代理
        mock_proxy = AsyncMock()
        mock_proxy.stop = AsyncMock()
        manager.proxies = {'1': mock_proxy}

        await manager.sync()

        mock_proxy.stop.assert_awaited_once()
        assert '1' not in manager.proxies

    @pytest.mark.asyncio
    async def test_sync_updates_changed_proxies(self, manager):
        """测试更新配置变化的代理"""
        manager.db.check_console_feature_enabled = AsyncMock(return_value=True)
        manager.db.get_all_configs = AsyncMock(return_value={
            '1': {'device': '/dev/C0-1', 'baud': 115200},  # 波特率变化
        })

        # 添加一个现有代理（波特率 9600）
        old_proxy = MagicMock()
        old_proxy.baud = 9600
        old_proxy.stop = AsyncMock()
        manager.proxies = {'1': old_proxy}

        new_proxy = MagicMock()
        new_proxy.start = AsyncMock(return_value=True)

        with patch('console_monitor.dce.SerialProxy', return_value=new_proxy):
            await manager.sync()

        old_proxy.stop.assert_awaited_once()
        new_proxy.start.assert_awaited_once()
        assert manager.proxies['1'] is new_proxy

    @pytest.mark.asyncio
    async def test_sync_ignores_unchanged_proxies(self, manager):
        """测试不影响未变化的代理"""
        manager.db.check_console_feature_enabled = AsyncMock(return_value=True)
        manager.db.get_all_configs = AsyncMock(return_value={
            '1': {'device': '/dev/C0-1', 'baud': 9600},  # 相同配置
        })

        # 添加一个现有代理（波特率相同）
        existing_proxy = MagicMock()
        existing_proxy.baud = 9600
        existing_proxy.stop = AsyncMock()
        manager.proxies = {'1': existing_proxy}

        await manager.sync()

        existing_proxy.stop.assert_not_awaited()
        assert manager.proxies['1'] is existing_proxy

    @pytest.mark.asyncio
    async def test_sync_proxy_start_failure(self, manager):
        """测试代理启动失败不加入 proxies 字典"""
        manager.db.check_console_feature_enabled = AsyncMock(return_value=True)
        manager.db.get_all_configs = AsyncMock(return_value={
            '1': {'device': '/dev/C0-1', 'baud': 9600},
        })

        mock_proxy = MagicMock()
        mock_proxy.start = AsyncMock(return_value=False)  # 启动失败

        with patch('console_monitor.dce.SerialProxy', return_value=mock_proxy):
            await manager.sync()

        assert '1' not in manager.proxies


# ============================================================
# ProxyManager Run 测试
# ============================================================

class TestProxyManagerRun:
    """ProxyManager 主循环测试"""

    @pytest.fixture
    def manager(self):
        """创建已启动的 ProxyManager 实例"""
        m = ProxyManager()
        m.loop = MagicMock()
        m.running = True
        return m

    @pytest.mark.asyncio
    async def test_run_processes_events(self, manager):
        """测试处理配置事件"""
        call_count = 0

        async def mock_get_event():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {'data': 'hset', 'channel': 'test'}
            else:
                manager.running = False
                return None

        manager.db.get_config_event = mock_get_event
        manager.db.check_console_feature_enabled = AsyncMock(return_value=False)
        manager.db.get_all_configs = AsyncMock(return_value={})

        await manager.run()

        assert call_count == 2

    @pytest.mark.asyncio
    async def test_run_syncs_on_event(self, manager):
        """测试收到事件后同步"""
        call_count = 0

        async def mock_get_event():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {'data': 'hset', 'channel': 'test'}
            else:
                manager.running = False
                return None

        manager.db.get_config_event = mock_get_event
        manager.db.check_console_feature_enabled = AsyncMock(return_value=False)
        manager.db.get_all_configs = AsyncMock(return_value={})

        await manager.run()

        # sync 在收到事件后被调用
        manager.db.check_console_feature_enabled.assert_awaited()

    @pytest.mark.asyncio
    async def test_run_stops_on_running_false(self, manager):
        """测试 running 为 False 时停止"""
        manager.running = False
        manager.db.get_config_event = AsyncMock()

        await manager.run()

        manager.db.get_config_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_run_continues_on_no_event(self, manager):
        """测试没有事件时继续循环"""
        call_count = 0

        async def mock_get_event():
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                manager.running = False
            return None  # 无事件

        manager.db.get_config_event = mock_get_event
        manager.db.check_console_feature_enabled = AsyncMock(return_value=False)

        await manager.run()

        assert call_count == 3
        # 没有事件时不应该调用 sync
        manager.db.check_console_feature_enabled.assert_not_awaited()


# ============================================================
# ProxyManager Stop 测试
# ============================================================

class TestProxyManagerStop:
    """ProxyManager 停止测试"""

    @pytest.fixture
    def manager(self):
        """创建 ProxyManager 实例"""
        m = ProxyManager()
        m.running = True
        return m

    @pytest.mark.asyncio
    async def test_stop_all_proxies(self, manager):
        """测试停止所有代理"""
        mock_proxy1 = AsyncMock()
        mock_proxy1.stop = AsyncMock()
        mock_proxy2 = AsyncMock()
        mock_proxy2.stop = AsyncMock()
        manager.proxies = {'1': mock_proxy1, '2': mock_proxy2}
        manager.db.close = AsyncMock()

        await manager.stop()

        mock_proxy1.stop.assert_awaited_once()
        mock_proxy2.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_closes_db(self, manager):
        """测试关闭数据库连接"""
        manager.db.close = AsyncMock()

        await manager.stop()

        manager.db.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_clears_proxies(self, manager):
        """测试清空代理字典"""
        mock_proxy = AsyncMock()
        mock_proxy.stop = AsyncMock()
        manager.proxies = {'1': mock_proxy}
        manager.db.close = AsyncMock()

        await manager.stop()

        assert manager.proxies == {}

    @pytest.mark.asyncio
    async def test_stop_sets_running_false(self, manager):
        """测试停止后 running 标志为 False"""
        manager.db.close = AsyncMock()

        await manager.stop()

        assert manager.running is False

    @pytest.mark.asyncio
    async def test_stop_handles_proxy_exception(self, manager):
        """测试代理停止时异常不阻塞其他代理"""
        mock_proxy1 = AsyncMock()
        mock_proxy1.stop = AsyncMock(side_effect=Exception("stop error"))
        mock_proxy2 = AsyncMock()
        mock_proxy2.stop = AsyncMock()
        manager.proxies = {'1': mock_proxy1, '2': mock_proxy2}
        manager.db.close = AsyncMock()

        # 不应抛出异常
        await manager.stop()

        mock_proxy1.stop.assert_awaited_once()
        mock_proxy2.stop.assert_awaited_once()
        assert manager.proxies == {}

    @pytest.mark.asyncio
    async def test_stop_with_empty_proxies(self, manager):
        """测试没有代理时停止"""
        manager.proxies = {}
        manager.db.close = AsyncMock()

        await manager.stop()

        manager.db.close.assert_awaited_once()
        assert manager.proxies == {}


# ============================================================
# 集成场景测试
# ============================================================

class TestProxyManagerIntegration:
    """ProxyManager 集成场景测试"""

    @pytest.mark.asyncio
    async def test_full_lifecycle(self):
        """测试完整生命周期：启动 -> 同步 -> 停止"""
        manager = ProxyManager()
        manager.db.connect = AsyncMock()
        manager.db.check_console_feature_enabled = AsyncMock(return_value=True)
        manager.db.get_all_configs = AsyncMock(return_value={
            '1': {'device': '/dev/C0-1', 'baud': 9600},
        })
        manager.db.subscribe_config_changes = AsyncMock()
        manager.db.close = AsyncMock()

        mock_proxy = MagicMock()
        mock_proxy.start = AsyncMock(return_value=True)
        mock_proxy.stop = AsyncMock()
        mock_proxy.baud = 9600

        with patch('console_monitor.dce.get_pty_symlink_prefix', return_value='/dev/VC0-'):
            with patch('console_monitor.dce.SerialProxy', return_value=mock_proxy):
                await manager.start()

                assert manager.running is True
                assert '1' in manager.proxies

                await manager.stop()

                assert manager.running is False
                assert manager.proxies == {}

        mock_proxy.stop.assert_awaited_once()
        manager.db.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_feature_enable_disable_cycle(self):
        """测试功能启用/禁用切换"""
        manager = ProxyManager()
        manager.loop = MagicMock()
        manager.pty_symlink_prefix = '/dev/VC0-'

        # 第一次同步：功能启用，添加代理
        manager.db.check_console_feature_enabled = AsyncMock(return_value=True)
        manager.db.get_all_configs = AsyncMock(return_value={
            '1': {'device': '/dev/C0-1', 'baud': 9600},
        })

        mock_proxy = MagicMock()
        mock_proxy.start = AsyncMock(return_value=True)
        mock_proxy.stop = AsyncMock()

        with patch('console_monitor.dce.SerialProxy', return_value=mock_proxy):
            await manager.sync()
            assert '1' in manager.proxies

        # 第二次同步：功能禁用，停止所有代理
        manager.db.check_console_feature_enabled = AsyncMock(return_value=False)

        await manager.sync()

        mock_proxy.stop.assert_awaited_once()
        assert manager.proxies == {}

    @pytest.mark.asyncio
    async def test_multiple_proxies_management(self):
        """测试多代理管理"""
        manager = ProxyManager()
        manager.loop = MagicMock()
        manager.pty_symlink_prefix = '/dev/VC0-'
        manager.db.check_console_feature_enabled = AsyncMock(return_value=True)

        # 初始配置：3 个串口
        manager.db.get_all_configs = AsyncMock(return_value={
            '1': {'device': '/dev/C0-1', 'baud': 9600},
            '2': {'device': '/dev/C0-2', 'baud': 9600},
            '3': {'device': '/dev/C0-3', 'baud': 9600},
        })

        proxies_created = []

        def create_proxy(*args, **kwargs):
            p = MagicMock()
            p.start = AsyncMock(return_value=True)
            p.stop = AsyncMock()
            p.baud = 9600
            proxies_created.append(p)
            return p

        with patch('console_monitor.dce.SerialProxy', side_effect=create_proxy):
            await manager.sync()

        assert len(manager.proxies) == 3
        assert len(proxies_created) == 3

        # 配置变更：删除一个，添加一个
        manager.db.get_all_configs = AsyncMock(return_value={
            '1': {'device': '/dev/C0-1', 'baud': 9600},  # 保留
            '2': {'device': '/dev/C0-2', 'baud': 9600},  # 保留
            # '3' 删除
            '4': {'device': '/dev/C0-4', 'baud': 9600},  # 新增
        })

        with patch('console_monitor.dce.SerialProxy', side_effect=create_proxy):
            await manager.sync()

        assert len(manager.proxies) == 3
        assert '1' in manager.proxies
        assert '2' in manager.proxies
        assert '3' not in manager.proxies
        assert '4' in manager.proxies
