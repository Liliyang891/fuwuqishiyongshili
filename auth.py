#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用户认证和权限管理模块"""

import json
import os
import re
import time
import uuid

import bcrypt

from tools import _get_db_conn, _resolve_path, FILES_DIR, ALLOWED_ROOTS

# 角色等级映射
ROLE_LEVEL = {
    'super_admin': 6,
    'chairman': 5,
    'gm': 4,
    'dept_head': 3,
    'staff': 2,
    'guest': 1,
}

ROLE_NAMES = {
    'super_admin': '超级管理员',
    'chairman': '董事长',
    'gm': '总经理',
    'dept_head': '部门长',
    'staff': '部门职员',
    'guest': '游客',
}

USERNAME_PATTERN = re.compile(r'^[a-zA-Z0-9_]{3,20}$')
SESSION_TTL = 24 * 3600
SESSION_TTL_REMEMBER = 7 * 24 * 3600

# 工具权限映射：工具名 -> (最小角色等级, 可执行的操作类型)
TOOL_PERMISSIONS = {
    # 文件读取
    'read_file': ('guest', 'read'),
    'get_file_info': ('guest', 'read'),
    'search_files': ('guest', 'read'),
    'search_content': ('guest', 'read'),
    'get_file_hash': ('guest', 'read'),
    'batch_read': ('guest', 'read'),
    'list_folder': ('guest', 'read'),
    'count_items': ('guest', 'read'),
    # 文件写入
    'write_file': ('staff', 'write'),
    'append_file': ('staff', 'write'),
    'insert_text': ('staff', 'write'),
    'replace_text': ('staff', 'write'),
    'delete_lines': ('staff', 'write'),
    'create_folder': ('staff', 'write'),
    # 文件管理
    'move_file': ('staff', 'write'),
    'copy_file': ('staff', 'write'),
    'move_folder': ('staff', 'write'),
    'copy_folder': ('staff', 'write'),
    # 文件删除（仅超管）
    'delete_file': ('super_admin', 'delete'),
    'delete_folder': ('super_admin', 'delete'),
    # 上传
    'save_uploaded_file': ('staff', 'write'),
    # 压缩
    'zip_files': ('staff', 'write'),
    'unzip_file': ('staff', 'write'),
    # DB 读取
    'db_list_tables': ('staff', 'read_db'),
    'db_describe_table': ('staff', 'read_db'),
    'db_query': ('staff', 'read_db'),
    'db_create_table': ('gm', 'write_db'),
    # DB 写入
    'db_execute': ('dept_head', 'write_db'),
    # DB 删除
    'db_drop_table': ('super_admin', 'delete_db'),
}

DANGEROUS_ACTIONS = {
    'delete_file': 'file_delete',
    'delete_folder': 'file_delete',
    'db_drop_table': 'db_drop',
}


def _hash_password(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt(rounds=12)).decode('utf-8')


def _verify_password(password, password_hash):
    return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))


def get_role_level(role_name):
    return ROLE_LEVEL.get(role_name, 0)


def get_allowed_tools(user):
    """根据用户角色返回允许的工具定义列表"""
    from tools import get_tools_definition
    all_tools = get_tools_definition()
    role = user.get('role', 'guest')
    user_level = ROLE_LEVEL.get(role, 0)

    allowed = []
    for tool in all_tools:
        tool_name = tool['function']['name']
        perm = TOOL_PERMISSIONS.get(tool_name)
        if perm is None:
            allowed.append(tool)
        else:
            min_role, _ = perm
            min_level = ROLE_LEVEL.get(min_role, 0)
            if user_level >= min_level:
                allowed.append(tool)
    return allowed


def get_file_scope(user):
    """返回用户文件操作范围"""
    role = user.get('role', 'guest')
    dept_id = user.get('department_id')
    if role in ('super_admin', 'chairman', 'gm'):
        return 'all'
    if role in ('dept_head', 'staff'):
        if dept_id:
            dept_name = _get_department_name(dept_id)
            return os.path.join(FILES_DIR, dept_name) if dept_name else 'public'
        return 'department'
    return 'public'


def can_execute_tool(tool_name, user, file_path=None):
    """检查用户是否可以执行指定工具
    返回: (allowed: bool, message: str)
    """
    role = user.get('role', 'guest')
    user_level = ROLE_LEVEL.get(role, 0)

    perm = TOOL_PERMISSIONS.get(tool_name)
    if perm is None:
        return True, ''

    min_role, action_type = perm
    min_level = ROLE_LEVEL.get(min_role, 0)
    if user_level < min_level:
        role_name = ROLE_NAMES.get(role, role)
        return False, f"权限不足：{role_name}不能执行{tool_name}操作"

    if tool_name in DANGEROUS_ACTIONS:
        _log_audit(user.get('id'), DANGEROUS_ACTIONS[tool_name],
                   json.dumps({'tool': tool_name, 'path': file_path}, ensure_ascii=False))

    return True, ''


def _log_audit(user_id, action, detail):
    conn = _get_db_conn()
    conn.execute(
        'INSERT INTO audit_log (user_id, action, detail, created_at) VALUES (?,?,?,?)',
        (user_id, action, detail, time.time())
    )
    conn.commit()
    conn.close()


def register_user(username, password, email=None):
    """注册新用户，默认游客角色"""
    if not USERNAME_PATTERN.match(username):
        raise ValueError('用户名格式无效：需要 3-20 个字符，仅字母数字下划线')
    if len(password) < 6:
        raise ValueError('密码至少需要 6 个字符')

    conn = _get_db_conn()
    existing = conn.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()
    if existing:
        conn.close()
        raise ValueError('用户名已存在')
    if email:
        existing = conn.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone()
        if existing:
            conn.close()
            raise ValueError('邮箱已被使用')

    password_hash = _hash_password(password)
    now = time.time()
    cursor = conn.execute(
        'INSERT INTO users (username, email, password_hash, role, department_id, is_active, created_at) VALUES (?,?,?,?,?,?,?)',
        (username, email, password_hash, 'guest', None, 1, now)
    )
    user_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return {
        'id': user_id,
        'username': username,
        'email': email,
        'role': 'guest',
        'role_name': '游客',
        'department_id': None,
        'is_active': 1,
    }


def login_user(login, password, remember_me=False):
    """登录验证，返回 session token"""
    conn = _get_db_conn()
    user = conn.execute(
        'SELECT id, username, email, password_hash, role, department_id, is_active FROM users WHERE username=? OR email=?',
        (login, login)
    ).fetchone()
    if not user:
        conn.close()
        raise ValueError('用户不存在')
    user = dict(user)
    if not user['is_active']:
        conn.close()
        raise ValueError('账号已被禁用，请联系管理员')
    if not _verify_password(password, user['password_hash']):
        conn.close()
        raise ValueError('密码错误')

    token = str(uuid.uuid4())
    now = time.time()
    ttl = SESSION_TTL_REMEMBER if remember_me else SESSION_TTL
    expires_at = now + ttl
    conn.execute(
        'INSERT OR REPLACE INTO user_sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)',
        (token, user['id'], now, expires_at)
    )
    conn.commit()
    conn.close()
    return token


def logout_session(token):
    """删除登录会话"""
    conn = _get_db_conn()
    conn.execute('DELETE FROM user_sessions WHERE token=?', (token,))
    conn.commit()
    conn.close()


def get_user_by_token(token):
    """通过 session token 获取当前用户，过期返回 None"""
    conn = _get_db_conn()
    now = time.time()
    row = conn.execute(
        '''SELECT u.id, u.username, u.email, u.role, u.department_id, u.is_active
           FROM user_sessions s JOIN users u ON s.user_id = u.id
           WHERE s.token=? AND s.expires_at > ? AND u.is_active = 1''',
        (token, now)
    ).fetchone()
    conn.close()
    if not row:
        return None
    user = dict(row)
    user['role_name'] = ROLE_NAMES.get(user['role'], user['role'])
    user['role_level'] = ROLE_LEVEL.get(user['role'], 0)
    if user['department_id']:
        user['department_name'] = _get_department_name(user['department_id'])
    else:
        user['department_name'] = None
    return user


def _get_department_name(dept_id):
    conn = _get_db_conn()
    row = conn.execute('SELECT name FROM departments WHERE id=?', (dept_id,)).fetchone()
    conn.close()
    return row['name'] if row else None


# ========== 部门管理 ==========

def create_department(name):
    if not name or not name.strip():
        raise ValueError('部门名称不能为空')
    name = name.strip()
    conn = _get_db_conn()
    existing = conn.execute('SELECT id FROM departments WHERE name=?', (name,)).fetchone()
    if existing:
        conn.close()
        raise ValueError(f'部门已存在: {name}')
    now = time.time()
    cursor = conn.execute('INSERT INTO departments (name, created_at) VALUES (?,?)', (name, now))
    dept_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return {'id': dept_id, 'name': name}


def list_departments():
    conn = _get_db_conn()
    rows = conn.execute(
        '''SELECT d.id, d.name, d.created_at,
                  (SELECT COUNT(*) FROM users u WHERE u.department_id = d.id) as user_count
           FROM departments d ORDER BY d.id'''
    ).fetchall()
    conn.close()
    return [{'id': r['id'], 'name': r['name'], 'user_count': r['user_count']} for r in rows]


def update_department(dept_id, name):
    if not name or not name.strip():
        raise ValueError('部门名称不能为空')
    conn = _get_db_conn()
    conn.execute('UPDATE departments SET name=? WHERE id=?', (name.strip(), dept_id))
    conn.commit()
    conn.close()


def delete_department(dept_id):
    conn = _get_db_conn()
    count = conn.execute('SELECT COUNT(*) as c FROM users WHERE department_id=?', (dept_id,)).fetchone()['c']
    if count > 0:
        conn.close()
        raise ValueError(f'该部门下有 {count} 个用户，无法删除')
    conn.execute('DELETE FROM departments WHERE id=?', (dept_id,))
    conn.commit()
    conn.close()


# ========== 用户管理 ==========

def list_users(role=None, department_id=None, active=None):
    conn = _get_db_conn()
    sql = 'SELECT u.id, u.username, u.email, u.role, u.department_id, u.is_active, u.created_at FROM users u WHERE 1=1'
    params = []
    if role:
        sql += ' AND u.role=?'
        params.append(role)
    if department_id is not None:
        sql += ' AND u.department_id=?'
        params.append(department_id)
    if active is not None:
        sql += ' AND u.is_active=?'
        params.append(1 if active else 0)
    sql += ' ORDER BY u.id'
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d['role_name'] = ROLE_NAMES.get(d['role'], d['role'])
        if d['department_id']:
            d['department_name'] = _get_department_name(d['department_id'])
        else:
            d['department_name'] = None
        result.append(d)
    return result


def update_user_role(user_id, role, department_id=None):
    if role not in ROLE_LEVEL:
        raise ValueError(f'无效的角色: {role}')
    conn = _get_db_conn()
    try:
        conn.execute(
            'UPDATE users SET role=?, department_id=? WHERE id=?',
            (role, department_id, user_id)
        )
        conn.commit()
    finally:
        conn.close()


def toggle_user_active(user_id, active):
    conn = _get_db_conn()
    conn.execute('UPDATE users SET is_active=? WHERE id=?', (1 if active else 0, user_id))
    conn.commit()
    conn.close()


def reset_user_password(user_id, new_password):
    if len(new_password) < 6:
        raise ValueError('密码至少需要 6 个字符')
    conn = _get_db_conn()
    conn.execute(
        'UPDATE users SET password_hash=? WHERE id=?',
        (_hash_password(new_password), user_id)
    )
    conn.commit()
    conn.close()
    return new_password


def delete_user(user_id):
    conn = _get_db_conn()
    conn.execute('DELETE FROM user_sessions WHERE user_id=?', (user_id,))
    conn.execute('DELETE FROM users WHERE id=?', (user_id,))
    conn.commit()
    conn.close()


def get_audit_logs(user_id=None, action=None, from_time=None, to_time=None, limit=200):
    conn = _get_db_conn()
    sql = 'SELECT a.id, a.user_id, u.username, a.action, a.detail, a.created_at FROM audit_log a LEFT JOIN users u ON a.user_id = u.id WHERE 1=1'
    params = []
    if user_id:
        sql += ' AND a.user_id=?'
        params.append(user_id)
    if action:
        sql += ' AND a.action=?'
        params.append(action)
    if from_time:
        sql += ' AND a.created_at >= ?'
        params.append(from_time)
    if to_time:
        sql += ' AND a.created_at <= ?'
        params.append(to_time)
    sql += ' ORDER BY a.id DESC LIMIT ?'
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]
