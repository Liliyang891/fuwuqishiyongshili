#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文件模式匹配工具 — 映射自 Claude Code: src/tools/GlobTool/

快速的 glob 文件搜索, 按修改时间排序。
"""

import glob as glob_module
import os
import fnmatch

from ..tool_base import Tool
from ..permissions import PermissionResult


class Glob(Tool):
    """快速文件模式匹配

    使用 glob 模式搜索文件, 结果按修改时间降序排列。
    参考 Claude Code 的 GlobTool 实现。
    """

    name = 'Glob'
    description = (
        '快速文件模式匹配工具。使用 glob 模式搜索文件, 结果按修改时间降序排列。'
        '用于按文件名模式查找文件, 例如 "**/*.py" 匹配所有 Python 文件。'
    )
    is_concurrency_safe = True
    is_read_only = True
    min_role = 'guest'
    tool_category = 'search'

    MAX_RESULTS = 500

    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'pattern': {
                    'type': 'string',
                    'description': 'Glob 模式, 例如 "**/*.py" 或 "src/**/*.ts"',
                },
                'path': {
                    'type': 'string',
                    'description': '搜索目录。默认使用当前工作目录。',
                },
            },
            'required': ['pattern'],
        }

    def prompt(self):
        return """## 文件模式匹配 (Glob)
- 使用标准 glob 模式搜索文件
- 结果按修改时间降序排列（最新的文件排前面）
- 支持递归模式: **/*.py"""

    def validate_input(self, arguments, context=None):
        pattern = arguments.get('pattern', '')
        if not pattern:
            return False, 'pattern 不能为空'
        if len(pattern) > 4096:
            return False, 'pattern 过长(最大 4096 字符)'
        return True, ''

    def check_permissions(self, arguments, user=None) -> PermissionResult:
        return PermissionResult.allow()

    def call(self, arguments, user=None, context=None) -> dict:
        pattern = arguments['pattern']
        search_path = arguments.get('path', os.getcwd())

        if not os.path.isdir(search_path):
            return {
                'success': False,
                'error': f'目录不存在: {search_path}',
                'error_type': 'invalid_path',
            }

        try:
            full_pattern = os.path.join(search_path, pattern)
            matches = glob_module.glob(full_pattern, recursive=True)
        except Exception as e:
            return {
                'success': False,
                'error': f'Glob 执行失败: {e}',
                'error_type': 'glob_error',
            }

        # 过滤掉目录, 只保留文件
        files = [m for m in matches if os.path.isfile(m)]

        # 按修改时间排序 (最新的排前面)
        try:
            files.sort(key=lambda f: os.path.getmtime(f), reverse=True)
        except OSError:
            pass

        total = len(files)
        truncated = total > self.MAX_RESULTS
        files = files[:self.MAX_RESULTS]

        return {
            'success': True,
            'result': {
                'files': files,
                'total_matches': total,
                'truncated': truncated,
                'pattern': pattern,
                'search_path': search_path,
            },
        }
