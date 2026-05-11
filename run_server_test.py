#!/usr/bin/env python3
"""Upload and run comprehensive test on server"""
import io
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

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, port=PORT, username=USER, password=PASSWORD, timeout=15)
print("SSH connected")

# Upload test script
with open('test_comprehensive.py', 'rb') as f:
    sftp = ssh.open_sftp()
    sftp.putfo(f, f'{REMOTE_DIR}/test_comprehensive.py')
    sftp.close()
print("Test script uploaded")

# Copy into Docker container
stdin, stdout, stderr = ssh.exec_command(
    f'docker cp {REMOTE_DIR}/test_comprehensive.py ai-server:/app/test_comprehensive.py',
    timeout=10)
print("Copied to container")

# Run the test
print("\n" + "="*60)
print("Running comprehensive test suite...")
print("="*60 + "\n")

stdin, stdout, stderr = ssh.exec_command(
    'docker exec ai-server python3 /app/test_comprehensive.py',
    timeout=600)
out = stdout.read().decode('utf-8', errors='replace')
err = stderr.read().decode('utf-8', errors='replace')
print(out)
if err.strip():
    print("\nSTDERR:", err[:500])

ssh.close()
print("\nDone.")
