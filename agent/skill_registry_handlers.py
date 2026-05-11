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


def search_announcements(*, user: dict, user_text: str = None) -> dict:
    """公告搜索：按类别或关键词搜索公告"""
    import os, sqlite3, time

    text = user_text or ''
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'data', 'app.db')
    conn = sqlite3.connect(db_path)

    now = time.time()
    # 提取类别关键词
    cat_map = {
        '紧急': 'urgent', '重要': 'urgent',
        '活动': 'event', '会议': 'event',
        '放假': 'general', '假期': 'general',
        '制度': 'policy', '政策': 'policy',
    }

    matched_cats = set()
    for kw, cat in cat_map.items():
        if kw in text:
            matched_cats.add(cat)

    if matched_cats:
        clauses = ' OR '.join(['category = ?' for _ in matched_cats])
        rows = conn.execute(
            f'SELECT title, content, category, creator_name, '
            f'datetime(created_at, "unixepoch", "localtime") as pub_date '
            f'FROM announcements WHERE is_active = 1 AND (expires_at IS NULL OR expires_at > ?) '
            f'AND ({clauses}) '
            f'ORDER BY priority DESC, created_at DESC LIMIT 10',
            [now] + list(matched_cats)
        ).fetchall()
    else:
        rows = conn.execute(
            'SELECT title, content, category, creator_name, '
            'datetime(created_at, "unixepoch", "localtime") as pub_date '
            'FROM announcements WHERE is_active = 1 AND (expires_at IS NULL OR expires_at > ?) '
            'ORDER BY priority DESC, created_at DESC LIMIT 10',
            (now,)
        ).fetchall()

    conn.close()

    if not rows:
        return {'success': True, 'reply': '当前没有相关公告。'}

    cat_names = {'urgent': '紧急', 'event': '活动', 'policy': '制度', 'general': '一般'}
    result = '\n'.join(
        f'  • [{cat_names.get(r[2], r[2])}] **{r[0]}** — {r[4]}\n    {r[1][:120]}'
        for r in rows
    )
    return {'success': True, 'reply': f'找到 **{len(rows)}** 条公告：\n{result}'}


def search_sop(*, user: dict, user_text: str = None) -> dict:
    """SOP搜索：按关键词/部门搜索标准操作规程"""
    import os, sqlite3

    text = user_text or ''
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'data', 'app.db')
    conn = sqlite3.connect(db_path)

    # 从输入中提取搜索词（滑动窗口方式，避免贪婪匹配遗漏关键词）
    import re
    stop = {'搜索', '查', '找', '查看', '查查', '找找', 'sop', 'SOP',
            '操作规程', '标准操作', '标准操作规程', '操作流程', '规程',
            '在哪', '有哪些', '哪个', '什么', '哪些', '帮我', '一下'}

    # 先移除停用词再提取
    cleaned = text
    for w in sorted(stop, key=len, reverse=True):
        cleaned = cleaned.replace(w, ' ')
    # 提取所有2-3字中文片段（滑动窗口，非贪婪）
    search_terms = []
    for start in range(len(cleaned)):
        for length in (3, 2):
            seg = cleaned[start:start+length]
            if len(seg) == length and all('一' <= c <= '鿿' for c in seg):
                if seg not in stop:
                    search_terms.append(seg)
    # 去重保持顺序
    seen = set()
    search_terms = [t for t in search_terms if not (t in seen or seen.add(t))][:6]

    if search_terms:
        clauses = ' OR '.join(['(title LIKE ? OR keywords LIKE ? OR content_summary LIKE ?)' for _ in search_terms])
        params = []
        for t in search_terms:
            params += [f'%{t}%', f'%{t}%', f'%{t}%']
        rows = conn.execute(
            f'SELECT title, doc_number, version, category, department_name, content_summary '
            f'FROM sop_documents WHERE status = \'active\' AND ({clauses}) '
            f'ORDER BY updated_at DESC LIMIT 12',
            params
        ).fetchall()
    else:
        rows = conn.execute(
            'SELECT title, doc_number, version, category, department_name, content_summary '
            'FROM sop_documents WHERE status = \'active\' '
            'ORDER BY updated_at DESC LIMIT 12'
        ).fetchall()

    conn.close()

    if not rows:
        return {'success': True,
                'reply': '未找到相关SOP。你可以尝试搜索"称量"、"变更"、"偏差"、"检测"等关键词。'}

    result = '\n'.join(
        f'  • [{r[3]}] **{r[0]}** v{r[2]}（{r[1]}）\n    部门：{r[4] or "通用"} | {r[5][:100]}'
        for r in rows
    )
    return {'success': True, 'reply': f'找到 **{len(rows)}** 条SOP：\n{result}'}
