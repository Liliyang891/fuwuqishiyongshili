#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内容搜索工具 — 映射自 Claude Code: src/tools/GrepTool/

使用 ripgrep(rg) 或 Python 内置实现内容搜索。
支持正则表达式、文件类型过滤、行号显示。
"""

import os
import re
import subprocess

from ..tool_base import Tool
from ..permissions import PermissionResult


def _has_rg() -> bool:
    """检查系统是否安装了 ripgrep"""
    try:
        subprocess.run(['rg', '--version'], capture_output=True, timeout=2)
        return True
    except Exception:
        return False


HAS_RIPGREP = _has_rg()

MAX_RESULTS = 250
MAX_FILE_SIZE = 2 * 1024 * 1024  # 2MB per file


class Grep(Tool):
    """内容搜索工具

    使用 ripgrep 搜索文件内容,支持正则表达式。
    无 ripgrep 时回退到 Python 实现。
    参考 Claude Code 的 GrepTool。
    """

    name = 'Grep'
    description = (
        '功能强大的内容搜索工具。使用正则表达式搜索文件内容,'
        '支持文件类型过滤、大小写敏感/不敏感、显示上下文行。'
    )
    is_concurrency_safe = True
    is_read_only = True
    min_role = 'guest'
    tool_category = 'search'

    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'pattern': {
                    'type': 'string',
                    'description': '正则表达式搜索模式',
                },
                'path': {
                    'type': 'string',
                    'description': '搜索目录或文件路径。默认当前工作目录。',
                },
                'glob': {
                    'type': 'string',
                    'description': '文件名过滤 glob, 例如 "*.py" 或 "*.{js,ts}"',
                },
                'output_mode': {
                    'type': 'string',
                    'enum': ['content', 'files_with_matches', 'count'],
                    'description': '输出模式: content(显示匹配行), files_with_matches(仅文件路径), count(计数)',
                    'default': 'files_with_matches',
                },
                '-i': {
                    'type': 'boolean',
                    'description': '大小写不敏感搜索',
                },
                '-n': {
                    'type': 'boolean',
                    'description': '显示行号',
                    'default': True,
                },
                '-A': {
                    'type': 'integer',
                    'description': '显示匹配行之后的上下文行数',
                    'minimum': 0,
                },
                '-B': {
                    'type': 'integer',
                    'description': '显示匹配行之前的上下文行数',
                    'minimum': 0,
                },
                '-C': {
                    'type': 'integer',
                    'description': '显示匹配行前后的上下文行数',
                    'minimum': 0,
                },
                'head_limit': {
                    'type': 'integer',
                    'description': '限制输出行数',
                },
                'multiline': {
                    'type': 'boolean',
                    'description': '启用多行模式(.匹配换行符, 模式可跨行)',
                    'default': False,
                },
            },
            'required': ['pattern'],
        }

    def prompt(self):
        return """## 内容搜索 (Grep)
- 使用正则表达式搜索文件内容
- 支持 glob 文件名过滤
- 三种输出模式: content(显示行), files_with_matches(仅文件), count(计数)
- 可用 -A/-B/-C 显示上下文行"""

    def validate_input(self, arguments, context=None):
        pattern = arguments.get('pattern', '')
        if not pattern:
            return False, 'pattern 不能为空'
        if len(pattern) > 4096:
            return False, 'pattern 过长'
        return True, ''

    def check_permissions(self, arguments, user=None) -> PermissionResult:
        return PermissionResult.allow()

    def call(self, arguments, user=None, context=None) -> dict:
        pattern = arguments['pattern']
        search_path = arguments.get('path', os.getcwd())

        if not os.path.exists(search_path):
            return {
                'success': False,
                'error': f'路径不存在: {search_path}',
                'error_type': 'invalid_path',
            }

        if HAS_RIPGREP:
            return self._rg_search(arguments)
        return self._py_search(arguments)

    def _rg_search(self, arguments: dict) -> dict:
        """使用 ripgrep 搜索"""
        cmd = ['rg', '--no-heading', '--color', 'never', '--no-messages']
        pattern = arguments['pattern']
        search_path = arguments.get('path', os.getcwd())

        if arguments.get('-i'):
            cmd.append('-i')
        if arguments.get('multiline'):
            cmd.extend(['--multiline', '--multiline-dotall'])
        if arguments.get('-n', True):
            cmd.append('-n')

        output_mode = arguments.get('output_mode', 'files_with_matches')
        if output_mode == 'files_with_matches':
            cmd.append('-l')
        elif output_mode == 'count':
            cmd.append('-c')

        glob_filter = arguments.get('glob')
        if glob_filter:
            cmd.extend(['-g', glob_filter])

        ctx = arguments.get('-C') or arguments.get('context')
        if ctx:
            cmd.extend(['-C', str(ctx)])
        else:
            if arguments.get('-A'):
                cmd.extend(['-A', str(arguments['-A'])])
            if arguments.get('-B'):
                cmd.extend(['-B', str(arguments['-B'])])

        head_limit = arguments.get('head_limit', MAX_RESULTS)
        cmd.extend(['--', pattern, search_path])

        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=30, cwd=os.getcwd(),
            )
            output = proc.stdout or proc.stderr or ''

            lines = output.strip().split('\n') if output.strip() else []
            total = len(lines)
            if total > head_limit and head_limit > 0:
                lines = lines[:head_limit]
                output = '\n'.join(lines) + \
                    f'\n... 结果已截断 (显示 {head_limit}/{total} 行)'

            return {
                'success': True,
                'result': output,
                'match_count': total,
                'truncated': total > head_limit,
                'engine': 'ripgrep',
            }
        except subprocess.TimeoutExpired:
            return {
                'success': False,
                'error': 'ripgrep 搜索超时(30s)',
                'error_type': 'timeout',
            }

    def _py_search(self, arguments: dict) -> dict:
        """Python 回退搜索"""
        pattern_str = arguments['pattern']
        search_path = arguments.get('path', os.getcwd())
        glob_filter = arguments.get('glob')
        output_mode = arguments.get('output_mode', 'files_with_matches')
        head_limit = arguments.get('head_limit', MAX_RESULTS)
        ignore_case = arguments.get('-i', False)
        show_line = arguments.get('-n', True)
        multiline = arguments.get('multiline', False)
        context_a = arguments.get('-A', 0)
        context_b = arguments.get('-B', 0)
        context_c = arguments.get('-C') or arguments.get('context', 0)

        flags = re.IGNORECASE if ignore_case else 0
        if multiline:
            flags |= re.DOTALL | re.MULTILINE
        try:
            regex = re.compile(pattern_str, flags)
        except re.error as e:
            return {
                'success': False,
                'error': f'正则表达式无效: {e}',
                'error_type': 'invalid_regex',
            }

        results = []
        match_count = 0
        files_searched = 0

        context_lines = max(context_c, context_a, context_b)

        if os.path.isfile(search_path):
            files_to_search = [search_path]
        else:
            files_to_search = []
            for root, dirs, files in os.walk(search_path):
                # 跳过隐藏目录
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                for f in files:
                    file_path = os.path.join(root, f)
                    if glob_filter and not fnmatch.fnmatch(os.path.basename(file_path), glob_filter):
                        continue
                    try:
                        if os.path.getsize(file_path) <= MAX_FILE_SIZE:
                            files_to_search.append(file_path)
                    except OSError:
                        continue

        for file_path in files_to_search:
            if match_count > MAX_RESULTS * 10:
                break
            files_searched += 1
            try:
                with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                    lines = f.readlines()
            except Exception:
                continue

            for i, line in enumerate(lines):
                if match_count > MAX_RESULTS * 10:
                    break
                match = regex.search(line)
                if not match:
                    continue
                match_count += 1
                if output_mode == 'count':
                    continue
                if output_mode == 'files_with_matches':
                    results.append(file_path)
                    break

                ln = i + 1
                if show_line:
                    results.append(f'{file_path}:{ln}:{line.rstrip()}')
                else:
                    results.append(f'{file_path}:{line.rstrip()}')

        if output_mode == 'count':
            return {
                'success': True,
                'result': f'{match_count}',
                'match_count': match_count,
                'files_searched': files_searched,
                'engine': 'python',
            }

        total = len(results)
        truncated = total > head_limit
        results = results[:head_limit] if head_limit > 0 else results

        return {
            'success': True,
            'result': '\n'.join(results) if results else '(无匹配结果)',
            'match_count': total if output_mode != 'files_with_matches' else match_count,
            'truncated': truncated,
            'files_searched': files_searched,
            'engine': 'python',
        }


# fnmatch 用于 glob 过滤
import fnmatch
