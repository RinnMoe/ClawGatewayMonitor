#!/usr/bin/env python3
"""
测试系统监控功能
"""

import sys
import os

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor import get_system_stats, check_system_health

def test_system_stats():
    """测试获取系统统计信息"""
    print("🔍 测试获取系统统计信息...")
    
    stats = get_system_stats()
    
    if stats:
        print("✅ 成功获取系统统计信息:")
        print(f"   CPU使用率: {stats['cpu_percent']}%")
        print(f"   内存使用率: {stats['memory_percent']}%")
        print(f"   磁盘使用率: {stats['disk_percent']}%")
        print(f"   系统负载: {stats['load_1min']}")
        print(f"   内存总量: {stats['memory_total_gb']}GB")
        print(f"   内存已用: {stats['memory_used_gb']}GB")
        print(f"   磁盘总量: {stats['disk_total_gb']}GB")
        print(f"   磁盘已用: {stats['disk_used_gb']}GB")
        return True
    else:
        print("❌ 获取系统统计信息失败")
        return False

def test_system_health_check():
    """测试系统健康检查"""
    print("\n🔍 测试系统健康检查...")
    
    # 模拟chat_ids
    chat_ids = []
    
    # 测试健康检查函数
    last_check_time = 0
    new_check_time = check_system_health(chat_ids, last_check_time)
    
    print(f"✅ 系统健康检查完成")
    print(f"   上次检查时间: {last_check_time}")
    print(f"   新检查时间: {new_check_time}")
    
    return True

if __name__ == "__main__":
    print("🚀 开始测试系统监控功能...")
    
    # 测试获取系统统计信息
    stats_ok = test_system_stats()
    
    # 测试系统健康检查
    health_ok = test_system_health_check()
    
    if stats_ok and health_ok:
        print("\n🎉 所有测试通过！")
        sys.exit(0)
    else:
        print("\n❌ 测试失败")
        sys.exit(1)