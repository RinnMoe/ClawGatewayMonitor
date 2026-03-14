#!/bin/bash

# OpenClaw Gateway Monitor 快速安装脚本

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MONITOR_FILE="$SCRIPT_DIR/monitor.py"

echo "🚀 开始安装 OpenClaw Gateway Monitor..."
echo ""

# ----------------------------
# 1. 检查 Python 3
# ----------------------------

echo "📋 步骤 1/4: 检查 Python 3..."

if ! command -v python3 &> /dev/null; then
    echo "❌ 错误: 需要 Python 3"
    exit 1
fi

PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo "✅ Python 3 已安装 (版本: $PYTHON_VERSION)"
echo ""

# ----------------------------
# 2. 安装依赖（使用虚拟环境）
# ----------------------------

echo "📋 步骤 2/4: 安装依赖..."

VENV_DIR="$SCRIPT_DIR/venv"

if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
    # 检查是否已有虚拟环境
    if [ -d "$VENV_DIR" ]; then
        echo "✅ 检测到已有虚拟环境: $VENV_DIR"
        VENV_PYTHON="$VENV_DIR/bin/python3"
    else
        echo "🔧 创建项目虚拟环境..."
        python3 -m venv "$VENV_DIR"
        VENV_PYTHON="$VENV_DIR/bin/python3"
        echo "✅ 虚拟环境已创建: $VENV_DIR"
    fi
    
    # 使用虚拟环境的 pip 安装依赖
    echo "📦 安装依赖到虚拟环境..."
    if [ -f "$VENV_DIR/bin/pip" ]; then
        "$VENV_DIR/bin/pip" install -q -r "$SCRIPT_DIR/requirements.txt"
    else
        # 回退：尝试使用系统 pip（兼容性）
        pip3 install -q -r "$SCRIPT_DIR/requirements.txt"
    fi
    
    # 验证关键依赖
    echo "🔍 验证依赖安装..."
    if ! "$VENV_PYTHON" -c "import websocket" 2>/dev/null; then
        echo "❌ 依赖安装失败，关键模块 'websocket' 不可用"
        echo "   请手动运行: $VENV_DIR/bin/pip install -U websocket-client"
        exit 1
    fi
    
    echo "✅ 依赖安装完成"
    
    # 给出启动提示
    echo ""
    echo "💡 提示："
    echo "  激活虚拟环境: source $VENV_DIR/bin/activate"
    echo "  运行监控: python3 $MONITOR_FILE"
    echo "  或直接使用: $VENV_DIR/bin/python3 $MONITOR_FILE"
else
    echo "⚠️  requirements.txt 未找到，跳过依赖安装"
fi
echo ""

# ----------------------------
# 3. 配置文件创建
# ----------------------------

echo "📋 步骤 3/4: 配置文件创建..."

CONFIG_FILE="$SCRIPT_DIR/config.json"

if [ -f "$CONFIG_FILE" ]; then
    echo "⚠️  config.json 已存在，跳过配置文件创建"
    echo ""
else
    # 检测 18789 端口是否在监听
    PORT_CHECK=$(ss -tlnp 2>/dev/null | grep ':18789 ' || netstat -tlnp 2>/dev/null | grep ':18789 ' || echo "")
    
    if [ -n "$PORT_CHECK" ]; then
        echo "✅ 检测到本地 18789 端口正在监听"
        GATEWAY_HOST="127.0.0.1"
        GATEWAY_PORT="18789"
        echo "   自动配置: $GATEWAY_HOST:$GATEWAY_PORT"
    else
        echo "⚠️  未检测到 18789 端口"
        echo ""
        
        read -p "请输入 Gateway 主机地址 [默认: 127.0.0.1]: " input_host
        GATEWAY_HOST=${input_host:-127.0.0.1}
        
        read -p "请输入 Gateway 端口 [默认: 18789]: " input_port
        GATEWAY_PORT=${input_port:-18789}
        
        echo "✅ 手动配置: $GATEWAY_HOST:$GATEWAY_PORT"
    fi
    
    echo ""
    echo "📢 飞书通知配置："
    read -p "请输入飞书群组 ID (格式: oc_xxx, 留空跳过): " CHAT_ID
    
    # 通知超时重试配置
    RETRY_ENABLED="false"
    RETRY_COUNT=2
    RETRY_DELAY=5
    COMMAND_TIMEOUT=150
    
    # 健康检查防抖配置（默认开启）
    DEBOUNCE_ENABLED="true"
    HEALTH_RETRIES=2
    HEALTH_RETRY_DELAY=1
    
    if [ -n "$CHAT_ID" ]; then
        echo ""
        echo "🔄 通知超时重试配置："
        read -p "是否启用超时重试? (y/n) [默认: n]: " enable_retry
        enable_retry=${enable_retry:-n}
        
        if [[ "$enable_retry" =~ ^[Yy]$ ]]; then
            RETRY_ENABLED="true"
            
            read -p "重试次数 [默认: 2]: " input_retry_count
            RETRY_COUNT=${input_retry_count:-2}
            
            read -p "重试间隔(秒) [默认: 5]: " input_retry_delay
            RETRY_DELAY=${input_retry_delay:-5}
            
            read -p "单次超时时间(秒) [默认: 150]: " input_timeout
            COMMAND_TIMEOUT=${input_timeout:-150}
            
            echo "✅ 重试配置: 启用, ${RETRY_COUNT}次重试, 间隔${RETRY_DELAY}s, 超时${COMMAND_TIMEOUT}s"
        else
            echo "⏭️  超时重试已禁用"
        fi
        
        echo ""
        echo "🛡️  健康检查防抖配置："
        read -p "是否启用健康检查防抖 (多次握手避免抖动)? (y/n) [默认: y]: " enable_debounce
        enable_debounce=${enable_debounce:-y}
        
        if [[ "$enable_debounce" =~ ^[Yy]$ ]]; then
            DEBOUNCE_ENABLED="true"
            
            read -p "健康检查失败重试次数 [默认: 2]: " input_health_retries
            HEALTH_RETRIES=${input_health_retries:-2}
            
            read -p "重试间隔(秒) [默认: 1]: " input_health_delay
            HEALTH_RETRY_DELAY=${input_health_delay:-1}
            
            echo "✅ 防抖配置: 启用, ${HEALTH_RETRIES}次重试, 间隔${HEALTH_RETRY_DELAY}s"
        else
            DEBOUNCE_ENABLED="false"
            echo "⏭️  防抖已禁用"
        fi
    fi
    
    if [ -n "$CHAT_ID" ]; then
        echo ""
        echo "🔄 通知超时重试配置："
        read -p "是否启用超时重试? (y/n) [默认: n]: " enable_retry
        enable_retry=${enable_retry:-n}
        
        if [[ "$enable_retry" =~ ^[Yy]$ ]]; then
            RETRY_ENABLED="true"
            
            read -p "重试次数 [默认: 2]: " input_retry_count
            RETRY_COUNT=${input_retry_count:-2}
            
            read -p "重试间隔(秒) [默认: 5]: " input_retry_delay
            RETRY_DELAY=${input_retry_delay:-5}
            
            read -p "单次超时时间(秒) [默认: 150]: " input_timeout
            COMMAND_TIMEOUT=${input_timeout:-150}
            
            echo "✅ 重试配置: 启用, ${RETRY_COUNT}次重试, 间隔${RETRY_DELAY}s, 超时${COMMAND_TIMEOUT}s"
        else
            echo "⏭️  超时重试已禁用"
        fi
    fi
    
    # 创建配置文件
    if [ -z "$CHAT_ID" ]; then
        cat > "$CONFIG_FILE" << EOF
{
    "monitoring": {
        "gateway_host": "$GATEWAY_HOST",
        "gateway_port": $GATEWAY_PORT,
        "check_interval": 2,
        "auto_restart_threshold": 180,
        "health_retries": $HEALTH_RETRIES,
        "health_retry_delay": $HEALTH_RETRY_DELAY
    },
    "notifications": {
        "enabled": false,
        "chat_ids": [],
        "retry_on_timeout": false,
        "retry_count": 2,
        "retry_delay": 5,
        "command_timeout": 150
    }
}
EOF
        echo "✅ 配置文件已创建（通知已禁用）"
        echo "   防抖配置: 默认启用, ${HEALTH_RETRIES}次重试, 间隔${HEALTH_RETRY_DELAY}s"
    else
        cat > "$CONFIG_FILE" << EOF
{
    "monitoring": {
        "gateway_host": "$GATEWAY_HOST",
        "gateway_port": $GATEWAY_PORT,
        "check_interval": 2,
        "auto_restart_threshold": 180,
        "health_retries": $HEALTH_RETRIES,
        "health_retry_delay": $HEALTH_RETRY_DELAY
    },
    "notifications": {
        "enabled": true,
        "chat_ids": [
            "$CHAT_ID"
        ],
        "retry_on_timeout": $RETRY_ENABLED,
        "retry_count": $RETRY_COUNT,
        "retry_delay": $RETRY_DELAY,
        "command_timeout": $COMMAND_TIMEOUT
    }
}
EOF
        echo "✅ 配置文件已创建"
        echo "   通知群组: $CHAT_ID"
        echo "   超时重试: $RETRY_ENABLED (次数=$RETRY_COUNT, 间隔=${RETRY_DELAY}s, 超时=${COMMAND_TIMEOUT}s)"
        echo "   防抖配置: $DEBOUNCE_ENABLED (次数=$HEALTH_RETRIES, 间隔=${HEALTH_RETRY_DELAY}s)"
    fi
    
    echo ""
    echo "💡 提示: 可以手动编辑 $CONFIG_FILE 修改配置"
fi

echo ""
echo "✨ 配置完成！"
echo ""

# ----------------------------
# 4. systemd 服务配置（可选）
# ----------------------------

echo "📋 步骤 4/4: systemd 服务配置..."

# 确保 VENV_PYTHON 已定义（如果步骤2跳过了依赖安装）
if [ -z "$VENV_PYTHON" ]; then
    if [ -f "$VENV_DIR/bin/python3" ]; then
        VENV_PYTHON="$VENV_DIR/bin/python3"
    else
        VENV_PYTHON=$(which python3)
    fi
fi

# 检查是否为 systemd 系统
if ! command -v systemctl &> /dev/null; then
    echo "⚠️  当前系统不支持 systemd，跳过服务安装"
    echo ""
    echo "✨ 安装完成！"
    echo ""
    echo "📚 启动方式："
    echo "  前台运行: $VENV_PYTHON $MONITOR_FILE"
    echo "  后台运行: nohup $VENV_PYTHON $MONITOR_FILE > monitor.log 2>&1 &"
    echo ""
    echo "📖 详细使用说明请查看 README.md"
    exit 0
fi

# 检测是否已安装服务
EXISTING_SERVICE=""
if sudo systemctl list-unit-files --type=service 2>/dev/null | grep -q "gateway-monitor.service"; then
    EXISTING_SERVICE="gateway-monitor.service"
elif sudo systemctl list-unit-files --type=service 2>/dev/null | grep -q "gateway-health-monitor.service"; then
    EXISTING_SERVICE="gateway-health-monitor.service"
fi

if [ -n "$EXISTING_SERVICE" ]; then
    echo "⚠️  检测到已存在的服务: $EXISTING_SERVICE"
    echo ""
    read -p "是否要卸载并重新安装服务? (y/n) [默认: n]: " reinstall_service
    reinstall_service=${reinstall_service:-n}
    
    if [[ "$reinstall_service" =~ ^[Yy]$ ]]; then
        echo ""
        echo "🔧 正在卸载旧服务..."
        # 停止服务
        sudo systemctl stop "$EXISTING_SERVICE" 2>/dev/null || true
        sudo systemctl disable "$EXISTING_SERVICE" 2>/dev/null || true
        # 删除服务文件
        if [ -f "/etc/systemd/system/$EXISTING_SERVICE" ]; then
            sudo rm -f "/etc/systemd/system/$EXISTING_SERVICE"
        elif [ -f "/etc/systemd/system/gateway-monitor.service" ]; then
            sudo rm -f "/etc/systemd/system/gateway-monitor.service"
        fi
        sudo systemctl daemon-reload
        echo "✅ 旧服务已卸载"
        echo ""
        # 设置安装标志
        FORCE_INSTALL=true
    else
        echo "⏭️  跳过服务重新安装"
        echo ""
        echo "✨ 安装完成！"
        echo ""
        echo "📚 启动方式："
        echo "  前台运行: $VENV_PYTHON $MONITOR_FILE"
        echo "  后台运行: nohup $VENV_PYTHON $MONITOR_FILE > monitor.log 2>&1 &"
        echo ""
        echo "📖 详细使用说明请查看 README.md"
        exit 0
    fi
else
    FORCE_INSTALL=false
fi

echo "✅ 检测到 systemd 支持"
echo ""

read -p "是否安装为 systemd 服务? (y/n) [默认: n]: " install_service
install_service=${install_service:-n}

if [[ "$install_service" =~ ^[Yy]$ ]]; then
    
    CURRENT_USER=$(whoami)
    CURRENT_HOME=$HOME
    # 使用虚拟环境的 Python，如果不存在则回退到系统 Python
    if [ -f "$VENV_DIR/bin/python3" ]; then
        PYTHON_PATH="$VENV_DIR/bin/python3"
    else
        PYTHON_PATH=$(which python3)
    fi
    
    # 自动检测 PATH，确保包含 openclaw 等 CLI 工具的路径
    # 优先从当前环境获取 PATH，然后补充常见路径
    DETECTED_PATH="$PATH"
    # 常见的 CLI 工具安装路径
    EXTRA_PATHS=(
        "$HOME/.npm-global/bin"
        "$HOME/.local/bin"
        "$HOME/.yarn/bin"
        "/usr/local/bin"
        "/usr/bin"
        "/bin"
    )
    for p in "${EXTRA_PATHS[@]}"; do
        if [ -d "$p" ] && [[ ":$DETECTED_PATH:" != *":$p:"* ]]; then
            DETECTED_PATH="$p:$DETECTED_PATH"
        fi
    done
    echo "🔧 检测到 PATH: $DETECTED_PATH"
    echo ""
    
    # 创建 systemd service 文件
    SERVICE_CONTENT="[Unit]
Description=OpenClaw Gateway Health Monitor
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
Group=$CURRENT_USER
WorkingDirectory=$SCRIPT_DIR
ExecStart=$PYTHON_PATH $MONITOR_FILE
Restart=always
RestartSec=10

# Environment - PATH auto-detected from current shell
Environment=\"PATH=$DETECTED_PATH\"
Environment=\"OPENCLAW_GATEWAY=ws://127.0.0.1:18789\"
Environment=\"OPENCLAW_GATEWAY_PORT=18789\"
Environment=\"HOME=$CURRENT_HOME\"
Environment=\"USER=$CURRENT_USER\"

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=gateway-monitor

# Security
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target"
    
    # 写入临时文件
    TEMP_SERVICE="/tmp/gateway-monitor-$$.service"
    echo "$SERVICE_CONTENT" > "$TEMP_SERVICE"
    
    # 安装 service（使用统一的服务名 gateway-monitor.service）
    echo "🔧 正在安装 systemd 服务..."
    sudo cp "$TEMP_SERVICE" /etc/systemd/system/gateway-monitor.service
    sudo systemctl daemon-reload
    
    rm -f "$TEMP_SERVICE"
    
    echo "✅ systemd 服务已安装"
    echo ""
    
    # 询问是否开机启动
    read -p "是否设置开机自动启动? (y/n) [默认: n]: " enable_service
    enable_service=${enable_service:-n}
    
    if [[ "$enable_service" =~ ^[Yy]$ ]]; then
        sudo systemctl enable gateway-monitor
        echo "✅ 已设置开机自动启动"
    else
        echo "⏭️  跳过开机自动启动"
    fi
    
    echo ""
    
    # 询问是否立即启动
    read -p "是否立即启动服务? (y/n) [默认: y]: " start_service
    start_service=${start_service:-y}
    
    if [[ "$start_service" =~ ^[Yy]$ ]]; then
        sudo systemctl start gateway-monitor
        echo "✅ 服务已启动"
        echo ""
        echo "查看服务状态:"
        sudo systemctl status gateway-monitor --no-pager -l
    else
        echo "⏭️  跳过启动服务"
        echo ""
        echo "手动启动: sudo systemctl start gateway-monitor"
    fi
    
    echo ""
    echo "📚 常用命令:"
    echo "  查看状态: sudo systemctl status gateway-monitor"
    echo "  查看日志: sudo journalctl -u gateway-monitor -f"
    echo "  重启服务: sudo systemctl restart gateway-monitor"
    echo "  停止服务: sudo systemctl stop gateway-monitor"
    
else
    echo "⏭️  跳过 systemd 服务安装"
    echo ""
    echo "📚 启动方式:"
    echo "  前台运行: python3 $MONITOR_FILE"
    echo "  后台运行: nohup python3 $MONITOR_FILE > monitor.log 2>&1 &"
fi

echo ""
echo "✨ 安装完成！"
echo ""
echo "📖 详细使用说明请查看 README.md"
