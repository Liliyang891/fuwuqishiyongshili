#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务追踪工具 — 映射自 Claude Code: src/tools/TaskCreateTool/ 等

TaskCreate, TaskUpdate, TaskList, TaskGet — 创建和管理任务列表。
任务支持状态流转和依赖关系。
"""

import json
import logging
import time
import uuid
from typing import Optional

from ..tool_base import Tool
from ..permissions import PermissionResult

logger = logging.getLogger(__name__)

VALID_STATUSES = {'pending', 'in_progress', 'completed', 'deleted'}


class TaskManager:
    """全局任务管理器"""

    def __init__(self):
        self._tasks: dict[str, dict] = {}

    def create(self, subject: str, description: str = '',
               metadata: Optional[dict] = None) -> dict:
        task_id = str(uuid.uuid4())[:8]
        now = time.time()
        task = {
            'id': task_id,
            'subject': subject,
            'description': description,
            'status': 'pending',
            'blocks': [],
            'blockedBy': [],
            'metadata': metadata or {},
            'created_at': now,
            'updated_at': now,
        }
        self._tasks[task_id] = task
        return dict(task)

    def get(self, task_id: str) -> Optional[dict]:
        task = self._tasks.get(task_id)
        return dict(task) if task else None

    def list_all(self, status: Optional[str] = None) -> list[dict]:
        tasks = list(self._tasks.values())
        if status:
            tasks = [t for t in tasks if t['status'] == status]
        tasks.sort(key=lambda t: t['created_at'])
        return [dict(t) for t in tasks]

    def update(self, task_id: str, **kwargs) -> Optional[dict]:
        task = self._tasks.get(task_id)
        if not task:
            return None
        allowed = {'subject', 'description', 'status', 'metadata'}
        for k, v in kwargs.items():
            if k in allowed and v is not None:
                task[k] = v
        task['updated_at'] = time.time()
        return dict(task)

    def set_dependency(self, task_id: str, blocks: Optional[list[str]] = None,
                       blocked_by: Optional[list[str]] = None):
        task = self._tasks.get(task_id)
        if not task:
            return None
        if blocks is not None:
            task['blocks'] = blocks
        if blocked_by is not None:
            task['blockedBy'] = blocked_by
        task['updated_at'] = time.time()
        return dict(task)

    def delete(self, task_id: str) -> bool:
        if task_id in self._tasks:
            self._tasks[task_id]['status'] = 'deleted'
            self._tasks[task_id]['updated_at'] = time.time()
            return True
        return False


# 全局单例
_task_manager = TaskManager()


class TaskCreate(Tool):
    """创建任务"""

    name = 'TaskCreate'
    description = '创建新任务。用于跟踪复杂多步骤工作的进度。'
    is_concurrency_safe = False
    is_read_only = False
    min_role = 'staff'
    tool_category = 'task'

    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'subject': {
                    'type': 'string',
                    'description': '任务标题（祈使句形式）',
                },
                'description': {
                    'type': 'string',
                    'description': '任务描述',
                },
                'metadata': {
                    'type': 'object',
                    'description': '附加元数据',
                },
            },
            'required': ['subject', 'description'],
        }

    def prompt(self):
        return """## 任务创建 (TaskCreate)
- 为复杂多步骤工作创建任务来追踪进度
- subject 使用祈使句形式, 如 "Fix auth bug"
- 使用 TaskUpdate 更新进度, TaskList 查看列表"""

    def validate_input(self, arguments, context=None):
        if not arguments.get('subject', '').strip():
            return False, 'subject 不能为空'
        return True, ''

    def check_permissions(self, arguments, user=None) -> PermissionResult:
        return PermissionResult.allow()

    def call(self, arguments, user=None, context=None) -> dict:
        task = _task_manager.create(
            subject=arguments['subject'].strip(),
            description=arguments.get('description', '').strip(),
            metadata=arguments.get('metadata'),
        )
        return {
            'success': True,
            'result': json.dumps(task, ensure_ascii=False, indent=2),
            'task': task,
        }


class TaskUpdate(Tool):
    """更新任务"""

    name = 'TaskUpdate'
    description = '更新任务的状态、标题或描述。支持设置依赖关系。'
    is_concurrency_safe = False
    is_read_only = False
    min_role = 'staff'
    tool_category = 'task'

    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'taskId': {
                    'type': 'string',
                    'description': '任务 ID',
                },
                'subject': {
                    'type': 'string',
                    'description': '新标题',
                },
                'description': {
                    'type': 'string',
                    'description': '新描述',
                },
                'status': {
                    'type': 'string',
                    'enum': list(VALID_STATUSES),
                    'description': '新状态: pending, in_progress, completed, deleted',
                },
                'addBlocks': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': '被此任务阻塞的任务 ID 列表',
                },
                'addBlockedBy': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': '阻塞此任务的任务 ID 列表',
                },
            },
            'required': ['taskId'],
        }

    def prompt(self):
        return '更新任务状态。状态流转: pending → in_progress → completed。'

    def validate_input(self, arguments, context=None):
        task_id = arguments.get('taskId', '')
        if not task_id:
            return False, 'taskId 不能为空'
        status = arguments.get('status')
        if status and status not in VALID_STATUSES:
            return False, f'无效状态: {status}'
        return True, ''

    def check_permissions(self, arguments, user=None) -> PermissionResult:
        return PermissionResult.allow()

    def call(self, arguments, user=None, context=None) -> dict:
        task_id = arguments['taskId']

        # 处理依赖
        blocks = arguments.get('addBlocks')
        blocked_by = arguments.get('addBlockedBy')
        if blocks is not None or blocked_by is not None:
            task = _task_manager.set_dependency(task_id, blocks, blocked_by)
            if not task:
                return {'success': False, 'error': f'任务不存在: {task_id}'}
            return {
                'success': True,
                'result': json.dumps(task, ensure_ascii=False, indent=2),
                'task': task,
            }

        # 更新字段
        updates = {}
        for k in ('subject', 'description', 'status', 'metadata'):
            if k in arguments:
                updates[k] = arguments[k]

        task = _task_manager.update(task_id, **updates)
        if not task:
            return {'success': False, 'error': f'任务不存在: {task_id}'}

        return {
            'success': True,
            'result': json.dumps(task, ensure_ascii=False, indent=2),
            'task': task,
        }


class TaskList(Tool):
    """列出任务"""

    name = 'TaskList'
    description = '列出所有任务, 可按状态过滤。'
    is_concurrency_safe = True
    is_read_only = True
    min_role = 'staff'
    tool_category = 'task'

    def input_schema(self):
        return {
            'type': 'object',
            'properties': {},
            'required': [],
        }

    def prompt(self):
        return '列出所有任务及状态。'

    def validate_input(self, arguments, context=None):
        return True, ''

    def check_permissions(self, arguments, user=None) -> PermissionResult:
        return PermissionResult.allow()

    def call(self, arguments, user=None, context=None) -> dict:
        tasks = _task_manager.list_all()
        # 只返回未删除的任务
        active = [t for t in tasks if t['status'] != 'deleted']
        return {
            'success': True,
            'result': json.dumps(active, ensure_ascii=False, indent=2),
            'tasks': active,
            'total': len(active),
        }


class TaskGet(Tool):
    """获取任务"""

    name = 'TaskGet'
    description = '获取单个任务的详细信息。'
    is_concurrency_safe = True
    is_read_only = True
    min_role = 'staff'
    tool_category = 'task'

    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'taskId': {
                    'type': 'string',
                    'description': '任务 ID',
                },
            },
            'required': ['taskId'],
        }

    def prompt(self):
        return '获取指定任务的完整信息。'

    def validate_input(self, arguments, context=None):
        if not arguments.get('taskId', ''):
            return False, 'taskId 不能为空'
        return True, ''

    def check_permissions(self, arguments, user=None) -> PermissionResult:
        return PermissionResult.allow()

    def call(self, arguments, user=None, context=None) -> dict:
        task = _task_manager.get(arguments['taskId'])
        if not task:
            return {'success': False, 'error': f'任务不存在: {arguments["taskId"]}'}
        return {
            'success': True,
            'result': json.dumps(task, ensure_ascii=False, indent=2),
            'task': task,
        }
