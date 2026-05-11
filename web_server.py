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
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse

# ---- 日志配置 ----
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')

_request_local = threading.local()

class RequestIDFilter(logging.Filter):
    def filter(self, record):
        record.request_id = getattr(_request_local, 'request_id', '-')[:8]
        return True

_log_handler = logging.StreamHandler()
_log_handler.setFormatter(logging.Formatter(
    '%(asctime)s [%(levelname)s] [%(request_id)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
))
_log_handler.addFilter(RequestIDFilter())

_root_logger = logging.getLogger()
_root_logger.handlers = []
_root_logger.addHandler(_log_handler)
_root_logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
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
MAX_REQUEST_SIZE = 10 * 1024 * 1024  # 10MB，普通请求最大 body 大小

PROVIDERS = []
SESSION_TTL = 3600

# ---- System Prompt（引导 LLM 使用工具） ----

# 初始化 Agent 系统
import agent
from agent.tools.builtin_tools import register_all as register_builtin_tools

_agent_registry = agent.ToolRegistry()
register_builtin_tools(_agent_registry)

# 注册 Phase 2 工具
from agent.tools.bash_tool import BashTool
from agent.tools.file_edit_tool import Edit as EditTool
from agent.tools.glob_tool import Glob as GlobTool
from agent.tools.grep_tool import Grep as GrepTool
from agent.tools.web_tools import WebFetch, WebSearch

_agent_registry.register(BashTool(), category='system', min_role='guest')
_agent_registry.register(EditTool(), category='file', min_role='staff')
_agent_registry.register(GlobTool(), category='search', min_role='guest')
_agent_registry.register(GrepTool(), category='search', min_role='guest')
_agent_registry.register(WebFetch(), category='web', min_role='guest')
_agent_registry.register(WebSearch(), category='web', min_role='guest')

# 注册 Phase 3 工具
from agent.tools.plan_mode_tool import EnterPlanMode, ExitPlanMode
from agent.tools.task_tools import TaskCreate, TaskUpdate, TaskList, TaskGet
from agent.tools.agent_tool import Agent as AgentTool

_agent_registry.register(EnterPlanMode(), category='meta', min_role='staff')
_agent_registry.register(ExitPlanMode(), category='meta', min_role='staff')
_agent_registry.register(TaskCreate(), category='task', min_role='staff')
_agent_registry.register(TaskUpdate(), category='task', min_role='staff')
_agent_registry.register(TaskList(), category='task', min_role='staff')
_agent_registry.register(TaskGet(), category='task', min_role='staff')
_agent_registry.register(AgentTool(), category='meta', min_role='staff')

# 注册策略框架工具
from agent.tools.policy_tools import (
    CreatePolicyTool, ApplyLeaveTool, QueryPoliciesTool,
    ApproveLeaveTool, RejectLeaveTool, LeaveHistoryTool,
    DeactivatePolicyTool,
)
_agent_registry.register(CreatePolicyTool(), category='policy', min_role='dept_head')
_agent_registry.register(ApplyLeaveTool(), category='policy', min_role='staff')
_agent_registry.register(QueryPoliciesTool(), category='policy', min_role='guest')
_agent_registry.register(ApproveLeaveTool(), category='policy', min_role='dept_head')
_agent_registry.register(RejectLeaveTool(), category='policy', min_role='dept_head')
_agent_registry.register(LeaveHistoryTool(), category='policy', min_role='staff')
_agent_registry.register(DeactivatePolicyTool(), category='policy', min_role='dept_head')

# 初始化技能快速通道注册表（文件驱动的意图匹配，秒级响应，不消耗 token）
from agent.skill_registry import SkillRegistry
_skill_registry = SkillRegistry()
_skill_registry.load()


def _build_system_prompt(user=None):
    """构建完整的系统提示词 — 可组合分段模式"""
    from agent.prompt import (
        get_identity_section, get_system_rules_section,
        get_tool_usage_section, get_git_rules_section,
        get_env_info_section, get_rbac_reminder_section,
        get_session_guidance_section, build_system_prompt,
    )
    from agent.memory import get_memory_context

    sections = [
        get_identity_section(),
        get_system_rules_section(),
        get_tool_usage_section(),
        get_git_rules_section(),
        get_session_guidance_section(_agent_registry),
        get_env_info_section(),
        get_rbac_reminder_section(user),
    ]

    # 注入策略摘要（当前生效的管理策略）
    try:
        from agent.policy_engine import PolicyEngine
        engine = PolicyEngine()
        policy_summary = engine.get_policy_summary_for_prompt(user)
        if policy_summary:
            sections.append(policy_summary)
    except Exception:
        pass

    # 注入记忆上下文 (CLAUDE.md + MEMORY.md)
    memory_ctx = get_memory_context()
    if memory_ctx:
        sections.append(memory_ctx)

    return build_system_prompt(sections)


def _create_agent_loop(user=None):
    """创建 AgentLoop 实例"""
    loop = agent.AgentLoop(_agent_registry, _llm_call_wrapper, max_turns=MAX_TOOL_ROUNDS + 1)
    if user:
        loop._user = user
    return loop


def _llm_call_wrapper(messages, model_name=None, allowed_tools=None):
    """LLM 调用包装器 — 适配 AgentLoop 的接口
    返回: (success, reply_text, tool_calls, reasoning_content)"""
    return call_llm_api(messages, model_name, allowed_tools=allowed_tools)


def _fast_query(user_text, user):
    """快速通道：通过 SkillRegistry 匹配文件定义的技能。
    返回 dict 表示命中，返回 None 表示未命中，走正常 Agent 流程。"""
    return _skill_registry.match(user_text, user)


def _handle_slash_command(text, session_id, history, user):
    """处理斜杠命令, 返回 None 表示不是斜杠命令"""
    text = text.strip()
    if not text.startswith('/'):
        return None

    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ''

    # /help — 显示可用命令
    if cmd == '/help':
        help_text = """可用斜杠命令:
  /help        — 显示此帮助信息
  /clear       — 清空当前对话历史
  /compact     — 手动压缩对话上下文
  /model <名称> — 切换 LLM 模型
  /status      — 查看当前会话状态
  /plan        — 查看计划模式说明
  /version     — 查看版本信息"""
        return {
            'success': True,
            'reply': help_text,
            'session_id': session_id,
            'server_ip': get_server_ip(),
        }

    # /clear — 清空对话历史
    if cmd == '/clear':
        try:
            tools.save_session(session_id, [], SESSION_TTL)
        except Exception:
            pass
        return {
            'success': True,
            'reply': '对话历史已清空。',
            'session_id': session_id,
            'server_ip': get_server_ip(),
        }

    # /compact — 手动触发压缩
    if cmd == '/compact':
        from agent.compact import estimate_tokens, compact_messages
        current = estimate_tokens(history)
        from agent.loop import TOKEN_BUDGET_HARD
        _, info = compact_messages(history, TOKEN_BUDGET_HARD)
        return {
            'success': True,
            'reply': (
                f'当前上下文: ~{current} tokens。\n'
                f'压缩后: ~{info.get("new_tokens", current)} tokens。\n'
                f'系统会在达到 {TOKEN_BUDGET_HARD} tokens 时自动压缩。'
            ),
            'session_id': session_id,
            'server_ip': get_server_ip(),
        }

    # /model — 切换模型
    if cmd == '/model':
        if not arg:
            return {
                'success': True,
                'reply': f'当前默认模型: {os.environ.get("PROVIDER1_MODEL", "未设置")}\n用法: /model <模型名称>',
                'session_id': session_id,
                'server_ip': get_server_ip(),
            }
        # 模型切换由客户端在下一次请求时通过 model 参数指定
        return {
            'success': True,
            'reply': f'模型已切换为: {arg}。将在下一次对话中生效。\n用法: 使用 /model <名称> 或在设置中选择。',
            'session_id': session_id,
            'server_ip': get_server_ip(),
        }

    # /status — 查看会话状态
    if cmd == '/status':
        from agent.loop import TOKEN_BUDGET_WARN, TOKEN_BUDGET_HARD
        from agent.compact import estimate_tokens
        tokens = estimate_tokens(history)
        pct = 100 * tokens / TOKEN_BUDGET_HARD if TOKEN_BUDGET_HARD else 0
        return {
            'success': True,
            'reply': (
                f'会话 ID: {session_id}\n'
                f'历史消息数: {len(history)}\n'
                f'估算 tokens: ~{tokens} / {TOKEN_BUDGET_HARD} ({pct:.1f}%)\n'
                f'警告阈值: {TOKEN_BUDGET_WARN}\n'
                f'角色: {user.get("role_name", "游客")} (等级 {user.get("role_level", 1)})\n'
                f'部门: {user.get("department_name", "无")}'
            ),
            'session_id': session_id,
            'server_ip': get_server_ip(),
        }

    # /plan — 计划模式说明
    if cmd == '/plan':
        return {
            'success': True,
            'reply': (
                '计划模式让你可以先分析需求再设计方案:\n'
                '- 在对话中直接说明你的需求\n'
                '- AI 会使用 EnterPlanMode 进入只读分析模式\n'
                '- 制定方案后使用 ExitPlanMode 恢复完整能力\n'
                '你也可以直接说"进入计划模式"来启动。'
            ),
            'session_id': session_id,
            'server_ip': get_server_ip(),
        }

    # /version
    if cmd == '/version':
        return {
            'success': True,
            'reply': 'AI Agent 服务器 v2.0\n基于 AgentLoop + Tool Pipeline + RBAC',
            'session_id': session_id,
            'server_ip': get_server_ip(),
        }

    # 未知命令 — 交给 AI 处理
    return None


def load_config():
    """从 .env 加载 LLM 配置，回退到 config.json"""
    providers = []

    for i in range(1, 10):
        name = os.environ.get(f'PROVIDER{i}_NAME')
        if not name:
            break
        api_url = os.environ.get(f'PROVIDER{i}_API_URL', '')
        api_key = os.environ.get(f'PROVIDER{i}_API_KEY', '')
        if not api_url or not api_key:
            logger.warning('PROVIDER%d 配置不完整，已跳过', i)
            continue
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

    # 禁用 DeepSeek thinking/reasoning 模式以加速响应
    if os.environ.get('DISABLE_THINKING', '1') == '1':
        request_body["thinking"] = {"type": "disabled"}

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
            reasoning_content = message.get("reasoning_content", "")

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

            return True, content or "", parsed_tool_calls, reasoning_content or ""
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

    def _set_request_id(self):
        _request_local.request_id = str(uuid.uuid4())

    def do_OPTIONS(self):
        self._set_request_id()
        self.send_response(204)
        self._set_cors_headers()
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_GET(self):
        self._set_request_id()
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
                self.send_header('Content-Length', '0')
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
        self._set_request_id()
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
            if content_length > MAX_REQUEST_SIZE:
                self._send_json(413, {'success': False, 'error': '请求体过大'})
                return
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
            user = self._get_user_from_cookie()
            if user is None:
                self._send_json(401, {'success': False, 'error': '请先登录'})
                return
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
            op_id = user['id']
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length > 0:
                body = self.rfile.read(content_length).decode('utf-8')
                try:
                    data = json.loads(body)
                except json.JSONDecodeError:
                    self._send_json(400, {'success': False, 'error': '请求格式错误'})
                    return
            else:
                data = {}

            if parsed.path == '/api/admin/departments':
                try:
                    dept = auth_module.create_department(data.get('name', ''))
                    self._send_json(200, {'success': True, 'department': dept})
                except ValueError as e:
                    self._send_json(400, {'success': False, 'error': str(e)})
            elif parsed.path == '/api/admin/users/reset-password':
                try:
                    auth_module.reset_user_password(data['user_id'], data['new_password'], operator_id=op_id)
                    self._send_json(200, {'success': True, 'message': '密码已重置'})
                except (ValueError, KeyError) as e:
                    self._send_json(400, {'success': False, 'error': str(e)})
            elif parsed.path.startswith('/api/admin/users/') and parsed.path.endswith('/role'):
                user_id = int(parsed.path.split('/')[-2])
                try:
                    auth_module.update_user_role(
                        user_id,
                        data.get('role', 'guest'),
                        data.get('department_id'),
                        operator_id=op_id
                    )
                    if 'is_active' in data:
                        auth_module.toggle_user_active(user_id, data['is_active'], operator_id=op_id)
                    self._send_json(200, {'success': True, 'message': '用户已更新'})
                except ValueError as e:
                    self._send_json(400, {'success': False, 'error': str(e)})
            elif parsed.path.startswith('/api/admin/users/') and parsed.path.endswith('/delete'):
                user_id = int(parsed.path.split('/')[-2])
                try:
                    auth_module.delete_user(user_id, operator_id=op_id)
                    self._send_json(200, {'success': True, 'message': '用户已删除'})
                except Exception as e:
                    self._send_json(400, {'success': False, 'error': str(e)})
            elif parsed.path.startswith('/api/admin/departments/') and parsed.path.endswith('/update'):
                dept_id = int(parsed.path.split('/')[-2])
                try:
                    auth_module.update_department(dept_id, data.get('name', ''), operator_id=op_id)
                    self._send_json(200, {'success': True, 'message': '部门已更新'})
                except ValueError as e:
                    self._send_json(400, {'success': False, 'error': str(e)})
            elif parsed.path.startswith('/api/admin/departments/') and parsed.path.endswith('/delete'):
                dept_id = int(parsed.path.split('/')[-2])
                try:
                    auth_module.delete_department(dept_id, operator_id=op_id)
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

        # 斜杠命令处理
        slash_result = _handle_slash_command(user_text, session_id, history, user)
        if slash_result is not None:
            return slash_result

        # 快速通道：对简单事实性问题直接查数据库，秒级响应
        fast_result = _fast_query(user_text, user)
        if fast_result is not None:
            reply_text = fast_result.get('reply', '')
            # 保存到历史
            history.append({"role": "user", "content": user_text})
            history.append({"role": "assistant", "content": reply_text})
            if len(history) > MAX_HISTORY * 2:
                del history[:2]
            tools.save_session(session_id, history, SESSION_TTL)
            fast_result['session_id'] = session_id
            fast_result['server_ip'] = get_server_ip()
            return fast_result

        # 构建消息（system + history + user）
        system_prompt = _build_system_prompt(user)
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_text})

        logger.info('开始 Agent 对话 (历史 %d 条) ...', len(history))

        # 使用 AgentLoop 替代固定轮次 for 循环
        # 工具列表由 AgentLoop 的 ToolRegistry 按角色自动过滤（包含策略工具）
        agent_loop = _create_agent_loop(user)
        result = agent_loop.run(
            messages, user=user, session_id=session_id,
            model_name=model_name,
        )

        if not result['success']:
            return {
                'success': False,
                'reply': result.get('reply', '大模型调用失败'),
                'session_id': session_id,
                'server_ip': get_server_ip(),
            }

        reply_text = result['reply']
        tool_call_history = result.get('tool_calls', [])
        logger.info('Agent 最终回复 (轮次 %d): %s...',
                    result.get('turn_count', 0), reply_text[:80])

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
        user = self._get_user_from_cookie()
        tools.set_file_context(user)
        try:
            full_path = os.path.join(tools.FILES_DIR, file_path)
            resolved = os.path.realpath(full_path)
            allowed = tools._get_allowed_roots()
            ok = False
            for root in allowed:
                if resolved.startswith(root + os.sep) or resolved == root:
                    ok = True
                    break
            if not ok:
                self._send_json(403, {'success': False, 'error': '禁止访问'})
                return
        finally:
            tools.clear_file_context()

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
            file_size = os.path.getsize(resolved)
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

            # SVG 文件强制下载，防止 XSS
            if content_type == 'image/svg+xml':
                content_type = 'application/octet-stream'

            # 如果是图片，直接返回 inline（SVG 除外）
            if content_type.startswith('image/'):
                content_disposition = 'inline'
            else:
                safe_filename = os.path.basename(file_path).replace('"', '').replace('\\', '').replace('\r', '').replace('\n', '')
                content_disposition = f'attachment; filename="{safe_filename}"'

            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(file_size))
            self.send_header('Content-Disposition', content_disposition)
            self.send_header('Cache-Control', 'public, max-age=3600')
            self._set_security_headers()
            self._set_cors_headers()
            self.end_headers()
            # 流式发送大文件
            with open(resolved, 'rb') as f:
                chunk = 64 * 1024
                while True:
                    data = f.read(chunk)
                    if not data:
                        break
                    self.wfile.write(data)
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

    # 登录频率限制
    _login_attempts = {}  # {ip: [(timestamp, ...)]}
    _login_lock = threading.Lock()

    def _handle_login(self):
        client_ip = self.client_address[0]
        now = time.time()

        # 线程安全地检查并记录登录尝试
        with self._login_lock:
            if client_ip in self._login_attempts:
                self._login_attempts[client_ip] = [
                    t for t in self._login_attempts[client_ip] if now - t < 60
                ]
            attempts = self._login_attempts.get(client_ip, [])
            if len(attempts) >= 5:
                self._send_json(429, {'success': False, 'error': '登录过于频繁，请稍后再试'})
                return
            attempts.append(now)
            self._login_attempts[client_ip] = attempts

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
                f'session_token={token}; Path=/; HttpOnly; Secure; Max-Age={ttl}; SameSite=Strict')
            self._set_cors_headers()
            body = json.dumps({'success': True, 'user': user}).encode('utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except ValueError as e:
            self._send_json(401, {'success': False, 'error': str(e)})

    def _handle_logout(self):
        user = self._get_user_from_cookie()
        cookie_header = self.headers.get('Cookie', '')
        for part in cookie_header.split(';'):
            part = part.strip()
            if part.startswith('session_token='):
                token = part[len('session_token='):]
                auth_module.logout_session(token, user_id=user['id'] if user else None)
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
        self._set_security_headers()
        self._set_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _set_cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', CORS_ORIGIN)
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Subfolder')
        self.send_header('Access-Control-Allow-Credentials', 'true')

    def _set_security_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('X-XSS-Protection', '1; mode=block')
        self.send_header('Referrer-Policy', 'strict-origin-when-cross-origin')

    def _get_user_from_cookie(self):
        """从 Cookie 中获取当前登录用户，未登录返回 None（请求级缓存）"""
        cached = getattr(self, '_cached_user', None)
        if cached is not None and cached is not False:
            return cached
        if cached is False:
            return None

        cookie_header = self.headers.get('Cookie', '')
        session_token = None
        for part in cookie_header.split(';'):
            part = part.strip()
            if part.startswith('session_token='):
                session_token = part[len('session_token='):]
                break
        if not session_token:
            self._cached_user = False
            return None
        user = auth_module.get_user_by_token(session_token)
        if user is None:
            self._cached_user = False
        else:
            self._cached_user = user
        return user

    def _require_auth(self):
        """要求登录，未登录返回 302"""
        user = self._get_user_from_cookie()
        if user is None:
            self.send_response(302)
            self.send_header('Location', '/login')
            self.send_header('Content-Length', '0')
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
    logger.info('应用数据库: %s', tools.DB_PATH)
    logger.info('用户数据库: %s', tools.USERS_DB_PATH)
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

    def _shutdown(signum, frame):
        logger.info('收到信号 %s，正在关闭服务器...', signum)
        server.shutdown()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        logger.info('服务器已关闭。')


if __name__ == '__main__':
    main()