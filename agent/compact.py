#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上下文压缩 — 映射自 Claude Code: src/services/compact/compact.ts

当对话历史超过 token 预算时, 自动压缩为摘要。
"""

import json
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# 压缩阈值
COMPACT_WARN_RATIO = 0.8
COMPACT_TARGET_RATIO = 0.5


def estimate_tokens(messages: list) -> int:
    """估算消息列表的 token 数 (粗略: 1 token ≈ 4 chars)"""
    total = 0
    for m in messages:
        if isinstance(m, dict):
            total += len(json.dumps(m, ensure_ascii=False))
        else:
            total += len(str(m))
    return int(total * 0.25)


def compact_messages(messages: list, budget: int,
                     keep_last: int = 4) -> tuple[list, dict]:
    """压缩消息历史

    保留:
    - system 消息 (保持不变)
    - 最后 keep_last 条消息 (最近上下文)

    中间部分替换为压缩摘要。

    返回: (compacted_messages, compact_info)
    """
    if not messages:
        return messages, {'compacted': False}

    # 估算当前 token
    current = estimate_tokens(messages)
    if current <= budget * COMPACT_WARN_RATIO:
        return messages, {
            'compacted': False,
            'current_tokens': current,
            'budget': budget,
        }

    logger.info('Compacting messages: %d tokens > %d budget (%.0f%%)',
                current, budget, 100 * current / budget)

    # 分离 system 消息
    system_msgs = [m for m in messages if m.get('role') == 'system']
    non_system = [m for m in messages if m.get('role') != 'system']

    if len(non_system) <= keep_last:
        return messages, {'compacted': False, 'current_tokens': current, 'budget': budget}

    # 提取要压缩的中间消息
    to_compact = non_system[:-keep_last]
    recent = non_system[-keep_last:]

    # 构建摘要
    summary = _build_summary(to_compact)
    compacted_count = len(to_compact)

    # 重建消息列表
    compacted = list(system_msgs)
    compacted.append({
        'role': 'system',
        'content': summary,
    })
    compacted.extend(recent)

    new_tokens = estimate_tokens(compacted)
    logger.info('Compacted %d messages → %d messages (%d → %d tokens)',
                compacted_count, len(compacted), current, new_tokens)

    return compacted, {
        'compacted': True,
        'compacted_count': compacted_count,
        'original_tokens': current,
        'new_tokens': new_tokens,
        'budget': budget,
        'summary_length': len(summary),
    }


def _build_summary(messages: list) -> str:
    """从一组消息构建摘要"""

    # 提取关键信息
    user_messages = []
    tool_results = []
    assistant_replies = []

    for m in messages:
        role = m.get('role', '')
        content = m.get('content', '') or ''

        if role == 'user':
            if content:
                user_messages.append(content[:200])
        elif role == 'assistant':
            if content:
                assistant_replies.append(content[:300])
        elif role == 'tool':
            try:
                result = json.loads(content) if isinstance(content, str) else content
                rtext = str(result.get('result', result.get('error', '')))[:150]
                tool_results.append(rtext)
            except Exception:
                tool_results.append(str(content)[:150])

    lines = [
        f'[上下文压缩于 {time.strftime("%Y-%m-%d %H:%M:%S")}]',
        '',
        '## 之前的对话摘要',
        '',
        f'用户发出了 {len(user_messages)} 条消息。',
    ]

    if tool_results:
        lines.append(f'执行了 {len(tool_results)} 个工具操作。')
        lines.append('工具执行结果摘要:')
        for i, r in enumerate(tool_results[:5]):
            lines.append(f'  - {r[:100]}')
        if len(tool_results) > 5:
            lines.append(f'  ... 及其他 {len(tool_results) - 5} 个结果')

    if user_messages:
        lines.append('')
        lines.append('用户消息:')
        for i, msg in enumerate(user_messages):
            lines.append(f'  {i + 1}. {msg[:150]}')

    lines.append('')
    lines.append('上面对话已压缩。请基于摘要和最新消息继续协助用户。')

    return '\n'.join(lines)
