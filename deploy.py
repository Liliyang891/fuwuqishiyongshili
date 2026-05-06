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
REMOTE_DIR = os.environ.get('REMOTE_DIR', '/root/fuwuqishiyongshili')

LOCAL_FILES = [
    'web_server.py',
    'tools.py',
    '.env',
    'gui_client.py',
    'requirements.txt',
    'Dockerfile',
]


def deploy():
    if not HOST or not PASSWORD:
        print("错误：请在 .env 中配置 SSH_HOST 和 SSH_PASSWORD")
        return

    print("=== 连接到远程服务器 ===")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, port=PORT, username=USER, password=PASSWORD, timeout=30)
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
