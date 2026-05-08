#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""部署脚本：上传项目文件到远程服务器并启动服务"""

import os
import time
import paramiko
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

HOST = os.environ.get('SSH_HOST', '')
PORT = int(os.environ.get('SSH_PORT', 22))
USER = os.environ.get('SSH_USER', 'root')
PASSWORD = os.environ.get('SSH_PASSWORD', '')
SSH_KEY = os.environ.get('SSH_KEY', '')  # SSH 私钥路径
SSH_KEY_PASSPHRASE = os.environ.get('SSH_KEY_PASSPHRASE', '')
REMOTE_DIR = os.environ.get('REMOTE_DIR', '/root/fuwuqishiyongshili')

LOCAL_FILES = [
    'web_server.py',
    'tools.py',
    'auth.py',
    '.env',
    'gui_client.py',
    'requirements.txt',
    'Dockerfile',
]

LOCAL_DIRS = [
    'static',
]


def deploy():
    if not HOST:
        print("错误：请在 .env 中配置 SSH_HOST")
        return
    if not PASSWORD and not SSH_KEY:
        print("错误：请在 .env 中配置 SSH_PASSWORD 或 SSH_KEY")
        return

    print("=== 连接到远程服务器 ===")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    if SSH_KEY:
        key_path = os.path.expanduser(SSH_KEY)
        if not os.path.exists(key_path):
            print(f"错误：SSH 私钥文件不存在: {key_path}")
            return
        key = paramiko.RSAKey.from_private_key_file(
            key_path,
            password=SSH_KEY_PASSPHRASE or None
        ) if SSH_KEY_PASSPHRASE else paramiko.RSAKey.from_private_key_file(key_path)
        ssh.connect(HOST, port=PORT, username=USER, pkey=key, timeout=30)
        print(f"使用密钥认证: {key_path}")
    else:
        ssh.connect(HOST, port=PORT, username=USER, password=PASSWORD, timeout=30)
        print("使用密码认证")

    print("连接成功")

    sftp = ssh.open_sftp()

    print(f"\n=== 创建远程目录 {REMOTE_DIR} ===")
    try:
        sftp.stat(REMOTE_DIR)
    except IOError:
        sftp.mkdir(REMOTE_DIR)
        print("目录已创建")

    print("\n=== 上传文件 ===")
    for f in LOCAL_FILES:
        local_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f)
        remote_path = f'{REMOTE_DIR}/{f}'
        if os.path.exists(local_path):
            sftp.put(local_path, remote_path)
            print(f"  上传: {f}")
        else:
            print(f"  跳过(不存在): {f}")

    # 上传目录
    for d in LOCAL_DIRS:
        local_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), d)
        remote_dir = f'{REMOTE_DIR}/{d}'
        if os.path.isdir(local_dir):
            try:
                sftp.stat(remote_dir)
            except IOError:
                sftp.mkdir(remote_dir)
            for root, dirs, files in os.walk(local_dir):
                rel_root = os.path.relpath(root, local_dir)
                dest_dir = os.path.join(remote_dir, rel_root).replace('\\', '/')
                for sub_dir in dirs:
                    try:
                        sftp.stat(os.path.join(dest_dir, sub_dir).replace('\\', '/'))
                    except IOError:
                        sftp.mkdir(os.path.join(dest_dir, sub_dir).replace('\\', '/'))
                for file in files:
                    src = os.path.join(root, file)
                    dst = os.path.join(dest_dir, file).replace('\\', '/')
                    sftp.put(src, dst)
            print(f"  上传目录: {d}")
        else:
            print(f"  跳过目录(不存在): {d}")

    print("\n=== 停止旧服务 ===")
    ssh.exec_command('pkill -f "python3 web_server.py" || true')
    stdin, stdout, stderr = ssh.exec_command('docker stop ai-server 2>/dev/null; docker rm ai-server 2>/dev/null || true')
    stdout.read()
    time.sleep(1)

    print("\n=== 检查 Docker ===")
    stdin, stdout, stderr = ssh.exec_command('which docker')
    docker_exists = stdout.read().decode().strip()

    if docker_exists:
        print("Docker 已安装，使用 Docker 部署")
        print("\n=== 构建 Docker 镜像 ===")
        stdin, stdout, stderr = ssh.exec_command(f'cd {REMOTE_DIR} && docker build -t ai-server .')
        build_out = stdout.read().decode()
        build_err = stderr.read().decode().strip()
        print(build_out)
        if build_err:
            print("构建信息:", build_err)

        print("\n=== 启动 Docker 容器 ===")
        stdin, stdout, stderr = ssh.exec_command(
            f'docker run -d --name ai-server --restart unless-stopped '
            f'-p 8888:8888 --env-file {REMOTE_DIR}/.env ai-server'
        )
        run_out = stdout.read().decode().strip()
        run_err = stderr.read().decode().strip()
        if run_out:
            print(f"容器ID: {run_out[:12]}")
        if run_err:
            print(f"启动信息: {run_err}")
    else:
        print("Docker 未安装，使用 Python 直接运行")
        ssh.exec_command(f'cd {REMOTE_DIR} && pip3 install python-dotenv 2>/dev/null || true')
        ssh.exec_command(
            f'cd {REMOTE_DIR} && nohup python3 web_server.py > /tmp/web_server.log 2>&1 &'
        )

    print("服务已启动")
    time.sleep(2)

    print("\n=== 检查服务状态 ===")
    stdin, stdout, stderr = ssh.exec_command('curl -s http://127.0.0.1:8888/api/status')
    result = stdout.read().decode('utf-8', errors='replace')
    print(result or "无响应")

    sftp.close()
    ssh.close()
    print("\n部署完成!")


if __name__ == '__main__':
    deploy()
