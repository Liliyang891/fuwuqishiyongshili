#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI Agent 模块 — 类 Claude Code 架构

核心组件:
- Tool: 工具抽象基类 (validate → check → call 流水线)
- ToolRegistry: 工具注册中心
- PromptSection: 可组合系统提示词
- AgentLoop: while True 主循环 (token 预算, 并发工具编排)
- PlanMode: 计划模式
- TaskManager: 任务追踪
- MemorySystem: CLAUDE.md 记忆系统
- ContextCompactor: 上下文压缩
"""

from .tool_base import Tool
from .tool_registry import ToolRegistry
from .prompt import PromptSection, build_system_prompt
from .permissions import PermissionResult
from .loop import AgentLoop
from .skill_registry import SkillRegistry
from .memory import get_memory_context, load_claude_md
from .compact import compact_messages, estimate_tokens

__all__ = [
    'Tool', 'ToolRegistry', 'PromptSection', 'build_system_prompt',
    'PermissionResult', 'AgentLoop', 'SkillRegistry',
    'get_memory_context', 'load_claude_md',
    'compact_messages', 'estimate_tokens',
]
