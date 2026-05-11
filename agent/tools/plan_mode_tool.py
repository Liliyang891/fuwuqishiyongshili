#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""计划模式工具 — 映射自 Claude Code: src/tools/EnterPlanModeTool/ + ExitPlanModeV2Tool/

计划模式将 Agent 限制为只读操作, 先分析需求再设计方案。
"""

import json
import logging
import os
import time

from ..tool_base import Tool
from ..permissions import PermissionResult

logger = logging.getLogger(__name__)

PLANS_DIR = '.agent/plans'


class EnterPlanMode(Tool):
    """进入计划模式

    限制 Agent 只能使用只读工具, 强制先分析再设计。
    """

    name = 'EnterPlanMode'
    description = (
        '进入计划模式。Agent 将限制为只读操作, 先分析需求再设计方案。'
        '在实现之前获取用户对方案的认可。'
    )
    is_concurrency_safe = False
    is_read_only = False  # 虽不写文件, 但改变 agent 状态
    min_role = 'staff'
    tool_category = 'meta'

    def input_schema(self):
        return {
            'type': 'object',
            'properties': {},
            'required': [],
        }

    def prompt(self):
        return """## 计划模式 (EnterPlanMode / ExitPlanMode)
- 在着手实现前, 使用 EnterPlanMode 进入只读分析模式
- 在计划模式下, 只有读取类工具可用
- 制定方案后使用 ExitPlanMode 退出并恢复完整工具集"""

    def validate_input(self, arguments, context=None):
        return True, ''

    def check_permissions(self, arguments, user=None) -> PermissionResult:
        return PermissionResult.allow()

    def call(self, arguments, user=None, context=None) -> dict:
        # 通知 AgentLoop 进入计划模式
        if context and 'plan_mode' in context:
            context['plan_mode'] = True

        os.makedirs(PLANS_DIR, exist_ok=True)

        return {
            'success': True,
            'result': (
                '已进入计划模式。\n'
                '现在只能使用只读工具（读取文件、搜索、查看等）。\n'
                '请先分析用户需求, 探索代码库, 然后制定实现方案。\n\n'
                '完成计划后, 使用 ExitPlanMode 退出。'
            ),
            'plan_mode': True,
        }


class ExitPlanMode(Tool):
    """退出计划模式

    将计划保存到 .agent/plans/ 目录, 恢复完整工具集。
    """

    name = 'ExitPlanMode'
    description = (
        '退出计划模式, 保存计划文件, 恢复完整工具集。'
        '在制定好实现方案后使用。'
    )
    is_concurrency_safe = False
    is_read_only = False
    min_role = 'staff'
    tool_category = 'meta'

    def input_schema(self):
        return {
            'type': 'object',
            'properties': {},
            'required': [],
        }

    def prompt(self):
        return '退出计划模式, 保存计划到 .agent/plans/, 恢复全部工具。'

    def validate_input(self, arguments, context=None):
        return True, ''

    def check_permissions(self, arguments, user=None) -> PermissionResult:
        return PermissionResult.allow()

    def call(self, arguments, user=None, context=None) -> dict:
        # 通知 AgentLoop 退出计划模式
        if context and 'plan_mode' in context:
            context['plan_mode'] = False

        return {
            'success': True,
            'result': (
                '已退出计划模式。完整工具集已恢复。\n'
                '现在可以按照计划进行实现。'
            ),
            'plan_mode': False,
        }
