#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工具权限流水线 — 映射自 Claude Code: bashPermissions.ts / PermissionResult

权限行为 (behavior):
- allow:       自动允许
- deny:        拒绝
- ask:         需要用户确认
- passthrough: 不在规则中,交给下一个检查器
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class PermissionBehavior(str, Enum):
    ALLOW = 'allow'
    DENY = 'deny'
    ASK = 'ask'
    PASSTHROUGH = 'passthrough'


@dataclass
class PermissionResult:
    """权限检查结果"""
    behavior: PermissionBehavior
    message: str = ''
    decision_reason: str = ''
    suggestions: list = field(default_factory=list)
    updated_input: Optional[dict] = None

    @classmethod
    def allow(cls, reason: str = '', updated_input: dict = None):
        return cls(PermissionBehavior.ALLOW, reason,
                   decision_reason=reason, updated_input=updated_input)

    @classmethod
    def deny(cls, reason: str = ''):
        return cls(PermissionBehavior.DENY, reason, decision_reason=reason)

    @classmethod
    def ask(cls, reason: str = ''):
        return cls(PermissionBehavior.ASK, reason, decision_reason=reason)

    @classmethod
    def passthrough(cls, reason: str = ''):
        return cls(PermissionBehavior.PASSTHROUGH, reason,
                   decision_reason=reason)

    @property
    def is_allowed(self) -> bool:
        return self.behavior == PermissionBehavior.ALLOW


def check_role_level(user: Optional[dict], min_role: str,
                     role_levels: dict) -> PermissionResult:
    """基于角色等级的权限检查"""
    if not user:
        return PermissionResult.deny('未登录')
    user_level = user.get('role_level', 0)
    min_level = role_levels.get(min_role, 0)
    if user_level >= min_level:
        return PermissionResult.allow('角色权限通过')
    return PermissionResult.deny(
        f'权限不足: 需要 {min_role} (等级 {min_level}), 当前 {user.get("role", "guest")} (等级 {user_level})'
    )
