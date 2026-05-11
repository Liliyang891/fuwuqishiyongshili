#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""可组合系统提示词 — 映射自 Claude Code: src/constants/prompts.ts

系统提示词由多个 PromptSection 组成,可分为:
- 静态段 (全局缓存): 身份、规则、工具使用指南
- 动态段 (每次重算): 环境信息、当前状态、记忆
"""

import hashlib
import os
import platform
import sys
from datetime import datetime
from typing import Callable, Optional

_ = None  # 未实现占位


class PromptSection:
    """可缓存的提示词段"""

    def __init__(self, name: str, compute_fn: Callable[[], Optional[str]],
                 cache_break: bool = False):
        self.name = name
        self._compute = compute_fn
        self.cache_break = cache_break
        self._cached: Optional[str] = None
        self._cache_key: Optional[str] = None

    def resolve(self, force: bool = False) -> Optional[str]:
        """计算提示词段 (可能使用缓存)"""
        if not force and not self.cache_break and self._cached is not None:
            return self._cached
        value = self._compute()
        self._cached = value
        return value

    def invalidate(self):
        self._cached = None
        self._cache_key = None


def build_system_prompt(sections: list) -> str:
    """拼接所有提示词段,过滤空值"""
    parts = []
    for s in sections:
        if isinstance(s, PromptSection):
            val = s.resolve()
        elif callable(s):
            val = s()
        else:
            val = s
        if val:
            parts.append(str(val).strip())
    return '\n\n'.join(parts)


def invalidate_all_sections(sections: list):
    for s in sections:
        if isinstance(s, PromptSection):
            s.invalidate()


# ═══════════════════════════════════════════
# 预定义提示词段 — 参考 Claude Code prompts.ts
# ═══════════════════════════════════════════

IDENTITY = """你是博爱医药AI智能管理平台助手，运行在 DeepSeek 模型上，专门帮助博爱医药员工完成软件工程和文件系统操作任务。
你可以通过 Function Calling 执行工具: 读写文件、搜索代码、运行 Shell 命令、查询数据库、浏览网页等。
使用中文回复用户,但代码和技术术语保持英文。

**首次对话问候**: 当用户首次进入对话或打招呼时，回复"您好，欢迎登入博爱医药AI智能管理平台，有什么具体要求可以直接和我说。"

**重要身份声明**: 你是博爱医药AI智能管理平台助手，不是 Claude、不是 ChatGPT、不是任何其他 AI 产品。当用户询问你的模型或身份时，明确回答你运行在 DeepSeek 大模型上。"""

SYSTEM_RULES = """## 执行原则
- 偏好编辑现有文件而非创建新文件
- 只做任务要求的改动,不要额外重构或添加功能
- 不要为不可能发生的场景添加错误处理、回退或校验
- 不要写注释解释 WHAT — 好的命名已经说明了一切。只在 WHY 不明确时写注释
- 三个相似行比一个过早抽象好。不写完不完整的实现

## 安全原则
- 操作前评估影响范围和可逆性
- 破坏性操作 (rm -rf, DROP TABLE) 需确认
- 不在未确认的情况下推代码、发消息或修改共享资源"""

TOOL_USAGE = """## 工具使用原则
- **高效优先**: 简单问题直接回答，不要调用工具。例如"现在有多少用户"只需一次 db_query，不要多轮探索。
- **一次性获取**: 需要数据时，在一次工具调用中获取所有需要的信息，不要分多轮逐步查询。
- 优先使用专用工具: Grep 而非 grep/rg, Glob 而非 find/ls, Bash 用于这些工具无法完成的复杂操作
- 独立的工具调用在同一轮中并发发送 (利用 tool_choice=auto)
- Bash 命令默认 120s 超时,最长 600s
- 长任务使用 run_in_background=true 异步执行"""

GIT_RULES = """## Git 安全
- 永远不要推送 --force 到 main/master
- 永远不要修改 git config
- 永远不要跳过 hooks (--no-verify)
- 始终创建新 commit 而非 amend (除非用户明确要求)
- 提交前用户应确认"""


def get_identity_section() -> str:
    return IDENTITY


def get_system_rules_section() -> str:
    return SYSTEM_RULES


def get_tool_usage_section() -> str:
    return TOOL_USAGE


def get_git_rules_section() -> str:
    return GIT_RULES


def get_env_info_section() -> str:
    """环境信息段 — 每次请求动态计算"""
    cwd = os.getcwd()
    hostname = platform.node()
    python_ver = sys.version.split()[0]
    os_info = f'{platform.system()} {platform.release()}'
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    return f"""## 环境信息
- 工作目录: {cwd}
- 操作系统: {os_info}
- Python: {python_ver}
- 主机: {hostname}
- 当前时间: {now}"""


def get_rbac_reminder_section(user: Optional[dict] = None) -> str:
    """RBAC 角色提醒 — 让 LLM 知道当前用户能做什么"""
    if not user:
        return ''
    role = user.get('role_name', user.get('role', 'guest'))
    level = user.get('role_level', 0)
    dept = user.get('department_name', '无')
    user_role = user.get('role', 'guest')

    # 构建可访问的角色目录列表
    role_hierarchy = [
        ('super_admin', 6), ('chairman', 5), ('gm', 4),
        ('dept_head', 3), ('staff', 2), ('guest', 1),
    ]
    accessible = [r for r, l in role_hierarchy if l <= level]

    if level >= 2:
        max_tools = '所有工具' if level >= 6 else '读写+数据库'
        file_scope = (
            f'**文件夹结构**: 你的默认文件夹是 `{user_role}/`，写入文件时路径会自动归入此目录。'
            f'可访问: {", ".join(accessible)} 目录 + share/ 共享目录'
        )
    else:
        max_tools = '只读'
        file_scope = (
            f'**文件夹结构**: 你的默认文件夹是 `{user_role}/`，你只有只读权限。'
            f'可访问: {", ".join(accessible)} 目录（只读）。不能访问 share/ 共享目录'
        )

    return f"""## 当前用户
- 角色: {role} (等级 {level})
- 部门: {dept}
- 可用工具范围: {max_tools}
- {file_scope}"""


def get_session_guidance_section(registry=None) -> str:
    """会话指导 — 工具相关提示"""
    lines = ['## 会话指导']
    if registry:
        tool_count = len(registry._tools)
        read_only = len(registry.get_read_only_tools())
        write = tool_count - read_only
        lines.append(f'- 可用工具: {tool_count} 个 ({read_only} 只读, {write} 写入)')
    lines.append('- 复杂任务先制定计划,再执行')
    lines.append('- 完成后进行验证,确认结果符合预期')
    return '\n'.join(lines)
