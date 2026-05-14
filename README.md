# opencode-lansenger-remote

通过蓝信个人机器人远程控制 OpenCode AI 编码助手。

## 功能

- 🌠 蓝信个人机器人 WebSocket 长连接接收消息
- 💬 向 OpenCode 发送编码指令，获取响应
- 📡 渐进推送：每完成一个文本片段立即推送到蓝信，不等全部生成完
- 📄 超长内容自动生成 .md 文件发送附件，短内容直接发文本/Markdown
- ✂️ Markdown 智能拆分：4000 字目标拆分，6000 字硬限制，不截断代码块/粗体/行内代码等标记
- 🔌 自动探测 OpenCode 桌面版 HTTP Server，优先 HTTP 模式
- 📂 项目管理：列出项目、编号快速切换目录
- 💾 项目目录持久化：会话过期或重启后自动恢复上次工作目录
- 📝 审批工作流：文件编辑、Shell 命令需用户确认
- 🔐 单用户授权：首个 `/start` 用户自动成为 owner（持久化）
- ⏱ 会话管理：30 分钟空闲自动过期
- 🔒 文件权限保护：凭证文件 chmod 600

## 安装

```bash
pip3 install -e .
```

前置条件：
- OpenCode 已安装（桌面版或 CLI）
- 蓝信个人机器人凭证（AppID + AppSecret）

## 配置

创建 `~/.opencode-lansenger-remote/.env`：

```ini
LANSENGER_APP_ID=your_app_id
LANSENGER_APP_SECRET=your_app_secret
LANSENGER_API_GATEWAY_URL=https://apigw.lx.qianxin.com
```

获取蓝信机器人凭证：**蓝信桌面客户端 → 通讯录 → 智能机器人 → 个人机器人 → ℹ️ 图标**（手机端不支持查看凭证）

可选配置：

```ini
OPENCODE_SERVER_URL=http://localhost:4096   # 默认值，程序会自动探测桌面版
OPENCODE_SERVER_PASSWORD=                   # 桌面版 HTTP Auth 密码（程序自动从环境变量获取）
SESSION_IDLE_TIMEOUT_MS=1800000             # 30分钟空闲过期
APPROVAL_TIMEOUT_MS=300000                  # 5分钟审批超时
```

## 启动

只需一条命令：

```bash
opencode-lansenger
```

程序启动时会自动：
1. 加载上次记住的项目目录（`~/.opencode-lansenger-remote/workdir.json`）
2. 探测 OpenCode 桌面版 HTTP server（自动发现端口和认证）
3. 若桌面版未运行，回退到 CLI 模式

也支持 `python -m` 方式启动：

```bash
python -m opencode_lansenger_remote
```

## 命令

在蓝信对话中发送：

| 命令 | 说明 |
|------|------|
| `/start` | 认领 owner + 启动 |
| `/help` | 显示帮助 |
| `/status` | 检查连接状态 |
| `/projects` | 列出 OpenCode 项目（带编号） |
| `/project` | 查看当前项目信息 |
| `/pwd` | 查看当前工作目录 |
| `/cd <路径或编号>` | 切换项目目录（如 `/cd 1` 或 `/cd ~/my-project`） |
| `/reset` | 重置会话（不丢失项目目录） |
| `/approve` | 批准待审批变更 |
| `/reject` | 拒绝待审批变更 |
| `/diff` | 查看变更详情 |
| `/files` | 列出变更文件 |
| `/retry` | 重试 OpenCode 连接 |
| 其他文本 | 作为 prompt 发送给 OpenCode |

**项目切换流程**：先发 `/projects` 查看编号列表，再发 `/cd 1` 快速切换。切换后目录自动保存，下次回来不用重新 `/cd`。

## 消息发送策略

| 内容长度 | 发送方式 |
|----------|----------|
| ≤ 6000 字，含 Markdown 标记 | formatText（Markdown 渲染） |
| ≤ 6000 字，纯文本 | text（纯文本） |
| > 6000 字 | 生成 .md 文件作为附件发送 |

拆分规则：
- 目标每条 4000 字，为保 Markdown 完整性可延至 6000 字
- 不截断代码块（` ``` ``` `）、粗体（`**...**`）、行内代码（`...`）、链接（`[text](url)`）
- 超过 6000 字硬限制时强制拆分

OpenCode 响应推送：
- 蓝信不支持流式推送，采用**渐进推送**模式
- 每完成一个 assistant 文本片段，立即单独发一条蓝信消息
- 不等全部生成完才合并发送

## 架构

```
src/opencode_lansenger_remote/
├── core/
│   ├── types.py          # 数据类型、配置、常量
│   ├── session.py        # 会话管理（async 定时清理 + 空闲过期）
│   ├── auth.py           # 单用户授权（首认领 + 持久化 + chmod 600）
│   ├── approval.py       # 审批工作流（async + 超时自动拒绝）
│   └── notifications.py  # 消息格式化 + 模板 + Markdown 智能拆分
├── lansenger/
│   ├── bot.py            # 蓝信消息处理 + 命令路由 + 渐进推送 + 超长发文件
│   ├── client.py         # 蓝信 HTTP API（token 3级缓存 + 发消息/文件/appArticles）
│   └── ws.py             # WebSocket 连接管理（库级心跳 + 指数退避重连）
├── opencode/
│   ├── client.py         # OpenCode 客户端（HTTP 自动探测 + 工作目录持久化）
│   └── __main__.py       # python -m 支持
└── cli.py                # CLI 入口
```

## 数据持久化

| 文件 | 内容 | 权限 |
|------|------|------|
| `~/.opencode-lansenger-remote/.env` | 蓝信凭证 | 用户读写 |
| `~/.opencode-lansenger-remote/auth.json` | Owner 用户 ID | 600 |
| `~/.opencode-lansenger-remote/lansenger_token.json` | 蓝信 appToken 缓存 | 600 |
| `~/.opencode-lansenger-remote/workdir.json` | 上次工作目录 | 600 |

## 连接模式

| 模式 | 触发条件 | 特点 |
|------|----------|------|
| **HTTP Server（桌面版）** | OpenCode 桌面版正在运行 | 自动探测端口 + Basic Auth，持久会话，最快 |
| **HTTP Server（serve）** | 手动运行 `opencode serve` | 需手动指定端口和密码，持久会话 |
| **CLI 模式** | 无 server 运行 | 每次请求启动子进程，响应较慢 |

## 安全

- Owner 认领后持久化到 `~/.opencode-lansenger-remote/auth.json`（chmod 600）
- 非 owner 用户发送消息会被拒绝
- Token 缓存文件 `lansenger_token.json`（chmod 600）
- 工作目录文件 `workdir.json`（chmod 600）
- 支持 HTTP Basic Auth 连接 OpenCode server

## License

MIT