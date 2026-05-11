#!/usr/bin/env python3
"""Fix .env and verify deployment"""
import os
import sys
import time

import paramiko

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
except ImportError:
    pass

HOST = os.environ.get('SSH_HOST', '')
PORT = int(os.environ.get('SSH_PORT', 22))
USER = os.environ.get('SSH_USER', 'root')
PASSWORD = os.environ.get('SSH_PASSWORD', '')
REMOTE_DIR = os.environ.get('REMOTE_DIR', '/root/fuwuqishiyongshili')

if not HOST or not PASSWORD:
    print('错误：请在 .env 中配置 SSH_HOST 和 SSH_PASSWORD')
    sys.exit(1)

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, port=PORT, username=USER, password=PASSWORD, timeout=60)

def run(cmd):
    stdin, stdout, stderr = c.exec_command(cmd)
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    if out.strip():
        print(out.strip())
    if err.strip():
        print('[stderr]', err.strip()[:300])
    return out, err

# Check if .env exists on server
print('=== Check .env on server ===')
run(f'ls -la {REMOTE_DIR}/.env 2>&1')
run(f'cat {REMOTE_DIR}/.env 2>&1 | head -20')

# Check current docker run command
print('=== Current docker run ===')
run("docker inspect ai-server --format '{{.Config.Cmd}} {{.Config.Env}} {{range .Mounts}}{{.Source}}:{{.Destination}} {{end}}'")

# Remount with .env if exists
print('\n=== Restart with .env mount ===')
run('docker stop ai-server 2>/dev/null; docker rm ai-server 2>/dev/null')
run(f'docker run -d --name ai-server --restart unless-stopped -p 8888:8888 -v {REMOTE_DIR}/data:/app/data -v {REMOTE_DIR}/.env:/app/.env -e TZ=Asia/Shanghai ai-server')

time.sleep(3)

print('\n=== New logs ===')
run('docker logs --tail 20 ai-server 2>&1')

print('\n=== Final check ===')
run("docker exec ai-server python -c 'import web_server; print(\"Registry:\", len(web_server._agent_registry), \"tools\"); print(\"AgentLoop OK\")'")
run('curl -s http://localhost:8888/api/status')

c.close()
