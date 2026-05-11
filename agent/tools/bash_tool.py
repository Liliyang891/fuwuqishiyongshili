#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bash/Shell 执行工具 — 映射自 Claude Code: src/tools/BashTool/BashTool.tsx

支持命令分类权限、后台执行、超时控制、危险模式检测。
"""

import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from ..tool_base import Tool
from ..permissions import PermissionResult, PermissionBehavior

logger = logging.getLogger(__name__)

# ── 命令分类 ──
COMMAND_CLASSES = {
    'read': {'cat', 'head', 'tail', 'wc', 'stat', 'file', 'strings',
             'od', 'hexdump', 'xxd', 'less', 'more'},
    'search': {'grep', 'rg', 'ag', 'find', 'locate', 'which', 'whereis',
               'where', 'type'},
    'list': {'ls', 'dir', 'tree', 'du', 'df', 'pwd', 'realpath', 'readlink'},
    'write': {'echo', 'printf', 'mkdir', 'touch', 'cp', 'mv', 'tee',
              'ln', 'chmod', 'chown', 'ren', 'rename'},
    'destructive': {'rm', 'rmdir', 'dd', 'kill', 'pkill', 'shred'},
    'git_read': {'git', 'gh'},
    'python': {'python', 'python3', 'py'},
    'npm': {'npm', 'npx', 'yarn', 'pnpm'},
    'docker': {'docker', 'docker-compose'},
}

DANGEROUS_PATTERNS = [
    (re.compile(r'(curl|wget).*\|.*(bash|sh|zsh|ksh)'), 'curl-pipe-shell'),
    (re.compile(r'rm\s+(-rf|-fr)\s+/'), 'rm-root'),
    (re.compile(r'dd\s+if='), 'dd-write'),
    (re.compile(r'>\s*/dev/sd[a-z]'), 'write-block-device'),
    (re.compile(r'mkfs\.'), 'format-filesystem'),
    (re.compile(r'chmod\s+777\s+/'), 'chmod-root-777'),
    (re.compile(r'(sudo|su\s+-)'), 'sudo'),
    (re.compile(r'systemctl\s+(stop|disable|mask)'), 'systemctl-destructive'),
]

MAX_OUTPUT_LENGTH = 100_000
TIMEOUT_DEFAULT = 120
TIMEOUT_MAX = 600


def _classify_command(cmd: str) -> str:
    """分类命令字符串"""
    cmd_stripped = cmd.strip()
    if not cmd_stripped:
        return 'read'
    first_word = cmd_stripped.split()[0].lower()
    if first_word.endswith('.exe'):
        first_word = first_word[:-4]
    for cls, words in COMMAND_CLASSES.items():
        if first_word in words:
            return cls
    return 'other'


def _check_dangerous_patterns(cmd: str) -> list[str]:
    """检查危险命令模式,返回匹配的规则名列表"""
    found = []
    for pattern, name in DANGEROUS_PATTERNS:
        if pattern.search(cmd):
            found.append(name)
    return found


class BashTool(Tool):
    """Shell 命令执行工具

    支持 bash 和 powershell 命令。
    自动检测危险模式, 按角色分类授权:
    - guest: read/search/list/git_read
    - staff: + write/python/npm
    - super_admin: + destructive/docker
    """

    name = 'Bash'
    description = '执行 Shell 命令。用于运行脚本、安装依赖、构建项目等系统操作。'
    is_concurrency_safe = False
    is_read_only = False
    min_role = 'guest'
    tool_category = 'system'

    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'command': {
                    'type': 'string',
                    'description': '要执行的 Shell 命令（bash 或 powershell）',
                },
                'description': {
                    'type': 'string',
                    'description': '命令描述（用于审计日志）',
                },
                'timeout': {
                    'type': 'integer',
                    'description': f'超时秒数，默认 {TIMEOUT_DEFAULT}s，最大 {TIMEOUT_MAX}s',
                    'default': TIMEOUT_DEFAULT,
                },
                'dangerouslyDisableSandbox': {
                    'type': 'boolean',
                    'description': '设为 true 可越过沙箱限制（仅高级角色可用）',
                    'default': False,
                },
                'run_in_background': {
                    'type': 'boolean',
                    'description': '设为 true 在后台运行，完成时通知',
                    'default': False,
                },
            },
            'required': ['command'],
        }

    def prompt(self):
        return """## Shell 命令执行 (Bash / PowerShell)
- 执行系统命令前先评估影响范围
- 危险操作（删除文件、修改系统配置）会被标记
- 每次调用只能执行一个命令（禁止使用 && 或 ; 串联多个）"""

    def validate_input(self, arguments, context=None):
        cmd = arguments.get('command', '')
        if not cmd or not cmd.strip():
            return False, '命令不能为空'

        dangerous = _check_dangerous_patterns(cmd)
        if dangerous:
            return False, f'命令包含危险模式: {", ".join(dangerous)}'

        timeout = arguments.get('timeout', TIMEOUT_DEFAULT)
        if timeout > TIMEOUT_MAX:
            arguments['timeout'] = TIMEOUT_MAX
        elif timeout <= 0:
            arguments['timeout'] = TIMEOUT_DEFAULT

        return True, ''

    def check_permissions(self, arguments, user=None) -> PermissionResult:
        cmd = arguments.get('command', '')
        cmd_class = _classify_command(cmd)
        role = user.get('role', 'guest') if user else 'guest'
        try:
            from auth import ROLE_LEVEL
        except ImportError:
            ROLE_LEVEL = {'super_admin': 6, 'chairman': 5, 'gm': 4, 'dept_head': 3, 'staff': 2, 'guest': 1}
        user_level = ROLE_LEVEL.get(role, 0)

        if cmd_class in ('read', 'search', 'list', 'git_read'):
            return PermissionResult.allow('读取类命令，所有角色可用')
        if cmd_class in ('write', 'python', 'npm'):
            if user_level >= ROLE_LEVEL.get('staff', 2):
                return PermissionResult.allow('写入类命令，职员及以上可用')
            return PermissionResult.deny(f'写入类命令需要职员及以上权限，当前: {role}')
        if cmd_class in ('destructive', 'docker', 'other'):
            if user_level >= ROLE_LEVEL.get('super_admin', 6):
                return PermissionResult.allow('系统级命令，仅超级管理员可用')
            return PermissionResult.deny(f'系统级命令仅超级管理员可用，当前: {role}')
        return PermissionResult.deny(f'未知命令类别: {cmd_class}')

    def call(self, arguments, user=None, context=None) -> dict:
        cmd = arguments['command']
        timeout = arguments.get('timeout', TIMEOUT_DEFAULT)
        background = arguments.get('run_in_background', False)
        description = arguments.get('description', '')

        if background:
            return self._run_background(cmd, timeout, description)

        return self._run_sync(cmd, timeout)

    def _run_sync(self, cmd: str, timeout: int) -> dict:
        """同步执行命令"""
        start = time.time()
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=os.getcwd(),
                env={**os.environ, 'PYTHONUNBUFFERED': '1'},
            )
            duration_ms = int((time.time() - start) * 1000)
            output = (proc.stdout or '') + (proc.stderr or '')
            if len(output) > MAX_OUTPUT_LENGTH:
                output = output[:MAX_OUTPUT_LENGTH] + f'\n... 输出被截断 (超过 {MAX_OUTPUT_LENGTH} 字符)'

            return {
                'success': proc.returncode == 0,
                'result': output,
                'exit_code': proc.returncode,
                'duration_ms': duration_ms,
                'truncated': len(proc.stdout + proc.stderr) > MAX_OUTPUT_LENGTH,
            }
        except subprocess.TimeoutExpired:
            return {
                'success': False,
                'error': f'命令超时（{timeout}s）',
                'error_type': 'timeout',
                'duration_ms': timeout * 1000,
            }
        except Exception as e:
            return {
                'success': False,
                'error': f'命令执行失败: {e}',
                'error_type': 'exception',
                'duration_ms': int((time.time() - start) * 1000),
            }

    def _run_background(self, cmd: str, timeout: int, description: str) -> dict:
        """后台执行命令"""
        bg_id = f"bg_{int(time.time() * 1000)}"
        output_file = os.path.join(
            os.environ.get('TMP', '/tmp'),
            f'agent_bg_{bg_id}.txt'
        )

        def _bg_exec():
            try:
                proc = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True,
                    timeout=timeout, cwd=os.getcwd(),
                )
                with open(output_file, 'w', encoding='utf-8') as f:
                    f.write(proc.stdout or '')
                    f.write(proc.stderr or '')
            except Exception as e:
                with open(output_file, 'w', encoding='utf-8') as f:
                    f.write(f'Error: {e}')

        t = threading.Thread(target=_bg_exec, daemon=True)
        t.start()

        return {
            'success': True,
            'result': f'后台任务已启动 (ID: {bg_id})。\n'
                      f'输出将写入: {output_file}\n'
                      f'描述: {description or cmd[:80]}',
            'background': True,
            'bg_id': bg_id,
            'output_file': output_file,
        }
