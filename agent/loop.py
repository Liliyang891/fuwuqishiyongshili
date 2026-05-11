#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent 主循环 — 映射自 Claude Code: src/query.ts (while True loop)
                                 src/services/tools/toolOrchestration.ts (并发编排)

替代原有的 for round_num in range(MAX_TOOL_ROUNDS+1) 硬限制循环。

特性:
- while True 循环,带 token 预算管理
- 工具分区: 只读工具并发执行,写入工具串行执行
- 自动上下文压缩触发
- 可配置的最大轮次安全帽
"""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Optional

from .tool_base import Tool
from .tool_registry import ToolRegistry

logger = logging.getLogger(__name__)

# ── 默认配置 ──
DEFAULT_MAX_TURNS = 100         # 安全帽(防止无限循环)
TOKEN_ESTIMATE_RATIO = 0.25     # 粗略估算: 1 token ≈ 4 字符
TOKEN_BUDGET_WARN = 160_000     # 触发压缩警告
TOKEN_BUDGET_HARD = 200_000     # 硬限制

# 最大并发工具数(映射 Claude Code CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY)
MAX_TOOL_CONCURRENCY = int(
    __import__('os').environ.get('MAX_TOOL_CONCURRENCY', '10')
)


class AgentLoop:
    """AI Agent 主循环

    用法:
        registry = ToolRegistry()
        # ... 注册工具 ...

        loop = AgentLoop(registry, llm_call_fn)
        result = loop.run(messages, user, session_id)
    """

    def __init__(self, registry: ToolRegistry,
                 llm_call_fn: Callable,
                 max_turns: int = DEFAULT_MAX_TURNS,
                 token_budget_warn: int = TOKEN_BUDGET_WARN,
                 token_budget_hard: int = TOKEN_BUDGET_HARD):
        self.registry = registry
        self.llm_call = llm_call_fn  # (messages, model, allowed_tools) -> (ok, text, tool_calls)
        self.max_turns = max_turns
        self.token_budget_warn = token_budget_warn
        self.token_budget_hard = token_budget_hard

        # 运行时状态
        self.turn_count = 0
        self.total_tool_calls = 0
        self.total_tokens_est = 0
        self.plan_mode = False
        self.read_file_state: dict[str, dict] = {}  # Edit 工具的读前校验

    def run(self, messages: list, user: Optional[dict] = None,
            session_id: Optional[str] = None,
            model_name: Optional[str] = None,
            allowed_tools: Optional[list] = None) -> dict:
        """运行 Agent 循环直到完成

        参数:
            messages: 消息历史 (含 system prompt 和 user message)
            user: 当前用户 (用于权限检查)
            session_id: 会话 ID
            model_name: 指定模型
            allowed_tools: 允许的工具定义列表 (None=全部)

        返回:
            {'success': bool, 'reply': str, 'tool_calls': [...], ...}
        """
        self.turn_count = 0
        self.total_tool_calls = 0
        executed_tools: list[dict] = []  # 本轮执行的所有工具记录

        while self.turn_count < self.max_turns:
            self.turn_count += 1

            # ── 1. Token 预算检查 ──
            estimated_tokens = self._estimate_tokens(messages)
            self.total_tokens_est = estimated_tokens

            if estimated_tokens > self.token_budget_hard:
                # 尝试压缩上下文
                try:
                    from .compact import compact_messages
                    messages, info = compact_messages(
                        messages, self.token_budget_hard)
                    if info.get('compacted'):
                        logger.info('Context compacted: %d → %d tokens',
                                    info['original_tokens'], info['new_tokens'])
                        continue  # 用压缩后的消息重新调用 LLM
                except ImportError:
                    pass

                logger.warning('Token budget hard limit reached: %d > %d',
                               estimated_tokens, self.token_budget_hard)
                return {
                    'success': True,
                    'reply': self._compact_notice(estimated_tokens),
                    'session_id': session_id,
                    'tool_calls': executed_tools,
                    'turn_count': self.turn_count,
                    'truncated': True,
                }

            if estimated_tokens > self.token_budget_warn:
                logger.info('Token budget warning: %d > %d',
                            estimated_tokens, self.token_budget_warn)

            # ── 2. 调用 LLM ──
            context = {
                'plan_mode': self.plan_mode,
                'read_file_state': self.read_file_state,
                'session_id': session_id,
                'turn_count': self.turn_count,
                '_registry': self.registry,
            }

            tools_for_llm = allowed_tools
            if tools_for_llm is None:
                tools_for_llm = self.registry.get_definitions_for_mode(
                    plan_mode=self.plan_mode, user=user,
                    role_levels=self._get_role_levels())

            result = self.llm_call(
                messages, model_name, allowed_tools=tools_for_llm)

            # 解包: 支持 3 元组(兼容) 或 4 元组(含 reasoning_content)
            if len(result) >= 4:
                success, content, tool_calls, reasoning_content = result[:4]
            else:
                success, content, tool_calls = result
                reasoning_content = ''

            if not success:
                return {
                    'success': False,
                    'reply': content,
                    'session_id': session_id,
                    'tool_calls': executed_tools,
                    'turn_count': self.turn_count,
                }

            # ── 3. 无工具调用 → 最终回复 ──
            if not tool_calls:
                return {
                    'success': True,
                    'reply': content,
                    'session_id': session_id,
                    'tool_calls': executed_tools,
                    'turn_count': self.turn_count,
                }

            # ── 4. 追加 assistant 消息 (保留 reasoning_content 以兼容 DeepSeek thinking mode) ──
            assistant_msg: dict = {
                'role': 'assistant',
                'content': content or None,
                'tool_calls': [
                    {
                        'id': tc['id'],
                        'type': 'function',
                        'function': {
                            'name': tc['name'],
                            'arguments': json.dumps(tc.get('arguments', {}),
                                                    ensure_ascii=False),
                        },
                    }
                    for tc in tool_calls
                ],
            }
            if reasoning_content:
                assistant_msg['reasoning_content'] = reasoning_content
            messages.append(assistant_msg)

            # ── 5. 分区工具调用 ──
            read_tcs, write_tcs = self._partition_tool_calls(tool_calls)

            # ── 6. 执行只读工具 (并发) ──
            read_results = self._execute_concurrent(read_tcs, user, context)

            # ── 7. 执行写入工具 (串行) ──
            write_results = self._execute_serial(write_tcs, user, context)

            # ── 8. 同步计划模式状态 ──
            for tc_id, result in read_results + write_results:
                if result.get('plan_mode') is not None:
                    self.plan_mode = result['plan_mode']
                    logger.info('Plan mode changed to: %s', self.plan_mode)

            # ── 9. 合并结果 ──
            all_results = []
            # 按原始顺序重组
            tc_map = {}
            for tc, result in read_results + write_results:
                all_results.append({'tool': tc['name'],
                                    'arguments': tc.get('arguments', {}),
                                    'success': result.get('success', False),
                                    'result': result})
                tc_map[tc['id']] = result

            executed_tools.extend(all_results)
            self.total_tool_calls += len(all_results)

            # ── 10. 追加 tool_result 消息 ──
            for tc in tool_calls:
                result = tc_map.get(tc['id'], {
                    'success': False,
                    'error': 'Tool execution skipped',
                })
                messages.append({
                    'role': 'tool',
                    'tool_call_id': tc['id'],
                    'content': json.dumps(result, ensure_ascii=False),
                })

            # ── 11. 继续循环 (LLM 看到 tool_results 后决定下一步) ──

        # 达到最大轮次
        return {
            'success': True,
            'reply': f'已达到最大工具调用轮次 ({self.max_turns})，请简化你的请求。',
            'session_id': session_id,
            'tool_calls': executed_tools,
            'turn_count': self.turn_count,
            'truncated': True,
        }

    # ── 内部方法 ──

    def _partition_tool_calls(self, tool_calls: list) -> tuple:
        """分区工具调用为只读(并发安全)和写入(必须串行)组

        映射自 toolOrchestration.ts partitionToolCalls()"""
        read_tcs, write_tcs = [], []
        for tc in tool_calls:
            tool = self.registry.get(tc['name'])
            if tool and tool.is_concurrency_safe:
                read_tcs.append(tc)
            else:
                write_tcs.append(tc)
        return read_tcs, write_tcs

    def _execute_concurrent(self, tool_calls: list, user: Optional[dict],
                            context: dict) -> list:
        """并发执行只读工具

        使用 ThreadPoolExecutor,最大并发数由 MAX_TOOL_CONCURRENCY 控制"""
        if not tool_calls:
            return []

        results = []
        max_workers = min(len(tool_calls), MAX_TOOL_CONCURRENCY)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for tc in tool_calls:
                future = executor.submit(
                    self.registry.execute,
                    tc['name'],
                    tc.get('arguments', {}),
                    user,
                    context,
                )
                futures[future] = tc

            for future in as_completed(futures):
                tc = futures[future]
                try:
                    result = future.result()
                except Exception as e:
                    result = {
                        'success': False,
                        'error': f'线程执行异常: {e}',
                        'tool_name': tc['name'],
                    }
                results.append((tc, result))

        return results

    def _execute_serial(self, tool_calls: list, user: Optional[dict],
                        context: dict) -> list:
        """串行执行写入工具"""
        results = []
        for tc in tool_calls:
            result = self.registry.execute(
                tc['name'],
                tc.get('arguments', {}),
                user,
                context,
            )
            results.append((tc, result))
        return results

    def _estimate_tokens(self, messages: list) -> int:
        """粗略估算消息的 token 数"""
        total = 0
        for m in messages:
            if isinstance(m, dict):
                total += len(json.dumps(m, ensure_ascii=False))
            else:
                total += len(str(m))
        return int(total * TOKEN_ESTIMATE_RATIO)

    def _compact_notice(self, estimated: int) -> str:
        """上下文已满的通知"""
        return (
            f'对话上下文已满 (估算 {estimated} tokens,上限 {self.token_budget_hard})。\n'
            '已执行的操作结果仍然有效。建议:\n'
            '1. 使用 /clear 清空对话重新开始\n'
            '2. 总结当前进度,在新会话中继续\n'
            '3. 将复杂任务拆分为多个小步骤'
        )

    def _get_role_levels(self) -> dict:
        """获取角色等级映射"""
        from role_levels import ROLE_LEVEL
        return ROLE_LEVEL

    # ── 计划模式 ──

    def enter_plan_mode(self):
        self.plan_mode = True
        logger.info('Entered plan mode')

    def exit_plan_mode(self):
        self.plan_mode = False
        logger.info('Exited plan mode')

    def is_plan_mode(self) -> bool:
        return self.plan_mode

    # ── 读前状态追踪 (Edit 工具使用) ──

    def mark_file_read(self, file_path: str):
        self.read_file_state[file_path] = {
            'read_at': time.time(),
            'timestamp': __import__('os').path.getmtime(file_path),
        }

    def mark_file_written(self, file_path: str):
        """文件写入后更新追踪状态"""
        try:
            self.read_file_state[file_path] = {
                'read_at': time.time(),
                'timestamp': __import__('os').path.getmtime(file_path),
            }
        except OSError:
            self.read_file_state.pop(file_path, None)
