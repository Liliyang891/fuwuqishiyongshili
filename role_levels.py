# -*- coding: utf-8 -*-
"""角色等级常量和工具（单一数据源，避免多处重复定义）"""

ROLE_LEVEL = {
    'super_admin': 6, 'chairman': 5, 'gm': 4,
    'dept_head': 3, 'staff': 2, 'guest': 1,
}

ROLE_NAMES = {
    'super_admin': '超级管理员',
    'chairman': '董事长',
    'gm': '总经理',
    'dept_head': '部门长',
    'staff': '部门职员',
    'guest': '游客',
}

ROLE_DISPLAY = ROLE_NAMES  # 向后兼容别名

ROLE_ORDER = sorted(ROLE_LEVEL.items(), key=lambda x: -x[1])

# 级别→角色名反向映射
LEVEL_TO_ROLE = {v: k for k, v in ROLE_LEVEL.items()}

# 角色升级链
ROLE_CHAIN = ['guest', 'staff', 'dept_head', 'gm', 'chairman', 'super_admin']
SUPERIOR_MAP = {}
for i, role in enumerate(ROLE_CHAIN):
    if i + 1 < len(ROLE_CHAIN):
        SUPERIOR_MAP[role] = ROLE_CHAIN[i + 1]
