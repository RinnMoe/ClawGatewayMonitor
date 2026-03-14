#!/usr/bin/env python3
"""
OpenClaw Gateway Health Monitor

功能：
1. WebSocket 检测 Gateway 健康状态
2. 离线 → 在线 时发送恢复通知
3. 离线超过阈值自动 restart
4. 捕获 restart 输出并分析异常
5. 状态持久化避免重复 restart
6. 系统资源监控 (CPU、内存、磁盘、负载)
7. 高负载时发送告警通知
"""

import os
import sys
import json
import time
import atexit
import getpass
import subprocess
from datetime import datetime

import websocket
import psutil


# ----------------------------
# 环境变量
# ----------------------------

os.environ.setdefault("OPENCLAW_GATEWAY", "ws://127.0.0.1:18789")
os.environ.setdefault("OPENCLAW_GATEWAY_PORT", "18789")
os.environ.setdefault("HOME", os.path.expanduser("~"))
os.environ.setdefault("USER", getpass.getuser())


# ----------------------------
# 配置加载
# ----------------------------

def load_config():
    """加载配置文件"""
    config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    
    # 默认配置
    default_config = {
        "monitoring": {
            "gateway_host": "127.0.0.1",
            "gateway_port": 18789,
            "check_interval": 2,
            "auto_restart_threshold": 180,
            "health_retries": 2,        # 断线检测重试次数
            "health_retry_delay": 1,    # 重试间隔(秒)
            "system_monitoring": {
                "enabled": True,
                "check_interval": 60,   # 系统监控检查间隔(秒)
                "cpu_threshold": 80,    # CPU使用率阈值(%)
                "memory_threshold": 80, # 内存使用率阈值(%)
                "disk_threshold": 90,   # 磁盘使用率阈值(%)
                "load_threshold": 5.0   # 系统负载阈值
            }
        },
        "notifications": {
            "enabled": True,
            "chat_ids": [],
            "retry_on_timeout": False,
            "retry_count": 2,
            "retry_delay": 5,
            "command_timeout": 60
        }
    }
    
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            config = json.load(f)
            # 合并默认配置
            for key in default_config:
                if key not in config:
                    config[key] = default_config[key]
            return config
    except FileNotFoundError:
        log(f"⚠️ 配置文件未找到: {config_file}")
        log("📝 使用默认配置，请创建 config.json 并设置通知群组 ID")
        return default_config
    except json.JSONDecodeError as e:
        log(f"❌ 配置文件格式错误: {e}")
        log("📝 使用默认配置")
        return default_config


# 加载配置
CONFIG = load_config()

GATEWAY_HOST = CONFIG["monitoring"]["gateway_host"]
GATEWAY_PORT = CONFIG["monitoring"]["gateway_port"]
CHECK_INTERVAL = CONFIG["monitoring"]["check_interval"]
AUTO_RESTART_THRESHOLD = CONFIG["monitoring"]["auto_restart_threshold"]
HEALTH_RETRIES = CONFIG["monitoring"].get("health_retries", 2)
HEALTH_RETRY_DELAY = CONFIG["monitoring"].get("health_retry_delay", 1)

# 系统监控配置
SYSTEM_MONITORING_ENABLED = CONFIG["monitoring"].get("system_monitoring", {}).get("enabled", True)
SYSTEM_CHECK_INTERVAL = CONFIG["monitoring"].get("system_monitoring", {}).get("check_interval", 60)
CPU_THRESHOLD = CONFIG["monitoring"].get("system_monitoring", {}).get("cpu_threshold", 80)
MEMORY_THRESHOLD = CONFIG["monitoring"].get("system_monitoring", {}).get("memory_threshold", 80)
DISK_THRESHOLD = CONFIG["monitoring"].get("system_monitoring", {}).get("disk_threshold", 90)
LOAD_THRESHOLD = CONFIG["monitoring"].get("system_monitoring", {}).get("load_threshold", 5.0)

# 通知重试配置
NOTIFY_RETRY_ON_TIMEOUT = CONFIG["notifications"].get("retry_on_timeout", False)
NOTIFY_RETRY_COUNT = CONFIG["notifications"].get("retry_count", 2)
NOTIFY_RETRY_DELAY = CONFIG["notifications"].get("retry_delay", 5)
NOTIFY_COMMAND_TIMEOUT = CONFIG["notifications"].get("command_timeout", 60)

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gateway_monitor_state.json")


# ----------------------------
# 日志
# ----------------------------

def log(msg: str):
    print(
        f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}",
        flush=True
    )


# ----------------------------
# 状态管理
# ----------------------------

def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)

    except Exception:
        return {
            "last_port_status": False,
            "last_notify_time": 0,
            "offline_since": None,
            "restart_attempted": False
        }


def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)

    except Exception as e:
        log(f"⚠️ 保存状态失败: {e}")


# ----------------------------
# Gateway 检测
# ----------------------------

def is_gateway_online(host, port, timeout=2, retries=2, retry_delay=1):
    """WebSocket health check with debouncing (retry to avoid flapping)."""

    url = f"ws://{host}:{port}"

    for attempt in range(1, retries + 2):  # total attempts = retries + 1
        try:
            ws = websocket.create_connection(url, timeout=timeout)
            ws.close()
            if attempt > 1:
                log(f"✅ Gateway 恢复在线 (retry {attempt} 成功)")
            return True
        except Exception as e:
            if attempt <= retries + 1:
                time.sleep(retry_delay)
                continue
            return False


# ----------------------------
# 通知
# ----------------------------

def read_chat_ids():
    """通知群组"""
    if CONFIG["notifications"]["enabled"]:
        return CONFIG["notifications"]["chat_ids"]
    return []


def format_duration(seconds):

    if seconds < 60:
        return f"{int(seconds)} 秒"

    minutes = int(seconds // 60)
    secs = int(seconds % 60)

    return f"{minutes} 分 {secs} 秒"


def send_message(chat_ids, message):

    for chat_id in chat_ids:

        max_attempts = (NOTIFY_RETRY_COUNT + 1) if NOTIFY_RETRY_ON_TIMEOUT else 1
        last_error = None

        for attempt in range(1, max_attempts + 1):
            try:
                if attempt > 1:
                    log(f"🔄 重试通知 {chat_id} (第 {attempt}/{max_attempts} 次)")

                subprocess.run(
                    [
                        "openclaw",
                        "message",
                        "send",
                        "--channel",
                        "feishu",
                        "--target",
                        f"chat:{chat_id}",
                        "--message",
                        message
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=NOTIFY_COMMAND_TIMEOUT,
                    check=True
                )

                log(f"📢 已通知 {chat_id}")
                last_error = None
                break  # 成功，跳出重试循环

            except subprocess.TimeoutExpired as e:
                last_error = e
                log(f"⏰ 通知超时 {chat_id} (attempt {attempt}/{max_attempts}, timeout={NOTIFY_COMMAND_TIMEOUT}s)")
                if attempt < max_attempts:
                    time.sleep(NOTIFY_RETRY_DELAY)

            except Exception as e:
                last_error = e
                log(f"❌ 通知失败 {chat_id}: {e}")
                break  # 非超时错误不重试

        if last_error is not None:
            log(f"❌ 通知最终失败 {chat_id}: {last_error}")


# ----------------------------
# restart 输出分析接口
# ----------------------------

def analyze_restart_error(stdout, stderr):

    text = (stdout + "\n" + stderr).lower()

    if "config invalid" in text:
        log("⚠️ 检测到配置错误 (Config invalid)")
        # 未来扩展接口

    if "permission denied" in text:
        log("⚠️ 检测到权限问题")

    if "port already in use" in text:
        log("⚠️ 端口占用")


# ----------------------------
# Gateway restart
# ----------------------------

def restart_gateway(chat_ids):

    log("🚑 Gateway 离线超过阈值，尝试自动重启")

    try:

        result = subprocess.run(
            ["openclaw", "gateway", "restart"],
            capture_output=True,
            text=True,
            timeout=60
        )

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        if stdout:
            log(f"restart stdout: {stdout}")

        if stderr:
            log(f"restart stderr: {stderr}")

        analyze_restart_error(stdout, stderr)

        msg = (
            "🚑 Gateway 离线超过 3 分钟\n"
            "已执行自动 restart 尝试\n"
            f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        send_message(chat_ids, msg)

    except subprocess.TimeoutExpired:

        log("❌ restart 超时")

    except Exception as e:

        log(f"❌ restart 异常: {e}")


# ----------------------------
# 系统资源监控
# ----------------------------

def get_system_stats():
    """获取系统资源使用情况"""
    try:
        # CPU使用率
        cpu_percent = psutil.cpu_percent(interval=1)
        
        # 内存使用率
        memory = psutil.virtual_memory()
        memory_percent = memory.percent
        
        # 磁盘使用率
        disk = psutil.disk_usage('/')
        disk_percent = disk.percent
        
        # 系统负载 (仅Unix系统)
        try:
            load_avg = psutil.getloadavg()
            load_1min = load_avg[0] if load_avg else 0
        except (AttributeError, OSError):
            # Windows系统不支持getloadavg
            load_1min = 0
        
        return {
            "cpu_percent": cpu_percent,
            "memory_percent": memory_percent,
            "disk_percent": disk_percent,
            "load_1min": load_1min,
            "memory_total_gb": round(memory.total / (1024**3), 2),
            "memory_used_gb": round(memory.used / (1024**3), 2),
            "disk_total_gb": round(disk.total / (1024**3), 2),
            "disk_used_gb": round(disk.used / (1024**3), 2)
        }
    except Exception as e:
        log(f"❌ 获取系统信息失败: {e}")
        return None


def check_system_health(chat_ids, last_check_time):
    """检查系统健康状态并发送通知"""
    current_time = time.time()
    
    # 检查是否到了系统监控检查时间
    if current_time - last_check_time < SYSTEM_CHECK_INTERVAL:
        return last_check_time
    
    if not SYSTEM_MONITORING_ENABLED:
        return current_time
    
    stats = get_system_stats()
    if not stats:
        return current_time
    
    # 检查各项指标
    alerts = []
    
    if stats["cpu_percent"] > CPU_THRESHOLD:
        alerts.append(f"CPU使用率: {stats['cpu_percent']}% (阈值: {CPU_THRESHOLD}%)")
    
    if stats["memory_percent"] > MEMORY_THRESHOLD:
        alerts.append(f"内存使用率: {stats['memory_percent']}% (阈值: {MEMORY_THRESHOLD}%)")
    
    if stats["disk_percent"] > DISK_THRESHOLD:
        alerts.append(f"磁盘使用率: {stats['disk_percent']}% (阈值: {DISK_THRESHOLD}%)")
    
    if stats["load_1min"] > LOAD_THRESHOLD:
        alerts.append(f"系统负载: {stats['load_1min']} (阈值: {LOAD_THRESHOLD})")
    
    # 如果有告警，发送通知
    if alerts:
        alert_msg = "⚠️ 系统资源告警\n"
        alert_msg += "\n".join(alerts)
        alert_msg += f"\n\n📊 系统状态:\n"
        alert_msg += f"CPU: {stats['cpu_percent']}%\n"
        alert_msg += f"内存: {stats['memory_percent']}% ({stats['memory_used_gb']}GB/{stats['memory_total_gb']}GB)\n"
        alert_msg += f"磁盘: {stats['disk_percent']}% ({stats['disk_used_gb']}GB/{stats['disk_total_gb']}GB)\n"
        alert_msg += f"负载: {stats['load_1min']}\n"
        alert_msg += f"🕐 检查时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        log(f"⚠️ 系统资源告警: {', '.join(alerts)}")
        send_message(chat_ids, alert_msg)
    
    # 记录系统状态（即使没有告警）
    log(f"📊 系统状态: CPU {stats['cpu_percent']}%, 内存 {stats['memory_percent']}%, 磁盘 {stats['disk_percent']}%, 负载 {stats['load_1min']}")
    
    return current_time


# ----------------------------
# 主程序
# ----------------------------

def main():

    log("🔧 Gateway 健康监控启动")

    atexit.register(lambda: log("🛑 监控退出"))

    state = load_state()

    last_status = state["last_port_status"]

    log(f"📊 初始状态: {'在线' if last_status else '离线'}")

    chat_ids = read_chat_ids()

    log(f"📋 通知目标: {len(chat_ids)} 个群组")
    
    # 系统监控相关变量
    last_system_check_time = 0
    if SYSTEM_MONITORING_ENABLED:
        log(f"📊 系统监控已启用 (CPU阈值: {CPU_THRESHOLD}%, 内存阈值: {MEMORY_THRESHOLD}%, 磁盘阈值: {DISK_THRESHOLD}%, 负载阈值: {LOAD_THRESHOLD})")

    try:

        while True:

            current_status = is_gateway_online(
                GATEWAY_HOST,
                GATEWAY_PORT,
                timeout=2,
                retries=HEALTH_RETRIES,
                retry_delay=HEALTH_RETRY_DELAY
            )

            # ----------------------------
            # 恢复检测
            # ----------------------------

            if not last_status and current_status:

                log("🔄 检测到服务恢复")

                downtime = None

                if state.get("offline_since"):

                    downtime = time.time() - state["offline_since"]

                if downtime:

                    duration = format_duration(downtime)

                    msg = (
                        "✅ Gateway 服务已恢复\n"
                        f"⏱️ 中断时长：{duration}\n"
                        f"🕐 恢复时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    )

                else:

                    msg = (
                        "✅ Gateway 服务已恢复\n"
                        f"🕐 当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    )

                send_message(chat_ids, msg)

                state["offline_since"] = None
                state["restart_attempted"] = False
                state["last_port_status"] = True

                save_state(state)

                last_status = True

            # ----------------------------
            # 离线检测
            # ----------------------------

            elif not current_status:

                if last_status:

                    log("⚠️ 服务离线")

                    state["offline_since"] = time.time()
                    state["restart_attempted"] = False

                    save_state(state)

                if state.get("offline_since") and not state.get("restart_attempted"):

                    downtime = time.time() - state["offline_since"]

                    if downtime >= AUTO_RESTART_THRESHOLD:

                        restart_gateway(chat_ids)

                        state["restart_attempted"] = True
                        save_state(state)

                last_status = False
            
            # ----------------------------
            # 系统资源监控
            # ----------------------------
            last_system_check_time = check_system_health(chat_ids, last_system_check_time)

            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:

        log("👋 收到退出信号")
        sys.exit(0)

    except Exception as e:

        log(f"❌ 监控异常: {e}")
        sys.exit(1)


# ----------------------------
# 启动
# ----------------------------

if __name__ == "__main__":
    main()
