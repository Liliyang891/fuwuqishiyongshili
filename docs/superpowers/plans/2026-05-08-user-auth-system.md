# 用户注册登录系统 — 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为现有 AI Agent 服务器新增 6 级角色的用户注册登录系统和管理后台

**架构：** 新建 auth.py 模块处理认证和权限，web_server.py 增加路由和中间件，tools.py 增加权限检查，公共 SQLite 数据库新增 4 张表（users、departments、user_sessions、audit_log），前端新增 login/register/admin 三个 HTML 页面

**技术栈：** Python 3.11+、SQLite、bcrypt、pytest、原生 HTML/CSS/JS（无框架）

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `auth.py` | **新建** | 用户认证、会话管理、权限检查、用户/部门管理、审计日志 |
| `tools.py` | 修改 | `_init_db()` 新增 4 张表；`execute_tool()` 新增权限校验 |
| `web_server.py` | 修改 | 新增认证中间件、公开/需登录/管理后台路由 |
| `static/login.html` | **新建** | 登录页面 |
| `static/register.html` | **新建** | 注册页面 |
| `static/admin.html` | **新建** | 管理后台（用户/部门/审计） |
| `static/index.html` | 修改 | 增加登录状态显示和登出按钮 |
| `requirements.txt` | 修改 | 新增 bcrypt、pytest |
| `Dockerfile` | 修改 | 增加 COPY auth.py |
| `test_auth.py` | **新建** | auth.py 单元测试 |

---

### 任务 1：环境准备 — 添加依赖

**文件：**
- 修改：`requirements.txt`
- 修改：`Dockerfile`

- [ ] **步骤 1：更新 requirements.txt**

在第 1 行后插入 bcrypt 和 pytest：

```
bcrypt>=4.0
pytest>=8.0
```

用 Edit 工具在 `paramiko>=3.0` 之前插入：

```
# === 认证模块 ===
bcrypt>=4.0

# === 测试 ===
pytest>=8.0
```

- [ ] **步骤 2：更新 Dockerfile**

在第 10 行（`COPY web_server.py ./` 之后）新增：

```
COPY auth.py ./
```

用 Edit 在 `COPY tools.py ./` 后新增一行 `COPY auth.py ./`。

- [ ] **步骤 3：安装依赖**

```bash
pip install bcrypt pytest
```

- [ ] **步骤 4：验证安装**

```bash
python -c "import bcrypt; print('bcrypt OK')"
python -c "import pytest; print('pytest OK')"
```

预期输出：`bcrypt OK` 和 `pytest OK`

- [ ] **步骤 5：Commit**

```bash
git add requirements.txt Dockerfile
git commit -m "build: add bcrypt, pytest dependencies"
```

---

### 任务 2：数据库 — 创建 4 张新表

**文件：**
- 修改：`tools.py` — `_init_db()` 函数

- [ ] **步骤 1：在 _init_db() 末尾新增建表语句**

在 `_init_db()` 函数中，现有 `sessions` 表的 `CREATE TABLE IF NOT EXISTS` 之后（conn.close() 之前），添加 4 张新表：

```python
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS departments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            created_at REAL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'guest',
            department_id INTEGER,
            is_active INTEGER DEFAULT 1,
            created_at REAL,
            FOREIGN KEY (department_id) REFERENCES departments(id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at REAL,
            expires_at REAL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT NOT NULL,
            detail TEXT,
            created_at REAL
        )
    ''')
```

- [ ] **步骤 2：运行验证**

```bash
python -c "import tools; print('DB init OK'); conn = tools._get_db_conn(); tables = [r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()]; print(tables); conn.close()"
```

预期输出中应包含 `departments`, `users`, `user_sessions`, `audit_log`。

- [ ] **步骤 3：删除旧数据库并重新测试**

```bash
rm -f data/app.db && python -c "import tools; print('Re-init OK')"
```

- [ ] **步骤 4：Commit**

```bash
git add tools.py
git commit -m "feat: add users, departments, user_sessions, audit_log tables to DB init"
```

---

### 任务 3：auth.py — 密码哈希和用户注册

**文件：**
- 创建：`auth.py`
- 创建：`test_auth.py`

- [ ] **步骤 1：编写失败的测试**

创建 `test_auth.py`：

```python
import os
import sys
import time
import pytest

# 确保项目路径在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tools
from tools import _get_db_conn


def _clean_users():
    conn = _get_db_conn()
    conn.execute('DELETE FROM users')
    conn.execute('DELETE FROM user_sessions')
    conn.execute('DELETE FROM departments')
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def setup():
    _clean_users()
    yield
    _clean_users()


def test_register_user():
    import auth
    user = auth.register_user('testuser', 'password123')
    assert user['username'] == 'testuser'
    assert user['role'] == 'guest'
    assert user['department_id'] is None
    assert user['is_active'] == 1


def test_register_duplicate_username():
    import auth
    auth.register_user('testuser', 'password123')
    with pytest.raises(ValueError, match='用户名已存在'):
        auth.register_user('testuser', 'another456')


def test_register_username_too_short():
    import auth
    with pytest.raises(ValueError, match='3-20'):
        auth.register_user('ab', 'password123')


def test_register_password_too_short():
    import auth
    with pytest.raises(ValueError, match='6'):
        auth.register_user('validuser', '12345')


def test_password_hashing():
    import auth
    user = auth.register_user('hashuser', 'mypassword')
    assert user['password_hash'].startswith('$2b$')
    # 验证密码不存明文
    conn = _get_db_conn()
    row = conn.execute('SELECT password_hash FROM users WHERE username=?', ('hashuser',)).fetchone()
    conn.close()
    assert 'mypassword' not in row[0]
```

- [ ] **步骤 2：运行测试验证失败**

```bash
python -m pytest test_auth.py -v
```

预期：全部 FAIL（auth 模块不存在）

- [ ] **步骤 3：创建 auth.py 实现 register_user**

创建 `auth.py`：

```python
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
```

- [ ] **步骤 4：运行测试验证通过**

```bash
python -m pytest test_auth.py -v
```

预期：5 个测试全部 PASS

- [ ] **步骤 5：Commit**

```bash
git add auth.py test_auth.py
git commit -m "feat: add auth.py with password hashing and user registration"
```

---

### 任务 4：auth.py — 登录和登出

**文件：**
- 修改：`auth.py`
- 修改：`test_auth.py`

- [ ] **步骤 1：在 test_auth.py 中添加测试**

```python
def test_login_success():
    import auth
    auth.register_user('loginuser', 'password123')
    token = auth.login_user('loginuser', 'password123')
    assert token is not None
    assert len(token) > 0
    # 验证 session 已存入数据库
    conn = _get_db_conn()
    row = conn.execute('SELECT user_id, expires_at FROM user_sessions WHERE token=?', (token,)).fetchone()
    conn.close()
    assert row is not None


def test_login_wrong_password():
    import auth
    auth.register_user('loginuser2', 'password123')
    with pytest.raises(ValueError, match='密码错误'):
        auth.login_user('loginuser2', 'wrongpass')


def test_login_inactive_user():
    import auth
    auth.register_user('inactiveuser', 'password123')
    conn = _get_db_conn()
    conn.execute('UPDATE users SET is_active=0 WHERE username=?', ('inactiveuser',))
    conn.commit()
    conn.close()
    with pytest.raises(ValueError, match='已禁用'):
        auth.login_user('inactiveuser', 'password123')


def test_login_nonexistent_user():
    import auth
    with pytest.raises(ValueError, match='用户不存在'):
        auth.login_user('nobody', 'password123')


def test_login_by_email():
    import auth
    auth.register_user('emailuser', 'password123', email='test@test.com')
    token = auth.login_user('test@test.com', 'password123')
    assert token is not None


def test_get_user_by_token():
    import auth
    auth.register_user('tokenuser', 'password123')
    token = auth.login_user('tokenuser', 'password123')
    user = auth.get_user_by_token(token)
    assert user is not None
    assert user['username'] == 'tokenuser'
    assert user['role'] == 'guest'


def test_get_user_by_invalid_token():
    import auth
    user = auth.get_user_by_token('invalid-token-xyz')
    assert user is None


def test_get_user_by_expired_token():
    import auth
    auth.register_user('expireduser', 'password123')
    # 手动创建过期 session
    conn = _get_db_conn()
    row = conn.execute('SELECT id FROM users WHERE username=?', ('expireduser',)).fetchone()
    old_token = 'expired-token-001'
    conn.execute(
        'INSERT INTO user_sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)',
        (old_token, row[0], time.time() - 7200, time.time() - 3600)
    )
    conn.commit()
    conn.close()
    user = auth.get_user_by_token(old_token)
    assert user is None


def test_logout():
    import auth
    auth.register_user('logoutuser', 'password123')
    token = auth.login_user('logoutuser', 'password123')
    auth.logout_session(token)
    user = auth.get_user_by_token(token)
    assert user is None


def test_login_remember_me():
    import auth
    auth.register_user('rememberuser', 'password123')
    token = auth.login_user('rememberuser', 'password123', remember_me=True)
    conn = _get_db_conn()
    row = conn.execute('SELECT expires_at FROM user_sessions WHERE token=?', (token,)).fetchone()
    conn.close()
    # 7 天后 > 24 小时后
    assert row['expires_at'] - time.time() > 6 * 24 * 3600
```

- [ ] **步骤 2：运行测试验证失败**

```bash
python -m pytest test_auth.py -v -k "test_login or test_get_user or test_logout"
```

预期：FAIL（login_user, logout_session 等未定义）

- [ ] **步骤 3：在 auth.py 中实现 login_user、logout_session、get_user_by_token**

```python
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
           WHERE s.token=? AND s.expires_at > ?''',
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
```

- [ ] **步骤 4：运行测试验证通过**

```bash
python -m pytest test_auth.py -v -k "test_login or test_get_user or test_logout"
```

预期：全部 PASS

- [ ] **步骤 5：Commit**

```bash
git add auth.py test_auth.py
git commit -m "feat: add login, logout, session token management to auth.py"
```

---

### 任务 5：auth.py — 权限检查和工具过滤

**文件：**
- 修改：`auth.py`
- 修改：`test_auth.py`

- [ ] **步骤 1：在 test_auth.py 中添加权限测试**

```python
def test_role_level():
    import auth
    assert auth.get_role_level('super_admin') == 6
    assert auth.get_role_level('guest') == 1


def test_get_allowed_tools_super_admin():
    import auth
    user = {'role': 'super_admin', 'department_id': None}
    tools = auth.get_allowed_tools(user)
    # 超级管理员应看到所有工具
    tool_names = [t['function']['name'] for t in tools]
    assert 'delete_file' in tool_names
    assert 'db_drop_table' in tool_names
    assert 'write_file' in tool_names


def test_get_allowed_tools_guest():
    import auth
    user = {'role': 'guest', 'department_id': None}
    tools = auth.get_allowed_tools(user)
    tool_names = [t['function']['name'] for t in tools]
    assert 'delete_file' not in tool_names
    assert 'db_drop_table' not in tool_names
    assert 'db_execute' not in tool_names
    assert 'write_file' not in tool_names
    # 游客可以读文件
    assert 'read_file' in tool_names


def test_can_execute_tool():
    import auth
    user = {'role': 'super_admin', 'department_id': None}
    ok, msg = auth.can_execute_tool('delete_file', user, 'some/file.txt')
    assert ok is True
```

- [ ] **步骤 2：运行测试验证失败**

```bash
python -m pytest test_auth.py -v -k "test_role_level or test_get_allowed or test_can_execute"
```

预期：FAIL

- [ ] **步骤 3：在 auth.py 中实现权限逻辑**

```python
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

    # 危险操作记录审计日志
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
```

- [ ] **步骤 4：运行测试验证通过**

```bash
python -m pytest test_auth.py -v -k "test_role_level or test_get_allowed or test_can_execute"
```

预期：全部 PASS

- [ ] **步骤 5：Commit**

```bash
git add auth.py test_auth.py
git commit -m "feat: add role-based tool permission filtering and audit logging"
```

---

### 任务 6：auth.py — 用户管理和部门管理

**文件：**
- 修改：`auth.py`
- 修改：`test_auth.py`

- [ ] **步骤 1：添加管理功能测试到 test_auth.py**

```python
def test_create_department():
    import auth
    dept = auth.create_department('技术部')
    assert dept['name'] == '技术部'


def test_list_departments():
    import auth
    auth.create_department('技术部')
    auth.create_department('财务部')
    depts = auth.list_departments()
    assert len(depts) >= 2


def test_update_user_role():
    import auth
    auth.create_department('技术部')
    user = auth.register_user('promoteme', 'password123')
    auth.update_user_role(user['id'], 'staff', 1)
    conn = _get_db_conn()
    row = conn.execute('SELECT role, department_id FROM users WHERE id=?', (user['id'],)).fetchone()
    conn.close()
    assert row['role'] == 'staff'
    assert row['department_id'] == 1


def test_toggle_user_active():
    import auth
    user = auth.register_user('toggleuser', 'password123')
    auth.toggle_user_active(user['id'], False)
    conn = _get_db_conn()
    row = conn.execute('SELECT is_active FROM users WHERE id=?', (user['id'],)).fetchone()
    conn.close()
    assert row['is_active'] == 0


def test_list_users():
    import auth
    auth.create_department('技术部')
    auth.register_user('user1', 'password123')
    auth.register_user('user2', 'password123', email='a@b.com')
    users = auth.list_users()
    assert len(users) >= 2


def test_reset_password():
    import auth
    user = auth.register_user('resetuser', 'password123')
    new_pass = auth.reset_user_password(user['id'], 'newpass456')
    # 验证密码已变
    token = auth.login_user('resetuser', 'newpass456')
    assert token is not None
```

- [ ] **步骤 2：运行测试验证失败**

```bash
python -m pytest test_auth.py -v -k "test_create_department or test_list_departments or test_update_user_role or test_toggle or test_list_users or test_reset"
```

预期：FAIL

- [ ] **步骤 3：在 auth.py 中实现管理函数**

```python
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
    conn.execute(
        'UPDATE users SET role=?, department_id=? WHERE id=?',
        (role, department_id, user_id)
    )
    conn.commit()
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
```

- [ ] **步骤 4：运行测试验证通过**

```bash
python -m pytest test_auth.py -v
```

预期：全部 PASS

- [ ] **步骤 5：Commit**

```bash
git add auth.py test_auth.py
git commit -m "feat: add user management, department management, and audit log functions"
```

---

### 任务 7：web_server.py — 认证中间件和公开路由

**文件：**
- 修改：`web_server.py`

- [ ] **步骤 1：在文件顶部导入 auth 模块并添加 cookie 解析辅助方法**

在 `import tools` 之后添加：

```python
import auth as auth_module
```

在 `RequestHandler` 类中添加辅助方法（放在 `_set_cors_headers` 之后）：

```python
    def _get_user_from_cookie(self):
        """从 Cookie 中获取当前登录用户，未登录返回 None"""
        cookie_header = self.headers.get('Cookie', '')
        session_token = None
        for part in cookie_header.split(';'):
            part = part.strip()
            if part.startswith('session_token='):
                session_token = part[len('session_token='):]
                break
        if not session_token:
            return None
        return auth_module.get_user_by_token(session_token)

    def _require_auth(self):
        """要求登录，未登录返回 302"""
        user = self._get_user_from_cookie()
        if user is None:
            self.send_response(302)
            self.send_header('Location', '/login')
            self.end_headers()
            return None
        return user

    def _require_role(self, min_role):
        """要求最低角色，不够返回 403"""
        user = self._require_auth()
        if user is None:
            return None
        if auth_module.get_role_level(user['role']) < auth_module.get_role_level(min_role):
            self._send_json(403, {'success': False, 'error': '权限不足'})
            return None
        return user
```

- [ ] **步骤 2：新增 /api/register 路由**

在 `do_POST` 方法中，`parsed.path` 判断链中（`/api/command` 之前）加入：

```python
        if parsed.path == '/api/register':
            self._handle_register()
            return
        if parsed.path == '/api/login':
            self._handle_login()
            return
        if parsed.path == '/api/logout':
            self._handle_logout()
            return
```

然后添加处理方法：

```python
    def _handle_register(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')
        try:
            data = json.loads(body)
            username = data.get('username', '').strip()
            password = data.get('password', '')
            email = data.get('email', '').strip() or None
        except json.JSONDecodeError:
            self._send_json(400, {'success': False, 'error': '请求格式错误'})
            return

        try:
            user = auth_module.register_user(username, password, email)
            self._send_json(200, {'success': True, 'user': user, 'message': '注册成功'})
        except ValueError as e:
            self._send_json(400, {'success': False, 'error': str(e)})

    def _handle_login(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')
        try:
            data = json.loads(body)
            login = data.get('login', '').strip()
            password = data.get('password', '')
            remember_me = data.get('remember_me', False)
        except json.JSONDecodeError:
            self._send_json(400, {'success': False, 'error': '请求格式错误'})
            return

        try:
            token = auth_module.login_user(login, password, remember_me)
            user = auth_module.get_user_by_token(token)
            ttl = 7 * 24 * 3600 if remember_me else 24 * 3600
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Set-Cookie',
                f'session_token={token}; Path=/; HttpOnly; Max-Age={ttl}; SameSite=Lax')
            self._set_cors_headers()
            body = json.dumps({'success': True, 'user': user}).encode('utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except ValueError as e:
            self._send_json(401, {'success': False, 'error': str(e)})

    def _handle_logout(self):
        cookie_header = self.headers.get('Cookie', '')
        for part in cookie_header.split(';'):
            part = part.strip()
            if part.startswith('session_token='):
                token = part[len('session_token='):]
                auth_module.logout_session(token)
                break
        self._send_json(200, {'success': True, 'message': '已登出'})
```

- [ ] **步骤 3：新增 /api/me 路由**

在 `do_GET` 方法中添加：

```python
        elif parsed.path == '/api/me':
            user = self._require_auth()
            if user:
                self._send_json(200, {'success': True, 'user': user})
```

- [ ] **步骤 4：修改 GET 路由，增加页面路由**

在 `do_GET` 中，`/` 的处理改为：

```python
        if parsed.path == '/login' or (parsed.path == '/' and not self._get_user_from_cookie()):
            try:
                login_path = os.path.join(base_dir, 'static', 'login.html')
                with open(login_path, 'r', encoding='utf-8') as f:
                    html = f.read()
            except FileNotFoundError:
                html = '<html><body><h1>登录页面未找到</h1></body></html>'
            body = html.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self._set_cors_headers()
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == '/register':
            # 返回注册页面
            ...  # 类似 login
```

（说明：页面文件在后续任务创建，此处路由预留即可）

- [ ] **步骤 5：Commit**

```bash
git add web_server.py
git commit -m "feat: add auth middleware, register/login/logout API routes to web_server"
```

---

### 任务 8：web_server.py — 保护现有路由和权限检查

**文件：**
- 修改：`web_server.py`

- [ ] **步骤 1：保护 /api/command 路由**

在 `_handle_command` 方法开头添加用户验证和权限过滤。修改 `_handle_command` 方法签名接受 user 参数，或者在该方法内部获取 user：

```python
    def _handle_command(self, user_text, client_ip, model_name=None, session_id=None):
        # 获取当前用户
        user = self._get_user_from_cookie()
        if user is None:
            return {
                'success': False,
                'reply': '请先登录',
                'session_id': '',
                'server_ip': get_server_ip(),
            }
        # 后续代码不变……
```

在 `call_llm_api` 调用前，使用权限过滤的工具列表替换全量工具：

将 `call_llm_api` 函数签名中增加 `allowed_tools` 参数（默认 None），不传则使用全部工具。然后修改请求构建部分：

```python
def call_llm_api(messages, provider_name=None, tools_enabled=True, allowed_tools=None):
    # ...
    if tools_enabled:
        request_body["tools"] = allowed_tools if allowed_tools is not None else tools.get_tools_definition()
        request_body["tool_choice"] = "auto"
```

在 `_handle_command` 中调用时传入过滤后的工具：

```python
        allowed_tools = auth_module.get_allowed_tools(user)
        # … 在循环中调用
        success, content, tool_calls = call_llm_api(messages, model_name, allowed_tools=allowed_tools)
```

- [ ] **步骤 2：在 execute_tool 调用处添加权限检查**

在 `_handle_command` 的工具执行循环中调用 `can_execute_tool`：

```python
                for tc in tool_calls:
                    # 权限检查
                    file_path = tc['arguments'].get('path', '') if 'arguments' in tc else ''
                    can_exec, err_msg = auth_module.can_execute_tool(tc['name'], user, file_path)
                    if not can_exec:
                        tool_call_history.append({
                            "tool": tc['name'],
                            "arguments": tc['arguments'],
                            "success": False,
                            "result": {"error": err_msg},
                        })
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc['id'],
                            "content": json.dumps({"error": err_msg}, ensure_ascii=False),
                        })
                        continue
                    tool_ok, tool_result = tools.execute_tool(tc['name'], tc['arguments'])
                    # 后续不变……
```

- [ ] **步骤 3：保护 /api/upload**

在 `_handle_upload` 方法开头添加：

```python
        user = self._get_user_from_cookie()
        if user is None:
            self._send_json(401, {'success': False, 'error': '请先登录'})
            return
        if auth_module.get_role_level(user['role']) < auth_module.get_role_level('staff'):
            self._send_json(403, {'success': False, 'error': '权限不足：职员及以上可上传'})
            return
```

- [ ] **步骤 4：保护 /chat/ 页面路由**

在 `do_GET` 中，`/` 和 `/chat/` 的处理处添加登录检查：

```python
        if parsed.path == '/' or parsed.path == '/index.html' or parsed.path == '/chat/':
            user = self._get_user_from_cookie()
            if user is None:
                self.send_response(302)
                self.send_header('Location', '/login')
                self.end_headers()
                return
            # 正常返回 chat 页面
            html = get_web_page()
            # … 现有逻辑
```

- [ ] **步骤 5：修改现有 do_POST 中 /api/command 的调用以传入 user**

定位到 `/api/command` 处理的代码块，确认 `_handle_command` 调用前 user 已通过 `_get_user_from_cookie()` 获取：

`/api/command` 分支需添加：

```python
        if parsed.path == '/api/command':
            user = self._get_user_from_cookie()
            if user is None:
                self._send_json(401, {'success': False, 'error': '请先登录'})
                return
            # 继续原有解析和调用逻辑……
```

- [ ] **步骤 6：启动服务手动测试**

```bash
python web_server.py &
# Test register
curl -X POST http://127.0.0.1:8888/api/register -H "Content-Type: application/json" -d '{"username":"test","password":"123456"}'
# Test login
curl -X POST http://127.0.0.1:8888/api/login -H "Content-Type: application/json" -d '{"login":"test","password":"123456"}' -v
# Check Set-Cookie header in response
```

- [ ] **步骤 7：Commit**

```bash
git add web_server.py
git commit -m "feat: add auth guards to API routes and page routes"
```

---

### 任务 9：web_server.py — 管理后台 API

**文件：**
- 修改：`web_server.py`

- [ ] **步骤 1：在 do_GET 中添加管理后台 API 路由**

在 `do_GET` 中添加：

```python
        elif parsed.path == '/api/admin/users':
            user = self._require_role('super_admin')
            if user is None: return
            qs = parse_qs(parsed.query)
            role = qs.get('role', [None])[0]
            dept_id = qs.get('department_id', [None])[0]
            active = qs.get('active', [None])[0]
            users = auth_module.list_users(
                role=role,
                department_id=int(dept_id) if dept_id else None,
                active=active.lower() == 'true' if active else None
            )
            self._send_json(200, {'success': True, 'users': users})
        elif parsed.path == '/api/admin/departments':
            user = self._require_role('super_admin')
            if user is None: return
            depts = auth_module.list_departments()
            self._send_json(200, {'success': True, 'departments': depts})
        elif parsed.path == '/api/admin/audit-logs':
            user = self._require_role('super_admin')
            if user is None: return
            qs = parse_qs(parsed.query)
            logs = auth_module.get_audit_logs(
                user_id=qs.get('user_id', [None])[0],
                action=qs.get('action', [None])[0],
            )
            self._send_json(200, {'success': True, 'logs': logs})
```

- [ ] **步骤 2：在 do_POST 中添加管理后台 API 路由**

```python
        elif parsed.path.startswith('/api/admin/'):
            user = self._require_role('super_admin')
            if user is None: return
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self._send_json(400, {'success': False, 'error': '请求格式错误'})
                return

            if parsed.path == '/api/admin/departments':
                try:
                    dept = auth_module.create_department(data.get('name', ''))
                    self._send_json(200, {'success': True, 'department': dept})
                except ValueError as e:
                    self._send_json(400, {'success': False, 'error': str(e)})
            elif parsed.path == '/api/admin/users/reset-password':
                try:
                    auth_module.reset_user_password(data['user_id'], data['new_password'])
                    self._send_json(200, {'success': True, 'message': '密码已重置'})
                except (ValueError, KeyError) as e:
                    self._send_json(400, {'success': False, 'error': str(e)})
```

- [ ] **步骤 3：在 do_POST 中添加 PUT/DELETE 方式管理 API**

由于 BaseHTTPRequestHandler 默认不支持 PUT/DELETE，通过 do_POST 中路径匹配实现：

```python
            elif parsed.path.startswith('/api/admin/users/') and parsed.path.endswith('/role'):
                # PUT: update user role/department/active
                user_id = int(parsed.path.split('/')[-2])
                try:
                    auth_module.update_user_role(
                        user_id,
                        data.get('role', 'guest'),
                        data.get('department_id')
                    )
                    if 'is_active' in data:
                        auth_module.toggle_user_active(user_id, data['is_active'])
                    self._send_json(200, {'success': True, 'message': '用户已更新'})
                except ValueError as e:
                    self._send_json(400, {'success': False, 'error': str(e)})
            elif parsed.path.startswith('/api/admin/users/') and parsed.path.endswith('/delete'):
                user_id = int(parsed.path.split('/')[-2])
                auth_module.delete_user(user_id)
                self._send_json(200, {'success': True, 'message': '用户已删除'})
            elif parsed.path.startswith('/api/admin/departments/') and parsed.path.endswith('/update'):
                dept_id = int(parsed.path.split('/')[-2])
                try:
                    auth_module.update_department(dept_id, data.get('name', ''))
                    self._send_json(200, {'success': True, 'message': '部门已更新'})
                except ValueError as e:
                    self._send_json(400, {'success': False, 'error': str(e)})
            elif parsed.path.startswith('/api/admin/departments/') and parsed.path.endswith('/delete'):
                dept_id = int(parsed.path.split('/')[-2])
                try:
                    auth_module.delete_department(dept_id)
                    self._send_json(200, {'success': True, 'message': '部门已删除'})
                except ValueError as e:
                    self._send_json(400, {'success': False, 'error': str(e)})
```

- [ ] **步骤 4：手动测试管理 API**

```bash
# 先创建超管用户（直接写入数据库）
python -c "
import tools
conn = tools._get_db_conn()
import bcrypt, time
pw = bcrypt.hashpw(b'admin123', bcrypt.gensalt(12)).decode()
conn.execute('INSERT INTO users (username, password_hash, role, is_active, created_at) VALUES (?,?,?,?,?)', ('admin', pw, 'super_admin', 1, time.time()))
conn.commit()
conn.close()
print('Admin created')
"

# 登录获取 token
curl -X POST http://127.0.0.1:8888/api/login -H "Content-Type: application/json" -d '{"login":"admin","password":"admin123"}' -c cookie.txt

# 创建部门
curl -X POST http://127.0.0.1:8888/api/admin/departments -b cookie.txt -H "Content-Type: application/json" -d '{"name":"技术部"}'

# 查看用户列表
curl http://127.0.0.1:8888/api/admin/users -b cookie.txt
```

- [ ] **步骤 5：Commit**

```bash
git add web_server.py
git commit -m "feat: add admin API routes for user/department/audit management"
```

---

### 任务 10：前端 — 登录页面

**文件：**
- 创建：`static/login.html`

- [ ] **步骤 1：创建 login.html**

创建 `static/login.html`，深色主题企业登录页面，包含：
- HDXT AI 助手 标题
- 用户名或邮箱输入框
- 密码输入框
- "记住我" 复选框
- 登录按钮（异步 POST /api/login）
- 跳转注册页链接
- 错误提示区域

完整 HTML（约 180 行）：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>登录 - HDXT AI 助手</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    background: #141425; color: #d0d0e0;
    font-family: "Microsoft YaHei", sans-serif;
    display: flex; justify-content: center; align-items: center;
    min-height: 100vh;
}
.login-card {
    background: #1e1e2e; border-radius: 12px; padding: 40px;
    width: 380px; max-width: 90vw;
    box-shadow: 0 4px 24px rgba(0,0,0,0.4);
}
.login-card h2 { text-align: center; color: #60a5fa; margin-bottom: 4px; }
.login-card .subtitle { text-align: center; color: #8888aa; font-size: 13px; margin-bottom: 28px; }
.form-group { margin-bottom: 18px; }
.form-group label { display: block; font-size: 13px; color: #a0a0c0; margin-bottom: 6px; }
.form-group input {
    width: 100%; padding: 10px 12px;
    background: #2a2a3e; border: 1px solid #3a3a5e; border-radius: 6px;
    color: #fff; font-size: 14px; outline: none; transition: border-color 0.2s;
}
.form-group input:focus { border-color: #3b82f6; }
.remember-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; font-size: 13px; }
.remember-row label { color: #a0a0c0; cursor: pointer; }
.remember-row a { color: #60a5fa; text-decoration: none; }
.btn-login {
    width: 100%; padding: 12px; background: #3b82f6; color: #fff;
    border: none; border-radius: 6px; font-size: 15px; font-weight: bold;
    cursor: pointer; transition: background 0.2s;
}
.btn-login:hover { background: #2563eb; }
.btn-login:disabled { opacity: 0.6; cursor: not-allowed; }
.error-msg { color: #ef4444; font-size: 13px; text-align: center; margin-top: 12px; min-height: 20px; }
.register-link { text-align: center; font-size: 13px; color: #a0a0c0; margin-top: 16px; }
.register-link a { color: #60a5fa; text-decoration: none; }
</style>
</head>
<body>
<div class="login-card">
    <h2>HDXT AI 助手</h2>
    <p class="subtitle">请登录您的账号</p>
    <div class="form-group">
        <label>用户名或邮箱</label>
        <input type="text" id="login" placeholder="请输入用户名或邮箱" autocomplete="username">
    </div>
    <div class="form-group">
        <label>密码</label>
        <input type="password" id="password" placeholder="请输入密码" autocomplete="current-password">
    </div>
    <div class="remember-row">
        <label><input type="checkbox" id="remember"> 记住我</label>
    </div>
    <button class="btn-login" id="btnLogin" onclick="doLogin()">登  录</button>
    <div class="error-msg" id="error"></div>
    <div class="register-link">还没有账号？<a href="/register">立即注册</a></div>
</div>
<script>
async function doLogin() {
    const login = document.getElementById('login').value.trim();
    const password = document.getElementById('password').value;
    const remember = document.getElementById('remember').checked;
    const errorEl = document.getElementById('error');
    const btn = document.getElementById('btnLogin');

    if (!login || !password) {
        errorEl.textContent = '请输入用户名和密码';
        return;
    }
    btn.disabled = true;
    btn.textContent = '登录中...';
    errorEl.textContent = '';

    try {
        const resp = await fetch('/api/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ login, password, remember_me: remember }),
        });
        const data = await resp.json();
        if (data.success) {
            window.location.href = '/chat/';
        } else {
            errorEl.textContent = data.error || '登录失败';
        }
    } catch (e) {
        errorEl.textContent = '网络错误，请检查连接';
    } finally {
        btn.disabled = false;
        btn.textContent = '登  录';
    }
}

document.getElementById('password').addEventListener('keydown', function(e) {
    if (e.key === 'Enter') doLogin();
});
document.getElementById('login').addEventListener('keydown', function(e) {
    if (e.key === 'Enter') document.getElementById('password').focus();
});
</script>
</body>
</html>
```

- [ ] **步骤 2：在浏览器中打开登录页面测试**

```bash
python web_server.py &
# 浏览器访问 http://127.0.0.1:8888/login
```

- [ ] **步骤 3：Commit**

```bash
git add static/login.html
git commit -m "feat: add login page"
```

---

### 任务 11：前端 — 注册页面

**文件：**
- 创建：`static/register.html`

- [ ] **步骤 1：创建 register.html**

创建注册页面（结构类似 login，增加邮箱选填、确认密码字段）。提交到 `/api/register`，注册成功后自动登录跳转 `/chat/`。

页面内容与 login.html 类似，核心差异：
- 4 个输入框：用户名、邮箱（选填）、密码、确认密码
- 前端校验：用户名格式、密码长度、两次密码一致
- 注册成功 → 自动调用 login API → 跳转 /chat/

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>注册 - HDXT AI 助手</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: #141425; color: #d0d0e0; font-family: "Microsoft YaHei", sans-serif; display: flex; justify-content: center; align-items: center; min-height: 100vh; }
.reg-card { background: #1e1e2e; border-radius: 12px; padding: 36px; width: 380px; max-width: 90vw; box-shadow: 0 4px 24px rgba(0,0,0,0.4); }
.reg-card h2 { text-align: center; color: #60a5fa; margin-bottom: 4px; }
.reg-card .subtitle { text-align: center; color: #8888aa; font-size: 13px; margin-bottom: 24px; }
.form-group { margin-bottom: 14px; }
.form-group label { display: block; font-size: 13px; color: #a0a0c0; margin-bottom: 5px; }
.form-group label .optional { color: #5a5a7a; }
.form-group input { width: 100%; padding: 9px 12px; background: #2a2a3e; border: 1px solid #3a3a5e; border-radius: 6px; color: #fff; font-size: 14px; outline: none; }
.form-group input:focus { border-color: #3b82f6; }
.form-group input.error { border-color: #ef4444; }
.btn-register { width: 100%; padding: 12px; background: #3b82f6; color: #fff; border: none; border-radius: 6px; font-size: 15px; font-weight: bold; cursor: pointer; }
.btn-register:hover { background: #2563eb; }
.btn-register:disabled { opacity: 0.6; cursor: not-allowed; }
.error-msg { color: #ef4444; font-size: 13px; text-align: center; margin-top: 10px; min-height: 20px; }
.login-link { text-align: center; font-size: 13px; color: #a0a0c0; margin-top: 14px; }
.login-link a { color: #60a5fa; text-decoration: none; }
</style>
</head>
<body>
<div class="reg-card">
    <h2>创建新账号</h2>
    <p class="subtitle">注册后默认为游客权限，需管理员提权</p>
    <div class="form-group">
        <label>用户名 <span style="color:#ef4444;">*</span></label>
        <input type="text" id="username" placeholder="3-20字符，字母数字下划线" autocomplete="username">
    </div>
    <div class="form-group">
        <label>邮箱 <span class="optional">(选填)</span></label>
        <input type="email" id="email" placeholder="用于找回密码" autocomplete="email">
    </div>
    <div class="form-group">
        <label>密码 <span style="color:#ef4444;">*</span></label>
        <input type="password" id="password" placeholder="至少6位" autocomplete="new-password">
    </div>
    <div class="form-group">
        <label>确认密码 <span style="color:#ef4444;">*</span></label>
        <input type="password" id="confirm" placeholder="再次输入密码" autocomplete="new-password">
    </div>
    <button class="btn-register" id="btnReg" onclick="doRegister()">注  册</button>
    <div class="error-msg" id="error"></div>
    <div class="login-link">已有账号？<a href="/login">去登录</a></div>
</div>
<script>
async function doRegister() {
    const username = document.getElementById('username').value.trim();
    const email = document.getElementById('email').value.trim() || null;
    const password = document.getElementById('password').value;
    const confirm = document.getElementById('confirm').value;
    const errorEl = document.getElementById('error');
    const btn = document.getElementById('btnReg');

    if (!/^[a-zA-Z0-9_]{3,20}$/.test(username)) {
        errorEl.textContent = '用户名需要3-20个字符（字母数字下划线）'; return;
    }
    if (password.length < 6) {
        errorEl.textContent = '密码至少需要6个字符'; return;
    }
    if (password !== confirm) {
        errorEl.textContent = '两次密码输入不一致'; return;
    }
    btn.disabled = true; btn.textContent = '注册中...'; errorEl.textContent = '';

    try {
        const resp = await fetch('/api/register', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password, email }),
        });
        const data = await resp.json();
        if (data.success) {
            // 自动登录
            const loginResp = await fetch('/api/login', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ login: username, password, remember_me: false }),
            });
            const loginData = await loginResp.json();
            if (loginData.success) {
                window.location.href = '/chat/';
            } else {
                window.location.href = '/login';
            }
        } else {
            errorEl.textContent = data.error || '注册失败';
        }
    } catch (e) {
        errorEl.textContent = '网络错误，请检查连接';
    } finally {
        btn.disabled = false; btn.textContent = '注  册';
    }
}
</script>
</body>
</html>
```

- [ ] **步骤 2：测试注册流程**

浏览器测试：注册 → 自动登录 → 进入聊天页面

- [ ] **步骤 3：Commit**

```bash
git add static/register.html
git commit -m "feat: add registration page with auto-login"
```

---

### 任务 12：前端 — 管理后台页面

**文件：**
- 创建：`static/admin.html`

- [ ] **步骤 1：创建 admin.html**

管理后台页面结构：
- 左侧边栏菜单（用户管理 / 部门管理 / 审计日志 / 返回助手）
- 右侧内容区，三个面板用 JS 切换
- 用户管理面板：表格 + 筛选下拉框 + 编辑模态框
- 部门管理面板：列表 + 新增/编辑/删除
- 审计日志面板：表格 + 时间筛选

纯前端 JS 调用 /api/admin/* 接口。

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>管理后台 - HDXT AI 助手</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { background:#141425; color:#d0d0e0; font-family:"Microsoft YaHei",sans-serif; display:flex; min-height:100vh; }
.sidebar { width:180px; background:#1a1a30; padding:16px 0; display:flex; flex-direction:column; }
.sidebar h3 { color:#fbbf24; font-size:14px; padding:0 16px; margin-bottom:16px; }
.sidebar a { color:#a0a0c0; text-decoration:none; padding:10px 16px; font-size:13px; cursor:pointer; transition:all 0.2s; display:block; }
.sidebar a:hover, .sidebar a.active { background:#3b82f6; color:#fff; }
.sidebar .back-link { margin-top:auto; color:#ef4444; }
.main { flex:1; padding:24px; overflow-y:auto; }
.panel { display:none; }
.panel.active { display:block; }
h2 { color:#60a5fa; margin-bottom:16px; font-size:18px; }
.toolbar { display:flex; gap:12px; margin-bottom:16px; align-items:center; }
.toolbar select, .toolbar input { background:#2a2a3e; border:1px solid #3a3a5e; color:#fff; padding:6px 10px; border-radius:4px; font-size:13px; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th { background:#1a1a3e; padding:8px 10px; text-align:left; border-bottom:2px solid #3b82f6; }
td { padding:7px 10px; border-bottom:1px solid #2a2a4e; }
.btn { padding:5px 12px; border:none; border-radius:4px; cursor:pointer; font-size:12px; }
.btn-primary { background:#3b82f6; color:#fff; }
.btn-danger { background:#ef4444; color:#fff; }
.btn-success { background:#4ade80; color:#000; }
.btn-sm { padding:3px 8px; font-size:11px; }
.modal-overlay { display:none; position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(0,0,0,0.6); z-index:100; align-items:center; justify-content:center; }
.modal-overlay.show { display:flex; }
.modal { background:#1e1e2e; border-radius:12px; padding:24px; min-width:320px; }
.modal h3 { color:#60a5fa; margin-bottom:16px; }
.modal .form-group { margin-bottom:12px; }
.modal label { font-size:13px; color:#a0a0c0; display:block; margin-bottom:4px; }
.modal select, .modal input { width:100%; background:#2a2a3e; border:1px solid #3a3a5e; color:#fff; padding:8px; border-radius:4px; }
.modal .actions { display:flex; gap:8px; margin-top:16px; justify-content:flex-end; }
.role-super_admin { color:#fbbf24; }
.role-chairman { color:#60a5fa; }
.role-gm { color:#818cf8; }
.role-dept_head { color:#34d399; }
.role-staff { color:#a78bfa; }
.role-guest { color:#6b7280; }
</style>
</head>
<body>
<div class="sidebar">
    <h3>管理后台</h3>
    <a class="active" onclick="showPanel('users')">用户管理</a>
    <a onclick="showPanel('departments')">部门管理</a>
    <a onclick="showPanel('audit')">审计日志</a>
    <a class="back-link" href="/chat/">← 返回助手</a>
</div>
<div class="main">
    <!-- 用户管理面板 -->
    <div class="panel active" id="panel-users">
        <h2>用户管理</h2>
        <div class="toolbar">
            <select id="filterRole" onchange="loadUsers()">
                <option value="">全部角色</option>
                <option value="super_admin">超级管理员</option><option value="chairman">董事长</option>
                <option value="gm">总经理</option><option value="dept_head">部门长</option>
                <option value="staff">部门职员</option><option value="guest">游客</option>
            </select>
            <select id="filterActive" onchange="loadUsers()">
                <option value="">全部状态</option><option value="true">正常</option><option value="false">已禁用</option>
            </select>
        </div>
        <table><thead><tr><th>ID</th><th>用户名</th><th>邮箱</th><th>角色</th><th>部门</th><th>状态</th><th>注册时间</th><th>操作</th></tr></thead>
        <tbody id="userTableBody"></tbody></table>
    </div>
    <!-- 部门管理面板 -->
    <div class="panel" id="panel-departments">
        <h2>部门管理</h2>
        <div class="toolbar">
            <input type="text" id="newDeptName" placeholder="新部门名称">
            <button class="btn btn-primary" onclick="createDept()">新增部门</button>
        </div>
        <table><thead><tr><th>ID</th><th>部门名称</th><th>人数</th><th>创建时间</th><th>操作</th></tr></thead>
        <tbody id="deptTableBody"></tbody></table>
    </div>
    <!-- 审计日志面板 -->
    <div class="panel" id="panel-audit">
        <h2>审计日志</h2>
        <table><thead><tr><th>ID</th><th>操作者</th><th>操作类型</th><th>详情</th><th>时间</th></tr></thead>
        <tbody id="auditTableBody"></tbody></table>
    </div>
</div>
<!-- 编辑用户模态框 -->
<div class="modal-overlay" id="editModal">
    <div class="modal">
        <h3>编辑用户</h3>
        <input type="hidden" id="editUserId">
        <div class="form-group"><label>角色</label><select id="editRole">
            <option value="super_admin">超级管理员</option><option value="chairman">董事长</option>
            <option value="gm">总经理</option><option value="dept_head">部门长</option>
            <option value="staff">部门职员</option><option value="guest">游客</option>
        </select></div>
        <div class="form-group"><label>部门</label><select id="editDept"><option value="">无部门</option></select></div>
        <div class="actions">
            <button class="btn btn-danger btn-sm" onclick="toggleUser()" id="btnToggle">禁用</button>
            <button class="btn btn-primary" onclick="saveUser()">保存</button>
            <button class="btn" onclick="closeModal()">取消</button>
        </div>
    </div>
</div>
<script>
let currentPanel = 'users';

function showPanel(name) {
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.sidebar a').forEach(a => a.classList.remove('active'));
    document.getElementById('panel-' + name).classList.add('active');
    event.target.classList.add('active');
    currentPanel = name;
    if (name === 'users') loadUsers();
    else if (name === 'departments') loadDepts();
    else if (name === 'audit') loadAudit();
}

async function api(url, method='GET', body=null) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body) opts.body = JSON.stringify(body);
    const resp = await fetch(url, opts);
    return resp.json();
}

async function loadUsers() {
    const role = document.getElementById('filterRole').value;
    const active = document.getElementById('filterActive').value;
    let url = '/api/admin/users?';
    if (role) url += 'role=' + role + '&';
    if (active) url += 'active=' + active;
    const data = await api(url);
    document.getElementById('userTableBody').innerHTML = data.users.map(u =>
        `<tr>
            <td>${u.id}</td><td>${u.username}</td><td>${u.email || '-'}</td>
            <td class="role-${u.role}">${u.role_name}</td><td>${u.department_name || '-'}</td>
            <td style="color:${u.is_active?'#4ade80':'#ef4444'}">${u.is_active?'正常':'已禁用'}</td>
            <td>${new Date(u.created_at*1000).toLocaleDateString()}</td>
            <td>
                <button class="btn btn-primary btn-sm" onclick="editUser(${u.id},'${u.role}',${u.department_id||'null'},${u.is_active})">编辑</button>
                <button class="btn btn-sm" style="background:#fbbf24;color:#000" onclick="resetPwd(${u.id})">重置密码</button>
                <button class="btn btn-danger btn-sm" onclick="deleteUser(${u.id})">删除</button>
            </td>
        </tr>`).join('');
}

function editUser(id, role, deptId, isActive) {
    document.getElementById('editUserId').value = id;
    document.getElementById('editRole').value = role;
    document.getElementById('btnToggle').textContent = isActive ? '禁用' : '启用';
    document.getElementById('editModal').classList.add('show');
    // 加载部门选项
    document.getElementById('editDept').innerHTML = '<option value="">无部门</option>';
    api('/api/admin/departments').then(d => {
        d.departments.forEach(dept => {
            const opt = document.createElement('option');
            opt.value = dept.id;
            opt.textContent = dept.name;
            if (dept.id === deptId) opt.selected = true;
            document.getElementById('editDept').appendChild(opt);
        });
    });
}

function closeModal() { document.getElementById('editModal').classList.remove('show'); }

async function saveUser() {
    const id = document.getElementById('editUserId').value;
    const role = document.getElementById('editRole').value;
    const deptId = document.getElementById('editDept').value || null;
    await api('/api/admin/users/' + id + '/role', 'POST', {
        role, department_id: deptId ? parseInt(deptId) : null
    });
    closeModal(); loadUsers();
}

async function toggleUser() {
    const id = document.getElementById('editUserId').value;
    const isActive = document.getElementById('btnToggle').textContent === '禁用';
    await api('/api/admin/users/' + id + '/role', 'POST', { role: document.getElementById('editRole').value, is_active: !isActive });
    closeModal(); loadUsers();
}

async function resetPwd(id) {
    const newPw = prompt('输入新密码（至少6位）：');
    if (!newPw || newPw.length < 6) { alert('密码至少6位'); return; }
    await api('/api/admin/users/reset-password', 'POST', { user_id: id, new_password: newPw });
    alert('密码已重置为：' + newPw);
}

async function deleteUser(id) {
    if (!confirm('确定要删除此用户吗？此操作不可恢复。')) return;
    await api('/api/admin/users/' + id + '/delete', 'POST');
    loadUsers();
}

async function loadDepts() {
    const data = await api('/api/admin/departments');
    document.getElementById('deptTableBody').innerHTML = data.departments.map(d =>
        `<tr>
            <td>${d.id}</td><td id="deptName-${d.id}">${d.name}</td><td>${d.user_count}</td>
            <td>${new Date(d.created_at*1000).toLocaleDateString()}</td>
            <td>
                <button class="btn btn-primary btn-sm" onclick="editDeptName(${d.id},'${d.name}')">改名</button>
                <button class="btn btn-danger btn-sm" onclick="deleteDept(${d.id})">删除</button>
            </td>
        </tr>`).join('');
}

async function createDept() {
    const name = document.getElementById('newDeptName').value.trim();
    if (!name) return alert('请输入部门名称');
    await api('/api/admin/departments', 'POST', { name });
    document.getElementById('newDeptName').value = '';
    loadDepts();
}

async function editDeptName(id, oldName) {
    const name = prompt('修改部门名称：', oldName);
    if (!name || name === oldName) return;
    await api('/api/admin/departments/' + id + '/update', 'POST', { name });
    loadDepts();
}

async function deleteDept(id) {
    if (!confirm('确定删除此部门？')) return;
    try {
        await api('/api/admin/departments/' + id + '/delete', 'POST');
        loadDepts();
    } catch(e) { alert('删除失败'); }
}

async function loadAudit() {
    const data = await api('/api/admin/audit-logs');
    document.getElementById('auditTableBody').innerHTML = data.logs.map(l =>
        `<tr>
            <td>${l.id}</td><td>${l.username || l.user_id}</td><td>${l.action}</td>
            <td>${l.detail}</td><td>${new Date(l.created_at*1000).toLocaleString()}</td>
        </tr>`).join('');
}

loadUsers();
</script>
</body>
</html>
```

- [ ] **步骤 2：测试管理后台**

1. 以 admin 登录
2. 访问 /admin/
3. 测试：创建部门、修改用户角色、重置密码、查看审计日志

- [ ] **步骤 3：Commit**

```bash
git add static/admin.html
git commit -m "feat: add admin panel page with user/department/audit management"
```

---

### 任务 13：更新 chat 页面增加登录状态和登出

**文件：**
- 修改：`static/index.html`

- [ ] **步骤 1：在页面顶部加用户信息栏和登出按钮**

在 chat 页面顶部添加：
- 当前用户名和角色显示
- 登出按钮（POST /api/logout 后跳转 /login）
- 超管显示"管理后台"链接

在页面加载时 fetch `/api/me` 获取用户信息。

- [ ] **步骤 2：测试**

登录后访问 /chat/，看到用户名和角色信息，点击登出可退出。

- [ ] **步骤 3：Commit**

```bash
git add static/index.html
git commit -m "feat: add user info bar and logout button to chat page"
```

---

### 任务 14：tools.py — 执行层权限检查

**文件：**
- 修改：`tools.py`

- [ ] **步骤 1：在 execute_tool 中添加权限检查预留接口**

在 `execute_tool` 函数开头增加一个可选的 `user` 参数：

```python
def execute_tool(tool_name, arguments, user=None):
    """执行工具调用，user 参数用于权限检查"""
    # 如果传入 user 且有 auth 模块，做权限检查
    if user:
        try:
            import auth as auth_mod
            can_exec, msg = auth_mod.can_execute_tool(tool_name, user,
                          arguments.get('path', '') if isinstance(arguments, dict) else '')
            if not can_exec:
                return False, {"error": msg}
        except ImportError:
            pass

    if tool_name not in TOOL_MAP:
        return False, {"error": f"未知工具: {tool_name}"}
    # 后续代码不变……
```

- [ ] **步骤 2：在 web_server.py 调用 execute_tool 时传入 user**

修改 `_handle_command` 中的 `tools.execute_tool(tc['name'], tc['arguments'])` 为：

```python
tool_ok, tool_result = tools.execute_tool(tc['name'], tc['arguments'], user=user)
```

- [ ] **步骤 3：运行现有功能验证不破坏原有逻辑**

```bash
python -m pytest test_auth.py -v
python web_server.py &  # 快速手动测试 chat 功能正常
```

- [ ] **步骤 4：Commit**

```bash
git add tools.py web_server.py
git commit -m "feat: add permission check in tool execution layer"
```

---

### 任务 15：集成测试和收尾

**文件：**
- 修改：`.env.example`（可选）
- 创建：初始化脚本或说明

- [ ] **步骤 1：创建首个超级管理员脚本**

创建 `create_admin.py`：

```python
#!/usr/bin/env python3
"""创建首个超级管理员账号"""
import sys
sys.path.insert(0, '.')
import auth as auth_module

if __name__ == '__main__':
    username = input('管理员用户名: ').strip()
    password = input('管理员密码: ').strip()
    try:
        user = auth_module.register_user(username, password)
        auth_module.update_user_role(user['id'], 'super_admin', None)
        print(f'超级管理员 {username} 创建成功！')
    except Exception as e:
        print(f'创建失败: {e}')
```

- [ ] **步骤 2：端到端测试**

1. `python create_admin.py` 创建超管
2. 启动服务 `python web_server.py`
3. 浏览器访问 `/register` 注册游客账号
4. 浏览器访问 `/login` 登录游客
5. 游客访问 `/chat/` 发送对话，验证无写文件权限
6. 用超管登录 `/admin/`，将游客提权为部门职员
7. 再次测试提权后的权限

- [ ] **步骤 3：运行所有单元测试**

```bash
python -m pytest test_auth.py -v
```

预期：所有测试 PASS

- [ ] **步骤 4：最终 Commit**

```bash
git add create_admin.py
git commit -m "feat: add admin account creation script, finalize auth system"
```
