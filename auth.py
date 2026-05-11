#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用户认证和权限管理模块"""

import json
import os
import re
import time
import uuid

import bcrypt

from role_levels import ROLE_LEVEL, ROLE_NAMES, ROLE_DISPLAY, SUPERIOR_MAP, LEVEL_TO_ROLE
from tools import _get_users_db_conn, _resolve_path, FILES_DIR, ALLOWED_ROOTS

# 角色等级映射 — 从 role_levels 导入
USERNAME_PATTERN = re.compile(r'^[一-鿿a-zA-Z0-9_]{2,20}$')
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
    # Shell 执行 (Phase 2)
    'Bash': ('guest', 'bash'),
    'Powershell': ('guest', 'bash'),
    # 搜索工具 (Phase 2)
    'Glob': ('guest', 'read'),
    'Grep': ('guest', 'read'),
    # 编辑工具 (Phase 2)
    'Edit': ('staff', 'write'),
    # Web 工具 (Phase 2)
    'WebFetch': ('guest', 'web'),
    'WebSearch': ('guest', 'web'),
    # Agent 工具 (Phase 3)
    'Agent': ('staff', 'agent'),
    'TaskCreate': ('staff', 'task'),
    'TaskUpdate': ('staff', 'task'),
    'TaskList': ('staff', 'task'),
    'TaskGet': ('staff', 'task'),
    'TodoWrite': ('staff', 'task'),
    # 计划模式 (Phase 3)
    'EnterPlanMode': ('staff', 'plan'),
    'ExitPlanMode': ('staff', 'plan'),
    # 策略框架 (Phase 4)
    'CreatePolicy': ('dept_head', 'policy'),
    'ApplyLeave': ('staff', 'policy'),
    'QueryPolicies': ('guest', 'policy'),
    'ApproveLeave': ('dept_head', 'policy'),
    'RejectLeave': ('dept_head', 'policy'),
    'LeaveHistory': ('staff', 'policy'),
    'DeactivatePolicy': ('dept_head', 'policy'),
}

DANGEROUS_ACTIONS = {
    'delete_file': 'file_delete',
    'delete_folder': 'file_delete',
    'db_drop_table': 'db_drop',
    'Bash': 'bash_exec',
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
    """返回用户可访问的文件路径列表（层级权限）

    高级别角色可访问 ≤ 自己等级的所有角色目录 + share/(staff+)
    """
    if not user:
        return [os.path.realpath(FILES_DIR)]

    DATA_DIR = os.path.dirname(FILES_DIR)
    level = user.get('role_level', 0)
    roots = [os.path.realpath(FILES_DIR)]

    for role_name, role_level in ROLE_LEVEL.items():
        if role_level <= level:
            role_dir = os.path.realpath(os.path.join(FILES_DIR, role_name))
            if role_dir not in roots:
                roots.append(role_dir)

    if level >= ROLE_LEVEL.get('staff', 2):
        share_dir = os.path.realpath(os.path.join(DATA_DIR, 'share'))
        if share_dir not in roots:
            roots.append(share_dir)

    return roots


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


def _log_audit(user_id, action, detail=''):
    conn = _get_users_db_conn()
    try:
        conn.execute(
            'INSERT INTO audit_log (user_id, action, detail, created_at) VALUES (?,?,?,?)',
            (user_id, action, detail, time.time())
        )
        conn.commit()
    except Exception:
        pass  # 审计日志写入失败不应影响主流程
    # conn 由线程本地缓存管理，不关闭


def register_user(username, password, email=None):
    """注册新用户，默认游客角色"""
    if not USERNAME_PATTERN.match(username):
        raise ValueError('用户名格式无效：需要 2-20 字符（支持中文、字母、数字、下划线）')
    if len(password) < 8 or not password.isdigit():
        raise ValueError('密码需要至少 8 位数字')

    conn = _get_users_db_conn()
    existing = conn.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()
    if existing:
        # conn 由线程本地缓存管理，不关闭
        raise ValueError('用户名已存在')
    if email:
        existing = conn.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone()
        if existing:
            # conn 由线程本地缓存管理，不关闭
            raise ValueError('邮箱已被使用')

    password_hash = _hash_password(password)
    now = time.time()
    cursor = conn.execute(
        'INSERT INTO users (username, email, password_hash, role, department_id, is_active, created_at) VALUES (?,?,?,?,?,?,?)',
        (username, email, password_hash, 'guest', None, 1, now)
    )
    user_id = cursor.lastrowid
    conn.commit()
    _log_audit(user_id, 'user_register', json.dumps({'username': username, 'email': email}, ensure_ascii=False))

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
    conn = _get_users_db_conn()
    user = conn.execute(
        'SELECT id, username, email, password_hash, role, department_id, is_active FROM users WHERE username=? OR email=?',
        (login, login)
    ).fetchone()
    if not user:
        # conn 由线程本地缓存管理，不关闭
        raise ValueError('用户不存在')
    user = dict(user)
    if not user['is_active']:
        # conn 由线程本地缓存管理，不关闭
        raise ValueError('账号已被禁用，请联系管理员')
    if not _verify_password(password, user['password_hash']):
        # conn 由线程本地缓存管理，不关闭
        raise ValueError('密码错误')

    token = str(uuid.uuid4())
    now = time.time()
    ttl = SESSION_TTL_REMEMBER if remember_me else SESSION_TTL
    expires_at = now + ttl
    conn.execute(
        'INSERT OR REPLACE INTO user_sessions (token, user_id, created_at, expires_at, last_active) VALUES (?,?,?,?,?)',
        (token, user['id'], now, expires_at, now)
    )
    conn.commit()
    _log_audit(user['id'], 'user_login', json.dumps({'remember_me': remember_me}, ensure_ascii=False))
    return token


def logout_session(token, user_id=None):
    """删除登录会话，可选传入 user_id 用于审计"""
    conn = _get_users_db_conn()
    conn.execute('DELETE FROM user_sessions WHERE token=?', (token,))
    conn.commit()
    if user_id:
        _log_audit(user_id, 'user_logout', '')
    # conn 由线程本地缓存管理，不关闭


def get_user_by_token(token):
    """通过 session token 获取当前用户，过期返回 None"""
    conn = _get_users_db_conn()
    now = time.time()
    row = conn.execute(
        '''SELECT u.id, u.username, u.email, u.role, u.department_id, u.is_active
           FROM user_sessions s JOIN users u ON s.user_id = u.id
           WHERE s.token=? AND s.expires_at > ? AND u.is_active = 1''',
        (token, now)
    ).fetchone()
    if not row:
        return None
    # 更新最后活跃时间
    try:
        conn.execute('UPDATE user_sessions SET last_active=? WHERE token=?', (now, token))
        conn.commit()
    except Exception:
        pass
    user = dict(row)
    user['role_name'] = ROLE_NAMES.get(user['role'], user['role'])
    user['role_level'] = ROLE_LEVEL.get(user['role'], 0)
    if user['department_id']:
        user['department_name'] = _get_department_name(user['department_id'])
    else:
        user['department_name'] = None
    return user


def _get_department_name(dept_id):
    conn = _get_users_db_conn()
    row = conn.execute('SELECT name FROM departments WHERE id=?', (dept_id,)).fetchone()
    # conn 由线程本地缓存管理，不关闭
    return row['name'] if row else None


# ========== 部门管理 ==========

def create_department(name):
    if not name or not name.strip():
        raise ValueError('部门名称不能为空')
    name = name.strip()
    conn = _get_users_db_conn()
    existing = conn.execute('SELECT id FROM departments WHERE name=?', (name,)).fetchone()
    if existing:
        # conn 由线程本地缓存管理，不关闭
        raise ValueError(f'部门已存在: {name}')
    now = time.time()
    cursor = conn.execute('INSERT INTO departments (name, created_at) VALUES (?,?)', (name, now))
    dept_id = cursor.lastrowid
    conn.commit()
    _log_audit(None, 'dept_create', json.dumps({'dept_id': dept_id, 'name': name}, ensure_ascii=False))
    # conn 由线程本地缓存管理，不关闭
    return {'id': dept_id, 'name': name}


def list_departments():
    conn = _get_users_db_conn()
    rows = conn.execute(
        '''SELECT d.id, d.name, d.created_at,
                  (SELECT COUNT(*) FROM users u WHERE u.department_id = d.id) as user_count
           FROM departments d ORDER BY d.id'''
    ).fetchall()
    # conn 由线程本地缓存管理，不关闭
    return [{'id': r['id'], 'name': r['name'], 'user_count': r['user_count']} for r in rows]


def update_department(dept_id, name, operator_id=None):
    if not name or not name.strip():
        raise ValueError('部门名称不能为空')
    conn = _get_users_db_conn()
    conn.execute('UPDATE departments SET name=? WHERE id=?', (name.strip(), dept_id))
    conn.commit()
    _log_audit(operator_id, 'dept_update',
               json.dumps({'dept_id': dept_id, 'name': name.strip()}, ensure_ascii=False))
    # conn 由线程本地缓存管理，不关闭


def delete_department(dept_id, operator_id=None):
    conn = _get_users_db_conn()
    count = conn.execute('SELECT COUNT(*) as c FROM users WHERE department_id=?', (dept_id,)).fetchone()['c']
    if count > 0:
        # conn 由线程本地缓存管理，不关闭
        raise ValueError(f'该部门下有 {count} 个用户，无法删除')
    conn.execute('DELETE FROM departments WHERE id=?', (dept_id,))
    conn.commit()
    _log_audit(operator_id, 'dept_delete',
               json.dumps({'dept_id': dept_id}, ensure_ascii=False))
    # conn 由线程本地缓存管理，不关闭


# ========== 用户管理 ==========

def list_users(role=None, department_id=None, active=None):
    conn = _get_users_db_conn()
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
    # conn 由线程本地缓存管理，不关闭
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


def update_user_role(user_id, role, department_id=None, operator_id=None):
    if role not in ROLE_LEVEL:
        raise ValueError(f'无效的角色: {role}')
    conn = _get_users_db_conn()
    old = conn.execute('SELECT role, department_id FROM users WHERE id=?', (user_id,)).fetchone()
    conn.execute(
        'UPDATE users SET role=?, department_id=? WHERE id=?',
        (role, department_id, user_id)
    )
    conn.commit()
    _log_audit(operator_id or user_id, 'user_role_change',
               json.dumps({'target_id': user_id, 'old_role': old['role'] if old else '?',
                           'new_role': role, 'new_dept': department_id}, ensure_ascii=False))


def toggle_user_active(user_id, active, operator_id=None):
    conn = _get_users_db_conn()
    conn.execute('UPDATE users SET is_active=? WHERE id=?', (1 if active else 0, user_id))
    conn.commit()
    _log_audit(operator_id or user_id, 'user_toggle_active',
               json.dumps({'target_id': user_id, 'active': active}, ensure_ascii=False))
    # conn 由线程本地缓存管理，不关闭


def reset_user_password(user_id, new_password, operator_id=None):
    if len(new_password) < 8 or not new_password.isdigit():
        raise ValueError('密码需要至少 8 位数字')
    conn = _get_users_db_conn()
    conn.execute(
        'UPDATE users SET password_hash=? WHERE id=?',
        (_hash_password(new_password), user_id)
    )
    conn.commit()
    _log_audit(operator_id or user_id, 'user_password_reset',
               json.dumps({'target_id': user_id}, ensure_ascii=False))
    # conn 由线程本地缓存管理，不关闭
    return new_password


def delete_user(user_id, operator_id=None):
    conn = _get_users_db_conn()
    target = conn.execute('SELECT username FROM users WHERE id=?', (user_id,)).fetchone()
    conn.execute('DELETE FROM user_sessions WHERE user_id=?', (user_id,))
    conn.execute('DELETE FROM users WHERE id=?', (user_id,))
    conn.commit()
    _log_audit(operator_id or user_id, 'user_delete',
               json.dumps({'target_id': user_id, 'username': target['username'] if target else '?'}, ensure_ascii=False))
    # conn 由线程本地缓存管理，不关闭


def get_audit_logs(user_id=None, action=None, from_time=None, to_time=None, limit=200):
    conn = _get_users_db_conn()
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
    # conn 由线程本地缓存管理，不关闭
    return [dict(r) for r in rows]
