#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""子代理工具 — 映射自 Claude Code: src/tools/AgentTool/

生成独立的子代理: 创建新的 AgentLoop, 受限工具集, 独立消息历史。
支持 foreground(前台) 和 background(后台) 两种模式。
"""

import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Optional

from ..tool_base import Tool
from ..permissions import PermissionResult, PermissionBehavior

logger = logging.getLogger(__name__)

# 子代理不可用的工具 (防止递归)
AGENT_DISALLOWED_TOOLS = {
    'Agent', 'EnterPlanMode', 'ExitPlanMode',
    'TaskCreate', 'TaskUpdate', 'TaskList', 'TaskGet',
}

# 活跃的子代理任务
_active_subagents: dict[str, 'SubAgentTask'] = {}


class SubAgentTask:
    """子代理任务包装"""

    def __init__(self, agent_id: str, description: str = ''):
        self.agent_id = agent_id
        self.description = description
        self.status = 'running'
        self.started_at = time.time()
        self.completed_at: Optional[float] = None
        self.result: Optional[dict] = None
        self._future: Optional[Future] = None

    def set_result(self, result: dict):
        self.result = result
        self.status = 'completed' if result.get('success') else 'failed'
        self.completed_at = time.time()

    def to_dict(self) -> dict:
        return {
            'agent_id': self.agent_id,
            'description': self.description,
            'status': self.status,
            'started_at': self.started_at,
            'completed_at': self.completed_at,
        }


class Agent(Tool):
    """子代理工具

    创建独立的 AgentLoop 实例执行子任务。
    子代理有受限的工具集 (无 AgentTool 本身, 无计划模式工具)。
    """

    name = 'Agent'
    description = (
        '启动子代理处理复杂多步骤任务。'
        '子代理有独立的工具集和消息历史。'
        '支持前台(等待结果)和后台(异步执行)两种模式。'
    )
    is_concurrency_safe = False
    is_read_only = False
    min_role = 'staff'
    tool_category = 'meta'

    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'description': {
                    'type': 'string',
                    'description': '子代理任务描述 (3-5词)',
                },
                'prompt': {
                    'type': 'string',
                    'description': '子代理的完整任务指令',
                },
                'subagent_type': {
                    'type': 'string',
                    'description': '子代理类型: general-purpose, Explore, Plan 等',
                },
                'run_in_background': {
                    'type': 'boolean',
                    'description': '设为 true 在后台异步执行',
                    'default': False,
                },
            },
            'required': ['description', 'prompt'],
        }

    def prompt(self):
        return """## 子代理 (Agent)
- 用于委托独立的复杂任务
- 子代理有受限工具集 (无 Agent/PlanMode 工具)
- 可并发运行多个子代理
- 后台模式: 异步执行, 完成时通知"""

    def validate_input(self, arguments, context=None):
        if not arguments.get('prompt', '').strip():
            return False, 'prompt 不能为空'
        if not arguments.get('description', '').strip():
            return False, 'description 不能为空'
        return True, ''

    def check_permissions(self, arguments, user=None) -> PermissionResult:
        return PermissionResult.allow()

    def call(self, arguments, user=None, context=None) -> dict:
        description = arguments.get('description', 'sub-agent')
        prompt_text = arguments['prompt']
        background = arguments.get('run_in_background', False)
        subagent_type = arguments.get('subagent_type', 'general-purpose')

        agent_id = 'sa_' + str(uuid.uuid4())[:8]
        task = SubAgentTask(agent_id, description)

        if background:
            return self._run_background(agent_id, task, prompt_text,
                                        subagent_type, user, context)
        else:
            return self._run_foreground(agent_id, task, prompt_text,
                                        subagent_type, user, context)

    def _run_foreground(self, agent_id: str, task: SubAgentTask,
                        prompt_text: str, subagent_type: str,
                        user: Optional[dict], context: Optional[dict]) -> dict:
        """前台执行子代理 — 等待完成"""
        result = self._execute_subagent(prompt_text, subagent_type, user, context)
        task.set_result(result)
        return result

    def _run_background(self, agent_id: str, task: SubAgentTask,
                        prompt_text: str, subagent_type: str,
                        user: Optional[dict], context: Optional[dict]) -> dict:
        """后台执行子代理 — 立即返回"""
        _active_subagents[agent_id] = task

        def _bg_run():
            try:
                result = self._execute_subagent(prompt_text, subagent_type, user, context)
                task.set_result(result)
            except Exception as e:
                task.set_result({
                    'success': False,
                    'reply': f'子代理异常: {e}',
                })

        t = threading.Thread(target=_bg_run, daemon=True)
        t.start()
        task._future = None  # Thread 不是 Future, 但保留接口

        return {
            'success': True,
            'result': (
                f'后台子代理已启动 (ID: {agent_id})。\n'
                f'描述: {task.description}\n'
                f'子代理将在后台执行任务。使用 Monitor 工具查看运行状态。'
            ),
            'subagent': task.to_dict(),
            'background': True,
        }

    def _execute_subagent(self, prompt_text: str, subagent_type: str,
                          user: Optional[dict], context: Optional[dict]) -> dict:
        """实际执行子代理"""
        from ..loop import AgentLoop

        # 获取工具注册表 (从调用栈中获取或创建新的)
        try:
            # 尝试从上下文获取 registry
            registry = None
            if context and '_registry' in context:
                registry = context['_registry']
            if not registry:
                from ..tool_registry import ToolRegistry
                from .builtin_tools import register_all
                registry = ToolRegistry()
                register_all(registry)
        except Exception:
            from ..tool_registry import ToolRegistry
            from .builtin_tools import register_all
            registry = ToolRegistry()
            register_all(registry)

        # 构建受限工具列表 (排除子代理工具和计划模式工具)
        allowed_defs = []
        for tool_name in registry.list_all():
            if tool_name in AGENT_DISALLOWED_TOOLS:
                continue
            tool = registry.get(tool_name)
            if tool:
                allowed_defs.append(tool.get_function_definition())

        # Mock LLM 调用 — 子代理实际由父代理的 LLM 调用驱动
        # 这里我们直接使用一个简单的 mock 让子代理返回说明
        return {
            'success': True,
            'reply': (
                f'[子代理 {subagent_type}] 收到任务: {prompt_text[:100]}...\n'
                '子代理工具集已准备就绪 (不含 Agent/PlanMode 工具)。\n'
                '在完整实现中, 这里会调用独立的 LLM 实例。'
            ),
            'tool_calls': [],
            'turn_count': 0,
            'subagent_type': subagent_type,
        }
