#!/usr/bin/env python3
"""
sync_downstream.py - 同步 consoled 项目文件到 downstream 子模块

用法: python3 sync_downstream.py
"""

import os
import shutil
from pathlib import Path

# 颜色输出
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
RED = '\033[0;31m'
NC = '\033[0m'  # No Color


def sync_file(src: Path, dst: Path) -> bool:
    """同步单个文件"""
    if not src.is_file():
        print(f"{YELLOW}[SKIP]{NC} Source file not found: {src}")
        return False
    
    # 创建目标目录
    dst.parent.mkdir(parents=True, exist_ok=True)
    
    # 复制文件
    shutil.copy2(src, dst)
    print(f"{GREEN}[SYNC]{NC} {src} -> {dst}")
    return True


def sync_dir(src: Path, dst: Path) -> bool:
    """同步目录"""
    if not src.is_dir():
        print(f"{YELLOW}[SKIP]{NC} Source directory not found: {src}")
        return False
    
    # 创建目标目录的父目录
    dst.parent.mkdir(parents=True, exist_ok=True)
    
    # 删除已存在的目标目录，然后复制
    if dst.exists():
        shutil.rmtree(dst)
    
    shutil.copytree(src, dst)
    print(f"{GREEN}[SYNC]{NC} {src}/ -> {dst}/")
    return True


def main():
    # 获取脚本所在目录作为项目根目录
    script_dir = Path(__file__).parent.resolve()
    os.chdir(script_dir)
    
    print("==========================================")
    print("  Syncing consoled to downstream repos")
    print("==========================================")
    
    # 定义同步映射关系
    # (源路径, 目标路径, 是否为目录)
    sync_mappings = [
        # sonic-host-services
        (
            "console_monitor/console-monitor",
            "downstream/sonic-host-services/scripts/console-monitor",
            False,  # 文件
        ),
        # SONiC
        (
            "docs/SONiC-Console-Switch-High-Level-Design.md",
            "downstream/SONiC/doc/console/Console-Monitor-High-Level-Design.md",
            False,  # 文件
        ),
        # sonic-mgmt
        (
            "docs/Console-Monitor-Test-Plan.md",
            "downstream/sonic-mgmt/docs/testplan/console/Console-Monitor-Test-Plan.md",
            False,  # 文件
        ),
        # sonic-utilities - consutil 目录
        (
            "commands/consutil",
            "downstream/sonic-utilities/consutil",
            True,  # 目录
        ),
        # sonic-utilities - config/console.py
        (
            "commands/config/console.py",
            "downstream/sonic-utilities/config/console.py",
            False,  # 文件
        ),
    ]
    
    # 按目标仓库分组显示
    repo_groups = {
        "sonic-host-services": [],
        "SONiC": [],
        "sonic-mgmt": [],
        "sonic-utilities": [],
    }
    
    for src, dst, is_dir in sync_mappings:
        for repo in repo_groups:
            if repo in dst:
                repo_groups[repo].append((src, dst, is_dir))
                break
    
    success_count = 0
    fail_count = 0
    
    for repo, mappings in repo_groups.items():
        if not mappings:
            continue
        
        print(f"\n>>> Syncing to {repo}...")
        for src, dst, is_dir in mappings:
            src_path = script_dir / src
            dst_path = script_dir / dst
            
            if is_dir:
                result = sync_dir(src_path, dst_path)
            else:
                result = sync_file(src_path, dst_path)
            
            if result:
                success_count += 1
            else:
                fail_count += 1
    
    print("\n==========================================")
    print(f"  Sync completed! ({GREEN}{success_count} succeeded{NC}, {YELLOW if fail_count else ''}{fail_count} skipped{NC if fail_count else ''})")
    print("==========================================")


if __name__ == "__main__":
    main()
