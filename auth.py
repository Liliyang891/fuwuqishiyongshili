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


def _hash_password(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt(rounds=12)).decode('utf-8')


def _verify_password(password, password_hash):
    return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))


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
