#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Web 指令服务器 (HTTP)
支持：文字消息回复（多模型切换）、对话历史
"""

import json
import os
import socket
import sys
import time
import urllib.request
import urllib.error
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
except ImportError:
    pass

HOST = '0.0.0.0'
PORT = int(os.environ.get('SERVER_PORT', 8888))
CORS_ORIGIN = os.environ.get('CORS_ORIGIN', '*')
MAX_HISTORY = 20

PROVIDERS = []

# 对话历史: {session_id: {"messages": [...], "last_active": timestamp}}
SESSIONS = {}
SESSION_TTL = 3600


def load_config():
    """从 .env 加载 LLM 配置，回退到 config.json"""
    providers = []

    for i in range(1, 10):
        name = os.environ.get(f'PROVIDER{i}_NAME')
        if not name:
            break
        api_url = os.environ.get(f'PROVIDER{i}_API_URL', '')
        api_key = os.environ.get(f'PROVIDER{i}_API_KEY', '')
        if api_url and api_key:
            providers.append({
                'name': name,
                'api_url': api_url,
                'api_key': api_key,
                'model': os.environ.get(f'PROVIDER{i}_MODEL', name),
                'max_tokens': int(os.environ.get(f'PROVIDER{i}_MAX_TOKENS', 4096)),
                'temperature': float(os.environ.get(f'PROVIDER{i}_TEMPERATURE', 0.7)),
            })

    if providers:
        return providers

    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                file_providers = config.get('providers', [])
                if file_providers:
                    return file_providers
                llm = config.get('llm')
                if llm:
                    llm['name'] = llm.get('model', '默认模型')
                    return [llm]
        except Exception as e:
            print(f"⚠️  加载 config.json 失败: {e}")
    return []


def get_provider(name):
    """根据名称查找模型提供商，不指定则返回第一个"""
    if not PROVIDERS:
        return None
    if not name:
        return PROVIDERS[0]
    for p in PROVIDERS:
        if p.get('name') == name:
            return p
    return None


def get_model_list():
    """返回模型名称列表"""
    return [p.get('name', '未知') for p in PROVIDERS]


def get_or_create_session(session_id):
    """获取或创建会话"""
    cleanup_sessions()
    if session_id and session_id in SESSIONS:
        SESSIONS[session_id]['last_active'] = time.time()
        return session_id, SESSIONS[session_id]['messages']
    new_id = session_id or str(uuid.uuid4())
    SESSIONS[new_id] = {'messages': [], 'last_active': time.time()}
    return new_id, SESSIONS[new_id]['messages']


def cleanup_sessions():
    """清理过期会话"""
    now = time.time()
    expired = [sid for sid, s in SESSIONS.items() if now - s['last_active'] > SESSION_TTL]
    for sid in expired:
        del SESSIONS[sid]


def call_llm_api(user_text, history, provider_name=None):
    """
    调用大模型 API，带对话历史
    返回: (success: bool, reply_text: str)
    """
    provider = get_provider(provider_name)
    if provider is None:
        if not PROVIDERS:
            return False, "未配置任何大模型，请在 .env 中设置 PROVIDER1_* 环境变量"
        else:
            names = get_model_list()
            return False, f"未找到模型 '{provider_name}'，可用模型: {', '.join(names)}"

    api_url = provider.get('api_url')
    api_key = provider.get('api_key')
    model = provider.get('model', 'MiniMax-Text-01')
    max_tokens = provider.get('max_tokens', 2048)
    temperature = provider.get('temperature', 0.7)
    display_name = provider.get('name', model)

    if not api_url or not api_key:
        return False, f"模型 '{display_name}' 配置不完整，缺少 api_url 或 api_key"

    messages = list(history) + [{"role": "user", "content": user_text}]

    request_data = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }).encode("utf-8")

    try:
        print(f"    [i] 正在调用 {display_name} ({model})...")
        req = urllib.request.Request(
            api_url,
            data=request_data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=180)
        print(f"    [i] API 响应状态: {resp.status}")
        result = json.loads(resp.read().decode("utf-8"))

        choices = result.get("choices", [])
        if choices:
            content = choices[0].get("message", {}).get("content", "")
            if content:
                return True, content
            else:
                return False, "大模型返回了空内容"
        else:
            error_msg = result.get("error", {}).get("message", "未知错误")
            return False, f"大模型 API 返回错误: {error_msg}"

    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return False, f"大模型 API HTTP {e.code}: {body[:200]}"
    except urllib.error.URLError as e:
        return False, f"无法连接到大模型 API: {e.reason}"
    except Exception as e:
        return False, f"调用大模型失败: {type(e).__name__}: {e}"


def get_server_ip():
    """获取服务器的本机IP地址"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('8.8.8.8', 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        return '127.0.0.1'


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class RequestHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器"""
    protocol_version = "HTTP/1.1"

    def do_OPTIONS(self):
        self.send_response(204)
        self._set_cors_headers()
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == '/' or parsed.path == '/index.html':
            html = get_web_page()
            body = html.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self._set_cors_headers()
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == '/api/status':
            self._send_json(200, {
                'status': 'online',
                'server_ip': get_server_ip(),
                'message': '服务器正在运行',
                'models': get_model_list(),
            })
        elif parsed.path == '/api/models':
            self._send_json(200, {'models': get_model_list()})
        else:
            body = b'Not Found'
            self.send_response(404)
            self.send_header('Content-Length', str(len(body)))
            self._set_cors_headers()
            self.end_headers()
            self.wfile.write(body)

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path == '/api/command':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')

            try:
                data = json.loads(body)
                user_text = data.get('text', '').strip()
                model_name = data.get('model', None)
                session_id = data.get('session_id', None)
            except json.JSONDecodeError:
                params = parse_qs(body)
                user_text = params.get('text', [''])[0].strip()
                model_name = params.get('model', [None])[0]
                session_id = params.get('session_id', [None])[0]

            client_ip = self.client_address[0]
            reply = self._handle_command(user_text, client_ip, model_name, session_id)
            self._send_json(200, reply)

        elif parsed.path == '/api/clear':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            try:
                data = json.loads(body)
                session_id = data.get('session_id', '')
            except json.JSONDecodeError:
                session_id = ''

            if session_id and session_id in SESSIONS:
                SESSIONS[session_id]['messages'] = []
            self._send_json(200, {'success': True, 'message': '对话已清空'})

        else:
            body = b'Not Found'
            self.send_response(404)
            self.send_header('Content-Length', str(len(body)))
            self._set_cors_headers()
            self.end_headers()
            self.wfile.write(body)

    def _handle_command(self, user_text, client_ip, model_name=None, session_id=None):
        """处理 /api/command 请求"""
        print(f"\n[>] 收到来自 {client_ip} 的消息: {user_text}")
        if model_name:
            print(f"[i] 指定模型: {model_name}")

        session_id, history = get_or_create_session(session_id)

        if user_text:
            print(f"[i] 转发消息给大模型 (历史 {len(history)} 条)...")
            success, llm_reply = call_llm_api(user_text, history, model_name)

            if success:
                history.append({"role": "user", "content": user_text})
                history.append({"role": "assistant", "content": llm_reply})
                if len(history) > MAX_HISTORY * 2:
                    del history[:2]
                reply_text = llm_reply
                print(f"[<] 大模型回复: {reply_text[:60]}...")
            else:
                reply_text = llm_reply
                print(f"[!] 大模型调用失败: {reply_text}")
        else:
            reply_text = "我已收到你的信息，但你似乎没有输入任何内容。"

        return {
            'success': True,
            'reply': reply_text,
            'session_id': session_id,
            'server_ip': get_server_ip(),
        }

    def _send_json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self._set_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _set_cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', CORS_ORIGIN)
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def log_message(self, format, *args):
        print(f"[HTTP] {self.client_address[0]} - {format % args}")


def get_web_page():
    """返回 Web 客户端页面 HTML"""
    return '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI 对话客户端</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Microsoft YaHei', sans-serif;
            background: linear-gradient(135deg, #0c0c1d 0%, #1a1a3e 50%, #0d0d2b 100%);
            min-height: 100vh; display: flex; justify-content: center; align-items: center; padding: 20px;
        }
        .container {
            width: 100%; max-width: 650px;
            background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1);
            border-radius: 20px; padding: 40px;
            backdrop-filter: blur(20px); box-shadow: 0 25px 50px rgba(0,0,0,0.4);
        }
        .header { text-align: center; margin-bottom: 35px; }
        .header .icon { font-size: 48px; margin-bottom: 10px; }
        .header h1 { color: #fff; font-size: 24px; font-weight: 600; letter-spacing: 1px; }
        .header .status { display: inline-flex; align-items: center; gap: 6px; margin-top: 8px; font-size: 13px; color: #a0a0c0; }
        .status-dot { width: 8px; height: 8px; border-radius: 50%; background: #4ade80; animation: pulse 2s infinite; }
        .status-dot.offline { background: #ef4444; animation: none; }
        @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }
        .chat-area {
            background: rgba(0,0,0,0.3); border-radius: 14px; padding: 20px;
            margin-bottom: 20px; min-height: 200px; max-height: 400px; overflow-y: auto;
            border: 1px solid rgba(255,255,255,0.06);
        }
        .message { margin-bottom: 16px; animation: fadeIn 0.3s ease; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        .message.user .label { color: #60a5fa; font-size: 12px; font-weight: 600; margin-bottom: 4px; text-transform: uppercase; letter-spacing: 1px; }
        .message.server .label { color: #4ade80; font-size: 12px; font-weight: 600; margin-bottom: 4px; text-transform: uppercase; letter-spacing: 1px; }
        .message .bubble {
            padding: 14px 18px; border-radius: 12px; font-size: 15px; line-height: 1.6;
            word-break: break-word; white-space: pre-wrap;
        }
        .message.user .bubble { background: linear-gradient(135deg, #3b82f6, #2563eb); color: #fff; border-top-left-radius: 4px; }
        .message.server .bubble { background: rgba(255,255,255,0.08); color: #e0e0f0; border: 1px solid rgba(255,255,255,0.1); border-top-left-radius: 4px; }
        .message .time { font-size: 11px; color: #6b6b8a; margin-top: 4px; }
        .message.server .time { text-align: right; }
        .toolbar { margin-bottom: 10px; display: flex; align-items: center; gap: 8px; }
        .toolbar label { color: #a0a0c0; font-size: 12px; white-space: nowrap; }
        .toolbar select {
            padding: 8px 12px; border-radius: 8px;
            border: 1px solid rgba(255,255,255,0.15); background: rgba(255,255,255,0.06);
            color: #fff; font-size: 13px; outline: none; transition: all 0.3s ease;
            cursor: pointer;
        }
        .toolbar select option { background: #1a1a3e; color: #fff; }
        .toolbar select:focus { border-color: #3b82f6; }
        .clear-btn {
            margin-left: auto; padding: 6px 14px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.15);
            background: rgba(239,68,68,0.15); color: #ef4444; font-size: 12px; cursor: pointer;
            transition: all 0.3s ease;
        }
        .clear-btn:hover { background: rgba(239,68,68,0.3); }
        .input-area { display: flex; gap: 10px; }
        .input-area input {
            flex: 1; padding: 14px 18px; border-radius: 12px;
            border: 1px solid rgba(255,255,255,0.15); background: rgba(255,255,255,0.06);
            color: #fff; font-size: 15px; outline: none; transition: all 0.3s ease;
        }
        .input-area input::placeholder { color: #6b6b8a; }
        .input-area input:focus { border-color: #3b82f6; box-shadow: 0 0 0 3px rgba(59,130,246,0.15); }
        .input-area button {
            padding: 14px 28px; border-radius: 12px; border: none;
            background: linear-gradient(135deg, #3b82f6, #2563eb); color: #fff;
            font-size: 15px; font-weight: 600; cursor: pointer; transition: all 0.3s ease; white-space: nowrap;
        }
        .input-area button:hover { background: linear-gradient(135deg, #4b92ff, #3573f3); box-shadow: 0 8px 25px rgba(59,130,246,0.3); transform: translateY(-1px); }
        .input-area button:active { transform: scale(0.97); }
        .input-area button:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
        .footer { text-align: center; margin-top: 20px; font-size: 12px; color: #5a5a7a; }
        .empty-hint { text-align: center; color: #5a5a7a; padding: 40px 0; font-size: 14px; }
        .empty-hint .big-icon { font-size: 40px; display: block; margin-bottom: 10px; }
        .chat-area::-webkit-scrollbar { width: 5px; }
        .chat-area::-webkit-scrollbar-track { background: transparent; }
        .chat-area::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 10px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="icon">\U0001f916</div>
            <h1>AI 对话客户端</h1>
            <div class="status">
                <span class="status-dot" id="statusDot"></span>
                <span id="statusText">连接中...</span>
            </div>
        </div>
        <div class="chat-area" id="chatArea">
            <div class="empty-hint">
                <span class="big-icon">\U0001f4ac</span>
                输入消息发送给 AI 模型
            </div>
        </div>
        <div class="toolbar">
            <label>\U0001f916 模型:</label>
            <select id="modelSelect" onchange="selectModel(this.value)">
                <option value="">加载中...</option>
            </select>
            <button class="clear-btn" onclick="clearChat()">\U0001f5d1 清空对话</button>
        </div>
        <div class="input-area">
            <input type="text" id="inputBox" placeholder="输入消息..." maxlength="2000" autofocus />
            <button id="sendBtn" onclick="sendMessage()">发 送</button>
        </div>
        <div class="footer">多模型 AI 对话服务</div>
    </div>
    <script>
        const chatArea = document.getElementById('chatArea');
        const inputBox = document.getElementById('inputBox');
        const sendBtn = document.getElementById('sendBtn');
        const statusDot = document.getElementById('statusDot');
        const statusText = document.getElementById('statusText');
        const emptyHint = chatArea.querySelector('.empty-hint');
        let messageCount = 0;
        let selectedModel = '';
        let sessionId = '';

        async function checkStatus() {
            try {
                const resp = await fetch('/api/status');
                if (resp.ok) {
                    statusDot.classList.remove('offline');
                    statusText.textContent = '服务器在线';
                }
            } catch (e) {
                statusDot.classList.add('offline');
                statusText.textContent = '服务器离线';
            }
        }

        function addMessage(type, text) {
            if (emptyHint && messageCount === 0) emptyHint.remove();
            const now = new Date();
            const timeStr = now.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
            const msgDiv = document.createElement('div');
            msgDiv.className = 'message ' + type;
            const labelText = type === 'user' ? '\U0001f464 你' : '\U0001f916 AI';

            msgDiv.innerHTML = `
                <div class="label">${labelText}</div>
                <div class="bubble">${escapeHtml(text)}</div>
                <div class="time">${timeStr}</div>
            `;
            chatArea.appendChild(msgDiv);
            chatArea.scrollTop = chatArea.scrollHeight;
            messageCount++;
        }

        function escapeHtml(str) {
            const div = document.createElement('div');
            div.textContent = str;
            return div.innerHTML.replace(/\\n/g, '<br>');
        }

        async function sendMessage() {
            const text = inputBox.value.trim();
            if (!text) return;

            addMessage('user', text);
            inputBox.value = '';
            sendBtn.disabled = true;
            sendBtn.textContent = '发送中...';

            try {
                const resp = await fetch('/api/command', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text: text, model: selectedModel, session_id: sessionId })
                });
                const data = await resp.json();
                if (data.session_id) sessionId = data.session_id;
                addMessage('server', data.reply);
            } catch (err) {
                addMessage('server', '错误：无法连接到服务器');
                statusDot.classList.add('offline');
                statusText.textContent = '服务器离线';
            } finally {
                sendBtn.disabled = false;
                sendBtn.textContent = '发 送';
                inputBox.focus();
            }
        }

        async function clearChat() {
            try {
                await fetch('/api/clear', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ session_id: sessionId })
                });
            } catch (e) {}
            chatArea.innerHTML = '<div class="empty-hint"><span class="big-icon">\U0001f4ac</span>对话已清空，输入消息开始新对话</div>';
            messageCount = 0;
        }

        inputBox.addEventListener('keydown', function(e) {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
        });

        async function loadModels() {
            const sel = document.getElementById('modelSelect');
            try {
                const resp = await fetch('/api/models');
                if (resp.ok) {
                    const data = await resp.json();
                    if (data.models && data.models.length > 0) {
                        const currentVal = sel.value;
                        sel.innerHTML = '';
                        data.models.forEach(function(m, i) {
                            const opt = document.createElement('option');
                            opt.value = m;
                            opt.text = m;
                            if (i === 0 && !currentVal) {
                                opt.selected = true;
                                selectedModel = m;
                            } else if (currentVal && m === currentVal) {
                                opt.selected = true;
                                selectedModel = m;
                            }
                            sel.appendChild(opt);
                        });
                    } else {
                        sel.innerHTML = '<option value="">无可用模型</option>';
                    }
                }
            } catch (e) {
                sel.innerHTML = '<option value="">加载失败</option>';
            }
        }

        function selectModel(val) { selectedModel = val; }

        loadModels();
        checkStatus();
        setInterval(checkStatus, 10000);
        inputBox.focus();
    </script>
</body>
</html>'''


def main():
    global PROVIDERS
    port = PORT

    args = sys.argv[1:]
    for arg in args:
        try:
            port = int(arg)
        except ValueError:
            print(f"警告：忽略无效参数 '{arg}'")

    PROVIDERS = load_config()
    server_ip = get_server_ip()

    print("=" * 50)
    print(f"  \U0001f310 AI 对话服务器已启动")
    print(f"  服务器IP地址: {server_ip}")
    print(f"  监听端口: {port}")
    print(f"  CORS: {CORS_ORIGIN}")
    if PROVIDERS:
        print(f"  \U0001f916 已配置 {len(PROVIDERS)} 个模型:")
        for p in PROVIDERS:
            print(f"     - {p.get('name', 'N/A')} ({p.get('model', 'N/A')})")
    else:
        print(f"  ⚠️  未配置大模型，请在 .env 中设置 PROVIDER1_* 环境变量")
    print()
    print(f"  \U0001f4f1 浏览器访问: http://{server_ip}:{port}")
    print(f"  \U0001f4f1 本地访问: http://127.0.0.1:{port}")
    print("=" * 50)
    print()

    server = ThreadedHTTPServer((HOST, port), RequestHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\n服务器正在关闭...")
        server.shutdown()
        print("服务器已关闭。")


if __name__ == '__main__':
    main()
