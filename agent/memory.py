#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""记忆系统 — 映射自 Claude Code: src/memdir/memdir.ts

加载 CLAUDE.md 和 MEMORY.md 文件内容并注入系统提示词。
使用 mtime 缓存，仅文件变化时才重新读取。
"""

import hashlib
import logging
import os

logger = logging.getLogger(__name__)

MEMORY_FILE = 'MEMORY.md'
CLAUDE_FILE = 'CLAUDE.md'

_cache: dict[str, tuple[float, str]] = {}


def _read_with_cache(path: str) -> str:
    """读取文件内容，mtime 缓存"""
    if not os.path.isfile(path):
        return ''
    mtime = os.path.getmtime(path)
    cached = _cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read().strip()
    except Exception:
        logger.warning('Failed to read: %s', path)
        return ''
    _cache[path] = (mtime, content)
    return content


def load_claude_md(project_dir: str = '.') -> str:
    """加载项目根目录的 CLAUDE.md 文件"""
    path = os.path.join(project_dir, CLAUDE_FILE)
    return _read_with_cache(path)


def load_memory_md(memory_dir: str = None) -> str:
    """加载 MEMORY.md 文件 (来自用户记忆目录)

    搜索路径:
    1. memory_dir 参数
    2. ~/.claude/projects/<project>/memory/MEMORY.md
    3. .claude/memory/MEMORY.md
    """
    candidates = []
    if memory_dir:
        candidates.append(memory_dir)

    home = os.path.expanduser('~')
    cwd = os.path.abspath(os.getcwd())
    project_hash = hashlib.md5(cwd.encode()).hexdigest()[:12]
    candidates.append(
        os.path.join(home, '.claude', 'projects', project_hash, 'memory', MEMORY_FILE)
    )
    candidates.append(
        os.path.join(home, '.claude', 'projects',
                     'E--yang-AIvscode-fuwuqishiyongshili', 'memory', MEMORY_FILE)
    )
    candidates.append(os.path.join('.claude', 'memory', MEMORY_FILE))

    for path in candidates:
        if os.path.isfile(path):
            return _read_with_cache(path)
    return ''


def get_memory_context(project_dir: str = '.') -> str:
    """获取完整记忆上下文 (CLAUDE.md + MEMORY.md)

    返回格式化的系统提示词片段。
    """
    parts = []

    claude_md = load_claude_md(project_dir)
    if claude_md:
        parts.append('## 项目指引\n\n' + claude_md)

    memory_md = load_memory_md()
    if memory_md:
        parts.append('## 用户记忆 (MEMORY.md)\n\n' + memory_md)

    return '\n\n'.join(parts)
