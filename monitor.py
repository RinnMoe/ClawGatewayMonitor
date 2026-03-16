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

环境变量（可选，优先于 openclaw.json）：
  FEISHU_APP_ID       飞书应用 App ID
  FEISHU_APP_SECRET   飞书应用 App Secret

  不设置环境变量时，自动从 ~/.openclaw/openclaw.json 的 channels.feishu 读取。
"""

import os
import sys
import json
import time
import atexit
import getpass
import subprocess
from datetime import datetime

import requests
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
            "health_retries": 2,
            "health_retry_delay": 1,
            "system_monitoring": {
                "enabled": True,
                "check_interval": 60,
                "cpu_threshold": 80,
                "memory_threshold": 80,
                "disk_threshold": 90,
                "load_threshold": 5.0,
            },
        },
        "notifications": {
            "enabled": True,
            "chat_ids": [],
            "retry_on_timeout": False,
            "retry_count": 2,
            "retry_delay": 5,
        },
    }

    try:
        with open(config_file, "r", encoding="utf-8") as f:
            config = json.load(f)
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
SYS_MON = CONFIG["monitoring"].get("system_monitoring", {})
SYSTEM_MONITORING_ENABLED = SYS_MON.get("enabled", True)
SYSTEM_CHECK_INTERVAL = SYS_MON.get("check_interval", 60)
CPU_THRESHOLD = SYS_MON.get("cpu_threshold", 80)
MEMORY_THRESHOLD = SYS_MON.get("memory_threshold", 80)
DISK_THRESHOLD = SYS_MON.get("disk_threshold", 90)
LOAD_THRESHOLD = SYS_MON.get("load_threshold", 5.0)

# 通知重试配置
NOTIFY_RETRY_ON_TIMEOUT = CONFIG["notifications"].get("retry_on_timeout", False)
NOTIFY_RETRY_COUNT = CONFIG["notifications"].get("retry_count", 2)
NOTIFY_RETRY_DELAY = CONFIG["notifications"].get("retry_delay", 5)

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gateway_monitor_state.json")


# ----------------------------
# 日志
# ----------------------------

def log(msg: str):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


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
            "restart_attempted": False,
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
    for attempt in range(1, retries + 2):
        try:
            ws = websocket.create_connection(url, timeout=timeout)
            ws.close()
            if attempt > 1:
                log(f"✅ Gateway 恢复在线 (retry {attempt} 成功)")
            return True
        except Exception:
            if attempt <= retries + 1:
                time.sleep(retry_delay)
                continue
            return False


# ----------------------------
# 通知（直接调用飞书 API）
# ----------------------------

_cached_token = None
_token_expires_at = 0
_openclaw_config_path = os.path.expanduser("~/.openclaw/openclaw.json")


def _load_feishu_credentials():
    """从环境变量或 openclaw.json 加载飞书 App 凭据。"""
    app_id = os.environ.get("FEISHU_APP_ID", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET", "")
    if app_id and app_secret:
        return app_id, app_secret

    try:
        with open(_openclaw_config_path, "r", encoding="utf-8") as f:
            oc_cfg = json.load(f)
        feishu = oc_cfg.get("channels", {}).get("feishu", {})
        app_id = feishu.get("appId", "")
        app_secret = feishu.get("appSecret", "")
        if app_id and app_secret:
            return app_id, app_secret
    except Exception as e:
        log(f"⚠️ 读取 openclaw.json 失败: {e}")

    return None, None


def get_tenant_access_token():
    """获取飞书 tenant_access_token，带内存缓存（有效期 2h，提前 5 分钟刷新）。"""
    global _cached_token, _token_expires_at

    now = time.time()
    if _cached_token and now < _token_expires_at - 300:
        return _cached_token

    app_id, app_secret = _load_feishu_credentials()
    if not app_id or not app_secret:
        log("❌ 未找到飞书凭据（环境变量 FEISHU_APP_ID/SECRET 或 openclaw.json channels.feishu）")
        return None

    try:
        r = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            log(f"❌ 获取 token 失败: {data.get('msg', data)}")
            return None
        _cached_token = data["tenant_access_token"]
        _token_expires_at = now + data.get("expire", 7200)
        return _cached_token
    except Exception as e:
        log(f"❌ 获取 token 异常: {e}")
        return None


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


def _build_card(title, color, fields):
    """构建飞书交互式卡片。

    fields: list of (label, value) tuples rendered as markdown rows.
    """
    elements = []
    for label, value in fields:
        elements.append({"tag": "markdown", "content": f"**{label}**：{value}"})

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": color,
        },
        "elements": elements,
    }


def send_feishu_card(chat_id, card):
    """发送飞书交互式卡片到指定群聊。"""
    token = get_tenant_access_token()
    if not token:
        return False

    try:
        r = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            json={
                "receive_id": chat_id,
                "msg_type": "interactive",
                "content": json.dumps(card),
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        r.raise_for_status()
        body = r.json()
        if body.get("code") == 0:
            return True
        log(f"❌ 飞书 API 返回错误 [{chat_id}]: {body.get('msg', body)}")
        return False
    except Exception as e:
        log(f"❌ 发送卡片异常 [{chat_id}]: {e}")
        return False


def send_message(chat_ids, message, card=None):
    """发送通知（优先发卡片，无 card 时回退纯文本）。

    Args:
        chat_ids: 目标群聊 ID 列表
        message: 纯文本回退内容（日志/兼容用）
        card:    可选，飞书交互式卡片 dict
    """
    for chat_id in chat_ids:
        max_attempts = (NOTIFY_RETRY_COUNT + 1) if NOTIFY_RETRY_ON_TIMEOUT else 1
        last_error = None

        for attempt in range(1, max_attempts + 1):
            try:
                if attempt > 1:
                    log(f"🔄 重试通知 {chat_id} (第 {attempt}/{max_attempts} 次)")

                if card:
                    ok = send_feishu_card(chat_id, card)
                    if not ok:
                        raise RuntimeError("send_feishu_card returned False")
                else:
                    ok = _send_plain_text(chat_id, message)
                    if not ok:
                        raise RuntimeError("_send_plain_text returned False")

                log(f"📢 已通知 {chat_id}")
                last_error = None
                break

            except Exception as e:
                last_error = e
                log(f"⏰ 通知失败 {chat_id} (attempt {attempt}/{max_attempts}): {e}")
                if attempt < max_attempts:
                    time.sleep(NOTIFY_RETRY_DELAY)

        if last_error is not None:
            log(f"❌ 通知最终失败 {chat_id}: {last_error}")


def _send_plain_text(chat_id, text):
    """发送纯文本消息（降级方案）。"""
    token = get_tenant_access_token()
    if not token:
        return False
    try:
        r = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            json={
                "receive_id": chat_id,
                "msg_type": "text",
                "content": json.dumps({"text": text}),
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        r.raise_for_status()
        return r.json().get("code") == 0
    except Exception as e:
        log(f"❌ 发送文本异常 [{chat_id}]: {e}")
        return False


# ----------------------------
# restart 输出分析接口
# ----------------------------

def analyze_restart_error(stdout, stderr):
    text = (stdout + "\n" + stderr).lower()

    if "config invalid" in text:
        log("⚠️ 检测到配置错误 (Config invalid)")

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
            timeout=60,
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if stdout:
            log(f"restart stdout: {stdout}")
        if stderr:
            log(f"restart stderr: {stderr}")
        analyze_restart_error(stdout, stderr)

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        card = _build_card(
            title="🚑 Gateway 自动重启",
            color="red",
            fields=[
                ("状态", "离线超过 3 分钟，已执行自动 restart"),
                ("时间", now_str),
                ("退出码", str(result.returncode)),
            ],
        )
        send_message(chat_ids, "", card=card)

    except subprocess.TimeoutExpired:
        log("❌ restart 超时")
        card = _build_card(
            title="⚠️ Gateway 重启超时",
            color="red",
            fields=[
                ("状态", "restart 命令执行超时 (>60s)，请手动检查"),
                ("时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            ],
        )
        send_message(chat_ids, "", card=card)
    except Exception as e:
        log(f"❌ restart 异常: {e}")


# ----------------------------
# 系统资源监控
# ----------------------------

def get_system_stats():
    """获取系统资源使用情况"""
    try:
        cpu_percent = psutil.cpu_percent(interval=1)

        memory = psutil.virtual_memory()
        memory_percent = memory.percent

        disk = psutil.disk_usage("/")
        disk_percent = disk.percent

        try:
            load_avg = psutil.getloadavg()
            load_1min = load_avg[0] if load_avg else 0
        except (AttributeError, OSError):
            load_1min = 0

        return {
            "cpu_percent": cpu_percent,
            "memory_percent": memory_percent,
            "disk_percent": disk_percent,
            "load_1min": load_1min,
            "memory_total_gb": round(memory.total / (1024**3), 2),
            "memory_used_gb": round(memory.used / (1024**3), 2),
            "disk_total_gb": round(disk.total / (1024**3), 2),
            "disk_used_gb": round(disk.used / (1024**3), 2),
        }
    except Exception as e:
        log(f"❌ 获取系统信息失败: {e}")
        return None


def check_system_health(chat_ids, last_check_time):
    """检查系统健康状态并发送告警卡片。"""
    current_time = time.time()

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
        alerts.append(f"CPU 使用率 {stats['cpu_percent']}% (阈值 {CPU_THRESHOLD}%)")

    if stats["memory_percent"] > MEMORY_THRESHOLD:
        alerts.append(f"内存使用率 {stats['memory_percent']}% (阈值 {MEMORY_THRESHOLD}%)")

    if stats["disk_percent"] > DISK_THRESHOLD:
        alerts.append(f"磁盘使用率 {stats['disk_percent']}% (阈值 {DISK_THRESHOLD}%)")

    if stats["load_1min"] > LOAD_THRESHOLD:
        alerts.append(f"系统负载 {stats['load_1min']} (阈值 {LOAD_THRESHOLD})")

    if alerts:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        card = _build_card(
            title="⚠️ 系统资源告警",
            color="orange",
            fields=[
                ("告警项", "、".join(alerts)),
                ("CPU", f"{stats['cpu_percent']}%"),
                ("内存", f"{stats['memory_percent']}% ({stats['memory_used_gb']}GB / {stats['memory_total_gb']}GB)"),
                ("磁盘", f"{stats['disk_percent']}% ({stats['disk_used_gb']}GB / {stats['disk_total_gb']}GB)"),
                ("负载", f"{stats['load_1min']}"),
                ("时间", now_str),
            ],
        )
        log(f"⚠️ 系统资源告警: {', '.join(alerts)}")
        send_message(chat_ids, "", card=card)

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

    last_system_check_time = 0
    if SYSTEM_MONITORING_ENABLED:
        log(f"📊 系统监控已启用 (CPU: {CPU_THRESHOLD}%, 内存: {MEMORY_THRESHOLD}%, 磁盘: {DISK_THRESHOLD}%, 负载: {LOAD_THRESHOLD})")

    try:
        while True:
            current_status = is_gateway_online(
                GATEWAY_HOST,
                GATEWAY_PORT,
                timeout=2,
                retries=HEALTH_RETRIES,
                retry_delay=HEALTH_RETRY_DELAY,
            )

            # ----------------------------
            # 恢复检测
            # ----------------------------
            if not last_status and current_status:
                log("🔄 检测到服务恢复")

                downtime = None
                if state.get("offline_since"):
                    downtime = time.time() - state["offline_since"]

                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if downtime:
                    duration = format_duration(downtime)
                    card = _build_card(
                        title="✅ Gateway 服务已恢复",
                        color="green",
                        fields=[
                            ("中断时长", duration),
                            ("恢复时间", now_str),
                        ],
                    )
                else:
                    card = _build_card(
                        title="✅ Gateway 服务已恢复",
                        color="green",
                        fields=[
                            ("恢复时间", now_str),
                        ],
                    )

                send_message(chat_ids, "", card=card)

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

                    # 立即推送离线通知
                    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    card = _build_card(
                        title="⚠️ Gateway 已离线",
                        color="orange",
                        fields=[
                            ("状态", "Gateway WebSocket 连接中断"),
                            ("时间", now_str),
                            ("自动重启", f"离线超过 {format_duration(AUTO_RESTART_THRESHOLD)} 后自动尝试重启"),
                        ],
                    )
                    send_message(chat_ids, "", card=card)

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
