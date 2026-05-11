#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""精确文件编辑工具 — 映射自 Claude Code: src/tools/FileEditTool/

与现有 replace_text 工具的区别:
- 要求 old_string 在文件中唯一匹配(除非 replace_all=True)
- 强制要求先 Read 文件(读前校验)
- 检测文件在外部被修改
"""

import os
import time

from ..tool_base import Tool
from ..permissions import PermissionResult


class Edit(Tool):
    """精确字符串匹配文件编辑

    执行精确的字符串替换: old_string → new_string。
    默认为首次匹配且要求唯一匹配(除非 replace_all=True)。
    要求先使用 Read 工具读取文件。
    """

    name = 'Edit'
    description = (
        '精确编辑文件: 将 old_string 替换为 new_string。'
        '默认要求 old_string 在文件中唯一匹配。'
        '使用 replace_all=true 替换所有匹配项。'
        '必须先读取文件后使用。'
    )
    is_concurrency_safe = False
    is_read_only = False
    min_role = 'staff'
    tool_category = 'file'

    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'file_path': {
                    'type': 'string',
                    'description': '要编辑的文件的绝对路径',
                },
                'old_string': {
                    'type': 'string',
                    'description': '要替换的文本,必须在文件中精确唯一匹配(除非 replace_all=True)',
                },
                'new_string': {
                    'type': 'string',
                    'description': '替换后的新文本',
                },
                'replace_all': {
                    'type': 'boolean',
                    'description': '设为 true 替换所有匹配项(不要求唯一)', 'default': False,
                },
            },
            'required': ['file_path', 'old_string', 'new_string'],
        }

    def prompt(self):
        return """## 编辑文件 (Edit)
- 执行精确的字符串替换: old_string → new_string
- 默认要求 old_string 在文件中**唯一匹配**
- 使用 replace_all=True 替换所有匹配项
- **必须先使用 Read 工具读取文件后再编辑**
- old_string 必须与文件内容精确匹配（包括空格、缩进、换行）"""

    def validate_input(self, arguments, context=None):
        file_path = arguments.get('file_path', '')
        if not file_path:
            return False, 'file_path 不能为空'
        if not os.path.isabs(file_path):
            return False, 'file_path 必须是绝对路径'
        if not os.path.exists(file_path):
            return False, f'文件不存在: {file_path}'

        old = arguments.get('old_string', '')
        new = arguments.get('new_string', '')
        if old == new:
            return False, 'old_string 和 new_string 不能相同'

        # 读前校验 — 必须先在 read_file_state 中有记录
        read_state = (context or {}).get('read_file_state', {})
        if file_path not in read_state:
            return False, f'请先使用 Read 工具读取文件 {file_path} 后再编辑'
        # 检查文件被外部修改
        try:
            current_mtime = os.path.getmtime(file_path)
            record = read_state[file_path]
            if isinstance(record, dict) and current_mtime > record.get('timestamp', 0):
                return False, (
                    f'文件 {file_path} 自上次读取后可能已被外部修改。'
                    '请重新读取文件后再编辑。'
                )
        except OSError:
            return False, f'无法获取文件状态: {file_path}'

        return True, ''

    def check_permissions(self, arguments, user=None) -> PermissionResult:
        file_path = arguments.get('file_path', '')
        if user:
            from auth import get_file_scope
            allowed_roots = get_file_scope(user)
            abs_path = os.path.realpath(file_path)
            ok = False
            for root in allowed_roots:
                if abs_path.startswith(root + os.sep) or abs_path == root:
                    ok = True
                    break
            if not ok:
                role = user.get('role_name', user.get('role', 'guest'))
                return PermissionResult.deny(
                    f'{role} 不能在路径 "{file_path}" 编辑文件（权限不足或目录不存在）')
        return PermissionResult.allow()

    def call(self, arguments, user=None, context=None) -> dict:
        file_path = arguments['file_path']
        old_string = arguments['old_string']
        new_string = arguments['new_string']
        replace_all = arguments.get('replace_all', False)

        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        count = content.count(old_string)
        if count == 0:
            return {
                'success': False,
                'error': 'old_string 在文件中未找到。请检查拼写、缩进和换行。',
                'error_type': 'not_found',
            }
        if count > 1 and not replace_all:
            return {
                'success': False,
                'error': f'old_string 匹配了 {count} 处。请提供更多上下文使匹配唯一，或设置 replace_all=true。',
                'error_type': 'ambiguous_match',
                'match_count': count,
            }

        new_content = content.replace(old_string, new_string) if replace_all \
            else content.replace(old_string, new_string, 1)

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)

        # 更新读前状态
        if context and 'read_file_state' in context:
            context['read_file_state'][file_path] = {
                'read_at': time.time(),
                'timestamp': os.path.getmtime(file_path),
            }

        return {
            'success': True,
            'result': f'文件已编辑: {os.path.basename(file_path)}\n'
                      f'替换了 {count if replace_all else 1} 处匹配。',
            'match_count': count if replace_all else 1,
        }
