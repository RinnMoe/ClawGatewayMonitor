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
    
    # 创建配置文件
    if [ -z "$CHAT_ID" ]; then
        cat > "$CONFIG_FILE" << EOF
{
    "monitoring": {
        "gateway_host": "$GATEWAY_HOST",
        "gateway_port": $GATEWAY_PORT,
        "check_interval": 2,
        "auto_restart_threshold": 180
    },
    "notifications": {
        "enabled": false,
        "chat_ids": []
    }
}
EOF
        echo "✅ 配置文件已创建（通知已禁用）"
    else
        cat > "$CONFIG_FILE" << EOF
{
    "monitoring": {
        "gateway_host": "$GATEWAY_HOST",
        "gateway_port": $GATEWAY_PORT,
        "check_interval": 2,
        "auto_restart_threshold": 180
    },
    "notifications": {
        "enabled": true,
        "chat_ids": [
            "$CHAT_ID"
        ]
    }
}
EOF
        echo "✅ 配置文件已创建"
        echo "   通知群组: $CHAT_ID"
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

# 检查是否为 systemd 系统
if ! command -v systemctl &> /dev/null; then
    echo "⚠️  当前系统不支持 systemd，跳过服务安装"
    echo ""
    echo "✨ 安装完成！"
    echo ""
    echo "📚 启动方式："
    echo "  前台运行: python3 $MONITOR_FILE"
    echo "  后台运行: nohup python3 $MONITOR_FILE > monitor.log 2>&1 &"
    echo ""
    echo "📖 详细使用说明请查看 README.md"
    exit 0
fi

echo "✅ 检测到 systemd 支持"
echo ""

read -p "是否安装为 systemd 服务? (y/n) [默认: n]: " install_service
install_service=${install_service:-n}

if [[ "$install_service" =~ ^[Yy]$ ]]; then
    
    CURRENT_USER=$(whoami)
    CURRENT_HOME=$HOME
    PYTHON_PATH=$(which python3)
    
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

# Environment
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
    
    # 安装 service
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
