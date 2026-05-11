#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""技能快速通道注册表 — 文件驱动的意图匹配系统

从 data/skills/*.yaml 加载技能定义，在 LLM 调用前匹配用户输入，
按 RBAC 权限执行处理器，实现秒级响应且不消耗 token。

支持三种处理器:
- sql: 直接查数据库（users.db 或 app.db）
- static: 返回静态文本
- function: 调用 Python 函数（适合需要格式化时间的场景）
"""

import logging
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ── 安全字典：模板渲染时缺失键返回原样而非报错 ──
class _SafeDict(dict):
    def __missing__(self, key):
        return f'{{{key}}}'


@dataclass
class SkillDefinition:
    name: str
    description: str
    min_role: str = 'guest'
    priority: int = 0
    intent_patterns: list = field(default_factory=list)
    intent_keywords: list = field(default_factory=list)
    handler_type: str = 'static'
    handler_config: dict = field(default_factory=dict)
    file_path: str = ''
    category: str = ''


class SkillRegistry:
    """技能快速通道注册表"""

    def __init__(self, skills_dir: str = None, role_levels: dict = None):
        self._skills: list[SkillDefinition] = []
        self._file_mtimes: dict[str, float] = {}
        self._lock = threading.Lock()
        if skills_dir:
            self._skills_dir = skills_dir
        else:
            self._skills_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                'data', 'skills'
            )
        self._role_levels = role_levels

    # ── 角色等级懒加载 ──
    def _get_role_levels(self) -> dict:
        if self._role_levels is None:
            from role_levels import ROLE_LEVEL
            self._role_levels = ROLE_LEVEL
        return self._role_levels

    # ═══════════════════════════════════════════
    # 加载与热更新
    # ═══════════════════════════════════════════

    def load(self) -> int:
        """加载所有技能文件，返回加载数量"""
        if not os.path.isdir(self._skills_dir):
            logger.warning('技能目录不存在: %s', self._skills_dir)
            return 0
        count = 0
        with self._lock:
            new_skills = []
            for fname in sorted(os.listdir(self._skills_dir)):
                if fname.endswith(('.yaml', '.yml')):
                    fpath = os.path.join(self._skills_dir, fname)
                    skill = self._load_one(fpath)
                    if skill:
                        new_skills.append(skill)
                        self._file_mtimes[fpath] = os.path.getmtime(fpath)
                        count += 1
            new_skills.sort(key=lambda s: (-s.priority, s.name))
            self._skills = new_skills
        logger.info('已加载 %d 个技能 (来源: %s)', count, self._skills_dir)
        return count

    def check_and_reload(self) -> int:
        """检查 mtime 变化，热加载变更的文件"""
        reloaded = 0
        with self._lock:
            # 检查已追踪文件的变更
            for fpath, old_mtime in list(self._file_mtimes.items()):
                try:
                    new_mtime = os.path.getmtime(fpath)
                    if new_mtime != old_mtime:
                        skill = self._load_one(fpath)
                        if skill:
                            for i, s in enumerate(self._skills):
                                if s.file_path == fpath:
                                    self._skills[i] = skill
                                    break
                            else:
                                self._skills.append(skill)
                            self._skills.sort(key=lambda s: (-s.priority, s.name))
                            self._file_mtimes[fpath] = new_mtime
                            reloaded += 1
                            logger.info('技能已重载: %s', skill.name)
                except FileNotFoundError:
                    self._skills = [s for s in self._skills if s.file_path != fpath]
                    del self._file_mtimes[fpath]
                    reloaded += 1
                    logger.info('技能文件已删除，移除: %s', fpath)

            # 检查新增文件
            if os.path.isdir(self._skills_dir):
                for fname in os.listdir(self._skills_dir):
                    if not fname.endswith(('.yaml', '.yml')):
                        continue
                    fpath = os.path.join(self._skills_dir, fname)
                    if fpath not in self._file_mtimes:
                        skill = self._load_one(fpath)
                        if skill:
                            self._skills.append(skill)
                            self._skills.sort(key=lambda s: (-s.priority, s.name))
                            self._file_mtimes[fpath] = os.path.getmtime(fpath)
                            reloaded += 1
                            logger.info('新增技能: %s', skill.name)
        return reloaded

    def _load_one(self, file_path: str) -> Optional[SkillDefinition]:
        """加载单个 YAML 技能文件"""
        try:
            import yaml
        except ImportError:
            logger.warning('pyyaml 未安装，跳过技能文件: %s', file_path)
            return None

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
        except Exception as e:
            logger.error('解析技能文件失败 %s: %s', file_path, e)
            return None

        if not data or not isinstance(data, dict):
            logger.warning('技能文件为空或格式错误: %s', file_path)
            return None

        name = data.get('name', '')
        if not name:
            logger.warning('技能文件缺少 name 字段: %s', file_path)
            return None

        handler = data.get('handler', {})
        return SkillDefinition(
            name=name,
            description=data.get('description', ''),
            min_role=data.get('min_role', 'guest'),
            priority=data.get('priority', 0),
            intent_patterns=data.get('intent_patterns', []),
            intent_keywords=data.get('intent_keywords', []),
            handler_type=handler.get('type', 'static'),
            handler_config=handler,
            file_path=os.path.abspath(file_path),
            category=data.get('category', ''),
        )

    # ═══════════════════════════════════════════
    # 匹配与执行
    # ═══════════════════════════════════════════

    def match(self, user_text: str, user: Optional[dict] = None) -> Optional[dict]:
        """匹配用户输入并执行技能处理器。

        返回: {'success': bool, 'reply': str} 或 None（未命中，走 LLM 流程）
        """
        self.check_and_reload()

        with self._lock:
            skills_snapshot = list(self._skills)

        for skill in skills_snapshot:
            if not self._check_role(skill, user):
                continue
            if self._match_intent(skill, user_text):
                logger.info('技能命中: %s (用户=%s, 角色=%s)',
                            skill.name,
                            user.get('username', 'anonymous') if user else 'anonymous',
                            user.get('role', 'guest') if user else 'guest')
                try:
                    return self._execute(skill, user, user_text)
                except Exception as e:
                    logger.error('技能 %s 执行失败: %s', skill.name, e)
                    return {'success': False, 'reply': f'技能执行出错: {e}'}

        return None

    # ── RBAC ──
    def _check_role(self, skill: SkillDefinition, user: Optional[dict]) -> bool:
        if user is None:
            return self._get_role_levels().get(skill.min_role, 0) <= 1
        user_level = user.get('role_level', 0)
        min_level = self._get_role_levels().get(skill.min_role, 0)
        return user_level >= min_level

    # ── 意图匹配 ──
    def _match_intent(self, skill: SkillDefinition, text: str) -> bool:
        text_lower = text.strip().lower()
        if any(k.lower() in text_lower for k in skill.intent_keywords):
            return True
        for pattern in skill.intent_patterns:
            try:
                if re.search(pattern, text_lower):
                    return True
            except re.error:
                logger.warning('技能 %s 的正则表达式无效: %s', skill.name, pattern)
        return False

    # ── 执行分发 ──
    def _execute(self, skill: SkillDefinition, user: Optional[dict],
                 user_text: str) -> dict:
        if skill.handler_type == 'sql':
            return self._execute_sql(skill, user)
        elif skill.handler_type == 'function':
            return self._execute_function(skill, user, user_text)
        else:
            return self._execute_static(skill, user)

    # ── SQL 处理器 ──
    def _execute_sql(self, skill: SkillDefinition, user: Optional[dict]) -> dict:
        sql = skill.handler_config.get('sql', '')
        template = skill.handler_config.get('result_template', '')
        result_mode = skill.handler_config.get('result_mode', 'single')
        row_template = skill.handler_config.get('row_template', '')

        sql = self._interpolate_user_vars(sql, user)

        conn = self._get_db_conn(skill.handler_config.get('db', 'users'))
        try:
            cursor = conn.execute(sql)
            if result_mode == 'list':
                rows = [dict(r) for r in cursor.fetchall()]
                if row_template:
                    rendered_rows = '\n'.join(
                        row_template.format_map(_SafeDict(row)) for row in rows
                    )
                else:
                    rendered_rows = '\n'.join(str(row) for row in rows)
                context = {'rows': rendered_rows, 'total_count': len(rows)}
                text = template.format_map(_SafeDict(context))
            else:
                row = cursor.fetchone()
                if row is None:
                    return {'success': True, 'reply': '未找到相关数据。'}
                context = dict(row)
                text = template.format_map(_SafeDict(context))
            return {'success': True, 'reply': text.strip()}
        except Exception as e:
            logger.error('技能 %s SQL 执行失败: %s', skill.name, e)
            return {'success': False, 'reply': f'查询数据时出错: {e}'}

    def _interpolate_user_vars(self, sql: str, user: Optional[dict]) -> str:
        """替换 SQL 中的 {fieldname} 占位符，自动转义。支持动态时间变量。"""
        import time as _time

        now = _time.time()

        def replacer(m):
            key = m.group(1)
            # 动态时间变量
            if key == 'unix_now':
                return str(int(now))
            if key == 'unix_7days':
                return str(int(now + 7 * 86400))
            if key == 'unix_30days':
                return str(int(now + 30 * 86400))

            if user is None:
                return 'NULL'
            val = user.get(key)
            if val is None:
                return 'NULL'
            if isinstance(val, (int, float)):
                return str(val)
            if isinstance(val, str):
                escaped = val.replace("'", "''")
                return f"'{escaped}'"
            return str(val)

        return re.sub(r'\{(\w+)\}', replacer, sql)

    # ── 静态文本处理器 ──
    def _execute_static(self, skill: SkillDefinition,
                        user: Optional[dict]) -> dict:
        template = skill.handler_config.get('static_text', '')
        text = template.format_map(_SafeDict(user or {}))
        return {'success': True, 'reply': text.strip()}

    # ── 函数处理器 ──
    def _execute_function(self, skill: SkillDefinition, user: Optional[dict],
                          user_text: str) -> dict:
        func_path = skill.handler_config.get('function', '')
        if not func_path:
            return {'success': False, 'reply': '技能未配置处理函数'}
        try:
            module_name, fn_name = func_path.rsplit('.', 1)
            import importlib
            mod = importlib.import_module(module_name)
            fn = getattr(mod, fn_name)
            return fn(user=user, user_text=user_text)
        except Exception as e:
            logger.error('技能 %s 函数调用失败 %s: %s', skill.name, func_path, e)
            return {'success': False, 'reply': f'技能处理出错: {e}'}

    # ── 数据库连接 ──
    def _get_db_conn(self, db_name: str):
        from tools import _get_users_db_conn, _get_db_conn
        if db_name == 'users':
            return _get_users_db_conn()
        else:
            return _get_db_conn()

    # ═══════════════════════════════════════════
    # 查询接口
    # ═══════════════════════════════════════════

    def list_all(self) -> list[dict]:
        """列出所有技能摘要"""
        return [
            {
                'name': s.name,
                'description': s.description,
                'min_role': s.min_role,
                'priority': s.priority,
                'handler_type': s.handler_type,
                'category': s.category,
            }
            for s in self._skills
        ]

    def __len__(self) -> int:
        return len(self._skills)
