#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""策略引擎 — 让高级管理者通过自然语言制定管理规矩

核心机制:
- 策略=结构化规则，存入 policies 表
- 冲突消解=按 creator_level DESC 排序，高级别覆盖低级别
- 策略自动生成制度文件 + 审批架构文件
"""

import json, logging, os, time
from datetime import datetime
from typing import Optional

from role_levels import ROLE_LEVEL, ROLE_NAMES, LEVEL_TO_ROLE, SUPERIOR_MAP

logger = logging.getLogger(__name__)


def _get_superior_role(role: str) -> str:
    """获取角色的上一级"""
    return SUPERIOR_MAP.get(role, role)


def _get_db():
    from tools import _get_db_conn
    conn = _get_db_conn()
    conn.row_factory = __import__('sqlite3').Row
    return conn


class PolicyEngine:
    """通用策略引擎"""

    def __init__(self):
        self._init_table()

    def _init_table(self):
        conn = _get_db()
        conn.execute('''CREATE TABLE IF NOT EXISTS policies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            rules TEXT NOT NULL DEFAULT '[]',
            created_by INTEGER,
            creator_name TEXT,
            creator_role TEXT,
            creator_level INTEGER,
            scope TEXT DEFAULT 'all',
            is_active INTEGER DEFAULT 1,
            created_at REAL
        )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS leave_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            applicant_id INTEGER,
            applicant_name TEXT,
            applicant_role TEXT,
            days REAL NOT NULL,
            reason TEXT DEFAULT '',
            approver_role TEXT,
            approver_name TEXT,
            status TEXT DEFAULT 'pending',
            policy_id INTEGER,
            created_at REAL,
            resolved_at REAL
        )''')
        conn.commit()
        # conn 由线程本地缓存管理，不关闭

    # ═══════════════════════════════════════
    # 策略 CRUD
    # ═══════════════════════════════════════

    def create_policy(self, policy_type: str, name: str, rules: list,
                      user: dict) -> dict:
        """创建一个策略

        Args:
            policy_type: 策略类型 (leave_approval, expense_approval, etc.)
            name: 策略名称
            rules: 规则列表 [{"condition":..., "action":...}, ...]
            user: 制定者信息

        Returns:
            dict with policy + generated files
        """
        role = user.get('role', 'guest')
        level = user.get('role_level', 0)
        if level < 3:  # dept_head+
            return {'success': False, 'error': f'只有部门长及以上级别可以制定策略，当前: {role}'}

        conn = _get_db()
        cursor = conn.execute(
            'INSERT INTO policies (type, name, rules, created_by, creator_name,'
            ' creator_role, creator_level, scope, is_active, created_at)'
            ' VALUES (?,?,?,?,?,?,?,?,?,?)',
            (policy_type, name, json.dumps(rules, ensure_ascii=False),
             user.get('id'), user.get('username', ''),
             role, level, 'all', 1, time.time())
        )
        policy_id = cursor.lastrowid
        conn.commit()
        # conn 由线程本地缓存管理，不关闭

        policy = {
            'id': policy_id, 'type': policy_type, 'name': name,
            'rules': rules, 'creator_role': role, 'creator_level': level,
        }

        # 生成制度文件 + 审批架构文件
        files = self._generate_policy_files(policy)

        return {
            'success': True,
            'policy': policy,
            'message': f'策略 "{name}" 已创建 (id={policy_id})，{len(rules)} 条规则',
            'generated_files': files,
        }

    def get_active_policies(self, policy_type: str = None, user: dict = None) -> list:
        """获取当前生效的策略列表（按级别降序，高级覆盖低级）"""
        conn = _get_db()
        if policy_type:
            rows = conn.execute(
                'SELECT * FROM policies WHERE type=? AND is_active=1 ORDER BY creator_level DESC',
                (policy_type,)
            ).fetchall()
        else:
            rows = conn.execute(
                'SELECT * FROM policies WHERE is_active=1 ORDER BY creator_level DESC'
            ).fetchall()
        # conn 由线程本地缓存管理，不关闭
        policies = []
        for r in rows:
            d = dict(r)
            d['rules'] = json.loads(d['rules'])
            policies.append(d)
        return policies

    def get_policies_for_user(self, user: dict) -> list:
        """获取对当前用户生效的策略（用户级别以下的所有策略）"""
        user_level = user.get('role_level', 0)
        all_policies = self.get_active_policies()
        return [p for p in all_policies if p['creator_level'] >= user_level]

    def deactivate_policy(self, policy_id: int, user: dict) -> dict:
        """停用策略（仅制定者本人或更高级别）"""
        conn = _get_db()
        row = conn.execute('SELECT * FROM policies WHERE id=?', (policy_id,)).fetchone()
        if not row:
            # conn 由线程本地缓存管理，不关闭
            return {'success': False, 'error': f'策略不存在: {policy_id}'}
        policy = dict(row)
        user_level = user.get('role_level', 0)
        if user_level < policy['creator_level']:
            # conn 由线程本地缓存管理，不关闭
            return {'success': False, 'error': '只有同级或更高级别才能停用此策略'}
        conn.execute('UPDATE policies SET is_active=0 WHERE id=?', (policy_id,))
        conn.commit()
        # conn 由线程本地缓存管理，不关闭
        return {'success': True, 'message': f'策略 {policy_id} 已停用'}

    # ═══════════════════════════════════════
    # 策略匹配（冲突消解核心）
    # ═══════════════════════════════════════

    def match_policy(self, policy_type: str, context: dict) -> Optional[dict]:
        """根据上下文匹配策略规则

        按 creator_level DESC 排序后取第一个匹配的规则。
        这意味着高级别者的策略自动覆盖低级别冲突策略。
        """
        policies = self.get_active_policies(policy_type)
        for policy in policies:
            for rule in policy['rules']:
                if self._rule_matches(rule, context):
                    return {
                        'policy': policy,
                        'matched_rule': rule,
                        'matched_at_level': policy['creator_level'],
                    }
        return None

    def _rule_matches(self, rule: dict, context: dict) -> bool:
        """判断规则是否匹配上下文"""
        for key, value in rule.items():
            if key in ('approver', 'approver_role', 'action', 'description'):
                continue
            ctx_val = context.get(key)
            if ctx_val is None:
                continue  # 未提供条件，跳过
            if isinstance(value, dict):
                # 范围匹配: {"min": 1, "max": 3}
                if 'min' in value and ctx_val < value['min']:
                    return False
                if 'max' in value and ctx_val >= value['max']:
                    return False
            elif ctx_val != value:
                return False
        return True

    # ═══════════════════════════════════════
    # 请假申请
    # ═══════════════════════════════════════

    def apply_leave(self, user: dict, days: float, reason: str = '') -> dict:
        """员工请假申请 — 自动匹配策略并创建审批记录"""
        context = {'days': days}
        match = self.match_policy('leave_approval', context)

        if not match:
            return {
                'success': False,
                'error': '未找到适用的请假审批策略，请联系管理员制定请假规则',
            }

        rule = match['matched_rule']
        approver_role = rule.get('approver_role', 'dept_head')

        # 如果规则中是"上一级"，则计算用户的上级
        if approver_role == '上一级':
            user_role = user.get('role', 'guest')
            approver_role = _get_superior_role(user_role)

        approver_name = ROLE_NAMES.get(approver_role, approver_role)

        conn = _get_db()
        cursor = conn.execute(
            'INSERT INTO leave_requests'
            ' (applicant_id, applicant_name, applicant_role, days, reason,'
            '  approver_role, approver_name, status, policy_id, created_at)'
            ' VALUES (?,?,?,?,?,?,?,?,?,?)',
            (user.get('id'), user.get('username', ''),
             user.get('role', 'guest'), days, reason,
             approver_role, approver_name, 'pending',
             match['policy']['id'], time.time())
        )
        leave_id = cursor.lastrowid
        conn.commit()
        # conn 由线程本地缓存管理，不关闭

        # 生成请假申请文件
        file_info = self._generate_leave_file(leave_id, user, days, reason,
                                              approver_role, approver_name)

        return {
            'success': True,
            'leave_id': leave_id,
            'days': days,
            'approver_role': approver_role,
            'approver_name': approver_name,
            'status': 'pending',
            'message': (
                f'请假申请已生成 (id={leave_id})。\n'
                f'申请 {days} 天假，需 {approver_name} 审批。\n'
                f'申请文件已生成: {file_info.get("path", "")}'
            ),
            'generated_file': file_info,
            'policy_name': match['policy']['name'],
        }

    def get_pending_leaves(self, approver_role: str = None) -> list:
        """获取待审批的请假申请"""
        conn = _get_db()
        if approver_role:
            rows = conn.execute(
                'SELECT * FROM leave_requests WHERE status="pending" AND approver_role=?',
                (approver_role,)
            ).fetchall()
        else:
            rows = conn.execute(
                'SELECT * FROM leave_requests WHERE status="pending"'
            ).fetchall()
        # conn 由线程本地缓存管理，不关闭
        return [dict(r) for r in rows]

    def approve_leave(self, leave_id: int, user: dict) -> dict:
        """审批请假"""
        conn = _get_db()
        row = conn.execute(
            'SELECT * FROM leave_requests WHERE id=?', (leave_id,)
        ).fetchone()
        if not row:
            # conn 由线程本地缓存管理，不关闭
            return {'success': False, 'error': f'请假申请不存在: {leave_id}'}
        leave = dict(row)
        if leave['status'] != 'pending':
            # conn 由线程本地缓存管理，不关闭
            return {'success': False, 'error': f'该申请状态为 {leave["status"]}，无需审批'}

        user_role = user.get('role', '')
        user_level = ROLE_LEVEL.get(user_role, 0)
        required_level = ROLE_LEVEL.get(leave['approver_role'], 0)
        if user_level < required_level:
            # conn 由线程本地缓存管理，不关闭
            return {
                'success': False,
                'error': f'审批权限不足: 需要 {leave["approver_name"]} 及以上级别',
            }

        conn.execute(
            'UPDATE leave_requests SET status="approved", approver_name=?,'
            ' resolved_at=? WHERE id=?',
            (user.get('username', ''), time.time(), leave_id)
        )
        conn.commit()
        # conn 由线程本地缓存管理，不关闭
        return {'success': True, 'message': f'请假申请 {leave_id} 已批准'}

    def reject_leave(self, leave_id: int, user: dict, reason: str = '') -> dict:
        """拒绝请假"""
        conn = _get_db()
        row = conn.execute(
            'SELECT * FROM leave_requests WHERE id=?', (leave_id,)
        ).fetchone()
        if not row:
            # conn 由线程本地缓存管理，不关闭
            return {'success': False, 'error': f'请假申请不存在: {leave_id}'}
        leave = dict(row)

        user_role = user.get('role', '')
        user_level = ROLE_LEVEL.get(user_role, 0)
        required_level = ROLE_LEVEL.get(leave['approver_role'], 0)
        if user_level < required_level:
            # conn 由线程本地缓存管理，不关闭
            return {'success': False, 'error': f'审批权限不足'}

        conn.execute(
            'UPDATE leave_requests SET status="rejected", approver_name=?,'
            ' resolved_at=? WHERE id=?',
            (user.get('username', ''), time.time(), leave_id)
        )
        conn.commit()
        # conn 由线程本地缓存管理，不关闭
        return {'success': True, 'message': f'请假申请 {leave_id} 已拒绝' + (f': {reason}' if reason else '')}

    def get_leave_history(self, user: dict = None, applicant_id: int = None) -> list:
        """查询请假历史"""
        conn = _get_db()
        if applicant_id:
            rows = conn.execute(
                'SELECT * FROM leave_requests WHERE applicant_id=? ORDER BY created_at DESC',
                (applicant_id,)
            ).fetchall()
        elif user:
            rows = conn.execute(
                'SELECT * FROM leave_requests ORDER BY created_at DESC'
            ).fetchall()
        else:
            rows = conn.execute(
                'SELECT * FROM leave_requests ORDER BY created_at DESC'
            ).fetchall()
        # conn 由线程本地缓存管理，不关闭
        return [dict(r) for r in rows]

    # ═══════════════════════════════════════
    # 文件生成
    # ═══════════════════════════════════════

    def _generate_policy_files(self, policy: dict) -> list:
        """生成制度文件 + 审批架构文件"""
        files = []
        from tools import FILES_DIR, DATA_DIR

        creator_role = policy['creator_role']
        policy_dir = os.path.join(FILES_DIR, creator_role)

        # 1. 制度文件
        doc_name = f'{policy["type"]}_{policy["id"]}.md'
        doc_path = os.path.join(policy_dir, doc_name)
        content = self._format_policy_md(policy)
        os.makedirs(policy_dir, exist_ok=True)
        with open(doc_path, 'w', encoding='utf-8') as f:
            f.write(content)
        files.append({'path': os.path.join(creator_role, doc_name), 'type': 'policy_doc'})

        # 2. 审批架构文件 (放到 share/policies/)
        share_dir = os.path.join(DATA_DIR, 'share', 'policies')
        os.makedirs(share_dir, exist_ok=True)
        arch_name = f'approval_hierarchy_{policy["type"]}.md'
        arch_path = os.path.join(share_dir, arch_name)
        arch_content = self._format_approval_hierarchy(policy)
        with open(arch_path, 'w', encoding='utf-8') as f:
            f.write(arch_content)
        files.append({'path': os.path.join('share', 'policies', arch_name),
                      'type': 'approval_hierarchy'})

        return files

    def _format_policy_md(self, policy: dict) -> str:
        lines = [
            f'# {policy["name"]}',
            '',
            f'- **策略类型**: {policy["type"]}',
            f'- **制定者**: {ROLE_NAMES.get(policy["creator_role"], policy["creator_role"])}',
            f'- **制定时间**: {datetime.now().strftime("%Y-%m-%d %H:%M")}',
            '',
            '## 规则列表',
            '',
        ]
        for i, rule in enumerate(policy['rules'], 1):
            lines.append(f'### 规则 {i}')
            for k, v in rule.items():
                if k in ('action', 'description'):
                    continue
                if isinstance(v, dict):
                    lines.append(f'- **{k}**: {v.get("min", "?")} ~ {v.get("max", "?")}')
                else:
                    lines.append(f'- **{k}**: {v}')
            if rule.get('action'):
                lines.append(f'- **执行**: {rule["action"]}')
            lines.append('')
        return '\n'.join(lines)

    def _format_approval_hierarchy(self, policy: dict) -> str:
        lines = [
            f'# 审批架构 — {policy["name"]}',
            '',
            '| 条件 | 审批人 |',
            '|------|--------|',
        ]
        for rule in policy['rules']:
            conditions = []
            for k, v in rule.items():
                if k in ('approver_role', 'approver', 'action', 'description'):
                    continue
                if isinstance(v, dict):
                    conditions.append(f'{k}: {v.get("min", "?")}~{v.get("max", "?")}')
                else:
                    conditions.append(f'{k}: {v}')
            cond_str = ', '.join(conditions)
            approver = ROLE_NAMES.get(rule.get('approver_role', ''), rule.get('approver', '?'))
            lines.append(f'| {cond_str} | {approver} |')
        lines.append('')
        lines.append(f'> 制定者: {ROLE_NAMES.get(policy["creator_role"], "")} · {datetime.now().strftime("%Y-%m-%d")}')
        return '\n'.join(lines)

    def _generate_leave_file(self, leave_id: int, user: dict, days: float,
                             reason: str, approver_role: str, approver_name: str) -> dict:
        """生成请假申请文件"""
        from tools import FILES_DIR, DATA_DIR

        user_role = user.get('role', 'guest')
        user_name = user.get('username', '')

        content = f'''# 请假申请单

- **申请编号**: {leave_id}
- **申请人**: {user_name}
- **申请角色**: {ROLE_NAMES.get(user_role, user_role)}
- **请假天数**: {days} 天
- **请假事由**: {reason or '未填写'}
- **需要审批**: {approver_name}
- **状态**: 待审批
- **申请时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}
'''

        # 存入申请人角色目录
        user_dir = os.path.join(FILES_DIR, user_role)
        os.makedirs(user_dir, exist_ok=True)
        file_name = f'leave_request_{leave_id}.md'
        file_path = os.path.join(user_dir, file_name)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)

        # 如果审批人是某个角色，也存一份到审批人目录
        approver_dir = os.path.join(FILES_DIR, approver_role)
        os.makedirs(approver_dir, exist_ok=True)
        approver_path = os.path.join(approver_dir, file_name)
        with open(approver_path, 'w', encoding='utf-8') as f:
            f.write(content)

        return {
            'path': os.path.join(user_role, file_name),
            'approver_path': os.path.join(approver_role, file_name),
            'filename': file_name,
        }

    # ═══════════════════════════════════════
    # 策略摘要（注入系统提示词）
    # ═══════════════════════════════════════

    def get_policy_summary_for_prompt(self, user: dict = None) -> str:
        """生成策略摘要文本，注入系统提示词"""
        policies = self.get_active_policies()
        if not policies:
            return ''

        user_level = user.get('role_level', 0) if user else 0
        lines = ['## 生效中的管理策略']
        lines.append('以下策略由高级管理者制定，你必须遵守：')
        lines.append('')

        type_names = {
            'leave_approval': '请假审批制度',
        }

        shown_types = set()
        for p in policies:
            if p['type'] in shown_types:
                continue  # 只显示最高级别的策略（冲突消解）
            shown_types.add(p['type'])

            type_label = type_names.get(p['type'], p['type'])
            creator_label = ROLE_NAMES.get(p['creator_role'], p['creator_role'])
            lines.append(f'### {type_label} (由 {creator_label} 制定)')
            lines.append(f'策略名称: {p["name"]}')
            for rule in p['rules']:
                parts = []
                for k, v in rule.items():
                    if k in ('action', 'description', 'approver_role', 'approver'):
                        continue
                    if isinstance(v, dict):
                        mn, mx = v.get('min', ''), v.get('max', '')
                        parts.append(f'{k}={mn}~{mx}')
                    else:
                        parts.append(f'{k}={v}')
                condition = ', '.join(parts)
                approver = rule.get('approver', ROLE_NAMES.get(rule.get('approver_role', ''), ''))
                action = f'→ {approver}审批'
                lines.append(f'- 当 {condition} 时 {action}')
            lines.append('')

        return '\n'.join(lines)
