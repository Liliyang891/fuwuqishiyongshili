#!/usr/bin/env python3
"""Debug login response"""
import urllib.request, json, time

ts = int(time.time())
BASE = 'http://127.0.0.1:8888'

# Register
data = json.dumps({'username': f'debug_{ts}', 'password': 'test123456', 'email': f'd_{ts}@test.com'}).encode()
req = urllib.request.Request(f'{BASE}/api/register', data=data, headers={'Content-Type': 'application/json'}, method='POST')
resp = urllib.request.urlopen(req, timeout=10)
reg_body = resp.read().decode()
print('Register status:', resp.status)
print('Register body:', reg_body[:200])
print()

# Login
time.sleep(2)
data = json.dumps({'login': f'debug_{ts}', 'password': 'test123456'}).encode()
req = urllib.request.Request(f'{BASE}/api/login', data=data, headers={'Content-Type': 'application/json'}, method='POST')
try:
    resp = urllib.request.urlopen(req, timeout=10)
    body = resp.read().decode()
    print('Login status:', resp.status)
    print('Login headers:', dict(resp.headers))
    print('Login body raw:', repr(body[:500]))
    parsed = json.loads(body)
    print('Parsed type:', type(parsed))
    print('Parsed:', json.dumps(parsed, ensure_ascii=False)[:300])
except urllib.error.HTTPError as e:
    body = e.read().decode()
    print('HTTPError:', e.code)
    print('Error body:', repr(body[:500]))
    print('Error parsed:', json.loads(body) if body else 'empty')
except Exception as e:
    print('Exception:', type(e).__name__, str(e))
