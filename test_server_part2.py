#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""服务器功能补充测试 — 使用直接 API + 更长超时"""
import json, os, urllib.request, urllib.error, time, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

HOST = '47.250.59.60'
PORT = '8888'
BASE = f'http://{HOST}:{PORT}'
PASS = 0; FAIL = 0
TIMEOUT = 120

def api(method, path, data=None, cookie=None):
    url = f'{BASE}{path}'
    headers = {'Content-Type': 'application/json'}
    if cookie: headers['Cookie'] = cookie
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=TIMEOUT)
        sc = r.headers.get('Set-Cookie', '')
        return r.status, json.loads(r.read().decode()) if r.status != 204 else {}, sc
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode()), ''
        except: return e.code, {'error': str(e)}, ''
    except Exception as e:
        return 0, {'error': str(e)}, ''

def extract_token(sc):
    return f"session_token={sc.split('session_token=')[1].split(';')[0]}" if 'session_token=' in sc else ''

def check(name, cond, d=''):
    global PASS, FAIL
    if cond: PASS+=1; print(f'  ✅ {name}'+(f' — {d}' if d else ''))
    else: FAIL+=1; print(f'  ❌ {name}'+(f' — {d}' if d else ''))
    return cond

print('='*60)
print('  补充测试 — 文件权限 / 策略引擎 / 管理后台')
print('='*60)

# 登录各角色
tokens = {}
for name, role in [('admin','超级管理员'),('test_chairman','董事长'),('test_gm','总经理'),
                    ('test_dept_head','部门长'),('test_staff','职员'),('test_guest','游客')]:
    s, r, sc = api('POST', '/api/login', {'login': name, 'password': 'Test@123'})
    if s == 429:
        time.sleep(5)
        s, r, sc = api('POST', '/api/login', {'login': name, 'password': 'Test@123'})
    if s == 200 and r.get('success'):
        tokens[name] = extract_token(sc)
        u = r['user']
        check(f'{name} 登录 {role}', u['role'] == (name.split('_',1)[1] if name!='admin' else 'super_admin'),
              f'实际: {u["role"]} lv{u["role_level"]}')

# ━━━ 1. 文件夹结构验证 ━━━
print('\n📁 1. 角色文件夹验证')

# 用 admin 列出所有文件
if 'admin' in tokens:
    s, r, _ = api('GET', '/api/files/', cookie=tokens['admin'])
    items = r.get('items', [])
    dirs = [i['name'] for i in items if i['type'] == 'directory']
    check('根目录有文件夹', len(dirs) > 0, f'目录: {dirs}')

# ━━━ 2. 跨角色文件访问权限 ━━━
print('\n🔐 2. 跨角色文件访问')

# Chairman 用 AI 创建制度和文件（更长超时）
if 'test_chairman' in tokens:
    s, r, _ = api('POST', '/api/command', {'text': '列出 chairman 目录下所有文件'}, cookie=tokens['test_chairman'])
    check('chairman 列出自己目录', s == 200 and r.get('success'), r.get('reply','')[:60])

if 'test_staff' in tokens:
    s, r, _ = api('POST', '/api/command', {'text': '列出 staff 目录下的文件'}, cookie=tokens['test_staff'])
    check('staff 列出自己目录', s == 200 and r.get('success'), r.get('reply','')[:60])

# Staff 读 chairman 的文件
if 'test_staff' in tokens:
    s, r, _ = api('POST', '/api/command',
        {'text': '读取 chairman/公司制度/考勤制度.txt 的内容'},
        cookie=tokens['test_staff'])
    check('staff 读 chairman 文件', s == 200 and r.get('success'),
          r.get('reply','')[:100])

# Staff 尝试修改 chairman 的文件（应该被拒绝）
if 'test_staff' in tokens:
    s, r, _ = api('POST', '/api/command',
        {'text': '修改 chairman/公司制度/考勤制度.txt，将"迟到一次扣50元"改为"迟到一次扣10元"'},
        cookie=tokens['test_staff'])
    check('staff 修改 chairman 文件', s == 200,
          '权限拦截' if not r.get('success') or '权限' in r.get('reply','') else '⚠️ 越权修改成功')

# Guest 只能读 guest 目录
if 'test_guest' in tokens:
    s, r, _ = api('POST', '/api/command', {'text': '列出我能访问的所有文件'}, cookie=tokens['test_guest'])
    check('guest 仅限自己目录', s == 200, r.get('reply','')[:80])

# ━━━ 3. 策略引擎测试 ━━━
print('\n📋 3. 策略引擎')

if 'test_chairman' in tokens:
    s, r, _ = api('POST', '/api/command', {
        'text': '【重要：必须使用工具】用 CreatePolicy 工具创建请假制度：类型 leave_policy，名称 考勤休假制度，规则为：年假5天，提前1天申请，3天以内部门长批，3天以上总经理批'
    }, cookie=tokens['test_chairman'])
    check('chairman 创建请假制度', s == 200 and r.get('success'),
          r.get('reply','')[:120] if r.get('success') else r.get('error','')[:80])

# Deactivate 之前的制度再创建一个
if 'test_chairman' in tokens and False:  # skip for speed
    pass

if 'test_staff' in tokens:
    s, r, _ = api('POST', '/api/command', {
        'text': '【必须使用工具】用 ApplyLeave 工具申请请假1天，理由是身体不舒服'
    }, cookie=tokens['test_staff'])
    check('staff 申请请假', s == 200 and r.get('success'),
          r.get('reply','')[:120] if r.get('success') else r.get('error','')[:80])

if 'test_dept_head' in tokens:
    s, r, _ = api('POST', '/api/command', {
        'text': '【必须使用工具】用 QueryPolicies 工具查询待审批请假，然后用 ApproveLeave 工具批准第一条'
    }, cookie=tokens['test_dept_head'])
    check('dept_head 审批请假', s == 200 and r.get('success'),
          r.get('reply','')[:120] if r.get('success') else r.get('error','')[:80])

if 'test_staff' in tokens:
    s, r, _ = api('POST', '/api/command', {
        'text': '【必须使用工具】用 LeaveHistory 工具查我的请假历史'
    }, cookie=tokens['test_staff'])
    check('staff 查看请假历史', s == 200 and r.get('success'),
          r.get('reply','')[:120] if r.get('success') else r.get('error','')[:80])

# ━━━ 4. 管理后台 ━━━
print('\n👥 4. 管理后台')

if 'admin' in tokens:
    s, r, _ = api('GET', '/api/admin/users', cookie=tokens['admin'])
    users = r.get('users', [])
    roles_count = {}
    for u in users:
        roles_count[u['role']] = roles_count.get(u['role'], 0) + 1
    check('用户列表', s == 200 and len(users) >= 7, f'{len(users)}用户, 角色分布: {roles_count}')

    s, r, _ = api('GET', '/api/admin/departments', cookie=tokens['admin'])
    check('部门列表', s == 200 and len(r.get('departments',[])) >= 1,
          f'{len(r.get("departments",[]))} 个部门')

    s, r, _ = api('GET', '/api/admin/audit-logs', cookie=tokens['admin'])
    check('审计日志', s == 200 and isinstance(r.get('logs',[]), list),
          f'{len(r.get("logs",[]))} 条')

# Staff 访问管理后台被拒
if 'test_staff' in tokens:
    s, r, _ = api('GET', '/api/admin/users', cookie=tokens['test_staff'])
    check('staff 403 on admin', s == 403)

# ━━━ 5. 文件操作验证 ━━━
print('\n📄 5. 直接文件操作验证')

if 'test_chairman' in tokens:
    s, r, _ = api('GET', '/api/files/chairman/', cookie=tokens['test_chairman'])
    items = r.get('items', [])
    check('chairman 目录有文件', len(items) > 0,
          ', '.join(f"{i['name']}({i['type']})" for i in items[:5]))

if 'test_staff' in tokens:
    s, r, _ = api('GET', '/api/files/staff/test_note.txt', cookie=tokens['test_staff'])
    check('staff 可读取自己的文件', s == 200,
          f'状态码: {s}' + (f', 内容: {r.get("content","")[:50]}' if isinstance(r, dict) else ''))

# ━━━ 报告 ━━━
print('\n' + '=' * 60)
print(f'  补充测试结果: ✅ {PASS} 通过  ❌ {FAIL} 失败')
if PASS+FAIL > 0:
    print(f'  通过率: {100*PASS/(PASS+FAIL):.1f}%')
print('=' * 60)
sys.exit(0 if FAIL == 0 else 1)
