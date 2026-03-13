#!/usr/bin/env python3
"""
OpenClaw Gateway Health Monitor

功能：
1. WebSocket 检测 Gateway 健康状态
2. 离线 → 在线 时发送恢复通知
3. 离线超过阈值自动 restart
4. 捕获 restart 输出并分析异常
5. 状态持久化避免重复 restart
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
            "health_retry_delay": 1     # 重试间隔(秒)
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
