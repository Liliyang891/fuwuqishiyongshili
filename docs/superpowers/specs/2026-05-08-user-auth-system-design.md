# 用户注册登录系统 — 设计规格文档

> 状态：设计完成 | 日期：2026-05-08

## 一、项目概述

为现有 AI Agent 服务器（web_server.py）新增用户注册登录系统和管理后台，支持 6 级角色权限控制。系统集成到现有项目，共用 SQLite 数据库和 Docker 容器。

## 二、角色等级与权限矩阵

### 角色层级总览

| 等级 | 角色 | 数据库名称 | AI 查询 | 文件操作 | 数据库操作 |
|------|------|-----------|---------|----------|------------|
| Lv.6 | 超级管理员 | super_admin | 全部功能 + 系统配置 | 全部：读写删 + 上传 | 全部：CRUD + 建表删表 |
| Lv.5 | 董事长 | chairman | 全部功能 + 查看审计 | 全部读写 / 不可删核心 | SELECT 全部 / INSERT UPDATE |
| Lv.4 | 总经理 | gm | 全部 AI 查询功能 | 读写 / 不可删 / 可上传 | SELECT + INSERT UPDATE / 不可 DDL |
| Lv.3 | 部门长 | dept_head | 本部门相关 AI 查询 | 本部门读写 / 其他只读 | 本部门 SELECT + INSERT UPDATE |
| Lv.2 | 部门职员 | staff | 受限 AI 查询 | 本部门只读 / 可上传 | 仅 SELECT 本部门数据 |
| Lv.1 | 游客 | guest | 仅基础问答 | 仅公开文件 | 无数据库权限 |

### 操作权限明细

| 操作 | super_admin | chairman | gm | dept_head | staff | guest |
|------|------------|----------|-----|-----------|-------|-------|
| AI 对话 | ✓ | ✓ | ✓ | ✓ | 受限 | 基础 |
| 文件读取 | 全部 | 全部 | 全部 | 部门+公开 | 本部门 | 仅公开 |
| 文件写入/修改 | ✓ | ✓ | ✓ | 本部门 | ✗ | ✗ |
| 文件删除 | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ |
| 文件上传 | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ |
| DB SELECT | 全部 | 全部 | 受限 | 本部门 | 本部门 | ✗ |
| DB INSERT/UPDATE | ✓ | ✓ | 受限 | 本部门 | ✗ | ✗ |
| DB DELETE / DDL | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ |
| 用户管理 | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ |

## 三、核心决策

| 决策项 | 结论 |
|--------|------|
| 集成方式 | 分离 auth.py 模块，共用进程和 SQLite |
| 注册方式 | 开放注册，默认游客角色 |
| 部门管理 | 管理员预设固定部门列表 |
| 登录凭证 | 用户名或邮箱 + 密码 |
| 游客策略 | 必须注册才能访问任何内容 |
| 会话管理 | 服务端 Session Token，httpOnly Cookie |
| Nginx Basic Auth | 移除，用本系统替代 |

## 四、数据库设计

在现有 `data/app.db` 中新增以下 3 张表：

### users 用户表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PRIMARY KEY AUTOINCREMENT | 自增 ID |
| username | TEXT UNIQUE NOT NULL | 用户名（3-20 字符，字母数字下划线） |
| email | TEXT UNIQUE | 邮箱（可选） |
| password_hash | TEXT NOT NULL | bcrypt 哈希，cost=12 |
| role | TEXT NOT NULL DEFAULT 'guest' | 角色：super_admin/chairman/gm/dept_head/staff/guest |
| department_id | INTEGER | 所属部门 ID，游客为 NULL |
| is_active | INTEGER DEFAULT 1 | 账号启用状态 |
| created_at | REAL | 注册时间戳 |

### departments 部门表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PRIMARY KEY AUTOINCREMENT | 自增 ID |
| name | TEXT UNIQUE NOT NULL | 部门名称 |
| created_at | REAL | 创建时间戳 |

### user_sessions 登录会话表

| 字段 | 类型 | 说明 |
|------|------|------|
| token | TEXT PRIMARY KEY | UUID 令牌 |
| user_id | INTEGER NOT NULL | 关联 users.id |
| created_at | REAL | 登录时间戳 |
| expires_at | REAL | 过期时间戳 |

注意：
- user_sessions 与现有 sessions 表（AI 对话历史）是独立的
- 默认过期 24 小时，"记住我" 勾选后 7 天
- 新增 audit_log 表记录危险操作

## 五、API 路由设计

### 公开接口（无需登录）

| 方法 | 路由 | 说明 | 入参 |
|------|------|------|------|
| POST | /api/register | 注册新用户 | username, password, email(可选) |
| POST | /api/login | 登录获取 session | login, password, remember_me(可选) |
| POST | /api/logout | 登出清除 session | (cookie 中 token) |

### 需登录接口

| 方法 | 路由 | 说明 | 最低角色 |
|------|------|------|----------|
| GET | /api/me | 当前用户信息 | guest |
| GET | /api/models | 模型列表（已有） | guest |
| POST | /api/command | AI 对话（已有，加权限） | guest |
| POST | /api/upload | 文件上传（已有，加权限） | staff |
| POST | /api/clear | 清空对话（已有） | guest |
| GET | /api/status | 服务状态（已有） | guest |

### 管理后台接口（超管专属）

| 方法 | 路由 | 说明 |
|------|------|------|
| GET/POST | /api/admin/* | 用户管理、部门管理、权限配置等 |

### 页面路由

| 路由 | 说明 | 需要登录 |
|------|------|----------|
| / 或 /login | 登录页面（未登录时） | 否 |
| /register | 注册页面 | 否 |
| /chat/ | AI 助手主页 | 是 |
| /admin/ | 管理后台 | 是（超管） |

未登录用户访问受保护页面 → 302 重定向到 /login

## 六、auth.py 模块设计

### 函数清单

```python
# 用户认证
register_user(username, password, email=None) -> user
login_user(login, password, remember_me=False) -> token
logout_session(token) -> None
get_user_by_token(token) -> user | None

# 权限检查
get_role_level(role_name) -> int  # guest=1 ~ super_admin=6
get_allowed_tools(user) -> list[dict]  # 返回过滤后的工具定义
get_file_scope(user) -> str  # "all" | "department" | "public"
get_db_scope(user) -> str

# 用户管理（管理后台用）
list_users() -> list
update_user_role(user_id, new_role, dept_id) -> None
toggle_user_active(user_id) -> None

# 部门管理
list_departments() -> list
create_department(name) -> dept
update_department(id, name) -> None
delete_department(id) -> None
```

### 权限检查流程（双层验证）

1. **LLM 层面**：根据用户角色调用 `get_allowed_tools()` 过滤工具定义，只发送允许的工具给大模型
2. **执行层面**：在 `execute_tool()` 中二次检查权限，拒绝执行时记录 audit_log

### 文件路径限定

- 部门用户（dept_head/staff）：文件操作限定在 `data/files/{部门名}/` 子目录
- 游客：只能读取 `data/files/public/` 目录
- 管理员（super_admin/chairman/gm）：可操作全部 `data/files/`

### 危险操作审计

以下操作写入 audit_log 表：文件删除、DB DELETE、DB DROP、DB DDL、用户权限变更

## 七、前端页面设计

### login.html（登录页面）

- 用户名或邮箱输入框
- 密码输入框
- "记住我" 复选框（延长 session 到 7 天）
- 登录按钮
- 跳转注册链接
- 登录失败提示（红色内联消息）

### register.html（注册页面）

- 用户名输入框（3-20 字符，字母数字下划线）
- 邮箱输入框（选填）
- 密码输入框（最小 6 位）
- 确认密码输入框
- 注册按钮
- 跳转登录链接
- 注册成功 → 自动登录跳转 /chat/
- 注册失败提示

### 样式规范

- 与现有 static/index.html 风格一致
- 深色主题，居中卡片布局
- 不依赖外部 CSS 框架，纯手写

## 八、部署变更

1. Nginx 配置：移除 `/chat/` 的 HTTP Basic Auth
2. Nginx 配置：`/login`、`/register` 代理到 web_server
3. Docker 容器无需变更（web_server.py 新增路由即可）
4. 首次启动时自动创建新表（通过 tools.py 的 _init_db 扩展）

## 九、管理后台设计

仅超级管理员可访问 `/admin/`。

### 功能模块

**用户管理：**
- 查看所有用户列表（支持按角色/部门/状态筛选）
- 编辑用户：修改角色和部门归属
- 启用/禁用账号
- 手动重置密码
- 删除用户

**部门管理：**
- 查看部门列表及人数统计
- 新增部门
- 修改部门名称
- 删除部门（需无用户归属）

**审计日志：**
- 查看所有危险操作记录
- 支持按操作类型/用户/时间筛选
- 记录：文件删除、DB DELETE、DROP TABLE、用户权限变更等
- 日志不可删除

### 管理后台 API（全部仅 super_admin）

| 方法 | 路由 | 说明 |
|------|------|------|
| GET | /api/admin/users | 用户列表（?role=&department_id=&active=） |
| PUT | /api/admin/users/{id} | 修改用户角色/部门/状态 |
| POST | /api/admin/users/{id}/reset-password | 重置密码 |
| DELETE | /api/admin/users/{id} | 删除用户 |
| GET | /api/admin/departments | 部门列表（含人数统计） |
| POST | /api/admin/departments | 新增部门 |
| PUT | /api/admin/departments/{id} | 修改部门名称 |
| DELETE | /api/admin/departments/{id} | 删除部门 |
| GET | /api/admin/audit-logs | 审计日志（?user_id=&action=&from=&to=） |

### 页面设计

- admin.html：左侧菜单 + 右侧内容区布局
- 风格与现有 chat 页面一致的深色主题
- 三个标签页切换（用户管理 / 部门管理 / 审计日志）
- 编辑用户弹出模态框，选择角色和部门

### 审计日志表 audit_log

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK AUTOINCREMENT | 自增 ID |
| user_id | INTEGER | 操作者 |
| action | TEXT | 操作类型：file_delete/db_delete/db_drop/role_change/user_delete/password_reset |
| detail | TEXT | 操作详情 JSON |
| created_at | REAL | 操作时间戳 |
