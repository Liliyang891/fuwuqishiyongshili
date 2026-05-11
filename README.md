# AI Agent 对话服务器 v1.0.1

多模型 AI Agent 对话服务，搭载 28 个 Function Calling 工具，支持 Web 浏览器和桌面 GUI 两种客户端，具备文件/数据库操作、语音转文字、一键远程部署等能力。

## 主要特性

- **多模型切换** — 支持多个 OpenAI 兼容 API（MiniMax、DeepSeek 等），运行时切换
- **AI Agent 工具调用** — 28 个工具函数，AI 自动调用完成文件和数据库操作
  - 文件/目录操作（CRUD、移动、复制、重命名、批量读取）
  - 文件内容编辑（写入、追加、插入、替换、删除行）
  - 搜索（按文件名/内容搜索、正则匹配）
  - 压缩解压（zip / tar.gz）
  - 数据库操作（建表、删表、查询、增删改、表结构查看）
  - 文件哈希、目录统计、文件类型检测、上传保存
- **Web 客户端** — 浏览器直连，无需安装，精美深色 UI，支持文件拖拽上传
- **GUI 桌面客户端** — tkinter 构建，支持文件选择上传、语音输入、对话历史
- **语音转文字** — 客户端 SpeechRecognition / PyAudio 录音，可选服务端 Whisper API 识别
- **对话记忆** — 服务端 session 管理，最近 20 轮对话历史，1 小时 TTL
- **文件服务器** — 支持文件上传（multipart / JSON base64）、下载、目录浏览、MIME 类型识别
- **安全机制** — 路径沙箱（限定 data/files/ 目录）、危险操作确认、SQL 注入防护
- **Docker 一键部署** — Dockerfile 内置，`python deploy.py` 完成打包+上传+启动
- **运维工具** — `ops.py` 提供 status/logs/restart/deploy/config 子命令
- **自动测试** — `test_simulate.py` 覆盖 9 大测试套件，验证所有工具函数和 Agent 流程

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 API Key

```bash
cp .env.example .env
```

编辑 `.env`，至少配置一个模型 Provider：

```env
PROVIDER1_NAME=MiniMax
PROVIDER1_API_URL=https://api.minimaxi.com/v1/chat/completions
PROVIDER1_API_KEY=你的API_Key
PROVIDER1_MODEL=MiniMax-M2.7-highspeed
PROVIDER1_MAX_TOKENS=4096
PROVIDER1_TEMPERATURE=0.7
```

支持最多 9 个 Provider（PROVIDER1 ~ PROVIDER9）。

### 3. 启动服务

```bash
python web_server.py
```

浏览器打开 `http://127.0.0.1:8888` 即可使用 Web 客户端。

### 4. GUI 客户端（可选）

```bash
python gui_client.py
```

GUI 客户端支持：
- 文字对话（带 AI 回复和工具调用展示）
- 📎 文件选择上传（支持多文件）
- 🎤 语音输入（需安装 `pip install SpeechRecognition pyaudio`）
- 🤖 运行时切换模型

## 部署到远程服务器

在 `.env` 中配置 SSH 连接：

```env
SSH_HOST=你的服务器IP
SSH_PORT=22
SSH_USER=root
SSH_PASSWORD=你的SSH密码
REMOTE_DIR=/root/fuwuqishiyongshili
```

一键部署：

```bash
python deploy.py
```

默认使用 Docker 部署；服务器无 Docker 时自动回退到 Python 直接运行。

## 运维工具

```bash
python ops.py status    # 检查服务器状态（SSH + 内部API + 公网访问）
python ops.py logs      # 查看服务器日志（最近 50 行 + Docker 容器日志）
python ops.py restart   # 重启远程服务（Docker restart 或 Python 重启）
python ops.py deploy    # 完整部署（调用 deploy.py）
python ops.py config    # 查看远程配置（隐藏敏感值）
```

## 自动测试

```bash
python test_simulate.py
```

覆盖 9 大测试套件：
1. 目录操作（创建/列出/复制/移动/删除/安全检查）
2. 文件读写（写入/读取/追加/插入/替换/删除行）
3. 文件管理（移动/复制/信息/搜索/哈希/删除/安全检查）
4. 批量与压缩（批量读取/统计/打包 zip/解压）
5. 数据库操作（建表/结构/增删改查/分页/安全防护）
6. 文件上传（文本/子目录/图片/类型检测）
7. Function Calling 定义完整性（28 个工具格式校验）
8. AI Agent 模拟对话（用户消息 → 工具调用 → 结果返回）
9. 文件信息格式化（_human_size / _format_file_info）

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | Web 客户端页面（内置 HTML） |
| GET | `/api/status` | 服务器状态（在线/模型列表/功能） |
| GET | `/api/models` | 可用 AI 模型列表 |
| POST | `/api/command` | 发送消息 `{"text":"...","model":"...","session_id":"..."}` |
| POST | `/api/clear` | 清空对话历史 `{"session_id":"..."}` |
| POST | `/api/upload` | 上传文件（multipart/form-data 或 JSON base64） |
| POST | `/api/speech` | 语音转文字（上传音频二进制或 base64） |
| GET | `/api/files/<path>` | 文件下载/预览/目录浏览 |

## 项目结构

```
├── web_server.py      # HTTP 服务器（主程序，含 Web 页面、文件上传、语音识别）
├── gui_client.py      # tkinter 桌面 GUI 客户端
├── tools.py           # 工具模块（28 个工具函数 + Function Calling 定义）
├── deploy.py          # 一键远程部署脚本（SSH + Docker/Python）
├── ops.py             # 运维工具（status/logs/restart/deploy/config）
├── test_simulate.py   # 模拟测试（9 大套件）
├── Dockerfile         # Docker 镜像定义
├── requirements.txt   # Python 依赖
├── .env.example       # 配置模板（SSH + 多 Provider + 服务器/Agent 设置）
└── .env               # 实际配置（不提交到 Git）
```

## 配置参考

完整 `.env` 配置项：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `SSH_HOST` | 远程服务器 IP | - |
| `SSH_PORT` | SSH 端口 | 22 |
| `SSH_USER` | SSH 用户 | root |
| `SSH_PASSWORD` | SSH 密码 | - |
| `REMOTE_DIR` | 远程部署目录 | /root/fuwuqishiyongshili |
| `PROVIDER[N]_NAME` | 模型显示名称 | - |
| `PROVIDER[N]_API_URL` | API 端点 | - |
| `PROVIDER[N]_API_KEY` | API 密钥 | - |
| `PROVIDER[N]_MODEL` | 模型标识 | 同 NAME |
| `PROVIDER[N]_MAX_TOKENS` | 最大 Token | 4096 |
| `PROVIDER[N]_TEMPERATURE` | 温度参数 | 0.7 |
| `SERVER_PORT` | 服务端口 | 8888 |
| `CORS_ORIGIN` | 跨域来源 | * |
| `MAX_TOOL_ROUNDS` | 最大工具调用轮次 | 15 |
| `SPEECH_API_URL` | 语音识别 API 端点 | - |
| `SPEECH_API_KEY` | 语音识别 API 密钥 | - |
| `UPLOAD_MAX_SIZE` | 最大上传字节数 | 52428800 (50MB) |