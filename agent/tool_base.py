#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tool 抽象基类 — 所有工具的父类

映射自 Claude Code: src/Tool.ts (ToolDef, buildTool)

每个工具必须实现:
- name / description: 元信息
- input_schema(): OpenAI Function Calling JSON Schema
- prompt(): 工具使用说明(注入系统提示词)
- validate_input(args, ctx): 输入校验
- check_permissions(args, user): 权限检查
- call(args, user, ctx): 工具执行
"""

import json
import time
from abc import ABC, abstractmethod
from typing import Any, Optional

from .permissions import PermissionResult


class Tool(ABC):
    """工具抽象基类

    子类设置以下类属性:
        name: str               — 工具名称 (如 'Bash', 'Read')
        description: str        — 简短描述 (显示在权限对话框)
        is_concurrency_safe: bool — True=可与其他只读工具并发执行
        is_read_only: bool      — True=纯读取,无副作用
    """

    name: str = ''
    description: str = ''
    is_concurrency_safe: bool = False
    is_read_only: bool = True

    # ── 抽象方法 (子类必须实现) ──

    @abstractmethod
    def input_schema(self) -> dict:
        """返回 OpenAI Function Calling 的 parameters JSON Schema"""
        ...

    @abstractmethod
    def prompt(self) -> str:
        """工具使用说明,注入到系统提示词中"""
        ...

    @abstractmethod
    def call(self, arguments: dict, user: Optional[dict] = None,
             context: Optional[dict] = None) -> dict:
        """执行工具,返回结果字典 (必须包含 'success' 键)"""
        ...

    # ── 可选覆写 ──

    def validate_input(self, arguments: dict,
                       context: Optional[dict] = None) -> tuple:
        """校验输入参数. 返回 (is_valid: bool, error_message: str)"""
        return True, ''

    def check_permissions(self, arguments: dict,
                          user: Optional[dict] = None) -> tuple:
        """权限检查. 返回 (allowed: bool, denial_reason: str)"""
        return True, ''

    def description_text(self, arguments: dict) -> str:
        """生成操作的简短描述,用于审计日志. 返回空字符串跳过审计."""
        return ''

    def map_result_to_tool_result(self, output: dict,
                                  tool_use_id: str) -> dict:
        """将工具输出转换为 API tool_result 格式"""
        return {
            'role': 'tool',
            'tool_call_id': tool_use_id,
            'content': json.dumps(output, ensure_ascii=False),
        }

    # ── 自动生成的方法 ──

    def get_function_definition(self) -> dict:
        """返回 OpenAI Function Calling 格式的工具定义"""
        return {
            'type': 'function',
            'function': {
                'name': self.name,
                'description': self.prompt(),
                'parameters': self.input_schema(),
            },
        }

    def get_short_description(self) -> str:
        """返回简短描述,用于权限提示等 UI 场景"""
        return self.description or self.name

    # ── 完整执行流水线 ──

    def execute(self, arguments: dict, user: Optional[dict] = None,
                context: Optional[dict] = None) -> dict:
        """完整的验证→权限→执行流水线。返回统一结构."""
        start_time = time.time()

        # 1. 输入校验
        is_valid, err_msg = self.validate_input(arguments, context)
        if not is_valid:
            return {
                'success': False,
                'error': err_msg,
                'error_type': 'validation',
                'tool_name': self.name,
                'duration_ms': int((time.time() - start_time) * 1000),
            }

        # 2. 权限检查 (支持 tuple 和 PermissionResult 两种返回)
        perm = self.check_permissions(arguments, user)
        if isinstance(perm, PermissionResult):
            if not perm.is_allowed:
                return {
                    'success': False,
                    'error': perm.message or '权限不足',
                    'error_type': 'permission',
                    'tool_name': self.name,
                    'duration_ms': int((time.time() - start_time) * 1000),
                }
        else:
            allowed, reason = perm
            if not allowed:
                return {
                    'success': False,
                    'error': reason or '权限不足',
                    'error_type': 'permission',
                    'tool_name': self.name,
                    'duration_ms': int((time.time() - start_time) * 1000),
                }

        # 3. 执行
        try:
            result = self.call(arguments, user=user, context=context)
            result['tool_name'] = self.name
            result['duration_ms'] = int((time.time() - start_time) * 1000)
            return result
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'error_type': 'execution',
                'tool_name': self.name,
                'duration_ms': int((time.time() - start_time) * 1000),
            }

    def __repr__(self) -> str:
        return f'<Tool:{self.name}>'
