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
