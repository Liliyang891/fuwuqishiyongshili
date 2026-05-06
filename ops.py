#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
服务器运维工具 — 合并了 status/logs/restart/deploy 等操作
用法: python ops.py <命令>
  status   - 检查服务器状态（SSH + 公网）
  logs     - 查看服务器日志
  restart  - 重启远程服务
  deploy   - 完整部署（上传文件 + 启动服务）
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error

try:
    import paramiko
except ImportError:
    print("错误：请先安装 paramiko: pip install paramiko")
    sys.exit(1)

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

HOST = os.environ.get('SSH_HOST', '')
PORT = int(os.environ.get('SSH_PORT', 22))
USER = os.environ.get('SSH_USER', 'root')
PASSWORD = os.environ.get('SSH_PASSWORD', '')
REMOTE_DIR = os.environ.get('REMOTE_DIR', '/root/fuwuqishiyongshili')
SERVER_PORT = os.environ.get('SERVER_PORT', '8888')


def _connect_ssh():
    if not HOST or not PASSWORD:
        print("错误：请在 .env 中配置 SSH_HOST 和 SSH_PASSWORD")
        sys.exit(1)
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, port=PORT, username=USER, password=PASSWORD, timeout=10)
    return ssh


def cmd_status():
    """检查服务器状态"""
    print("=== SSH 连接检查 ===")
    ssh = _connect_ssh()
    print("SSH 连接成功")

    print("\n=== Python 进程 ===")
    _, stdout, _ = ssh.exec_command('ps aux | grep python')
    print(stdout.read().decode('utf-8', errors='replace'))

    print(f"\n=== 端口 {SERVER_PORT} 监听状态 ===")
    _, stdout, _ = ssh.exec_command(f'netstat -tlnp 2>/dev/null | grep {SERVER_PORT} || ss -tlnp | grep {SERVER_PORT}')
    print(stdout.read().decode('utf-8', errors='replace') or "未监听")

    print("\n=== 内部 API 测试 ===")
    _, stdout, _ = ssh.exec_command(f'curl -s http://127.0.0.1:{SERVER_PORT}/api/status')
    print(stdout.read().decode('utf-8', errors='replace') or "(无响应)")

    _, stdout, _ = ssh.exec_command(f'curl -s http://127.0.0.1:{SERVER_PORT}/api/models')
    print(stdout.read().decode('utf-8', errors='replace') or "(无响应)")

    ssh.close()

    print(f"\n=== 公网访问测试 ===")
    try:
        r = urllib.request.urlopen(f'http://{HOST}:{SERVER_PORT}/api/status', timeout=10)
        print(r.read().decode('utf-8', errors='replace'))
    except Exception as e:
        print(f"公网访问失败: {e}")


def cmd_logs():
    """查看服务器日志"""
    ssh = _connect_ssh()

    print("=== 服务器日志 (最近 50 行) ===")
    _, stdout, _ = ssh.exec_command('tail -50 /tmp/web_server.log')
    print(stdout.read().decode('utf-8', errors='replace'))

    # 如果使用 Docker，也检查容器日志
    _, stdout, _ = ssh.exec_command('docker logs --tail 50 ai-server 2>/dev/null')
    docker_logs = stdout.read().decode('utf-8', errors='replace').strip()
    if docker_logs:
        print("\n=== Docker 容器日志 ===")
        print(docker_logs)

    ssh.close()


def cmd_restart():
    """重启远程服务"""
    ssh = _connect_ssh()

    print("=== 停止旧服务 ===")
    ssh.exec_command('pkill -f "python3 web_server.py" || true')
    ssh.exec_command('docker restart ai-server 2>/dev/null || true')
    time.sleep(1)

    # 检查 Docker 容器是否在运行
    _, stdout, _ = ssh.exec_command('docker ps --filter name=ai-server --format "{{.ID}}"')
    container_id = stdout.read().decode().strip()

    if not container_id:
        print("Docker 容器未运行，使用 Python 直接启动")
        ssh.exec_command(
            f'cd {REMOTE_DIR} && nohup python3 web_server.py > /tmp/web_server.log 2>&1 &'
        )

    print("服务已重启")
    time.sleep(2)

    _, stdout, _ = ssh.exec_command(f'curl -s http://127.0.0.1:{SERVER_PORT}/api/status')
    result = stdout.read().decode('utf-8', errors='replace')
    print(f"状态: {result or '无响应'}")

    ssh.close()


def cmd_deploy():
    """完整部署（调用 deploy.py 的逻辑）"""
    import deploy
    deploy.deploy()


def cmd_config():
    """查看远程服务器配置"""
    ssh = _connect_ssh()

    print("=== 远程 .env ===")
    _, stdout, _ = ssh.exec_command(f'cat {REMOTE_DIR}/.env 2>/dev/null | grep -v KEY | grep -v PASSWORD')
    result = stdout.read().decode('utf-8', errors='replace')
    print(result or "(文件不存在)")

    print("\n=== 远程 config.json ===")
    _, stdout, _ = ssh.exec_command(f'cat {REMOTE_DIR}/config.json 2>/dev/null')
    print(stdout.read().decode('utf-8', errors='replace') or "(文件不存在)")

    ssh.close()


COMMANDS = {
    'status': ('检查服务器状态', cmd_status),
    'logs': ('查看服务器日志', cmd_logs),
    'restart': ('重启远程服务', cmd_restart),
    'deploy': ('完整部署', cmd_deploy),
    'config': ('查看远程配置（隐藏敏感值）', cmd_config),
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("服务器运维工具")
        print(f"用法: python {os.path.basename(__file__)} <命令>\n")
        print("可用命令:")
        for name, (desc, _) in COMMANDS.items():
            print(f"  {name:<10} {desc}")
        sys.exit(0)

    cmd_name = sys.argv[1]
    _, func = COMMANDS[cmd_name]
    func()


if __name__ == '__main__':
    main()
