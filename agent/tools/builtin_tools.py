#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""现有 28 个工具的 Tool 子类包装器

将 tools.py 中的函数包装为 Tool 子类,保持原函数行为不变。
每个工具映射到现有的 Python 函数,通过 Tool 接口提供统一的
validate → check_permissions → call 流水线。

分类:
- read:     Guest+ (读取/搜索/列表)
- write:    Staff+ (写入/移动/复制)
- delete:   SuperAdmin only (删除)
- db_read:  Staff+ (查询)
- db_write: DeptHead+ (执行), GM+ (建表), SuperAdmin+ (删表)
"""

import json
import logging

from agent.tool_base import Tool

logger = logging.getLogger(__name__)


# ── 辅助: 权限检查(委托给 auth 模块) ──

def _check_auth(tool_name: str, user: dict, file_path: str = '') -> tuple:
    """调用 auth.can_execute_tool"""
    try:
        import auth
        return auth.can_execute_tool(tool_name, user, file_path)
    except ImportError:
        return True, ''


# ═══════════════════════════════════════════
# 目录操作 (5)
# ═══════════════════════════════════════════

class ListFolderTool(Tool):
    name = 'list_folder'
    description = '列出目录内容'
    is_concurrency_safe = True
    is_read_only = True
    min_role = 'guest'

    def prompt(self):
        return '列出目录中的文件和子目录。可递归、按通配符过滤、分页。'

    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'path': {'type': 'string', 'description': '目录路径'},
                'recursive': {'type': 'boolean'},
                'pattern': {'type': 'string'},
                'offset': {'type': 'integer', 'default': 0},
                'limit': {'type': 'integer', 'default': 100},
            },
            'required': [],
        }

    def validate_input(self, arguments, context=None):
        return True, ''

    def check_permissions(self, arguments, user=None):
        if not user:
            return True, ''
        return _check_auth(self.name, user, arguments.get('path', ''))

    def call(self, arguments, user=None, context=None):
        from tools import list_folder
        return list_folder(**arguments)


class CreateFolderTool(Tool):
    name = 'create_folder'
    description = '创建目录'
    is_concurrency_safe = False
    is_read_only = False
    min_role = 'staff'

    def prompt(self):
        return '创建目录（自动创建多级父目录）。'

    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'path': {'type': 'string', 'description': '目录路径'},
                'exist_ok': {'type': 'boolean', 'default': True},
            },
            'required': ['path'],
        }

    def validate_input(self, arguments, context=None):
        if not arguments.get('path', '').strip():
            return False, '路径不能为空'
        return True, ''

    def check_permissions(self, arguments, user=None):
        if not user: return True, ''
        return _check_auth(self.name, user, arguments.get('path', ''))

    def call(self, arguments, user=None, context=None):
        from tools import create_folder
        return create_folder(**arguments)


class DeleteFolderTool(Tool):
    name = 'delete_folder'
    description = '删除目录'
    is_concurrency_safe = False
    is_read_only = False
    min_role = 'super_admin'

    def prompt(self): return '删除目录（递归删除所有内容）。仅超级管理员可用。'
    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'path': {'type': 'string'},
                'recursive': {'type': 'boolean', 'default': True},
            },
            'required': ['path'],
        }
    def validate_input(self, arguments, context=None):
        if not arguments.get('path', '').strip():
            return False, '路径不能为空'
        return True, ''
    def check_permissions(self, arguments, user=None):
        if not user: return False, '未登录'
        return _check_auth(self.name, user, arguments.get('path', ''))
    def call(self, arguments, user=None, context=None):
        from tools import delete_folder
        return delete_folder(**arguments)


class MoveFolderTool(Tool):
    name = 'move_folder'
    description = '移动/重命名目录'
    is_concurrency_safe = False
    is_read_only = False
    min_role = 'staff'

    def prompt(self): return '移动或重命名目录。'
    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'source_path': {'type': 'string'},
                'dest_path': {'type': 'string'},
            },
            'required': ['source_path', 'dest_path'],
        }
    def validate_input(self, arguments, context=None): return True, ''
    def check_permissions(self, arguments, user=None):
        if not user: return True, ''
        return _check_auth(self.name, user, arguments.get('source_path', ''))
    def call(self, arguments, user=None, context=None):
        from tools import move_folder
        return move_folder(**arguments)


class CopyFolderTool(Tool):
    name = 'copy_folder'
    description = '复制目录'
    is_concurrency_safe = False
    is_read_only = False
    min_role = 'staff'

    def prompt(self): return '复制整个目录到目标位置。'
    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'source_path': {'type': 'string'},
                'dest_path': {'type': 'string'},
            },
            'required': ['source_path', 'dest_path'],
        }
    def validate_input(self, arguments, context=None): return True, ''
    def check_permissions(self, arguments, user=None):
        if not user: return True, ''
        return _check_auth(self.name, user, arguments.get('source_path', ''))
    def call(self, arguments, user=None, context=None):
        from tools import copy_folder
        return copy_folder(**arguments)


# ═══════════════════════════════════════════
# 文件读写 (6)
# ═══════════════════════════════════════════

class ReadFileTool(Tool):
    name = 'read_file'
    description = '读取文件内容'
    is_concurrency_safe = True
    is_read_only = True
    min_role = 'guest'

    def prompt(self):
        return '读取文件内容。支持指定行范围、自动截断大文件、二进制文件检测。'

    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'path': {'type': 'string', 'description': '文件路径'},
                'encoding': {'type': 'string', 'default': 'utf-8'},
                'start_line': {'type': 'integer'},
                'end_line': {'type': 'integer'},
            },
            'required': ['path'],
        }

    def validate_input(self, arguments, context=None):
        if not arguments.get('path', '').strip():
            return False, '路径不能为空'
        return True, ''

    def check_permissions(self, arguments, user=None):
        if not user: return True, ''
        return _check_auth(self.name, user, arguments.get('path', ''))

    def call(self, arguments, user=None, context=None):
        from tools import read_file
        result = read_file(**arguments)
        # 记录读前状态 (供 Edit 工具使用)
        if context and result.get('success'):
            ctx = context
            ctx.setdefault('read_file_state', {})[arguments['path']] = True
        return result


class WriteFileTool(Tool):
    name = 'write_file'
    description = '创建/覆盖写入文件'
    is_concurrency_safe = False
    is_read_only = False
    min_role = 'staff'

    def prompt(self):
        return '创建或覆盖写入文件。会自动创建父目录。优先编辑现有文件而非创建新文件。'

    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'path': {'type': 'string'},
                'content': {'type': 'string'},
                'encoding': {'type': 'string', 'default': 'utf-8'},
            },
            'required': ['path', 'content'],
        }

    def validate_input(self, arguments, context=None):
        if not arguments.get('path', '').strip():
            return False, '路径不能为空'
        if 'content' not in arguments:
            return False, 'content 参数必填'
        return True, ''

    def check_permissions(self, arguments, user=None):
        if not user: return True, ''
        return _check_auth(self.name, user, arguments.get('path', ''))

    def call(self, arguments, user=None, context=None):
        from tools import write_file
        return write_file(**arguments)


class AppendFileTool(Tool):
    name = 'append_file'
    description = '追加内容到文件尾部'
    is_concurrency_safe = False
    is_read_only = False
    min_role = 'staff'

    def prompt(self): return '追加内容到文件尾部。'
    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'path': {'type': 'string'},
                'content': {'type': 'string'},
                'encoding': {'type': 'string', 'default': 'utf-8'},
            },
            'required': ['path', 'content'],
        }
    def validate_input(self, arguments, context=None): return True, ''
    def check_permissions(self, arguments, user=None):
        if not user: return True, ''
        return _check_auth(self.name, user, arguments.get('path', ''))
    def call(self, arguments, user=None, context=None):
        from tools import append_file
        return append_file(**arguments)


class InsertTextTool(Tool):
    name = 'insert_text'
    description = '在文件中插入文本'
    is_concurrency_safe = False
    is_read_only = False
    min_role = 'staff'

    def prompt(self):
        return '在文件指定位置插入内容。position 可选 start/end 或数字行号。'

    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'path': {'type': 'string'},
                'content': {'type': 'string'},
                'position': {'type': 'string', 'description': "start/end/数字"},
            },
            'required': ['path', 'content', 'position'],
        }
    def validate_input(self, arguments, context=None): return True, ''
    def check_permissions(self, arguments, user=None):
        if not user: return True, ''
        return _check_auth(self.name, user, arguments.get('path', ''))
    def call(self, arguments, user=None, context=None):
        from tools import insert_text
        return insert_text(**arguments)


class ReplaceTextTool(Tool):
    name = 'replace_text'
    description = '在文件中查找替换文本'
    is_concurrency_safe = False
    is_read_only = False
    min_role = 'staff'

    def prompt(self):
        return '在文件中查找并替换文本。支持普通文本和正则表达式。对于精确编辑,使用 Edit 工具。'

    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'path': {'type': 'string'},
                'search_text': {'type': 'string'},
                'replace_text': {'type': 'string'},
                'count': {'type': 'integer', 'default': 0, 'description': '0=全部'},
                'regex': {'type': 'boolean', 'default': False},
            },
            'required': ['path', 'search_text', 'replace_text'],
        }
    def validate_input(self, arguments, context=None): return True, ''
    def check_permissions(self, arguments, user=None):
        if not user: return True, ''
        return _check_auth(self.name, user, arguments.get('path', ''))
    def call(self, arguments, user=None, context=None):
        from tools import replace_text
        return replace_text(**arguments)


class DeleteLinesTool(Tool):
    name = 'delete_lines'
    description = '删除文件中指定行'
    is_concurrency_safe = False
    is_read_only = False
    min_role = 'staff'

    def prompt(self): return '删除文件中指定范围的行。'
    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'path': {'type': 'string'},
                'start_line': {'type': 'integer'},
                'end_line': {'type': 'integer'},
            },
            'required': ['path', 'start_line', 'end_line'],
        }
    def validate_input(self, arguments, context=None): return True, ''
    def check_permissions(self, arguments, user=None):
        if not user: return True, ''
        return _check_auth(self.name, user, arguments.get('path', ''))
    def call(self, arguments, user=None, context=None):
        from tools import delete_lines
        return delete_lines(**arguments)


# ═══════════════════════════════════════════
# 文件管理 (7)
# ═══════════════════════════════════════════

class DeleteFileTool(Tool):
    name = 'delete_file'
    description = '删除文件'
    is_concurrency_safe = False
    is_read_only = False
    min_role = 'super_admin'

    def prompt(self): return '删除文件。仅超级管理员可用。'
    def input_schema(self):
        return {
            'type': 'object',
            'properties': {'path': {'type': 'string'}},
            'required': ['path'],
        }
    def validate_input(self, arguments, context=None): return True, ''
    def check_permissions(self, arguments, user=None):
        if not user: return False, '未登录'
        return _check_auth(self.name, user, arguments.get('path', ''))
    def call(self, arguments, user=None, context=None):
        from tools import delete_file
        return delete_file(**arguments)


class MoveFileTool(Tool):
    name = 'move_file'
    description = '移动/重命名文件'
    is_concurrency_safe = False
    is_read_only = False
    min_role = 'staff'

    def prompt(self): return '移动或重命名文件。'
    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'source_path': {'type': 'string'},
                'dest_path': {'type': 'string'},
            },
            'required': ['source_path', 'dest_path'],
        }
    def validate_input(self, arguments, context=None): return True, ''
    def check_permissions(self, arguments, user=None):
        if not user: return True, ''
        return _check_auth(self.name, user, arguments.get('source_path', ''))
    def call(self, arguments, user=None, context=None):
        from tools import move_file
        return move_file(**arguments)


class CopyFileTool(Tool):
    name = 'copy_file'
    description = '复制文件'
    is_concurrency_safe = False
    is_read_only = False
    min_role = 'staff'

    def prompt(self): return '复制文件到目标位置。'
    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'source_path': {'type': 'string'},
                'dest_path': {'type': 'string'},
            },
            'required': ['source_path', 'dest_path'],
        }
    def validate_input(self, arguments, context=None): return True, ''
    def check_permissions(self, arguments, user=None):
        if not user: return True, ''
        return _check_auth(self.name, user, arguments.get('source_path', ''))
    def call(self, arguments, user=None, context=None):
        from tools import copy_file
        return copy_file(**arguments)


class GetFileInfoTool(Tool):
    name = 'get_file_info'
    description = '获取文件详细信息'
    is_concurrency_safe = True
    is_read_only = True
    min_role = 'guest'

    def prompt(self): return '获取文件/目录的元数据（大小、修改时间、行数等）。'
    def input_schema(self):
        return {
            'type': 'object',
            'properties': {'path': {'type': 'string'}},
            'required': ['path'],
        }
    def validate_input(self, arguments, context=None): return True, ''
    def check_permissions(self, arguments, user=None):
        if not user: return True, ''
        return _check_auth(self.name, user, arguments.get('path', ''))
    def call(self, arguments, user=None, context=None):
        from tools import get_file_info
        return get_file_info(**arguments)


class SearchFilesTool(Tool):
    name = 'search_files'
    description = '按文件名模式搜索文件'
    is_concurrency_safe = True
    is_read_only = True
    min_role = 'guest'

    def prompt(self):
        return '按 glob 模式搜索文件（如 *.py, **/*.ts）。优先使用 Glob 工具进行快速文件匹配。'

    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'folder_path': {'type': 'string', 'default': ''},
                'pattern': {'type': 'string', 'description': '通配符, 如 *.py'},
                'recursive': {'type': 'boolean'},
            },
            'required': ['pattern'],
        }
    def validate_input(self, arguments, context=None): return True, ''
    def check_permissions(self, arguments, user=None): return True, ''
    def call(self, arguments, user=None, context=None):
        from tools import search_files
        return search_files(**arguments)


class SearchContentTool(Tool):
    name = 'search_content'
    description = '在文件内容中搜索文本(grep)'
    is_concurrency_safe = True
    is_read_only = True
    min_role = 'guest'

    def prompt(self):
        return '在文件内容中搜索文本或正则表达式。优先使用 Grep 工具进行内容搜索。'

    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'folder_path': {'type': 'string', 'default': ''},
                'text': {'type': 'string'},
                'file_pattern': {'type': 'string', 'default': '*'},
                'recursive': {'type': 'boolean'},
                'case_sensitive': {'type': 'boolean'},
            },
            'required': ['text'],
        }
    def validate_input(self, arguments, context=None): return True, ''
    def check_permissions(self, arguments, user=None): return True, ''
    def call(self, arguments, user=None, context=None):
        from tools import search_content
        return search_content(**arguments)


class GetFileHashTool(Tool):
    name = 'get_file_hash'
    description = '计算文件哈希值'
    is_concurrency_safe = True
    is_read_only = True
    min_role = 'guest'

    def prompt(self): return '计算文件的 MD5/SHA1/SHA256 哈希。'
    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'path': {'type': 'string'},
                'algorithm': {'type': 'string', 'default': 'md5'},
            },
            'required': ['path'],
        }
    def validate_input(self, arguments, context=None): return True, ''
    def check_permissions(self, arguments, user=None):
        if not user: return True, ''
        return _check_auth(self.name, user, arguments.get('path', ''))
    def call(self, arguments, user=None, context=None):
        from tools import get_file_hash
        return get_file_hash(**arguments)


# ═══════════════════════════════════════════
# 批量 & 工具 (4)
# ═══════════════════════════════════════════

class BatchReadTool(Tool):
    name = 'batch_read'
    description = '批量读取多个文件'
    is_concurrency_safe = True
    is_read_only = True
    min_role = 'guest'

    def prompt(self): return '批量读取多个文件(最多 20 个)。'
    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'paths': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': '文件路径列表, 最多 20 个',
                },
            },
            'required': ['paths'],
        }
    def validate_input(self, arguments, context=None): return True, ''
    def check_permissions(self, arguments, user=None):
        if not user: return True, ''
        for p in arguments.get('paths', []):
            ok, msg = _check_auth(self.name, user, p)
            if not ok:
                return False, msg
        return True, ''
    def call(self, arguments, user=None, context=None):
        from tools import batch_read
        return batch_read(**arguments)


class ZipFilesTool(Tool):
    name = 'zip_files'
    description = '打包压缩文件'
    is_concurrency_safe = False
    is_read_only = False
    min_role = 'staff'

    def prompt(self): return '将多个文件/目录打包为 zip 或 tar.gz。'
    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'paths': {'type': 'array', 'items': {'type': 'string'}},
                'output_path': {'type': 'string'},
                'format': {'type': 'string', 'default': 'zip'},
            },
            'required': ['paths', 'output_path'],
        }
    def validate_input(self, arguments, context=None): return True, ''
    def check_permissions(self, arguments, user=None):
        if not user: return True, ''
        return _check_auth(self.name, user, arguments.get('output_path', ''))
    def call(self, arguments, user=None, context=None):
        from tools import zip_files
        return zip_files(**arguments)


class UnzipFileTool(Tool):
    name = 'unzip_file'
    description = '解压文件'
    is_concurrency_safe = False
    is_read_only = False
    min_role = 'staff'

    def prompt(self): return '解压 zip/tar.gz/tar 文件。'
    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'path': {'type': 'string'},
                'dest_folder': {'type': 'string'},
            },
            'required': ['path'],
        }
    def validate_input(self, arguments, context=None): return True, ''
    def check_permissions(self, arguments, user=None):
        if not user: return True, ''
        return _check_auth(self.name, user, arguments.get('path', ''))
    def call(self, arguments, user=None, context=None):
        from tools import unzip_file
        return unzip_file(**arguments)


class CountItemsTool(Tool):
    name = 'count_items'
    description = '统计目录文件/子目录/总大小'
    is_concurrency_safe = True
    is_read_only = True
    min_role = 'guest'

    def prompt(self): return '统计目录中的文件数量、子目录数量和总大小。'
    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'path': {'type': 'string', 'default': ''},
                'recursive': {'type': 'boolean'},
            },
            'required': [],
        }
    def validate_input(self, arguments, context=None): return True, ''
    def check_permissions(self, arguments, user=None): return True, ''
    def call(self, arguments, user=None, context=None):
        from tools import count_items
        return count_items(**arguments)


# ═══════════════════════════════════════════
# 数据库 (6)
# ═══════════════════════════════════════════

class DbListTablesTool(Tool):
    name = 'db_list_tables'
    description = '列出所有数据库表'
    is_concurrency_safe = True
    is_read_only = True
    min_role = 'staff'

    def prompt(self): return '列出 SQLite 数据库中所有表。'
    def input_schema(self): return {'type': 'object', 'properties': {}, 'required': []}
    def validate_input(self, arguments, context=None): return True, ''
    def check_permissions(self, arguments, user=None):
        if not user: return True, ''
        return _check_auth(self.name, user)
    def call(self, arguments, user=None, context=None):
        from tools import db_list_tables
        return db_list_tables(**arguments)


class DbDescribeTableTool(Tool):
    name = 'db_describe_table'
    description = '查看表结构'
    is_concurrency_safe = True
    is_read_only = True
    min_role = 'staff'

    def prompt(self): return '查看指定表的列名、类型、约束。'
    def input_schema(self):
        return {
            'type': 'object',
            'properties': {'table_name': {'type': 'string'}},
            'required': ['table_name'],
        }
    def validate_input(self, arguments, context=None): return True, ''
    def check_permissions(self, arguments, user=None):
        if not user: return True, ''
        return _check_auth(self.name, user)
    def call(self, arguments, user=None, context=None):
        from tools import db_describe_table
        return db_describe_table(**arguments)


class DbQueryTool(Tool):
    name = 'db_query'
    description = '执行 SELECT 查询'
    is_concurrency_safe = True
    is_read_only = True
    min_role = 'staff'

    def prompt(self):
        return '执行 SELECT 查询（只读）。使用参数化查询防止 SQL 注入。限制返回 100 行。'

    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'sql': {'type': 'string'},
                'params': {'type': 'array', 'items': {}},
                'limit': {'type': 'integer', 'default': 100},
            },
            'required': ['sql'],
        }
    def validate_input(self, arguments, context=None):
        sql = arguments.get('sql', '').strip().upper()
        if not sql.startswith('SELECT') and not sql.startswith('WITH'):
            return False, 'db_query 只允许 SELECT/WITH 查询'
        return True, ''
    def check_permissions(self, arguments, user=None):
        if not user: return True, ''
        return _check_auth(self.name, user)
    def call(self, arguments, user=None, context=None):
        from tools import db_query
        return db_query(**arguments)


class DbExecuteTool(Tool):
    name = 'db_execute'
    description = '执行非查询 SQL (INSERT/UPDATE/DELETE/CREATE/DROP/ALTER)'
    is_concurrency_safe = False
    is_read_only = False
    min_role = 'dept_head'

    def prompt(self): return '执行 INSERT/UPDATE/DELETE/CREATE/DROP/ALTER。部门长及以上可用。'
    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'sql': {'type': 'string'},
                'params': {'type': 'array', 'items': {}},
            },
            'required': ['sql'],
        }
    def validate_input(self, arguments, context=None): return True, ''
    def check_permissions(self, arguments, user=None):
        if not user: return True, ''
        return _check_auth(self.name, user)
    def call(self, arguments, user=None, context=None):
        from tools import db_execute
        return db_execute(**arguments)


class DbCreateTableTool(Tool):
    name = 'db_create_table'
    description = '创建数据库表'
    is_concurrency_safe = False
    is_read_only = False
    min_role = 'gm'

    def prompt(self): return '在数据库中创建新表。总经理及以上可用。'
    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'table_name': {'type': 'string'},
                'columns': {
                    'type': 'array',
                    'items': {
                        'type': 'object',
                        'properties': {
                            'name': {'type': 'string'},
                            'type': {'type': 'string'},
                            'constraints': {'type': 'string'},
                        },
                        'required': ['name', 'type'],
                    },
                },
            },
            'required': ['table_name', 'columns'],
        }
    def validate_input(self, arguments, context=None): return True, ''
    def check_permissions(self, arguments, user=None):
        if not user: return True, ''
        return _check_auth(self.name, user)
    def call(self, arguments, user=None, context=None):
        from tools import db_create_table
        return db_create_table(**arguments)


class DbDropTableTool(Tool):
    name = 'db_drop_table'
    description = '删除数据库表'
    is_concurrency_safe = False
    is_read_only = False
    min_role = 'super_admin'

    def prompt(self): return '删除数据库表。仅超级管理员可用。'
    def input_schema(self):
        return {
            'type': 'object',
            'properties': {'table_name': {'type': 'string'}},
            'required': ['table_name'],
        }
    def validate_input(self, arguments, context=None): return True, ''
    def check_permissions(self, arguments, user=None):
        if not user: return True, ''
        return _check_auth(self.name, user)
    def call(self, arguments, user=None, context=None):
        from tools import db_drop_table
        return db_drop_table(**arguments)


# ═══════════════════════════════════════════
# 全部工具清单 — 按原始顺序
# ═══════════════════════════════════════════

ALL_BUILTIN_TOOLS = [
    # 目录操作
    ListFolderTool(), CreateFolderTool(), DeleteFolderTool(),
    MoveFolderTool(), CopyFolderTool(),
    # 文件读写
    ReadFileTool(), WriteFileTool(), AppendFileTool(),
    InsertTextTool(), ReplaceTextTool(), DeleteLinesTool(),
    # 文件管理
    DeleteFileTool(), MoveFileTool(), CopyFileTool(),
    GetFileInfoTool(), SearchFilesTool(), SearchContentTool(),
    GetFileHashTool(),
    # 批量 & 工具
    BatchReadTool(), ZipFilesTool(), UnzipFileTool(),
    CountItemsTool(),
    # 数据库
    DbListTablesTool(), DbDescribeTableTool(), DbQueryTool(),
    DbExecuteTool(), DbCreateTableTool(), DbDropTableTool(),
]


def register_all(registry):
    """将所有内置工具注册到 ToolRegistry"""
    for tool in ALL_BUILTIN_TOOLS:
        cat = _get_category(tool)
        registry.register(tool, cat, tool.min_role)


def _get_category(tool: Tool) -> str:
    """根据工具名推断分类"""
    name = tool.name
    if name.startswith('db_'):
        return 'database'
    if name in ('zip_files', 'unzip_file', 'count_items', 'batch_read'):
        return 'utility'
    if name in ('delete_file', 'delete_folder', 'delete_lines'):
        return 'destructive'
    if name in ('move_file', 'copy_file', 'move_folder', 'copy_folder',
                'write_file', 'append_file', 'insert_text', 'replace_text',
                'create_folder'):
        return 'write'
    return 'read'
