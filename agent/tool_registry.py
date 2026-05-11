#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工具注册中心 — 映射自 Claude Code: src/tools.ts getTools()

负责:
- 注册所有 Tool 实例
- 按角色过滤工具定义(发送给 LLM)
- 执行工具流水线 (validate → permissions → call)
- 分区只读/写入工具 (用于并发编排)
"""

import logging
from typing import Optional

from .tool_base import Tool
from .permissions import PermissionResult

logger = logging.getLogger(__name__)


class ToolRegistry:
    """中央工具注册表"""

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._categories: dict[str, list[str]] = {}
        # 按角色等级注册的最低角色要求
        self._role_requirements: dict[str, str] = {}

    def register(self, tool: Tool, category: str = 'misc',
                 min_role: str = 'guest'):
        """注册一个工具"""
        self._tools[tool.name] = tool
        self._categories.setdefault(category, []).append(tool.name)
        self._role_requirements[tool.name] = min_role
        logger.debug('Registered tool: %s (category=%s, min_role=%s)',
                     tool.name, category, min_role)

    def register_many(self, tools: list, default_category: str = 'misc',
                      default_min_role: str = 'guest'):
        """批量注册工具"""
        for t in tools:
            cat = getattr(t, 'tool_category', default_category)
            role = getattr(t, 'min_role', default_min_role)
            self.register(t, cat, role)

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def list_all(self) -> list[str]:
        return list(self._tools.keys())

    def list_by_category(self) -> dict[str, list[str]]:
        return dict(self._categories)

    def get_read_only_tools(self) -> list[str]:
        """只读工具列表 — 可并发执行"""
        return [n for n, t in self._tools.items() if t.is_read_only]

    def get_write_tools(self) -> list[str]:
        """写入工具列表 — 必须串行"""
        return [n for n, t in self._tools.items() if not t.is_read_only]

    def get_concurrency_safe_tools(self) -> list[str]:
        """并发安全工具列表"""
        return [n for n, t in self._tools.items() if t.is_concurrency_safe]

    # ── 获取发送给 LLM 的工具定义 ──

    def get_all_definitions(self) -> list[dict]:
        """返回所有工具的 OpenAI 定义"""
        return [t.get_function_definition() for t in self._tools.values()]

    def get_allowed_definitions(self, user: Optional[dict] = None,
                                role_levels: dict = None) -> list[dict]:
        """按用户角色过滤的工具定义列表(发送给 LLM)

        与 auth.py 的 get_allowed_tools() 功能相同,但基于 Tool 实例"""
        if not user or not role_levels:
            return self.get_all_definitions()

        user_level = user.get('role_level', 0)
        allowed = []
        for tool in self._tools.values():
            min_role = self._role_requirements.get(tool.name, 'guest')
            min_level = role_levels.get(min_role, 0)
            if user_level >= min_level:
                allowed.append(tool.get_function_definition())
        return allowed

    def get_definitions_for_mode(self, plan_mode: bool = False,
                                 user: Optional[dict] = None,
                                 role_levels: dict = None) -> list[dict]:
        """获取当前模式的工具定义

        plan_mode=True 时只返回只读工具"""
        if plan_mode:
            return [t.get_function_definition()
                    for t in self._tools.values()
                    if t.is_read_only]
        return self.get_allowed_definitions(user, role_levels)

    # ── 执行流水线 ──

    def execute(self, name: str, arguments: dict,
                user: Optional[dict] = None,
                context: Optional[dict] = None) -> dict:
        """完整执行流水线: validate → permissions → call

        返回统一结构:
            {'success': bool, 'result': ..., 'tool_name': str, 'duration_ms': int}
        """
        tool = self._tools.get(name)
        if not tool:
            return {
                'success': False,
                'error': f'未知工具: {name}',
                'error_type': 'not_found',
                'tool_name': name,
                'duration_ms': 0,
            }
        # 设置文件操作上下文，使 tools.py 的 _resolve_path 感知用户
        try:
            from tools import set_file_context, clear_file_context
            set_file_context(user)
            return tool.execute(arguments, user=user, context=context)
        finally:
            clear_file_context()

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __repr__(self) -> str:
        cats = {c: len(ns) for c, ns in self._categories.items()}
        return f'<ToolRegistry: {len(self._tools)} tools, {cats}>'
