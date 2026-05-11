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


def search_directory(*, user: dict, user_text: str = None) -> dict:
    """通讯录搜索：按姓名或部门搜索员工"""
    import re
    conn = _get_users_db_conn()

    stop_words = {'帮我', '查一下', '一下', '搜索', '查找', '请问', '有没有', '谁是',
                  '找一找', '看看', '我想', '我要', '想知道', '帮我查', '查查', '找找',
                  '通讯录', '联系方式', '电话', '员工', '同事', '部门'}
    text = user_text or ''

    name_match = re.findall(r'[一-鿿]{2,3}', text)
    keywords = [w for w in name_match if w not in stop_words]
    # 同时提取英文/数字词（如 jiangteng, zhangliang）
    ascii_match = re.findall(r'[a-zA-Z][a-zA-Z0-9_]{2,}', text)
    keywords += [w for w in ascii_match if w.lower() not in stop_words]
    if not keywords:
        keywords = [text.strip()]

    query = ' OR '.join(['username LIKE ?' for _ in keywords])
    params = [f'%{k}%' for k in keywords]
    query += ' OR ' + ' OR '.join(['d.name LIKE ?' for _ in keywords])
    params += [f'%{k}%' for k in keywords]

    rows = conn.execute(
        f'SELECT u.username, u.email, u.role, d.name as dept_name '
        f'FROM users u LEFT JOIN departments d ON u.department_id = d.id '
        f'WHERE u.is_active = 1 AND ({query}) '
        f'ORDER BY u.username LIMIT 15',
        params
    ).fetchall()

    if not rows:
        return {'success': True, 'reply': f'未找到与 "{text}" 相关的员工。请尝试用姓名或部门名搜索。'}

    role_names = {'super_admin': '超级管理员', 'chairman': '董事长', 'gm': '总经理',
                  'dept_head': '部门长', 'staff': '职员', 'guest': '游客'}
    result = '\n'.join(
        f'  • **{r[0]}** | {role_names.get(r[2], r[2])} | {r[3] or "无部门"} | {r[1] or "无邮箱"}'
        for r in rows
    )
    return {'success': True, 'reply': f'找到 **{len(rows)}** 名员工：\n{result}'}


def get_my_permissions(*, user: dict, user_text: str = None) -> dict:
    """返回当前用户的权限说明"""
    role = user.get('role', 'guest')
    level = user.get('role_level', 0)

    perm_map = {
        6: ('超级管理员', [
            '全部系统功能和管理权限',
            '用户管理：创建、删除、角色变更、密码重置',
            '部门管理：创建、修改、删除部门',
            '制度管理：制定和发布公司制度政策',
            '审计日志：查看所有操作记录',
            '文件操作：读写所有目录文件',
            '审批链最高级：可审批所有请假',
        ]),
        5: ('董事长', [
            '用户管理：查看所有用户信息',
            '部门管理：查看所有部门信息',
            '制度管理：制定和发布公司制度政策',
            '审计日志：查看所有操作记录',
            '文件操作：读写所有目录文件',
            '请假审批：审批总经理及以下员工的请假',
        ]),
        4: ('总经理', [
            '用户管理：查看本部门及下级部门用户',
            '制度管理：制定部门级制度',
            '文件操作：读写公司级公共目录',
            '请假审批：审批部门长及以下员工的请假',
        ]),
        3: ('部门长', [
            '用户管理：查看本部门员工信息',
            '文件操作：读写本部门目录',
            '请假审批：审批本部门员工的请假',
        ]),
        2: ('职员', [
            '个人信息：查看和修改自己的信息',
            '请假申请：提交请假申请',
            '文件操作：读写个人和共享目录',
            '查询：查看通讯录、制度、部门信息',
        ]),
        1: ('游客', [
            '仅可查看公开信息（制度、部门列表等）',
            '无文件读写权限',
            '无法提交请假申请',
        ]),
    }

    role_name, permissions = perm_map.get(level, ('未知', ['无权限信息']))
    perm_lines = '\n'.join(f'  • {p}' for p in permissions)
    return {
        'success': True,
        'reply': (
            f'**你的权限信息**\n'
            f'  角色：**{role_name}**（等级 {level}/6）\n'
            f'  用户名：{user.get("username", "未知")}\n'
            f'  部门：{user.get("department_name") or "无"}\n\n'
            f'**可执行操作：**\n{perm_lines}'
        ),
    }


def search_policies(*, user: dict, user_text: str = None) -> dict:
    """制度搜索：按关键词搜索相关政策"""
    import os, sqlite3

    text = user_text or ''
    kw_map = {
        '考勤': '%leave%', '请假': '%leave%', '休假': '%leave%', '调休': '%leave%',
        '出差': '%travel%', '差旅': '%travel%',
        '报销': '%expense%', '费用': '%expense%',
        '培训': '%training%', '学习': '%training%',
        '安全': '%safety%', '消防': '%safety%',
        '质量': '%quality%', 'GMP': '%quality%', '质检': '%quality%',
        '采购': '%purchase%', '供应商': '%purchase%',
        '合同': '%contract%', '印章': '%contract%', '用印': '%contract%',
        '会议': '%meeting%', '用车': '%vehicle%',
        '绩效': '%performance%', '考核': '%performance%',
        '福利': '%benefit%', '薪酬': '%benefit%', '工资': '%benefit%',
    }

    matched_types = set()
    for kw, db_type in kw_map.items():
        if kw in text:
            matched_types.add(db_type)

    if matched_types:
        clauses = ' OR '.join(['type LIKE ?' for _ in matched_types])
        param_list = list(matched_types)
    else:
        clauses = ' OR '.join(['name LIKE ?' for _ in kw_map.values()])
        param_list = list(kw_map.values())

    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'data', 'app.db')
    conn = sqlite3.connect(db_path)

    rows = conn.execute(
        f'SELECT name, type, '
        f'CASE WHEN scope = "all" THEN "全员适用" ELSE scope END as scope_name, '
        f'rules, creator_name, '
        f'datetime(created_at, "unixepoch", "localtime") as created_date '
        f'FROM policies WHERE is_active = 1 AND ({clauses}) '
        f'ORDER BY type, created_at DESC LIMIT 15',
        param_list
    ).fetchall()

    # 如果精确匹配没有结果，回退到全部制度
    if not rows:
        rows = conn.execute(
            'SELECT name, type, '
            'CASE WHEN scope = "all" THEN "全员适用" ELSE scope END as scope_name, '
            'rules, creator_name, '
            'datetime(created_at, "unixepoch", "localtime") as created_date '
            'FROM policies WHERE is_active = 1 '
            'ORDER BY type, created_at DESC LIMIT 10'
        ).fetchall()

    conn.close()

    if not rows:
        return {'success': True, 'reply': '未找到相关制度。你可以尝试输入"考勤制度"、"报销制度"、"培训制度"等关键词。'}

    result = '\n'.join(
        f'  • **{r[0]}**（{r[2]}）\n    规则：{r[3]}\n    制定人：{r[4]} | {r[5]}'
        for r in rows
    )
    return {'success': True, 'reply': f'找到 **{len(rows)}** 条相关制度：\n{result}'}
