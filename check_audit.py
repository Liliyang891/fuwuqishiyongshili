import sys, io, os, paramiko
from dotenv import load_dotenv
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
load_dotenv('.env')

PY_CODE = """from tools import _get_users_db_conn
c = _get_users_db_conn()
rows = c.execute(
    "SELECT a.id, a.user_id, a.action, "
    "datetime(a.created_at, 'unixepoch', 'localtime') as dt, "
    "a.detail FROM audit_log a ORDER BY a.id DESC LIMIT 20"
).fetchall()
print(f'Total audit entries: {len(rows)}')
print()
for r in rows:
    detail = (r[4] or '')[:80]
    print(f'  #{r[0]:<4} uid={r[1]:<5} {r[2]:30} {r[3]}  {detail}')
"""

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(
    os.environ['SSH_HOST'],
    port=int(os.environ.get('SSH_PORT', 22)),
    username=os.environ['SSH_USER'],
    password=os.environ['SSH_PASSWORD'],
    timeout=15,
)

sftp = ssh.open_sftp()
sftp.putfo(io.BytesIO(PY_CODE.encode('utf-8')), '/root/_audit_check.py')
sftp.close()

stdin, stdout, stderr = ssh.exec_command(
    'docker cp /root/_audit_check.py ai-server:/app/_audit_check.py && '
    'docker exec ai-server python3 /app/_audit_check.py',
    timeout=15
)
print(stdout.read().decode())
err = stderr.read().decode().strip()
if err:
    print('STDERR:', err)

ssh.exec_command('rm -f /root/_audit_check.py', timeout=5)
ssh.close()
