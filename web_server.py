#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Web 指令服务器 (HTTP) — AI Agent 版
支持：文字消息回复（多模型切换）、Function Calling 文件/数据库操作、
      文件上传、语音转文字、对话历史
"""

import json
import logging
import os
import socket
import sys
import time
import urllib.error
import urllib.request
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse

# ---- 日志配置 ----
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger('ai-server')

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
except ImportError:
    pass

# 导入工具模块
import tools
import auth as auth_module

HOST = '0.0.0.0'
PORT = int(os.environ.get('SERVER_PORT', 8888))
CORS_ORIGIN = os.environ.get('CORS_ORIGIN', '*')
MAX_HISTORY = 20
MAX_TOOL_ROUNDS = int(os.environ.get('MAX_TOOL_ROUNDS', 5))  # 最大工具调用轮次
SPEECH_API_URL = os.environ.get('SPEECH_API_URL', '')  # 语音识别 API（可选）
SPEECH_API_KEY = os.environ.get('SPEECH_API_KEY', '')
UPLOAD_MAX_SIZE = int(os.environ.get('UPLOAD_MAX_SIZE', 50 * 1024 * 1024))  # 50MB

PROVIDERS = []
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
            logger.warning('加载 config.json 失败: %s', e)
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
    """获取或创建会话（SQLite 持久化）"""
    tools.cleanup_sessions_db(SESSION_TTL)
    if session_id:
        messages = tools.load_session(session_id, SESSION_TTL)
        if messages:
            return session_id, messages
    new_id = session_id or str(uuid.uuid4())
    tools.save_session(new_id, [], SESSION_TTL)
    return new_id, []


def cleanup_sessions():
    """清理过期会话（委托给 tools 模块）"""
    tools.cleanup_sessions_db(SESSION_TTL)


def call_llm_api(messages, provider_name=None, tools_enabled=True, allowed_tools=None):
    """
    调用大模型 API
    参数:
        messages: 消息列表（含 system prompt）
        provider_name: 指定模型
        tools_enabled: 是否启用 function calling
        allowed_tools: 允许的工具列表（权限过滤后），为 None 则使用默认全部工具
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
        request_body["tools"] = allowed_tools if allowed_tools is not None else tools.get_tools_definition()
        request_body["tool_choice"] = "auto"

    request_data = json.dumps(request_body).encode("utf-8")

    try:
        logger.info('正在调用 %s (%s)...', display_name, model)
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
        logger.info('API 响应状态: %s', resp.status)
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

        if parsed.path == '/login':
            self._serve_static_file('login.html')
            return
        if parsed.path == '/register':
            self._serve_static_file('register.html')
            return
        if parsed.path == '/admin/' or parsed.path == '/admin':
            user = self._require_role('super_admin')
            if user is None: return
            self._serve_static_file('admin.html')
            return
        if parsed.path == '/api/me':
            user = self._require_auth()
            if user:
                self._send_json(200, {'success': True, 'user': user})
            return
        if parsed.path == '/' or parsed.path == '/index.html' or parsed.path == '/chat/':
            user = self._get_user_from_cookie()
            if user is None:
                self.send_response(302)
                self.send_header('Location', '/login')
                self.end_headers()
                return
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
        elif parsed.path == '/api/admin/users':
            user = self._require_role('super_admin')
            if user is None: return
            qs = parse_qs(parsed.query)
            role = qs.get('role', [None])[0]
            dept_id = qs.get('department_id', [None])[0]
            active = qs.get('active', [None])[0]
            users = auth_module.list_users(
                role=role,
                department_id=int(dept_id) if dept_id else None,
                active=active.lower() == 'true' if active else None
            )
            self._send_json(200, {'success': True, 'users': users})
        elif parsed.path == '/api/admin/departments':
            user = self._require_role('super_admin')
            if user is None: return
            depts = auth_module.list_departments()
            self._send_json(200, {'success': True, 'departments': depts})
        elif parsed.path == '/api/admin/audit-logs':
            user = self._require_role('super_admin')
            if user is None: return
            qs = parse_qs(parsed.query)
            logs = auth_module.get_audit_logs(
                user_id=qs.get('user_id', [None])[0],
                action=qs.get('action', [None])[0],
            )
            self._send_json(200, {'success': True, 'logs': logs})
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

        if parsed.path == '/api/register':
            self._handle_register()
            return
        if parsed.path == '/api/login':
            self._handle_login()
            return
        if parsed.path == '/api/logout':
            self._handle_logout()
            return
        if parsed.path == '/api/command':
            content_length = int(self.headers.get('Content-Length', 0))
            raw_body = self.rfile.read(content_length)
            body = raw_body.decode('utf-8', errors='replace')

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

            if session_id:
                tools.delete_session(session_id)
                tools.save_session(session_id, [], SESSION_TTL)
            self._send_json(200, {'success': True, 'message': '对话已清空'})

        elif parsed.path == '/api/upload':
            self._handle_upload(content_type)

        elif parsed.path == '/api/speech':
            self._handle_speech(content_type)

        elif parsed.path.startswith('/api/admin/'):
            user = self._require_role('super_admin')
            if user is None: return
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self._send_json(400, {'success': False, 'error': '请求格式错误'})
                return

            if parsed.path == '/api/admin/departments':
                try:
                    dept = auth_module.create_department(data.get('name', ''))
                    self._send_json(200, {'success': True, 'department': dept})
                except ValueError as e:
                    self._send_json(400, {'success': False, 'error': str(e)})
            elif parsed.path == '/api/admin/users/reset-password':
                try:
                    auth_module.reset_user_password(data['user_id'], data['new_password'])
                    self._send_json(200, {'success': True, 'message': '密码已重置'})
                except (ValueError, KeyError) as e:
                    self._send_json(400, {'success': False, 'error': str(e)})
            elif parsed.path.startswith('/api/admin/users/') and parsed.path.endswith('/role'):
                user_id = int(parsed.path.split('/')[-2])
                try:
                    auth_module.update_user_role(
                        user_id,
                        data.get('role', 'guest'),
                        data.get('department_id')
                    )
                    if 'is_active' in data:
                        auth_module.toggle_user_active(user_id, data['is_active'])
                    self._send_json(200, {'success': True, 'message': '用户已更新'})
                except ValueError as e:
                    self._send_json(400, {'success': False, 'error': str(e)})
            elif parsed.path.startswith('/api/admin/users/') and parsed.path.endswith('/delete'):
                user_id = int(parsed.path.split('/')[-2])
                auth_module.delete_user(user_id)
                self._send_json(200, {'success': True, 'message': '用户已删除'})
            elif parsed.path.startswith('/api/admin/departments/') and parsed.path.endswith('/update'):
                dept_id = int(parsed.path.split('/')[-2])
                try:
                    auth_module.update_department(dept_id, data.get('name', ''))
                    self._send_json(200, {'success': True, 'message': '部门已更新'})
                except ValueError as e:
                    self._send_json(400, {'success': False, 'error': str(e)})
            elif parsed.path.startswith('/api/admin/departments/') and parsed.path.endswith('/delete'):
                dept_id = int(parsed.path.split('/')[-2])
                try:
                    auth_module.delete_department(dept_id)
                    self._send_json(200, {'success': True, 'message': '部门已删除'})
                except ValueError as e:
                    self._send_json(400, {'success': False, 'error': str(e)})

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
        user = self._get_user_from_cookie()
        if user is None:
            return {
                'success': False,
                'reply': '请先登录',
                'session_id': '',
                'server_ip': get_server_ip(),
            }
        print(f"\n[>] 收到来自 {client_ip} 的消息: {user_text}")
        if model_name:
            logger.info('指定模型: %s', model_name)

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

        logger.info('开始 Agent 对话 (历史 %d 条)...', len(history))

        # 根据用户角色获取允许的工具
        allowed_tools = auth_module.get_allowed_tools(user)
        # Function Calling 循环
        tool_call_history = []  # 记录本轮所有工具调用
        for round_num in range(MAX_TOOL_ROUNDS + 1):
            success, content, tool_calls = call_llm_api(messages, model_name, allowed_tools=allowed_tools)

            if not success:
                # API 调用失败
                error_msg = f"大模型调用失败: {content}"
                logger.error('%s', error_msg)
                return {
                    'success': False,
                    'reply': error_msg,
                    'session_id': session_id,
                    'server_ip': get_server_ip(),
                }

            # LLM 返回了 tool_calls
            if tool_calls:
                logger.info('第 %d 轮: LLM 请求调用 %d 个工具', round_num + 1, len(tool_calls))
                for tc in tool_calls:
                    logger.debug('  ↳ %s(%s)', tc['name'], json.dumps(tc['arguments'], ensure_ascii=False))

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
                    # 权限检查
                    file_path = tc['arguments'].get('path', '') if 'arguments' in tc else ''
                    can_exec, err_msg = auth_module.can_execute_tool(tc['name'], user, file_path)
                    if not can_exec:
                        tool_call_history.append({
                            "tool": tc['name'],
                            "arguments": tc['arguments'],
                            "success": False,
                            "result": {"error": err_msg},
                        })
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc['id'],
                            "content": json.dumps({"error": err_msg}, ensure_ascii=False),
                        })
                        continue
                    tool_ok, tool_result = tools.execute_tool(tc['name'], tc['arguments'], user=user)
                    tool_call_history.append({
                        "tool": tc['name'],
                        "arguments": tc['arguments'],
                        "success": tool_ok,
                        "result": tool_result,
                    })
                    if tool_ok:
                        logger.debug('  ✓ %s 执行成功', tc['name'])
                    else:
                        logger.debug('  ✗ %s 执行失败: %s', tc['name'], tool_result.get('error', ''))

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
            logger.info('大模型最终回复: %s...', reply_text[:80])

            # 保存到历史
            history.append({"role": "user", "content": user_text})
            history.append({"role": "assistant", "content": reply_text})
            if len(history) > MAX_HISTORY * 2:
                del history[:2]
            tools.save_session(session_id, history, SESSION_TTL)

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
        logger.error('%s', error_msg)
        return {
            'success': False,
            'reply': error_msg,
            'session_id': session_id,
            'server_ip': get_server_ip(),
        }

    # ---- 文件上传 ----

    def _handle_upload(self, content_type):
        """处理文件上传（multipart/form-data）"""
        user = self._get_user_from_cookie()
        if user is None:
            self._send_json(401, {'success': False, 'error': '请先登录'})
            return
        if auth_module.get_role_level(user['role']) < auth_module.get_role_level('staff'):
            self._send_json(403, {'success': False, 'error': '权限不足：职员及以上可上传'})
            return
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
            logger.error('语音识别失败: %s', e)
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

    # ---- 认证处理方法 ----

    def _handle_register(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')
        try:
            data = json.loads(body)
            username = data.get('username', '').strip()
            password = data.get('password', '')
            email = data.get('email', '').strip() or None
        except json.JSONDecodeError:
            self._send_json(400, {'success': False, 'error': '请求格式错误'})
            return

        try:
            user = auth_module.register_user(username, password, email)
            self._send_json(200, {'success': True, 'user': user, 'message': '注册成功'})
        except ValueError as e:
            self._send_json(400, {'success': False, 'error': str(e)})

    def _handle_login(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')
        try:
            data = json.loads(body)
            login = data.get('login', '').strip()
            password = data.get('password', '')
            remember_me = data.get('remember_me', False)
        except json.JSONDecodeError:
            self._send_json(400, {'success': False, 'error': '请求格式错误'})
            return

        try:
            token = auth_module.login_user(login, password, remember_me)
            user = auth_module.get_user_by_token(token)
            ttl = 7 * 24 * 3600 if remember_me else 24 * 3600
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Set-Cookie',
                f'session_token={token}; Path=/; HttpOnly; Max-Age={ttl}; SameSite=Lax')
            self._set_cors_headers()
            body = json.dumps({'success': True, 'user': user}).encode('utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except ValueError as e:
            self._send_json(401, {'success': False, 'error': str(e)})

    def _handle_logout(self):
        cookie_header = self.headers.get('Cookie', '')
        for part in cookie_header.split(';'):
            part = part.strip()
            if part.startswith('session_token='):
                token = part[len('session_token='):]
                auth_module.logout_session(token)
                break
        self._send_json(200, {'success': True, 'message': '已登出'})

    def _serve_static_file(self, filename):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        filepath = os.path.join(base_dir, 'static', filename)
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                html = f.read()
        except FileNotFoundError:
            html = f'<html><body><h1>{filename} 未找到</h1></body></html>'
        body = html.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self._set_cors_headers()
        self.end_headers()
        self.wfile.write(body)

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

    def _get_user_from_cookie(self):
        """从 Cookie 中获取当前登录用户，未登录返回 None"""
        cookie_header = self.headers.get('Cookie', '')
        session_token = None
        for part in cookie_header.split(';'):
            part = part.strip()
            if part.startswith('session_token='):
                session_token = part[len('session_token='):]
                break
        if not session_token:
            return None
        return auth_module.get_user_by_token(session_token)

    def _require_auth(self):
        """要求登录，未登录返回 302"""
        user = self._get_user_from_cookie()
        if user is None:
            self.send_response(302)
            self.send_header('Location', '/login')
            self.end_headers()
            return None
        return user

    def _require_role(self, min_role):
        """要求最低角色，不够返回 403"""
        user = self._require_auth()
        if user is None:
            return None
        if auth_module.get_role_level(user['role']) < auth_module.get_role_level(min_role):
            self._send_json(403, {'success': False, 'error': '权限不足'})
            return None
        return user

    def log_message(self, format, *args):
        logger.info('HTTP %s - %s', self.client_address[0], format % args)


# ========== Web 页面（含上传+语音按钮） ==========

def get_web_page():
    """从 static/index.html 读取 Web 客户端页面"""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    html_path = os.path.join(base_dir, 'static', 'index.html')
    try:
        with open(html_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return '<html><body><h1>页面文件未找到</h1></body></html>'



def main():
    global PROVIDERS
    port = PORT

    args = sys.argv[1:]
    for arg in args:
        try:
            port = int(arg)
        except ValueError:
            logger.warning("忽略无效参数 '%s'", arg)

    PROVIDERS = load_config()
    server_ip = get_server_ip()

    logger.info('=' * 55)
    logger.info('AI Agent 服务器已启动')
    logger.info('服务器IP地址: %s', server_ip)
    logger.info('监听端口: %s', port)
    logger.info('CORS: %s', CORS_ORIGIN)
    logger.info('工具数量: %d 个', len(tools.TOOL_MAP))
    logger.info('数据目录: %s', tools.FILES_DIR)
    logger.info('数据库: %s', tools.DB_PATH)
    logger.info('最大工具调用轮次: %d', MAX_TOOL_ROUNDS)
    if PROVIDERS:
        logger.info('已配置 %d 个模型:', len(PROVIDERS))
        for p in PROVIDERS:
            logger.info('     - %s (%s)', p.get('name', 'N/A'), p.get('model', 'N/A'))
    else:
        logger.warning('未配置大模型，请在 .env 中设置 PROVIDER1_* 环境变量')
    print()
    logger.info('浏览器访问: http://%s:%s', server_ip, port)
    logger.info('本地访问: http://127.0.0.1:%s', port)
    logger.info('=' * 55)
    print()

    server = ThreadedHTTPServer((HOST, port), RequestHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\n服务器正在关闭...")
        server.shutdown()
        logger.info('服务器已关闭。')


if __name__ == '__main__':
    main()