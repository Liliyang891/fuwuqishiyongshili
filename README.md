# AI 对话服务器

多模型 AI 对话服务，支持 Web 浏览器和桌面 GUI 两种客户端，可一键部署到远程服务器。

## 功能

- 多模型切换（MiniMax、DeepSeek 等 OpenAI 兼容 API）
- 对话历史记忆（服务端 session，最近 20 轮）
- Web 客户端（浏览器访问，无需安装）
- GUI 桌面客户端（tkinter）
- Docker 一键部署
- 运维工具集成（状态检查、日志查看、远程重启）

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置

复制 `.env.example` 为 `.env`，填入你的 API Key：

```bash
cp .env.example .env
```

编辑 `.env`，至少配置一个模型 Provider：

```env
PROVIDER1_NAME=MiniMax
PROVIDER1_API_URL=https://api.minimaxi.com/v1/chat/completions
PROVIDER1_API_KEY=你的API Key
PROVIDER1_MODEL=MiniMax-M2.7-highspeed
```

### 3. 启动服务器

```bash
python web_server.py
```

浏览器打开 `http://127.0.0.1:8888` 即可使用。

### 4. GUI 客户端（可选）

```bash
python gui_client.py
```

## 部署到远程服务器

在 `.env` 中配置 SSH 连接信息：

```env
SSH_HOST=你的服务器IP
SSH_USER=root
SSH_PASSWORD=你的密码
```

运行部署：

```bash
python deploy.py
```

支持 Docker 自动构建，无 Docker 时回退到 Python 直接运行。

## 运维工具

```bash
python ops.py status    # 检查服务器状态
python ops.py logs      # 查看服务器日志
python ops.py restart   # 重启远程服务
python ops.py deploy    # 完整部署
python ops.py config    # 查看远程配置
```

## 项目结构

```
├── web_server.py      # HTTP 服务器（主程序）
├── gui_client.py      # tkinter 桌面客户端
├── deploy.py          # 远程部署脚本
├── ops.py             # 运维工具
├── Dockerfile         # Docker 镜像定义
├── requirements.txt   # Python 依赖
├── .env.example       # 配置模板
└── .env               # 实际配置（不提交到 Git）
```

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | Web 客户端页面 |
| GET | `/api/status` | 服务器状态 |
| GET | `/api/models` | 可用模型列表 |
| POST | `/api/command` | 发送消息 `{"text": "...", "model": "...", "session_id": "..."}` |
| POST | `/api/clear` | 清空对话历史 `{"session_id": "..."}` |
