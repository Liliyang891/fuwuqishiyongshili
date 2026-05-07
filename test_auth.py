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
