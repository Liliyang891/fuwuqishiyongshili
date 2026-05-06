#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Web 指令服务器 (HTTP) — AI Agent 版
支持：文字消息回复（多模型切换）、Function Calling 文件/数据库操作、
      文件上传、语音转文字、对话历史
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

# 导入工具模块
import tools

HOST = '0.0.0.0'
PORT = int(os.environ.get('SERVER_PORT', 8888))
CORS_ORIGIN = os.environ.get('CORS_ORIGIN', '*')
MAX_HISTORY = 20
MAX_TOOL_ROUNDS = int(os.environ.get('MAX_TOOL_ROUNDS', 5))  # 最大工具调用轮次
SPEECH_API_URL = os.environ.get('SPEECH_API_URL', '')  # 语音识别 API（可选）
SPEECH_API_KEY = os.environ.get('SPEECH_API_KEY', '')
UPLOAD_MAX_SIZE = int(os.environ.get('UPLOAD_MAX_SIZE', 50 * 1024 * 1024))  # 50MB

PROVIDERS = []
SESSIONS = {}
SESSION_TTL = 3600

# ---- System Prompt（引导 LLM 使用工具） ----

SYSTEM_PROMPT = """你是一个文件管理和数据库操作助手。你可以帮助用户管理服务器上 data/files/ 目录中的文件和 data/app.db 数据库。

## 你的能力
你可以使用以下工具来操作文件和数据库：
- **文件操作**: 列出目录、创建/删除/移动/复制 文件或目录、读取/写入/追加/插入/替换 文件内容、删除文件行、批量读取、搜索文件、搜索内容、获取文件哈希
- **压缩解压**: 打包压缩 zip/tar.gz、解压文件
- **数据库操作**: 列出表、查看表结构、查询数据(SELECT)、执行增删改(INSERT/UPDATE/DELETE)、创建表、删除表
- **统计信息**: 统计目录文件数量大小、获取文件详细信息

## 使用原则
1. 当用户要求进行文件或数据库操作时，**直接调用对应工具**，不要只是说"可以帮你做"。
2. 调用工具后，根据工具返回的结果用自然语言告知用户操作结果。
3. 如果用户没说清楚参数（如文件路径），可以根据上下文推断，或询问用户。
4. 文件路径可以使用相对路径（如 hello.txt）或子目录路径（如 subdir/hello.txt）。
5. 用户上传的文件存放在 data/files/ 目录下。
6. 用户可以上传图片，但图片内容你无法直接读取，可以告诉用户图片已保存。
7. 对于危险操作（如删除），在执行前向用户确认一下。
8. 数据库默认使用 SQLite，位于 data/app.db。

## 重要
当用户请求文件或数据库操作时，你必须调用对应的工具函数，不要凭空编造结果。"""


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
    """根据名称查找模型提供商"""
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


def call_llm_api(messages, provider_name=None, tools_enabled=True):
    """
    调用大模型 API
    参数:
        messages: 消息列表（含 system prompt）
        provider_name: 指定模型
        tools_enabled: 是否启用 function calling
    返回: (success: bool, reply_text: str, tool_calls: list or None)
    """
    provider = get_provider(provider_name)
    if provider is None:
        if not PROVIDERS:
            return False, "未配置任何大模型，请在 .env 中设置 PROVIDER1_* 环境变量", None
        else:
            names = get_model_list()
            return False, f"未找到模型 '{provider_name}'，可用模型: {', '.join(names)}", None

    api_url = provider.get('api_url')
    api_key = provider.get('api_key')
    model = provider.get('model', 'MiniMax-Text-01')
    max_tokens = provider.get('max_tokens', 4096)
    temperature = provider.get('temperature', 0.7)
    display_name = provider.get('name', model)

    if not api_url or not api_key:
        return False, f"模型 '{display_name}' 配置不完整，缺少 api_url 或 api_key", None

    request_body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    if tools_enabled:
        request_body["tools"] = tools.get_tools_definition()
        request_body["tool_choice"] = "auto"

    request_data = json.dumps(request_body).encode("utf-8")

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
            message = choices[0].get("message", {})
            content = message.get("content", "")
            tool_calls = message.get("tool_calls", [])

            # 转换 tool_calls 格式
            parsed_tool_calls = []
            if tool_calls:
                for tc in tool_calls:
                    func = tc.get("function", {})
                    try:
                        args = json.loads(func.get("arguments", "{}"))
                    except json.JSONDecodeError:
                        args = {}
                    parsed_tool_calls.append({
                        "id": tc.get("id", ""),
                        "name": func.get("name", ""),
                        "arguments": args,
                    })

            return True, content or "", parsed_tool_calls
        else:
            error_msg = result.get("error", {}).get("message", "未知错误")
            return False, f"大模型 API 返回错误: {error_msg}", None

    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return False, f"大模型 API HTTP {e.code}: {body[:200]}", None
    except urllib.error.URLError as e:
        return False, f"无法连接到大模型 API: {e.reason}", None
    except Exception as e:
        return False, f"调用大模型失败: {type(e).__name__}: {e}", None


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
                'message': '服务器正在运行 (AI Agent 模式)',
                'models': get_model_list(),
                'features': ['function_calling', 'file_upload', 'speech_to_text'],
            })
        elif parsed.path == '/api/models':
            self._send_json(200, {'models': get_model_list()})
        elif parsed.path.startswith('/api/files/'):
            # 文件下载/预览
            file_path = parsed.path[len('/api/files/'):]
            self._handle_file_download(file_path)
        else:
            body = b'Not Found'
            self.send_response(404)
            self.send_header('Content-Length', str(len(body)))
            self._set_cors_headers()
            self.end_headers()
            self.wfile.write(body)

    def do_POST(self):
        parsed = urlparse(self.path)
        content_type = self.headers.get('Content-Type', '')

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

        elif parsed.path == '/api/upload':
            self._handle_upload(content_type)

        elif parsed.path == '/api/speech':
            self._handle_speech(content_type)

        else:
            body = b'Not Found'
            self.send_response(404)
            self.send_header('Content-Length', str(len(body)))
            self._set_cors_headers()
            self.end_headers()
            self.wfile.write(body)

    # ---- 核心：带 Function Calling 的命令处理 ----

    def _handle_command(self, user_text, client_ip, model_name=None, session_id=None):
        """处理 /api/command 请求（支持 Function Calling 循环）"""
        print(f"\n[>] 收到来自 {client_ip} 的消息: {user_text}")
        if model_name:
            print(f"[i] 指定模型: {model_name}")

        session_id, history = get_or_create_session(session_id)

        if not user_text:
            return {
                'success': True,
                'reply': "我已收到你的信息，但你似乎没有输入任何内容。",
                'session_id': session_id,
                'server_ip': get_server_ip(),
            }

        # 构建消息（system + history + user）
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_text})

        print(f"[i] 开始 Agent 对话 (历史 {len(history)} 条)...")

        # Function Calling 循环
        tool_call_history = []  # 记录本轮所有工具调用
        for round_num in range(MAX_TOOL_ROUNDS + 1):
            success, content, tool_calls = call_llm_api(messages, model_name)

            if not success:
                # API 调用失败
                error_msg = f"大模型调用失败: {content}"
                print(f"[!] {error_msg}")
                return {
                    'success': False,
                    'reply': error_msg,
                    'session_id': session_id,
                    'server_ip': get_server_ip(),
                }

            # LLM 返回了 tool_calls
            if tool_calls:
                print(f"[i] 第 {round_num + 1} 轮: LLM 请求调用 {len(tool_calls)} 个工具")
                for tc in tool_calls:
                    print(f"    ↳ {tc['name']}({json.dumps(tc['arguments'], ensure_ascii=False)})")

                # 将 LLM 的 assistant 消息加入（含 tool_calls）
                assistant_msg = {
                    "role": "assistant",
                    "content": content or None,
                }
                # 序列化 tool_calls 为 API 格式
                api_tool_calls = []
                for tc in tool_calls:
                    api_tool_calls.append({
                        "id": tc['id'],
                        "type": "function",
                        "function": {
                            "name": tc['name'],
                            "arguments": json.dumps(tc['arguments'], ensure_ascii=False),
                        }
                    })
                assistant_msg["tool_calls"] = api_tool_calls
                messages.append(assistant_msg)

                # 执行每个工具调用
                for tc in tool_calls:
                    tool_ok, tool_result = tools.execute_tool(tc['name'], tc['arguments'])
                    tool_call_history.append({
                        "tool": tc['name'],
                        "arguments": tc['arguments'],
                        "success": tool_ok,
                        "result": tool_result,
                    })
                    if tool_ok:
                        print(f"    ✓ {tc['name']} 执行成功")
                    else:
                        print(f"    ✗ {tc['name']} 执行失败: {tool_result.get('error', '')}")

                    # 将 tool 结果加入消息
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc['id'],
                        "content": json.dumps(tool_result, ensure_ascii=False),
                    })

                # 继续循环，让 LLM 根据工具结果决定下一步
                continue

            # LLM 返回了纯文本回复（没有 tool_calls）
            reply_text = content or "（大模型未返回内容）"
            print(f"[<] 大模型最终回复: {reply_text[:80]}...")

            # 保存到历史
            history.append({"role": "user", "content": user_text})
            history.append({"role": "assistant", "content": reply_text})
            if len(history) > MAX_HISTORY * 2:
                del history[:2]

            result = {
                'success': True,
                'reply': reply_text,
                'session_id': session_id,
                'server_ip': get_server_ip(),
            }
            if tool_call_history:
                result['tool_calls'] = tool_call_history
            return result

        # 超过最大轮次
        error_msg = f"操作超过最大轮次限制（{MAX_TOOL_ROUNDS}轮），已终止。"
        print(f"[!] {error_msg}")
        return {
            'success': False,
            'reply': error_msg,
            'session_id': session_id,
            'server_ip': get_server_ip(),
        }

    # ---- 文件上传 ----

    def _handle_upload(self, content_type):
        """处理文件上传（multipart/form-data）"""
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length > UPLOAD_MAX_SIZE:
            self._send_json(413, {'success': False, 'error': f'文件太大，最大允许 {UPLOAD_MAX_SIZE // (1024*1024)}MB'})
            return

        body = self.rfile.read(content_length)

        # 解析 multipart/form-data
        if 'multipart/form-data' not in content_type:
            self._send_json(400, {'success': False, 'error': '请使用 multipart/form-data 格式上传'})
            return

        # 提取 boundary
        boundary = None
        for part in content_type.split(';'):
            part = part.strip()
            if part.startswith('boundary='):
                boundary = part[len('boundary='):].strip('"')
                break

        if not boundary:
            self._send_json(400, {'success': False, 'error': '缺少 boundary'})
            return

        # 解析 multipart 内容
        boundary_bytes = ('--' + boundary).encode('utf-8')
        end_boundary_bytes = ('--' + boundary + '--').encode('utf-8')

        # 简单解析（支持单个文件）
        parts = body.split(boundary_bytes)
        for part in parts:
            if not part or part == b'--' or part == b'--\r\n':
                continue
            if part == end_boundary_bytes:
                break

            # 分离 header 和 body
            if b'\r\n\r\n' in part:
                header_section, file_data = part.split(b'\r\n\r\n', 1)
                header_text = header_section.decode('utf-8', errors='replace')

                # 移除尾部的 boundary 标记
                if file_data.endswith(b'\r\n'):
                    file_data = file_data[:-2]
                # 检查是否有结尾的 --boundary--
                if b'\r\n--' in file_data:
                    file_data = file_data[:file_data.rfind(b'\r\n--')]

                # 解析 filename
                filename = 'uploaded_file'
                for line in header_text.split('\r\n'):
                    if 'Content-Disposition' in line and 'filename=' in line:
                        # 提取 filename
                        fn_start = line.index('filename="') + 10
                        fn_end = line.index('"', fn_start)
                        filename = line[fn_start:fn_end]
                        break

                # 也提取子文件夹参数（通过 X-Subfolder header）
                subfolder = self.headers.get('X-Subfolder', '')

                ok, result = tools.save_uploaded_file(file_data, filename, subfolder=subfolder)
                if ok:
                    self._send_json(200, result)
                else:
                    self._send_json(500, result)
                return

        # 如果没找到文件
        # 可能是 base64 编码的 JSON 上传
        try:
            data = json.loads(body.decode('utf-8'))
            filename = data.get('filename', 'file.bin')
            file_data = data.get('data', '')
            if isinstance(file_data, str):
                import base64
                file_data = base64.b64decode(file_data)
            elif isinstance(file_data, list):
                file_data = bytes(file_data)
            subfolder = data.get('subfolder', '')
            ok, result = tools.save_uploaded_file(file_data, filename, subfolder=subfolder)
            if ok:
                self._send_json(200, result)
            else:
                self._send_json(500, result)
        except Exception:
            self._send_json(400, {'success': False, 'error': '无法解析上传的文件'})

    # ---- 语音转文字 ----

    def _handle_speech(self, content_type):
        """处理语音上传并转为文字"""
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)

        # 解析上传的语音数据
        audio_data = None
        if 'multipart/form-data' in content_type:
            boundary = None
            for part in content_type.split(';'):
                part = part.strip()
                if part.startswith('boundary='):
                    boundary = part[len('boundary='):].strip('"')
                    break
            if boundary:
                boundary_bytes = ('--' + boundary).encode('utf-8')
                parts = body.split(boundary_bytes)
                for part in parts:
                    if b'\r\n\r\n' in part:
                        _, file_data = part.split(b'\r\n\r\n', 1)
                        if file_data.endswith(b'\r\n'):
                            file_data = file_data[:-2]
                        if b'\r\n--' in file_data:
                            file_data = file_data[:file_data.rfind(b'\r\n--')]
                        audio_data = file_data
                        break
        elif 'application/json' in content_type:
            try:
                data = json.loads(body.decode('utf-8'))
                audio_data_str = data.get('audio', '')
                import base64
                audio_data = base64.b64decode(audio_data_str)
            except Exception:
                self._send_json(400, {'success': False, 'error': '无法解析语音数据'})
                return
        else:
            # 直接是二进制音频数据
            audio_data = body

        if not audio_data:
            self._send_json(400, {'success': False, 'error': '未收到语音数据'})
            return

        # 保存音频文件
        timestamp = int(time.time())
        audio_filename = f"speech_{timestamp}.wav"
        audio_path = os.path.join(tools.FILES_DIR, audio_filename)
        try:
            with open(audio_path, 'wb') as f:
                f.write(audio_data)
        except Exception as e:
            self._send_json(500, {'success': False, 'error': f'保存音频文件失败: {e}'})
            return

        # 尝试调用语音识别 API
        if SPEECH_API_URL and SPEECH_API_KEY:
            recognized_text = self._call_speech_api(audio_data)
            if recognized_text:
                self._send_json(200, {
                    'success': True,
                    'text': recognized_text,
                    'audio_file': audio_filename,
                    'message': '语音识别成功',
                })
                return
            else:
                # 语音识别失败，返回提示
                self._send_json(200, {
                    'success': True,
                    'text': '',
                    'audio_file': audio_filename,
                    'message': '语音已保存但识别失败，文件路径: ' + audio_filename,
                    'note': '需要配置 SPEECH_API_URL 和 SPEECH_API_KEY 来启用语音识别',
                })
                return

        # 未配置语音 API，使用 Web Speech API 兼容模式（客户端已做了识别）
        self._send_json(200, {
            'success': True,
            'text': '',
            'audio_file': audio_filename,
            'message': '语音已保存，建议使用浏览器内置语音识别（客户端已处理）',
        })

    def _call_speech_api(self, audio_data):
        """调用语音识别 API"""
        try:
            # 尝试 OpenAI Whisper API 格式
            boundary = '----speech_boundary'
            body = b''
            body += f'--{boundary}\r\n'.encode('utf-8')
            body += b'Content-Disposition: form-data; name="model"\r\n\r\n'
            body += b'whisper-1\r\n'
            body += f'--{boundary}\r\n'.encode('utf-8')
            body += b'Content-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
            body += b'Content-Type: audio/wav\r\n\r\n'
            body += audio_data + b'\r\n'
            body += f'--{boundary}--\r\n'.encode('utf-8')

            req = urllib.request.Request(
                SPEECH_API_URL,
                data=body,
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "Authorization": f"Bearer {SPEECH_API_KEY}",
                },
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=30)
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("text", "")
        except Exception as e:
            print(f"    [!] 语音识别失败: {e}")
            return None

    # ---- 文件下载 ----

    def _handle_file_download(self, file_path):
        """处理文件下载/预览请求"""
        full_path = os.path.join(tools.FILES_DIR, file_path)
        # 安全检查
        resolved = os.path.realpath(full_path)
        if not resolved.startswith(tools.ALLOWED_ROOTS[0]):
            self._send_json(403, {'success': False, 'error': '禁止访问'})
            return

        if not os.path.exists(resolved):
            self._send_json(404, {'success': False, 'error': '文件不存在'})
            return

        if os.path.isdir(resolved):
            # 列出目录
            result = tools.list_folder(file_path)
            self._send_json(200, result)
            return

        # 读取文件并返回
        try:
            # 确定 MIME 类型
            ext = os.path.splitext(file_path)[1].lower()
            mime_types = {
                '.txt': 'text/plain',
                '.html': 'text/html',
                '.css': 'text/css',
                '.js': 'application/javascript',
                '.json': 'application/json',
                '.png': 'image/png',
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.gif': 'image/gif',
                '.svg': 'image/svg+xml',
                '.pdf': 'application/pdf',
                '.zip': 'application/zip',
                '.wav': 'audio/wav',
                '.mp3': 'audio/mpeg',
                '.mp4': 'video/mp4',
                '.webm': 'audio/webm',
            }
            content_type = mime_types.get(ext, 'application/octet-stream')

            # 如果是图片，直接返回 inline
            if content_type.startswith('image/'):
                content_disposition = 'inline'
            else:
                content_disposition = f'attachment; filename="{os.path.basename(file_path)}"'

            with open(resolved, 'rb') as f:
                file_data = f.read()

            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(file_data)))
            self.send_header('Content-Disposition', content_disposition)
            self.send_header('Cache-Control', 'public, max-age=3600')
            self._set_cors_headers()
            self.end_headers()
            self.wfile.write(file_data)
        except Exception as e:
            self._send_json(500, {'success': False, 'error': f'读取文件失败: {e}'})

    # ---- HTTP 辅助方法 ----

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
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Subfolder')

    def log_message(self, format, *args):
        print(f"[HTTP] {self.client_address[0]} - {format % args}")


# ========== Web 页面（含上传+语音按钮） ==========

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
            width: 100%; max-width: 700px;
            background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1);
            border-radius: 20px; padding: 30px 40px;
            backdrop-filter: blur(20px); box-shadow: 0 25px 50px rgba(0,0,0,0.4);
        }
        .header { text-align: center; margin-bottom: 25px; }
        .header .icon { font-size: 48px; margin-bottom: 6px; }
        .header h1 { color: #fff; font-size: 22px; font-weight: 600; letter-spacing: 1px; }
        .header .subtitle { color: #8888aa; font-size: 12px; margin-top: 4px; }
        .header .status { display: inline-flex; align-items: center; gap: 6px; margin-top: 8px; font-size: 12px; color: #a0a0c0; }
        .status-dot { width: 8px; height: 8px; border-radius: 50%; background: #4ade80; animation: pulse 2s infinite; }
        .status-dot.offline { background: #ef4444; animation: none; }
        @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }
        .chat-area {
            background: rgba(0,0,0,0.3); border-radius: 14px; padding: 18px;
            margin-bottom: 16px; min-height: 180px; max-height: 380px; overflow-y: auto;
            border: 1px solid rgba(255,255,255,0.06);
        }
        .message { margin-bottom: 14px; animation: fadeIn 0.3s ease; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        .message.user .label { color: #60a5fa; font-size: 11px; font-weight: 600; margin-bottom: 3px; text-transform: uppercase; letter-spacing: 1px; }
        .message.server .label { color: #4ade80; font-size: 11px; font-weight: 600; margin-bottom: 3px; text-transform: uppercase; letter-spacing: 1px; }
        .message .bubble {
            padding: 12px 16px; border-radius: 12px; font-size: 14px; line-height: 1.6;
            word-break: break-word; white-space: pre-wrap;
        }
        .message.user .bubble { background: linear-gradient(135deg, #3b82f6, #2563eb); color: #fff; border-top-left-radius: 4px; }
        .message.server .bubble { background: rgba(255,255,255,0.08); color: #e0e0f0; border: 1px solid rgba(255,255,255,0.1); border-top-left-radius: 4px; }
        .message .time { font-size: 10px; color: #6b6b8a; margin-top: 3px; }
        .message.server .time { text-align: right; }
        .message .extra { font-size: 10px; color: #fbbf24; margin-top: 2px; }
        .toolbar { margin-bottom: 8px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
        .toolbar label { color: #a0a0c0; font-size: 12px; white-space: nowrap; }
        .toolbar select {
            padding: 7px 10px; border-radius: 8px;
            border: 1px solid rgba(255,255,255,0.15); background: rgba(255,255,255,0.06);
            color: #fff; font-size: 13px; outline: none; transition: all 0.3s ease;
            cursor: pointer;
        }
        .toolbar select option { background: #1a1a3e; color: #fff; }
        .toolbar select:focus { border-color: #3b82f6; }
        .clear-btn {
            margin-left: auto; padding: 6px 12px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.15);
            background: rgba(239,68,68,0.15); color: #ef4444; font-size: 11px; cursor: pointer;
            transition: all 0.3s ease;
        }
        .clear-btn:hover { background: rgba(239,68,68,0.3); }
        .input-area { display: flex; gap: 8px; align-items: center; }
        .input-area input {
            flex: 1; padding: 12px 14px; border-radius: 12px;
            border: 1px solid rgba(255,255,255,0.15); background: rgba(255,255,255,0.06);
            color: #fff; font-size: 14px; outline: none; transition: all 0.3s ease; min-width: 0;
        }
        .input-area input::placeholder { color: #6b6b8a; }
        .input-area input:focus { border-color: #3b82f6; box-shadow: 0 0 0 3px rgba(59,130,246,0.15); }
        .input-area .icon-btn {
            width: 42px; height: 42px; border-radius: 12px; border: 1px solid rgba(255,255,255,0.15);
            background: rgba(255,255,255,0.06); color: #a0a0c0;
            font-size: 18px; cursor: pointer; transition: all 0.3s ease;
            display: flex; align-items: center; justify-content: center;
            flex-shrink: 0;
        }
        .input-area .icon-btn:hover { background: rgba(255,255,255,0.12); color: #fff; }
        .input-area .icon-btn.recording { background: rgba(239,68,68,0.3); border-color: #ef4444; color: #ef4444; animation: pulse 1.5s infinite; }
        .input-area .send-btn {
            padding: 12px 24px; border-radius: 12px; border: none;
            background: linear-gradient(135deg, #3b82f6, #2563eb); color: #fff;
            font-size: 14px; font-weight: 600; cursor: pointer; transition: all 0.3s ease; white-space: nowrap;
            flex-shrink: 0;
        }
        .input-area .send-btn:hover { background: linear-gradient(135deg, #4b92ff, #3573f3); box-shadow: 0 8px 25px rgba(59,130,246,0.3); transform: translateY(-1px); }
        .input-area .send-btn:active { transform: scale(0.97); }
        .input-area .send-btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
        .footer { text-align: center; margin-top: 16px; font-size: 11px; color: #5a5a7a; }
        .empty-hint { text-align: center; color: #5a5a7a; padding: 35px 0; font-size: 13px; }
        .empty-hint .big-icon { font-size: 36px; display: block; margin-bottom: 8px; }
        .upload-preview { display: none; margin: 6px 0; padding: 6px 10px; background: rgba(59,130,246,0.15); border-radius: 8px; color: #60a5fa; font-size: 12px; }
        .upload-preview.show { display: block; }
        .chat-area::-webkit-scrollbar { width: 5px; }
        .chat-area::-webkit-scrollbar-track { background: transparent; }
        .chat-area::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 10px; }
        #fileInput { display: none; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="icon">🤖</div>
            <h1>AI 智能助手</h1>
            <div class="subtitle">文件管理 · 数据库操作 · 智能对话</div>
            <div class="status">
                <span class="status-dot" id="statusDot"></span>
                <span id="statusText">连接中...</span>
            </div>
        </div>
        <div class="chat-area" id="chatArea">
            <div class="empty-hint">
                <span class="big-icon">💬</span>
                输入消息或上传文件，AI 帮你管理文件和数据库
            </div>
        </div>
        <div class="upload-preview" id="uploadPreview"></div>
        <div class="toolbar">
            <label>🤖 模型:</label>
            <select id="modelSelect" onchange="selectModel(this.value)">
                <option value="">加载中...</option>
            </select>
            <button class="clear-btn" onclick="clearChat()">🗑 清空对话</button>
        </div>
        <div class="input-area">
            <input type="text" id="inputBox" placeholder="输入消息，如：帮我看看 data/files 里有哪些文件..." maxlength="2000" autofocus />
            <input type="file" id="fileInput" multiple onchange="handleFileUpload(this.files)" accept="*/*" />
            <button class="icon-btn" id="uploadBtn" onclick="document.getElementById('fileInput').click()" title="上传文件">📎</button>
            <button class="icon-btn" id="voiceBtn" onclick="toggleVoice()" title="语音输入">🎤</button>
            <button class="send-btn" id="sendBtn" onclick="sendMessage()">发 送</button>
        </div>
        <div class="footer">AI Agent 模式 · 支持文件/数据库/语音</div>
    </div>
    <script>
        const chatArea = document.getElementById('chatArea');
        const inputBox = document.getElementById('inputBox');
        const sendBtn = document.getElementById('sendBtn');
        const statusDot = document.getElementById('statusDot');
        const statusText = document.getElementById('statusText');
        const uploadPreview = document.getElementById('uploadPreview');
        const voiceBtn = document.getElementById('voiceBtn');
        const emptyHint = chatArea.querySelector('.empty-hint');
        let messageCount = 0;
        let selectedModel = '';
        let sessionId = '';
        let isRecording = false;
        let recognition = null;
        let pendingFiles = [];

        // === 状态检查 ===
        async function checkStatus() {
            try {
                const resp = await fetch('/api/status');
                if (resp.ok) {
                    const data = await resp.json();
                    statusDot.classList.remove('offline');
                    statusText.textContent = '服务器在线 (Agent)';
                }
            } catch (e) {
                statusDot.classList.add('offline');
                statusText.textContent = '服务器离线';
            }
        }

        // === 消息添加 ===
        function addMessage(type, text, extra) {
            if (emptyHint && messageCount === 0) emptyHint.remove();
            const now = new Date();
            const timeStr = now.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
            const msgDiv = document.createElement('div');
            msgDiv.className = 'message ' + type;
            const labelText = type === 'user' ? '👤 你' : '🤖 AI';

            let extraHtml = '';
            if (extra && extra.tool_calls && extra.tool_calls.length > 0) {
                extraHtml = '<div class="extra">🔧 执行了 ' + extra.tool_calls.length + ' 个工具操作</div>';
            }

            msgDiv.innerHTML = `
                <div class="label">${labelText}</div>
                <div class="bubble">${escapeHtml(text)}</div>
                ${extraHtml}
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

        // === 发送消息 ===
        async function sendMessage() {
            const text = inputBox.value.trim();
            if (!text && pendingFiles.length === 0) return;

            let fullText = text;

            // 如果有待上传的文件，先上传
            if (pendingFiles.length > 0) {
                addMessage('user', '📎 正在上传 ' + pendingFiles.length + ' 个文件...');
                const uploadedPaths = [];
                for (const file of pendingFiles) {
                    try {
                        const formData = new FormData();
                        formData.append('file', file);
                        const resp = await fetch('/api/upload', { method: 'POST', body: formData });
                        const data = await resp.json();
                        if (data.success) {
                            uploadedPaths.push(data.path);
                        } else {
                            uploadedPaths.push('[上传失败: ' + (data.error || '未知错误') + ']');
                        }
                    } catch (e) {
                        uploadedPaths.push('[上传失败: ' + e.message + ']');
                    }
                }

                if (uploadedPaths.length > 0) {
                    const fileInfo = '\\n\\n[已上传文件: ' + uploadedPaths.join(', ') + ']';
                    fullText = (text || '请帮我看看上传的文件') + fileInfo;
                }
                pendingFiles = [];
                uploadPreview.classList.remove('show');
                uploadPreview.textContent = '';
            }

            if (!fullText) return;

            addMessage('user', fullText);
            inputBox.value = '';
            sendBtn.disabled = true;
            sendBtn.textContent = '处理中...';

            try {
                const resp = await fetch('/api/command', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text: fullText, model: selectedModel, session_id: sessionId })
                });
                const data = await resp.json();
                if (data.session_id) sessionId = data.session_id;
                addMessage('server', data.reply, data);
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

        // === 文件上传 ===
        function handleFileUpload(files) {
            if (!files || files.length === 0) return;
            pendingFiles = Array.from(files);
            const names = pendingFiles.map(f => f.name).join(', ');
            uploadPreview.textContent = '📎 已选择: ' + names + '（共 ' + formatSize(files[0].size * pendingFiles.length) + '）';
            uploadPreview.classList.add('show');

            // 自动填入提示文本
            if (!inputBox.value.trim()) {
                inputBox.value = '请帮我处理上传的文件: ' + names;
                inputBox.focus();
            }
        }

        function formatSize(bytes) {
            if (bytes < 1024) return bytes + ' B';
            if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
            return (bytes / 1048576).toFixed(1) + ' MB';
        }

        // === 语音输入（Web Speech API） ===
        function toggleVoice() {
            if (isRecording) {
                stopRecording();
                return;
            }

            const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
            if (!SpeechRecognition) {
                alert('你的浏览器不支持语音识别。请使用 Chrome 或 Edge 浏览器。');
                return;
            }

            recognition = new SpeechRecognition();
            recognition.lang = 'zh-CN';
            recognition.interimResults = true;
            recognition.continuous = false;

            recognition.onstart = function() {
                isRecording = true;
                voiceBtn.classList.add('recording');
                voiceBtn.textContent = '⏹';
                inputBox.placeholder = '正在聆听...';
            };

            recognition.onresult = function(event) {
                let transcript = '';
                for (let i = event.resultIndex; i < event.results.length; i++) {
                    transcript += event.results[i][0].transcript;
                }
                inputBox.value = transcript;
            };

            recognition.onerror = function(event) {
                console.error('语音识别错误:', event.error);
                stopRecording();
                if (event.error === 'not-allowed') {
                    alert('无法访问麦克风。请允许浏览器使用麦克风。');
                }
            };

            recognition.onend = function() {
                stopRecording();
            };

            try {
                recognition.start();
            } catch (e) {
                alert('语音识别启动失败: ' + e.message);
                stopRecording();
            }
        }

        function stopRecording() {
            isRecording = false;
            voiceBtn.classList.remove('recording');
            voiceBtn.textContent = '🎤';
            inputBox.placeholder = '输入消息...';
            if (recognition) {
                try { recognition.stop(); } catch (e) {}
                recognition = null;
            }
        }

        // === 清空对话 ===
        async function clearChat() {
            try {
                await fetch('/api/clear', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ session_id: sessionId })
                });
            } catch (e) {}
            chatArea.innerHTML = '<div class="empty-hint"><span class="big-icon">💬</span>对话已清空，输入消息开始新对话</div>';
            messageCount = 0;
        }

        // === 键盘快捷键 ===
        inputBox.addEventListener('keydown', function(e) {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
        });

        // === 模型加载 ===
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

        // === 粘贴图片 ===
        document.addEventListener('paste', function(e) {
            const items = e.clipboardData.items;
            for (const item of items) {
                if (item.type.startsWith('image/')) {
                    const blob = item.getAsFile();
                    if (blob) {
                        pendingFiles = [blob];
                        uploadPreview.textContent = '📎 已粘贴图片: ' + blob.type + ' (' + formatSize(blob.size) + ')';
                        uploadPreview.classList.add('show');
                        if (!inputBox.value.trim()) {
                            inputBox.value = '请帮我看看这张图片';
                            inputBox.focus();
                        }
                    }
                }
            }
        });

        // === 拖拽上传 ===
        const container = document.querySelector('.container');
        container.addEventListener('dragover', function(e) { e.preventDefault(); });
        container.addEventListener('drop', function(e) {
            e.preventDefault();
            const files = e.dataTransfer.files;
            if (files.length > 0) {
                handleFileUpload(files);
            }
        });

        // === 初始化 ===
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

    print("=" * 55)
    print(f"  🌐 AI Agent 服务器已启动")
    print(f"  服务器IP地址: {server_ip}")
    print(f"  监听端口: {port}")
    print(f"  CORS: {CORS_ORIGIN}")
    print(f"  工具数量: {len(tools.TOOL_MAP)} 个")
    print(f"  数据目录: {tools.FILES_DIR}")
    print(f"  数据库: {tools.DB_PATH}")
    print(f"  最大工具调用轮次: {MAX_TOOL_ROUNDS}")
    if PROVIDERS:
        print(f"  🤖 已配置 {len(PROVIDERS)} 个模型:")
        for p in PROVIDERS:
            print(f"     - {p.get('name', 'N/A')} ({p.get('model', 'N/A')})")
    else:
        print(f"  ⚠️  未配置大模型，请在 .env 中设置 PROVIDER1_* 环境变量")
    print()
    print(f"  📱 浏览器访问: http://{server_ip}:{port}")
    print(f"  📱 本地访问: http://127.0.0.1:{port}")
    print("=" * 55)
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