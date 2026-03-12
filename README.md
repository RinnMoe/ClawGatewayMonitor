# OpenClaw Gateway 健康监控

自动监控 OpenClaw Gateway 服务状态，实现故障自愈和智能通知。

## ✨ 功能特性

- 🔍 **WebSocket 健康检查** - 真实连接检测，避免端口误判
- 🚑 **自动重启** - 离线超过 3 分钟自动执行 `gateway restart`
- 📊 **状态持久化** - 防止重复重启和状态丢失
- 📢 **飞书通知** - 服务恢复和故障自动推送消息
- 🔧 **异常分析** - 捕获 restart 输出并分析常见错误
- ⏱️ **断线统计** - 记录并报告中断时长
- 🎯 **systemd 友好** - 输出 flush 适配 journald

## 📦 安装

### 快速安装（推荐）

使用自动安装脚本：

```bash
git clone https://github.com/RinnMoe/ClawGatewayMonitor.git
cd ClawGatewayMonitor
chmod +x install.sh
./install.sh
```

安装脚本将自动：
- 检查 Python 3 环境
- 安装依赖包
- 创建配置文件并引导配置
- 可选安装为 systemd 服务

### 手动安装

1. 克隆仓库：

```bash
git clone https://github.com/RinnMoe/ClawGatewayMonitor.git
cd ClawGatewayMonitor
```

2. 安装依赖：

```bash
pip3 install -r requirements.txt
```

3. 创建配置文件：

```bash
cp config.example.json config.json
```

4. 编辑 `config.json` 配置参数：

```json
{
    "monitoring": {
        "gateway_host": "127.0.0.1",
        "gateway_port": 18789,
        "check_interval": 2,
        "auto_restart_threshold": 180
    },
    "notifications": {
        "enabled": true,
        "chat_ids": [
            "oc_your_chat_id_here"
        ]
    }
}
```

## 🚀 使用方法

### 前台运行（调试）

```bash
python3 monitor.py
```

### 后台运行

```bash
nohup python3 monitor.py > monitor.log 2>&1 &
```

### systemd 服务（推荐）

如果使用 `install.sh` 安装，服务已自动配置。手动操作：

```bash
# 启动服务
sudo systemctl start gateway-monitor

# 开机自启
sudo systemctl enable gateway-monitor

# 查看状态
sudo systemctl status gateway-monitor

# 查看日志
sudo journalctl -u gateway-monitor -f
```

## 📋 配置说明

所有配置在 `config.json` 中管理：

| 参数                         | 默认值       | 说明               |
| -------------------------- | --------- | ---------------- |
| `gateway_host`             | `127.0.0.1` | Gateway 地址       |
| `gateway_port`             | `18789`   | Gateway 端口       |
| `check_interval`           | `2`       | 健康检查间隔（秒）        |
| `auto_restart_threshold`   | `180`     | 自动重启阈值（秒，3分钟）   |
| `notifications.enabled`    | `true`    | 是否启用通知           |
| `notifications.chat_ids`   | `[]`      | 飞书群组 ID 列表       |

**状态文件**：自动保存在项目根目录 `gateway_monitor_state.json`

## 📊 运行示例

### 正常运行

```
[2026-03-12 10:30:00] 🔧 Gateway 健康监控启动
[2026-03-12 10:30:00] 📊 初始状态: 在线
[2026-03-12 10:30:00] 📋 通知目标: 1 个群组
```

### 检测到离线

```
[2026-03-12 10:35:00] ⚠️ 服务离线
[2026-03-12 10:38:00] 🚑 Gateway 离线超过阈值，尝试自动重启
[2026-03-12 10:38:01] restart stdout: restarting gateway...
[2026-03-12 10:38:01] 📢 已通知 oc_xxxxx
```

### 服务恢复

```
[2026-03-12 10:38:30] 🔄 检测到服务恢复
[2026-03-12 10:38:30] 📢 已通知 oc_xxxxx
```

## 🔔 通知消息格式

### 服务恢复通知

```
✅ Gateway 服务已恢复
⏱️ 中断时长：3 分 15 秒
🕐 恢复时间：2026-03-12 10:38:30
```

### 自动重启通知

```
🚑 Gateway 离线超过 3 分钟
已执行自动 restart 尝试
时间：2026-03-12 10:38:01
```

## 🛠️ 故障排查

### 查看配置

```bash
cat config.json
```

### 查看状态文件

```bash
cat gateway_monitor_state.json
```

### 手动重置状态

```bash
rm gateway_monitor_state.json
```

### 查看 systemd 日志

```bash
sudo journalctl -u gateway-monitor -n 100
```

### 测试通知功能

```bash
openclaw message send \
  --channel feishu \
  --target "chat:oc_your_chat_id" \
  --message "测试消息"
```

### 配置文件不存在

如果 `config.json` 不存在，程序会使用默认配置运行，但通知功能将被禁用。建议：

```bash
cp config.example.json config.json
# 然后编辑 config.json
```

## 📈 改进点

相比基础端口检测方案：

| 改进项            | 说明                            |
| -------------- | ----------------------------- |
| WebSocket检查    | 真实连接测试，不受端口占用误判               |
| 自动重启           | 离线 3 分钟自动执行 restart            |
| 输出捕获           | 记录 stdout/stderr             |
| 异常分析           | 识别 `Config invalid`、权限、端口占用等错误 |
| 防重复重启          | 状态机防止短时间内多次重启                 |
| 恢复通知           | 服务上线主动通知                      |
| 中断时长统计         | 精确记录故障持续时间                    |
| systemd 适配     | `flush=True` 确保日志实时输出         |
| 配置文件化          | JSON 配置文件，无需修改代码              |
| 快速安装脚本         | 自动化安装和配置流程                    |
| 跨平台兼容          | 自动检测用户和环境变量                   |

## 🔮 未来扩展（V3 版本）

- ⏱️ **退避策略** - 避免重启风暴
- 🏓 **WebSocket Ping** - 更精准的心跳检测
- 💥 **Sandbox 崩溃检测** - 监控子进程状态
- 📝 **结构化日志** - JSON 格式输出
- 🤖 **状态机模型** - 更清晰的状态转换逻辑

## 📝 许可证

MIT License

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📧 联系方式

如有问题或建议，请创建 Issue。
