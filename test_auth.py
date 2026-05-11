import os
import sys
import time
import pytest

# 确保项目路径在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tools
from tools import _get_users_db_conn
from tools import _get_db_conn  # for app.db access (non-user tables)


def _clean_users():
    conn = _get_users_db_conn()
    conn.execute('DELETE FROM user_sessions')
    conn.execute('DELETE FROM users')
    conn.execute('DELETE FROM departments')
    conn.commit()


@pytest.fixture(autouse=True)
def setup():
    _clean_users()
    yield
    _clean_users()


def test_register_user():
    import auth
    user = auth.register_user('testuser', '12345678')
    assert user['username'] == 'testuser'
    assert user['role'] == 'guest'
    assert user['department_id'] is None
    assert user['is_active'] == 1


def test_register_duplicate_username():
    import auth
    auth.register_user('testuser', '12345678')
    with pytest.raises(ValueError, match='用户名已存在'):
        auth.register_user('testuser', '87654321')


def test_register_username_too_short():
    import auth
    with pytest.raises(ValueError, match='2-20'):
        auth.register_user('a', '12345678')


def test_register_password_too_short():
    import auth
    with pytest.raises(ValueError, match='8'):
        auth.register_user('validuser', '1234567')


def test_password_hashing():
    import auth
    auth.register_user('hashuser', '12345678')
    conn = _get_users_db_conn()
    row = conn.execute('SELECT password_hash FROM users WHERE username=?', ('hashuser',)).fetchone()
    assert row[0].startswith('$2b$')
    assert '12345678' not in row[0]


def test_login_success():
    import auth
    auth.register_user('loginuser', '12345678')
    token = auth.login_user('loginuser', '12345678')
    assert token is not None
    assert len(token) > 0
    # 验证 session 已存入数据库
    conn = _get_users_db_conn()
    row = conn.execute('SELECT user_id, expires_at FROM user_sessions WHERE token=?', (token,)).fetchone()
    assert row is not None


def test_login_wrong_password():
    import auth
    auth.register_user('loginuser2', '12345678')
    with pytest.raises(ValueError, match='密码错误'):
        auth.login_user('loginuser2', '99999999')


def test_login_inactive_user():
    import auth
    auth.register_user('inactiveuser', '12345678')
    conn = _get_users_db_conn()
    conn.execute('UPDATE users SET is_active=0 WHERE username=?', ('inactiveuser',))
    conn.commit()
    # thread-local conn, no close needed
    with pytest.raises(ValueError, match='禁用'):
        auth.login_user('inactiveuser', '12345678')


def test_login_nonexistent_user():
    import auth
    with pytest.raises(ValueError, match='用户不存在'):
        auth.login_user('nobody', '12345678')


def test_login_by_email():
    import auth
    auth.register_user('emailuser', '12345678', email='test@test.com')
    token = auth.login_user('test@test.com', '12345678')
    assert token is not None


def test_get_user_by_token():
    import auth
    auth.register_user('tokenuser', '12345678')
    token = auth.login_user('tokenuser', '12345678')
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
    auth.register_user('expireduser', '12345678')
    # 手动创建过期 session
    conn = _get_users_db_conn()
    row = conn.execute('SELECT id FROM users WHERE username=?', ('expireduser',)).fetchone()
    old_token = 'expired-token-001'
    conn.execute(
        'INSERT INTO user_sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)',
        (old_token, row[0], time.time() - 7200, time.time() - 3600)
    )
    conn.commit()
    # thread-local conn, no close needed
    user = auth.get_user_by_token(old_token)
    assert user is None


def test_logout():
    import auth
    auth.register_user('logoutuser', '12345678')
    token = auth.login_user('logoutuser', '12345678')
    auth.logout_session(token)
    user = auth.get_user_by_token(token)
    assert user is None


def test_login_remember_me():
    import auth
    auth.register_user('rememberuser', '12345678')
    token = auth.login_user('rememberuser', '12345678', remember_me=True)
    conn = _get_users_db_conn()
    row = conn.execute('SELECT expires_at FROM user_sessions WHERE token=?', (token,)).fetchone()
    # thread-local conn, no close needed
    # 7 天后 > 6 天后
    assert row['expires_at'] - time.time() > 6 * 24 * 3600


def test_role_level():
    import auth
    assert auth.get_role_level('super_admin') == 6
    assert auth.get_role_level('guest') == 1


def test_get_allowed_tools_super_admin():
    import auth
    user = {'role': 'super_admin', 'department_id': None}
    tools = auth.get_allowed_tools(user)
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
    assert 'read_file' in tool_names


def test_can_execute_tool():
    import auth
    user = {'role': 'super_admin', 'department_id': None}
    ok, msg = auth.can_execute_tool('delete_file', user, 'some/file.txt')
    assert ok is True


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
    dept = auth.create_department('技术部')
    user = auth.register_user('promoteme', '12345678')
    auth.update_user_role(user['id'], 'staff', dept['id'])
    conn = _get_users_db_conn()
    row = conn.execute('SELECT role, department_id FROM users WHERE id=?', (user['id'],)).fetchone()
    # thread-local conn, no close needed
    assert row['role'] == 'staff'
    assert row['department_id'] == dept['id']


def test_toggle_user_active():
    import auth
    user = auth.register_user('toggleuser', '12345678')
    auth.toggle_user_active(user['id'], False)
    conn = _get_users_db_conn()
    row = conn.execute('SELECT is_active FROM users WHERE id=?', (user['id'],)).fetchone()
    # thread-local conn, no close needed
    assert row['is_active'] == 0


def test_list_users():
    import auth
    auth.create_department('技术部')
    auth.register_user('user1', '12345678')
    auth.register_user('user2', '12345678', email='a@b.com')
    users = auth.list_users()
    assert len(users) >= 2


def test_reset_password():
    import auth
    user = auth.register_user('resetuser', '12345678')
    new_pass = auth.reset_user_password(user['id'], '11111111')
    token = auth.login_user('resetuser', '11111111')
    assert token is not None
