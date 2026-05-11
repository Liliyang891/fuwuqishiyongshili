#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""策略工具 — 让高级管理者通过自然语言制定管理规矩
"""

from ..tool_base import Tool
from ..permissions import PermissionResult, check_role_level

ROLE_LEVELS = {
    'super_admin': 6, 'chairman': 5, 'gm': 4,
    'dept_head': 3, 'staff': 2, 'guest': 1,
}

ROLE_NAMES = {
    'super_admin': '超级管理员', 'chairman': '董事长',
    'gm': '总经理', 'dept_head': '部门长',
    'staff': '部门职员', 'guest': '游客',
}


class CreatePolicyTool(Tool):
    """创建管理策略"""

    name = 'CreatePolicy'
    description = '制定管理策略（部门长及以上）—— 定义审批规则、制度文档'
    is_concurrency_safe = False
    is_read_only = False
    min_role = 'dept_head'
    tool_category = 'policy'

    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'policy_type': {
                    'type': 'string',
                    'description': '策略类型，目前支持: leave_approval (请假审批)',
                    'enum': ['leave_approval'],
                },
                'name': {
                    'type': 'string',
                    'description': '策略名称，如"请假审批制度"',
                },
                'rules': {
                    'type': 'array',
                    'description': (
                        '规则列表。每条规则包含条件字段和审批人字段。'
                        '支持的条件字段: days (天数, 支持范围 {"min":1, "max":3})。'
                        '审批人字段: approver_role (角色ID) 或 approver (角色中文名)。'
                        'approver_role 可设为 "上一级" 表示自动计算申请人的上一级。'
                        '例如: [{"days": {"min": 1, "max": 3}, "approver_role": "dept_head", "action": "部门长审批"}]'
                    ),
                    'items': {'type': 'object'},
                },
            },
            'required': ['policy_type', 'name', 'rules'],
        }

    def prompt(self):
        return """## 制定策略 (CreatePolicy)
- 高级管理者使用此工具制定管理规矩（请假审批、费用报销等）
- 策略由结构化规则组成：条件 + 审批人
- 高级别者制定的策略自动覆盖低级别者的冲突策略
- 策略制定后将自动生成制度文件和审批架构文件"""

    def validate_input(self, arguments, context=None):
        policy_type = arguments.get('policy_type', '')
        if policy_type not in ('leave_approval',):
            return False, f'不支持的策略类型: {policy_type}'
        rules = arguments.get('rules', [])
        if not rules or not isinstance(rules, list):
            return False, '策略至少需要一条规则'
        for i, rule in enumerate(rules):
            if not isinstance(rule, dict):
                return False, f'规则 {i+1} 必须是字典格式'
            if 'approver_role' not in rule and 'approver' not in rule:
                return False, f'规则 {i+1} 缺少审批人: approver_role 或 approver'
        return True, ''

    def check_permissions(self, arguments, user=None):
        from ..permissions import check_role_level
        return check_role_level(user, self.min_role, ROLE_LEVELS)

    def call(self, arguments, user=None, context=None):
        from agent.policy_engine import PolicyEngine
        engine = PolicyEngine()
        result = engine.create_policy(
            arguments['policy_type'],
            arguments['name'],
            arguments['rules'],
            user,
        )
        if result.get('success'):
            return {
                'success': True,
                'result': result.get('message', ''),
                'policy': result.get('policy'),
                'generated_files': result.get('generated_files'),
            }
        return {'success': False, 'error': result.get('error', '未知错误')}


class ApplyLeaveTool(Tool):
    """请假申请"""

    name = 'ApplyLeave'
    description = '申请请假 —— 自动匹配审批策略'
    is_concurrency_safe = False
    is_read_only = False
    min_role = 'staff'
    tool_category = 'policy'

    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'days': {
                    'type': 'number',
                    'description': '请假天数',
                },
                'reason': {
                    'type': 'string',
                    'description': '请假事由',
                    'default': '',
                },
            },
            'required': ['days'],
        }

    def prompt(self):
        return """## 请假申请 (ApplyLeave)
- 员工使用此工具提交请假申请
- 系统将自动匹配当前生效的审批策略
- 申请提交后生成申请表文件，通知相应审批人"""

    def validate_input(self, arguments, context=None):
        days = arguments.get('days', 0)
        if days <= 0:
            return False, '请假天数必须大于0'
        return True, ''

    def check_permissions(self, arguments, user=None):
        return check_role_level(user, self.min_role, ROLE_LEVELS)

    def call(self, arguments, user=None, context=None):
        from agent.policy_engine import PolicyEngine
        engine = PolicyEngine()
        result = engine.apply_leave(
            user,
            arguments['days'],
            arguments.get('reason', ''),
        )
        if result.get('success'):
            return {
                'success': True,
                'result': result.get('message', ''),
                'leave_id': result.get('leave_id'),
                'approver_role': result.get('approver_role'),
                'approver_name': result.get('approver_name'),
                'generated_file': result.get('generated_file'),
                'policy_name': result.get('policy_name'),
            }
        return {'success': False, 'error': result.get('error', '未知错误')}


class QueryPoliciesTool(Tool):
    """查询当前生效的策略"""

    name = 'QueryPolicies'
    description = '查询当前生效的管理策略'
    is_concurrency_safe = True
    is_read_only = True
    min_role = 'guest'
    tool_category = 'policy'

    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'policy_type': {
                    'type': 'string',
                    'description': '策略类型（可选，不填则返回所有）',
                },
            },
            'required': [],
        }

    def prompt(self):
        return """## 查询策略 (QueryPolicies)
- 查看当前生效的管理策略
- 返回策略名称、规则列表、制定者等信息"""

    def check_permissions(self, arguments, user=None):
        return PermissionResult.allow()

    def call(self, arguments, user=None, context=None):
        from agent.policy_engine import PolicyEngine
        engine = PolicyEngine()
        policies = engine.get_active_policies(
            arguments.get('policy_type'),
            user,
        )
        summary = []
        for p in policies:
            summary.append({
                'id': p['id'],
                'type': p['type'],
                'name': p['name'],
                'rules': p['rules'],
                'creator_role': p['creator_role'],
                'creator_name': p['creator_name'],
            })
        return {
            'success': True,
            'result': f'找到 {len(policies)} 条生效策略',
            'policies': summary,
        }


class ApproveLeaveTool(Tool):
    """审批请假"""

    name = 'ApproveLeave'
    description = '审批请假申请 —— 需要对应审批角色及以上级别'
    is_concurrency_safe = False
    is_read_only = False
    min_role = 'dept_head'
    tool_category = 'policy'

    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'leave_id': {
                    'type': 'integer',
                    'description': '请假申请编号',
                },
            },
            'required': ['leave_id'],
        }

    def prompt(self):
        return """## 审批请假 (ApproveLeave)
- 用于审批待处理的请假申请
- 审批人需要具有申请中指定的审批角色及以上级别"""

    def check_permissions(self, arguments, user=None):
        return check_role_level(user, self.min_role, ROLE_LEVELS)

    def call(self, arguments, user=None, context=None):
        from agent.policy_engine import PolicyEngine
        engine = PolicyEngine()
        result = engine.approve_leave(arguments['leave_id'], user)
        if result.get('success'):
            return {'success': True, 'result': result.get('message', '')}
        return {'success': False, 'error': result.get('error', '未知错误')}


class RejectLeaveTool(Tool):
    """拒绝请假"""

    name = 'RejectLeave'
    description = '拒绝请假申请 —— 需要对应审批角色及以上级别'
    is_concurrency_safe = False
    is_read_only = False
    min_role = 'dept_head'
    tool_category = 'policy'

    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'leave_id': {
                    'type': 'integer',
                    'description': '请假申请编号',
                },
                'reason': {
                    'type': 'string',
                    'description': '拒绝原因',
                    'default': '',
                },
            },
            'required': ['leave_id'],
        }

    def prompt(self):
        return """## 拒绝请假 (RejectLeave)
- 用于拒绝待处理的请假申请
- 审批人需要具有申请中指定的审批角色及以上级别"""

    def check_permissions(self, arguments, user=None):
        return check_role_level(user, self.min_role, ROLE_LEVELS)

    def call(self, arguments, user=None, context=None):
        from agent.policy_engine import PolicyEngine
        engine = PolicyEngine()
        result = engine.reject_leave(
            arguments['leave_id'], user,
            arguments.get('reason', ''),
        )
        if result.get('success'):
            return {'success': True, 'result': result.get('message', '')}
        return {'success': False, 'error': result.get('error', '未知错误')}


class LeaveHistoryTool(Tool):
    """查看请假历史"""

    name = 'LeaveHistory'
    description = '查看请假申请历史记录'
    is_concurrency_safe = True
    is_read_only = True
    min_role = 'staff'
    tool_category = 'policy'

    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'applicant_id': {
                    'type': 'integer',
                    'description': '申请人ID（可选，管理员可查看指定用户）',
                },
            },
            'required': [],
        }

    def prompt(self):
        return """## 请假历史 (LeaveHistory)
- 查看请假申请的历史记录
- 管理员可查看指定用户的记录"""

    def check_permissions(self, arguments, user=None):
        return check_role_level(user, self.min_role, ROLE_LEVELS)

    def call(self, arguments, user=None, context=None):
        from agent.policy_engine import PolicyEngine
        engine = PolicyEngine()
        records = engine.get_leave_history(user, arguments.get('applicant_id'))
        return {
            'success': True,
            'result': f'找到 {len(records)} 条记录',
            'records': records,
        }


class DeactivatePolicyTool(Tool):
    """停用策略"""

    name = 'DeactivatePolicy'
    description = '停用管理策略 —— 仅制定者本人或更高级别'
    is_concurrency_safe = False
    is_read_only = False
    min_role = 'dept_head'
    tool_category = 'policy'

    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'policy_id': {
                    'type': 'integer',
                    'description': '要停用的策略ID',
                },
            },
            'required': ['policy_id'],
        }

    def prompt(self):
        return """## 停用策略 (DeactivatePolicy)
- 停用指定ID的管理策略
- 仅策略制定者本人或更高级别者可停用"""

    def check_permissions(self, arguments, user=None):
        return check_role_level(user, self.min_role, ROLE_LEVELS)

    def call(self, arguments, user=None, context=None):
        from agent.policy_engine import PolicyEngine
        engine = PolicyEngine()
        result = engine.deactivate_policy(arguments['policy_id'], user)
        if result.get('success'):
            return {'success': True, 'result': result.get('message', '')}
        return {'success': False, 'error': result.get('error', '未知错误')}
