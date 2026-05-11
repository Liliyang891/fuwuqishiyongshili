#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""技能处理器函数 — 供 SkillRegistry function-type handlers 引用

每个函数签名: fn(*, user: dict, user_text: str) -> dict
返回: {'success': bool, 'reply': str}
"""

import time

from tools import _get_users_db_conn


def get_my_info(*, user: dict, user_text: str = None) -> dict:
    """返回当前登录用户的个人信息"""
    role = user.get('role_name', '未知')
    dept = user.get('department_name') or '无'
    username = user.get('username', '未知')
    level = user.get('role_level', 0)
    level_names = {6: '超级管理员', 5: '董事长', 4: '总经理', 3: '部门长', 2: '职员', 1: '游客'}
    return {
        'success': True,
        'reply': (
            f'**你的账号信息**\n'
            f'  • 用户名: {username}\n'
            f'  • 角色: {role}（{level_names.get(level, "未知")}，等级 {level}）\n'
            f'  • 部门: {dept}'
        ),
    }


def get_online_users(*, user: dict, user_text: str = None) -> dict:
    """查询当前活跃会话列表"""
    conn = _get_users_db_conn()
    now = time.time()
    rows = conn.execute(
        'SELECT u.username, u.role, us.last_active FROM user_sessions us '
        'JOIN users u ON u.id = us.user_id '
        'WHERE us.expires_at > ? AND u.is_active=1 '
        'ORDER BY us.last_active DESC LIMIT 20',
        (now,)
    ).fetchall()
    if rows:
        user_list = '\n'.join(
            f'  • {r[0]}（{r[1]}，最后活跃: {time.strftime("%H:%M", time.localtime(r[2]))}）'
            for r in rows
        )
        return {
            'success': True,
            'reply': f'当前有 **{len(rows)}** 个活跃会话：\n{user_list}',
        }
    else:
        return {'success': True, 'reply': '当前没有活跃的登录会话。'}


def get_my_approver(*, user: dict, user_text: str = None) -> dict:
    """查询当前用户的请假审批人"""
    from role_levels import SUPERIOR_MAP, ROLE_NAMES
    role = user.get('role', 'guest')
    superior_role = SUPERIOR_MAP.get(role)
    if not superior_role:
        return {
            'success': True,
            'reply': f'你当前的角色是 **{user.get("role_name", role)}**，已是最高审批层级，无需他人审批。',
        }

    superior_name = ROLE_NAMES.get(superior_role, superior_role)
    conn = _get_users_db_conn()
    rows = conn.execute(
        'SELECT u.username, d.name as dept_name FROM users u '
        'LEFT JOIN departments d ON u.department_id = d.id '
        'WHERE u.role = ? AND u.is_active = 1',
        (superior_role,)
    ).fetchall()

    if rows:
        approvers = '\n'.join(
            f'  • {r[0]}（{r[1] or "无部门"}）'
            for r in rows
        )
        return {
            'success': True,
            'reply': (
                f'你的角色是 **{user.get("role_name", role)}**，'
                f'请假需要 **{superior_name}** 审批。\n\n'
                f'当前可审批的{superior_name}：\n{approvers}'
            ),
        }
    else:
        return {
            'success': True,
            'reply': (
                f'你的角色是 **{user.get("role_name", role)}**，'
                f'请假需要 **{superior_name}** 审批。\n\n'
                f'当前系统没有在线的{superior_name}，请联系管理员。'
            ),
        }
