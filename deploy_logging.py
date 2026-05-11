#!/usr/bin/env python3
"""Deploy logging changes to server"""
import os, sys, io, paramiko, time
from dotenv import load_dotenv
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
load_dotenv('.env')

LOCAL = os.path.dirname(os.path.abspath(__file__))
REMOTE = os.environ.get('REMOTE_DIR', '/root/fuwuqishiyongshili')

FILES = [
    'auth.py',
    'web_server.py',
    'static/index.html',
    'static/login.html',
    'static/register.html',
]

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(os.environ['SSH_HOST'], port=int(os.environ.get('SSH_PORT', 22)),
            username=os.environ['SSH_USER'], password=os.environ['SSH_PASSWORD'], timeout=30)
print("[OK] Connected")

sftp = ssh.open_sftp()
for f in FILES:
    src = os.path.join(LOCAL, f)
    dst = f'{REMOTE}/{f}'
    sftp.put(src, dst)
    print(f"  Uploaded: {f}")
sftp.close()

# Copy py files into container, restart
for f in ['auth.py', 'web_server.py']:
    ssh.exec_command(f'docker cp {REMOTE}/{f} ai-server:/app/{f}', timeout=10)
# Static files go to container too
for f in ['static/index.html', 'static/login.html', 'static/register.html']:
    ssh.exec_command(f'docker cp {REMOTE}/{f} ai-server:/app/{f}', timeout=10)

print("[OK] Copied to container")
ssh.exec_command('docker restart ai-server', timeout=10)
print("[OK] Restarting, waiting 5s...")
time.sleep(5)

# Health check
stdin, stdout, stderr = ssh.exec_command('curl -s http://127.0.0.1:8888/api/status', timeout=10)
print("Health:", stdout.read().decode()[:100])
ssh.close()
print("[OK] Done")
