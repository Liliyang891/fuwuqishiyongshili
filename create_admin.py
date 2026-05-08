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
