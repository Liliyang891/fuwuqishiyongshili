#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件/目录/数据库 工具模块
提供 28 个工具函数，供 AI Agent 通过 Function Calling 调用
"""

import os
import json
import sqlite3
import shutil
import hashlib
import time
import zipfile
import tarfile
import fnmatch
import re
import mimetypes
from datetime import datetime

# ---- 路径配置 ----
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
FILES_DIR = os.path.join(DATA_DIR, 'files')
DB_PATH = os.path.join(DATA_DIR, 'app.db')

# 允许操作的文件根目录（安全限制）
ALLOWED_ROOTS = [os.path.realpath(FILES_DIR)]


def _ensure_dirs():
    """确保目录结构存在"""
    os.makedirs(FILES_DIR, exist_ok=True)
    _init_db()


def _resolve_path(path):
    """
    解析并安全检查文件路径
    返回: (full_path, error_msg)
    """
    if not path:
        return None, "路径不能为空"
    # 相对路径相对于 FILES_DIR
    if os.path.isabs(path):
        full = os.path.realpath(path)
    else:
        full = os.path.realpath(os.path.join(FILES_DIR, path))
    # 安全检查：路径必须在允许范围内
    for root in ALLOWED_ROOTS:
        if full.startswith(root + os.sep) or full == root:
            return full, None
    return None, f"安全限制：禁止访问路径 '{path}'（不在允许的目录内）"


def _format_file_info(path):
    """获取文件/目录信息，返回字典"""
    try:
        stat = os.stat(path)
        is_dir = os.path.isdir(path)
        return {
            "name": os.path.basename(path),
            "path": os.path.relpath(path, FILES_DIR) if path.startswith(FILES_DIR) else path,
            "type": "directory" if is_dir else "file",
            "size": stat.st_size if not is_dir else None,
            "size_human": _human_size(stat.st_size) if not is_dir else None,
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "permissions": oct(stat.st_mode)[-3:],
        }
    except Exception as e:
        return {"name": os.path.basename(path), "error": str(e)}


def _human_size(size):
    """字节数转为可读格式"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


# ========== 数据库初始化 ==========

def _init_db():
    """初始化 SQLite 数据库（元数据表 + 会话表）"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS _meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    cursor.execute(
        "INSERT OR REPLACE INTO _meta (key, value) VALUES (?, ?)",
        ('created_at', datetime.now().isoformat())
    )
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            messages TEXT NOT NULL DEFAULT '[]',
            last_active REAL NOT NULL,
            created_at REAL NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS departments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            created_at REAL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'guest',
            department_id INTEGER,
            is_active INTEGER DEFAULT 1,
            created_at REAL,
            FOREIGN KEY (department_id) REFERENCES departments(id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at REAL,
            expires_at REAL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT NOT NULL,
            detail TEXT,
            created_at REAL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
        )
    ''')
    conn.commit()
    conn.close()


def _get_db_conn():
    """获取数据库连接"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ========== 会话持久化 ==========

def save_session(session_id, messages, session_ttl=3600):
    """保存或更新会话到数据库"""
    conn = _get_db_conn()
    cursor = conn.cursor()
    now = time.time()
    cursor.execute('''
        INSERT OR REPLACE INTO sessions (session_id, messages, last_active, created_at)
        VALUES (?, ?, ?, COALESCE((SELECT created_at FROM sessions WHERE session_id=?), ?))
    ''', (session_id, json.dumps(messages, ensure_ascii=False), now, session_id, now))
    conn.commit()
    conn.close()


def load_session(session_id, session_ttl=3600):
    """从数据库加载会话，过期返回空列表"""
    conn = _get_db_conn()
    cursor = conn.cursor()
    now = time.time()
    cursor.execute(
        'SELECT messages, last_active FROM sessions WHERE session_id=?',
        (session_id,)
    )
    row = cursor.fetchone()
    conn.close()
    if row and now - row['last_active'] <= session_ttl:
        # 更新最后活跃时间
        save_session(session_id, json.loads(row['messages']), session_ttl)
        return json.loads(row['messages'])
    return []


def delete_session(session_id):
    """删除会话"""
    conn = _get_db_conn()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM sessions WHERE session_id=?', (session_id,))
    conn.commit()
    conn.close()


def cleanup_sessions_db(session_ttl=3600):
    """清理过期会话"""
    conn = _get_db_conn()
    cursor = conn.cursor()
    now = time.time()
    cursor.execute(
        'DELETE FROM sessions WHERE ? - last_active > ?',
        (now, session_ttl)
    )
    conn.commit()
    conn.close()


# ========== 📁 目录操作（5个） ==========

def list_folder(path="", recursive=False, pattern="*", offset=0, limit=100):
    """
    列出目录内容
    参数:
        path: 目录路径，空字符串表示根目录
        recursive: 是否递归列出
        pattern: 通配符过滤，如 *.txt
        offset: 分页起始位置
        limit: 最大返回数量
    """
    full, err = _resolve_path(path or ".")
    if err:
        return {"success": False, "error": err}
    if not os.path.exists(full):
        return {"success": False, "error": f"路径不存在: {path}"}
    if not os.path.isdir(full):
        return {"success": False, "error": f"不是目录: {path}"}

    items = []
    try:
        if recursive:
            for root, dirs, files in os.walk(full):
                for name in dirs + files:
                    if fnmatch.fnmatch(name, pattern):
                        items.append(os.path.join(root, name))
        else:
            for name in os.listdir(full):
                if fnmatch.fnmatch(name, pattern):
                    items.append(os.path.join(full, name))

        items.sort(key=lambda x: (not os.path.isdir(x), os.path.basename(x).lower()))
        total = len(items)
        items = items[offset:offset + limit]

        result_items = [_format_file_info(item) for item in items]
        return {
            "success": True,
            "path": os.path.relpath(full, FILES_DIR) if full.startswith(FILES_DIR) else path,
            "total": total,
            "items": result_items,
        }
    except Exception as e:
        return {"success": False, "error": f"列出目录失败: {e}"}


def create_folder(path, exist_ok=True):
    """
    创建目录（含多级父目录）
    参数:
        path: 要创建的目录路径
        exist_ok: 如果目录已存在是否报错
    """
    full, err = _resolve_path(path)
    if err:
        return {"success": False, "error": err}
    try:
        os.makedirs(full, exist_ok=exist_ok)
        return {"success": True, "path": path, "message": f"目录已创建: {path}"}
    except FileExistsError:
        return {"success": False, "error": f"目录已存在: {path}"}
    except Exception as e:
        return {"success": False, "error": f"创建目录失败: {e}"}


def delete_folder(path, recursive=True):
    """
    删除目录
    参数:
        path: 要删除的目录路径
        recursive: 是否删除目录内所有内容
    """
    full, err = _resolve_path(path)
    if err:
        return {"success": False, "error": err}
    if not os.path.exists(full):
        return {"success": False, "error": f"目录不存在: {path}"}
    if not os.path.isdir(full):
        return {"success": False, "error": f"不是目录: {path}"}
    try:
        if recursive:
            shutil.rmtree(full)
        else:
            os.rmdir(full)  # 只删除空目录
        return {"success": True, "message": f"目录已删除: {path}"}
    except OSError as e:
        if not recursive:
            return {"success": False, "error": f"目录不为空，无法删除: {path}"}
        return {"success": False, "error": f"删除目录失败: {e}"}


def move_folder(source_path, dest_path):
    """
    移动/重命名目录
    参数:
        source_path: 源目录路径
        dest_path: 目标目录路径
    """
    src, err = _resolve_path(source_path)
    if err:
        return {"success": False, "error": err}
    dst, err = _resolve_path(dest_path)
    if err:
        return {"success": False, "error": err}
    if not os.path.exists(src):
        return {"success": False, "error": f"源目录不存在: {source_path}"}
    try:
        shutil.move(src, dst)
        return {"success": True, "message": f"目录已移动: {source_path} → {dest_path}"}
    except Exception as e:
        return {"success": False, "error": f"移动目录失败: {e}"}


def copy_folder(source_path, dest_path):
    """
    复制整个目录
    参数:
        source_path: 源目录路径
        dest_path: 目标目录路径
    """
    src, err = _resolve_path(source_path)
    if err:
        return {"success": False, "error": err}
    dst, err = _resolve_path(dest_path)
    if err:
        return {"success": False, "error": err}
    if not os.path.exists(src):
        return {"success": False, "error": f"源目录不存在: {source_path}"}
    try:
        shutil.copytree(src, dst)
        return {"success": True, "message": f"目录已复制: {source_path} → {dest_path}"}
    except Exception as e:
        return {"success": False, "error": f"复制目录失败: {e}"}


# ========== 📄 文件读写操作（6个） ==========

def read_file(path, encoding="utf-8", start_line=None, end_line=None, max_lines=500):
    """
    读取文件内容
    参数:
        path: 文件路径
        encoding: 文件编码，默认 utf-8
        start_line: 起始行号（1-based，含）
        end_line: 结束行号（1-based，含）
        max_lines: 最大读取行数（文件太大时自动截断）
    """
    full, err = _resolve_path(path)
    if err:
        return {"success": False, "error": err}
    if not os.path.exists(full):
        return {"success": False, "error": f"文件不存在: {path}"}
    if os.path.isdir(full):
        return {"success": False, "error": f"路径是目录而非文件: {path}"}

    try:
        with open(full, 'r', encoding=encoding, errors='replace') as f:
            lines = f.readlines()
    except UnicodeDecodeError:
        # 尝试以二进制方式读取
        try:
            with open(full, 'rb') as f:
                content = f.read()
            info = _format_file_info(full)
            return {
                "success": True,
                "path": path,
                "file_info": info,
                "content": f"[二进制文件，大小: {_human_size(len(content))}]",
                "line_count": None,
                "is_binary": True,
            }
        except Exception as e:
            return {"success": False, "error": f"读取文件失败: {e}"}
    except Exception as e:
        return {"success": False, "error": f"读取文件失败: {e}"}

    total_lines = len(lines)
    if start_line or end_line:
        start = max(1, (start_line or 1)) - 1
        end = min(total_lines, end_line or total_lines)
        selected = lines[start:end]
    else:
        selected = lines[:max_lines] if total_lines > max_lines else lines

    content = ''.join(selected)
    info = _format_file_info(full)

    return {
        "success": True,
        "path": path,
        "file_info": info,
        "content": content,
        "line_count": total_lines,
        "displayed_lines": len(selected),
        "truncated": total_lines > len(selected),
        "is_binary": False,
    }


def write_file(path, content, encoding="utf-8", create_dirs=True):
    """
    创建/覆盖写入文件
    参数:
        path: 文件路径
        content: 要写入的内容
        encoding: 文件编码
        create_dirs: 是否自动创建父目录
    """
    full, err = _resolve_path(path)
    if err:
        return {"success": False, "error": err}
    try:
        if create_dirs:
            os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w', encoding=encoding) as f:
            f.write(content)
        info = _format_file_info(full)
        return {"success": True, "path": path, "file_info": info, "message": f"文件已写入: {path}"}
    except Exception as e:
        return {"success": False, "error": f"写入文件失败: {e}"}


def append_file(path, content, encoding="utf-8", add_newline=True):
    """
    追加内容到文件尾部
    参数:
        path: 文件路径
        content: 要追加的内容
        encoding: 文件编码
        add_newline: 是否在追加内容前添加换行
    """
    full, err = _resolve_path(path)
    if err:
        return {"success": False, "error": err}
    try:
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'a', encoding=encoding) as f:
            if add_newline and os.path.exists(full) and os.path.getsize(full) > 0:
                f.write('\n')
            f.write(content)
        info = _format_file_info(full)
        return {"success": True, "path": path, "file_info": info, "message": f"内容已追加到文件: {path}"}
    except Exception as e:
        return {"success": False, "error": f"追加文件失败: {e}"}


def insert_text(path, content, position="end", encoding="utf-8"):
    """
    在指定位置插入内容
    参数:
        path: 文件路径
        content: 要插入的内容
        position: 插入位置，"start"(开头), "end"(末尾), 或者数字(行号,1-based)
        encoding: 文件编码
    """
    full, err = _resolve_path(path)
    if err:
        return {"success": False, "error": err}
    if not os.path.exists(full):
        return {"success": False, "error": f"文件不存在: {path}"}

    try:
        with open(full, 'r', encoding=encoding, errors='replace') as f:
            lines = f.readlines()
    except Exception as e:
        return {"success": False, "error": f"读取文件失败: {e}"}

    if position == "start":
        lines.insert(0, content + '\n')
    elif position == "end":
        if lines and not lines[-1].endswith('\n'):
            lines[-1] += '\n'
        lines.append(content + '\n')
    else:
        try:
            line_num = int(position)
            if line_num < 1:
                line_num = 1
            if line_num > len(lines):
                line_num = len(lines) + 1
            lines.insert(line_num - 1, content + '\n')
        except ValueError:
            return {"success": False, "error": f"无效的位置参数: {position}，请使用 start/end/或数字行号"}

    try:
        with open(full, 'w', encoding=encoding) as f:
            f.writelines(lines)
        return {"success": True, "path": path, "message": f"内容已插入到文件 {path} 的 {position} 位置"}
    except Exception as e:
        return {"success": False, "error": f"写入文件失败: {e}"}


def replace_text(path, search_text, replace_text, count=0, regex=False):
    """
    查找并替换文件中的文本
    参数:
        path: 文件路径
        search_text: 要查找的文本或正则表达式
        replace_text: 替换为的文本
        count: 替换次数，0=全部替换
        regex: search_text 是否为正则表达式
    """
    full, err = _resolve_path(path)
    if err:
        return {"success": False, "error": err}
    if not os.path.exists(full):
        return {"success": False, "error": f"文件不存在: {path}"}

    try:
        with open(full, 'r', encoding='utf-8', errors='replace') as f:
            original = f.read()
    except Exception as e:
        return {"success": False, "error": f"读取文件失败: {e}"}

    if regex:
        new_content, n = re.subn(search_text, replace_text, original, count=count if count > 0 else 0)
    else:
        if count > 0:
            new_content = original.replace(search_text, replace_text, count)
        else:
            new_content = original.replace(search_text, replace_text)
        n = 0 if new_content == original else None  # approximate

    if new_content == original:
        return {"success": True, "path": path, "replacements": 0, "message": "未找到匹配内容"}

    try:
        with open(full, 'w', encoding='utf-8') as f:
            f.write(new_content)
        return {"success": True, "path": path, "replacements": n if n else "全部", "message": f"文本替换完成: {path}"}
    except Exception as e:
        return {"success": False, "error": f"写入文件失败: {e}"}


def delete_lines(path, start_line, end_line):
    """
    删除指定行范围
    参数:
        path: 文件路径
        start_line: 起始行号（1-based，含）
        end_line: 结束行号（1-based，含）
    """
    full, err = _resolve_path(path)
    if err:
        return {"success": False, "error": err}
    if not os.path.exists(full):
        return {"success": False, "error": f"文件不存在: {path}"}

    try:
        with open(full, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
    except Exception as e:
        return {"success": False, "error": f"读取文件失败: {e}"}

    total = len(lines)
    start = max(1, start_line) - 1
    end = min(total, end_line)
    if start >= total or start >= end:
        return {"success": False, "error": f"无效的行范围: {start_line}-{end_line}，文件共 {total} 行"}

    del lines[start:end]
    try:
        with open(full, 'w', encoding='utf-8') as f:
            f.writelines(lines)
        return {"success": True, "path": path, "deleted_lines": end - start, "message": f"已删除第 {start_line}-{end_line} 行（共 {end - start} 行）"}
    except Exception as e:
        return {"success": False, "error": f"写入文件失败: {e}"}


# ========== 📄 文件管理操作（7个） ==========

def delete_file(path):
    """删除文件"""
    full, err = _resolve_path(path)
    if err:
        return {"success": False, "error": err}
    if not os.path.exists(full):
        return {"success": False, "error": f"文件不存在: {path}"}
    if os.path.isdir(full):
        return {"success": False, "error": f"路径是目录而非文件，请使用 delete_folder: {path}"}
    try:
        os.remove(full)
        return {"success": True, "message": f"文件已删除: {path}"}
    except Exception as e:
        return {"success": False, "error": f"删除文件失败: {e}"}


def move_file(source_path, dest_path):
    """移动/重命名文件"""
    src, err = _resolve_path(source_path)
    if err:
        return {"success": False, "error": err}
    dst, err = _resolve_path(dest_path)
    if err:
        return {"success": False, "error": err}
    if not os.path.exists(src):
        return {"success": False, "error": f"源文件不存在: {source_path}"}
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        return {"success": True, "message": f"文件已移动: {source_path} → {dest_path}"}
    except Exception as e:
        return {"success": False, "error": f"移动文件失败: {e}"}


def copy_file(source_path, dest_path):
    """复制文件"""
    src, err = _resolve_path(source_path)
    if err:
        return {"success": False, "error": err}
    dst, err = _resolve_path(dest_path)
    if err:
        return {"success": False, "error": err}
    if not os.path.exists(src):
        return {"success": False, "error": f"源文件不存在: {source_path}"}
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        return {"success": True, "message": f"文件已复制: {source_path} → {dest_path}"}
    except Exception as e:
        return {"success": False, "error": f"复制文件失败: {e}"}


def get_file_info(path):
    """获取文件/目录元信息"""
    full, err = _resolve_path(path)
    if err:
        return {"success": False, "error": err}
    if not os.path.exists(full):
        return {"success": False, "error": f"路径不存在: {path}"}
    info = _format_file_info(full)
    # 如果是文件，追加更多信息
    if os.path.isfile(full):
        try:
            with open(full, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.readlines()
            info["line_count"] = len(lines)
        except:
            info["line_count"] = None
        info["extension"] = os.path.splitext(full)[1]
    return {"success": True, "path": path, "file_info": info}


def search_files(folder_path="", pattern="*", recursive=False):
    """
    按名称模式搜索文件
    参数:
        folder_path: 搜索的目录路径
        pattern: 文件名通配符，如 *.txt
        recursive: 是否递归搜索子目录
    """
    full, err = _resolve_path(folder_path or ".")
    if err:
        return {"success": False, "error": err}
    if not os.path.isdir(full):
        return {"success": False, "error": f"目录不存在: {folder_path}"}

    matches = []
    try:
        if recursive:
            for root, dirs, files in os.walk(full):
                for name in files:
                    if fnmatch.fnmatch(name, pattern):
                        matches.append(os.path.join(root, name))
        else:
            for name in os.listdir(full):
                fpath = os.path.join(full, name)
                if os.path.isfile(fpath) and fnmatch.fnmatch(name, pattern):
                    matches.append(fpath)

        return {
            "success": True,
            "pattern": pattern,
            "count": len(matches),
            "items": [_format_file_info(m) for m in matches],
        }
    except Exception as e:
        return {"success": False, "error": f"搜索文件失败: {e}"}


def search_content(folder_path="", text="", file_pattern="*", recursive=False, case_sensitive=False):
    """
    在文件中搜索内容（类似 grep）
    参数:
        folder_path: 搜索的目录路径
        text: 要搜索的文本（支持正则表达式）
        file_pattern: 搜索哪些文件，如 *.py
        recursive: 是否递归搜索子目录
        case_sensitive: 是否区分大小写
    """
    full, err = _resolve_path(folder_path or ".")
    if err:
        return {"success": False, "error": err}
    if not os.path.isdir(full):
        return {"success": False, "error": f"目录不存在: {folder_path}"}
    if not text:
        return {"success": False, "error": "搜索内容不能为空"}

    results = []
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        pattern = re.compile(text, flags)
    except re.error as e:
        return {"success": False, "error": f"正则表达式无效: {e}"}

    def search_in_file(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.readlines()
            for i, line in enumerate(lines, 1):
                if pattern.search(line):
                    results.append({
                        "file": os.path.relpath(file_path, FILES_DIR) if file_path.startswith(FILES_DIR) else file_path,
                        "line": i,
                        "content": line.rstrip('\n')[:200],
                    })
        except:
            pass  # 跳过无法读取的文件

    try:
        if recursive:
            for root, dirs, files in os.walk(full):
                for name in files:
                    if fnmatch.fnmatch(name, file_pattern):
                        search_in_file(os.path.join(root, name))
        else:
            for name in os.listdir(full):
                fpath = os.path.join(full, name)
                if os.path.isfile(fpath) and fnmatch.fnmatch(name, file_pattern):
                    search_in_file(fpath)

        return {
            "success": True,
            "search_text": text,
            "match_count": len(results),
            "matches": results[:200],  # 最多 200 条
            "truncated": len(results) > 200,
        }
    except Exception as e:
        return {"success": False, "error": f"搜索内容失败: {e}"}


def get_file_hash(path, algorithm="md5"):
    """
    获取文件哈希值
    参数:
        path: 文件路径
        algorithm: 哈希算法，md5/sha1/sha256
    """
    full, err = _resolve_path(path)
    if err:
        return {"success": False, "error": err}
    if not os.path.isfile(full):
        return {"success": False, "error": f"文件不存在: {path}"}

    algo_map = {"md5": hashlib.md5, "sha1": hashlib.sha1, "sha256": hashlib.sha256}
    if algorithm not in algo_map:
        return {"success": False, "error": f"不支持的算法: {algorithm}，请使用 md5/sha1/sha256"}

    try:
        hasher = algo_map[algorithm]()
        with open(full, 'rb') as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                hasher.update(chunk)
        return {"success": True, "path": path, "algorithm": algorithm, "hash": hasher.hexdigest()}
    except Exception as e:
        return {"success": False, "error": f"计算哈希失败: {e}"}


# ========== 📄 批量与实用操作（4个） ==========

def batch_read(paths):
    """
    批量读取多个文件
    参数:
        paths: 文件路径列表
    """
    if not isinstance(paths, list):
        return {"success": False, "error": "paths 必须是列表"}
    if len(paths) > 20:
        return {"success": False, "error": "一次最多批量读取 20 个文件"}

    results = []
    for p in paths:
        r = read_file(p, max_lines=100)
        results.append({"path": p, "result": r})
    return {"success": True, "files": results}


def zip_files(paths, output_path, format="zip"):
    """
    打包压缩文件/目录
    参数:
        paths: 要压缩的路径列表
        output_path: 压缩包输出路径
        format: 压缩格式，zip 或 tar.gz
    """
    if not isinstance(paths, list):
        return {"success": False, "error": "paths 必须是列表"}

    resolved_paths = []
    for p in paths:
        full, err = _resolve_path(p)
        if err:
            return {"success": False, "error": err}
        if not os.path.exists(full):
            return {"success": False, "error": f"路径不存在: {p}"}
        resolved_paths.append(full)

    output_full, err = _resolve_path(output_path)
    if err:
        return {"success": False, "error": err}
    os.makedirs(os.path.dirname(output_full), exist_ok=True)

    try:
        if format == "tar.gz":
            with tarfile.open(output_full, "w:gz") as tar:
                for p in resolved_paths:
                    arcname = os.path.basename(p)
                    tar.add(p, arcname=arcname)
        else:  # zip
            with zipfile.ZipFile(output_full, 'w', zipfile.ZIP_DEFLATED) as zf:
                for p in resolved_paths:
                    if os.path.isdir(p):
                        for root, dirs, files in os.walk(p):
                            for f in files:
                                fpath = os.path.join(root, f)
                                arcname = os.path.relpath(fpath, os.path.dirname(p))
                                zf.write(fpath, arcname)
                    else:
                        zf.write(p, os.path.basename(p))
        return {"success": True, "output": output_path, "message": f"压缩完成: {output_path}"}
    except Exception as e:
        return {"success": False, "error": f"压缩失败: {e}"}


def unzip_file(path, dest_folder=""):
    """
    解压文件
    参数:
        path: 压缩包路径
        dest_folder: 解压目标目录，默认为压缩包同目录
    """
    full, err = _resolve_path(path)
    if err:
        return {"success": False, "error": err}
    if not os.path.isfile(full):
        return {"success": False, "error": f"文件不存在: {path}"}

    if not dest_folder:
        dest_folder = os.path.splitext(path)[0]
    dest_full, err = _resolve_path(dest_folder)
    if err:
        return {"success": False, "error": err}
    os.makedirs(dest_full, exist_ok=True)

    try:
        name_lower = path.lower()
        if name_lower.endswith('.zip'):
            with zipfile.ZipFile(full, 'r') as zf:
                zf.extractall(dest_full)
        elif name_lower.endswith('.tar.gz') or name_lower.endswith('.tgz'):
            with tarfile.open(full, 'r:gz') as tar:
                tar.extractall(dest_full)
        elif name_lower.endswith('.tar'):
            with tarfile.open(full, 'r') as tar:
                tar.extractall(dest_full)
        else:
            return {"success": False, "error": f"不支持的压缩格式: {path}，支持 .zip / .tar.gz / .tar"}
        return {"success": True, "dest_folder": dest_folder, "message": f"解压完成: {dest_folder}"}
    except Exception as e:
        return {"success": False, "error": f"解压失败: {e}"}


def count_items(path="", recursive=False):
    """
    统计目录下文件数量和大小
    参数:
        path: 目录路径
        recursive: 是否递归统计子目录
    """
    full, err = _resolve_path(path or ".")
    if err:
        return {"success": False, "error": err}
    if not os.path.isdir(full):
        return {"success": False, "error": f"目录不存在: {path}"}

    file_count = 0
    dir_count = 0
    total_size = 0
    try:
        if recursive:
            for root, dirs, files in os.walk(full):
                dir_count += len(dirs)
                file_count += len(files)
                for f in files:
                    try:
                        total_size += os.path.getsize(os.path.join(root, f))
                    except:
                        pass
        else:
            for name in os.listdir(full):
                fpath = os.path.join(full, name)
                if os.path.isdir(fpath):
                    dir_count += 1
                else:
                    file_count += 1
                    try:
                        total_size += os.path.getsize(fpath)
                    except:
                        pass
        return {
            "success": True,
            "path": path or "根目录",
            "file_count": file_count,
            "dir_count": dir_count,
            "total_size": total_size,
            "total_size_human": _human_size(total_size),
        }
    except Exception as e:
        return {"success": False, "error": f"统计失败: {e}"}


# ========== 🗄️ 数据库操作（6个） ==========

def db_list_tables():
    """列出数据库中所有表"""
    try:
        conn = _get_db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in cursor.fetchall()]
        conn.close()
        return {"success": True, "tables": tables, "count": len(tables)}
    except Exception as e:
        return {"success": False, "error": f"列出表失败: {e}"}


def db_describe_table(table_name):
    """
    查看表结构
    参数:
        table_name: 表名
    """
    try:
        conn = _get_db_conn()
        cursor = conn.cursor()
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = []
        for row in cursor.fetchall():
            columns.append({
                "cid": row[0],
                "name": row[1],
                "type": row[2],
                "not_null": bool(row[3]),
                "default": row[4],
                "primary_key": bool(row[5]),
            })
        conn.close()
        return {"success": True, "table": table_name, "columns": columns}
    except Exception as e:
        return {"success": False, "error": f"查看表结构失败: {e}"}


def db_query(sql, params=None, limit=100):
    """
    执行 SELECT 查询
    参数:
        sql: SQL 查询语句（仅限 SELECT）
        params: 参数化查询参数列表
        limit: 最大返回行数
    """
    sql_stripped = sql.strip().upper()
    if not sql_stripped.startswith('SELECT') and not sql_stripped.startswith('PRAGMA'):
        return {"success": False, "error": "仅允许 SELECT 查询，修改操作请使用 db_execute"}

    try:
        conn = _get_db_conn()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        if params:
            cursor.execute(sql, params)
        else:
            cursor.execute(sql)

        rows = cursor.fetchall()
        total = len(rows)
        truncated = False
        if total > limit:
            rows = rows[:limit]
            truncated = True

        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        result_rows = [dict(row) for row in rows]
        conn.close()

        return {
            "success": True,
            "columns": columns,
            "rows": result_rows,
            "total": total,
            "displayed": len(result_rows),
            "truncated": truncated,
        }
    except Exception as e:
        return {"success": False, "error": f"查询失败: {e}"}


def db_execute(sql, params=None):
    """
    执行非查询 SQL（INSERT/UPDATE/DELETE/CREATE/DROP/ALTER）
    参数:
        sql: SQL 语句
        params: 参数化查询参数列表
    """
    sql_stripped = sql.strip().upper()
    forbidden = ['SELECT', 'PRAGMA']
    if any(sql_stripped.startswith(w) for w in forbidden):
        return {"success": False, "error": "请使用 db_query 执行 SELECT 查询"}

    try:
        conn = _get_db_conn()
        cursor = conn.cursor()
        if params:
            cursor.execute(sql, params)
        else:
            cursor.execute(sql)
        conn.commit()
        affected = cursor.rowcount
        conn.close()
        return {"success": True, "affected_rows": affected, "message": f"执行成功，影响 {affected} 行"}
    except Exception as e:
        return {"success": False, "error": f"执行失败: {e}"}


def db_create_table(table_name, columns):
    """
    创建新表
    参数:
        table_name: 表名
        columns: 列定义列表，每项为 {"name": "列名", "type": "类型", "constraints": "约束"}
                 例: [{"name": "id", "type": "INTEGER", "constraints": "PRIMARY KEY AUTOINCREMENT"},
                      {"name": "name", "type": "TEXT", "constraints": "NOT NULL"}]
    """
    if not table_name or not columns:
        return {"success": False, "error": "表名和列定义不能为空"}
    if not isinstance(columns, list):
        return {"success": False, "error": "columns 必须是列表"}

    col_defs = []
    for col in columns:
        if not isinstance(col, dict) or 'name' not in col or 'type' not in col:
            return {"success": False, "error": f"列定义格式错误: {col}，需要 name 和 type"}
        name = col['name']
        col_type = col['type']
        constraints = col.get('constraints', '')
        # 安全检查：禁止敏感关键字
        if not name.isidentifier():
            return {"success": False, "error": f"无效的列名: {name}"}
        col_defs.append(f"{name} {col_type} {constraints}".strip())

    sql = f"CREATE TABLE IF NOT EXISTS {table_name} ({', '.join(col_defs)})"
    return db_execute(sql)


def db_drop_table(table_name):
    """
    删除表
    参数:
        table_name: 表名
    """
    if not table_name:
        return {"success": False, "error": "表名不能为空"}
    # 防止误删系统表
    if table_name == '_meta':
        return {"success": False, "error": "不能删除系统表 _meta"}
    sql = f"DROP TABLE IF EXISTS {table_name}"
    return db_execute(sql)


# ========== 文件上传 ==========

def save_uploaded_file(file_data, filename, subfolder=""):
    """
    保存上传的文件
    参数:
        file_data: 文件二进制数据
        filename: 文件名
        subfolder: 子文件夹（可选）
    返回: (success, result_dict)
    """
    # 安全检查文件名
    safe_name = os.path.basename(filename)
    if not safe_name or safe_name in ('.', '..'):
        return False, {"error": "无效的文件名"}

    target_dir = FILES_DIR
    if subfolder:
        target_dir = os.path.join(FILES_DIR, subfolder)
    os.makedirs(target_dir, exist_ok=True)

    target_path = os.path.join(target_dir, safe_name)
    try:
        with open(target_path, 'wb') as f:
            f.write(file_data)
        info = _format_file_info(target_path)
        rel_path = os.path.relpath(target_path, FILES_DIR).replace('\\', '/')
        return True, {
            "success": True,
            "filename": safe_name,
            "path": rel_path,
            "size": len(file_data),
            "size_human": _human_size(len(file_data)),
            "file_info": info,
            "message": f"文件已上传: {rel_path}",
        }
    except Exception as e:
        return False, {"success": False, "error": f"保存文件失败: {e}"}


# ========== 工具定义（供 LLM Function Calling 使用） ==========

def get_tools_definition():
    """返回 OpenAI 兼容的 Function Calling 工具定义"""
    return [
        # 📁 目录操作
        {
            "type": "function",
            "function": {
                "name": "list_folder",
                "description": "列出目录中的文件和子目录。可以递归列出、按通配符过滤、分页。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "目录路径，空字符串表示根目录 data/files/"},
                        "recursive": {"type": "boolean", "description": "是否递归列出子目录"},
                        "pattern": {"type": "string", "description": "文件名通配符过滤，如 *.txt"},
                        "offset": {"type": "integer", "description": "分页起始位置，默认0"},
                        "limit": {"type": "integer", "description": "最大返回数量，默认100"},
                    },
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "create_folder",
                "description": "创建目录（自动创建多级父目录）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "要创建的目录路径"},
                        "exist_ok": {"type": "boolean", "description": "如果目录已存在是否视为成功，默认 true"},
                    },
                    "required": ["path"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "delete_folder",
                "description": "删除目录",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "要删除的目录路径"},
                        "recursive": {"type": "boolean", "description": "是否递归删除目录内所有内容，默认 true"},
                    },
                    "required": ["path"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "move_folder",
                "description": "移动或重命名目录",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source_path": {"type": "string", "description": "源目录路径"},
                        "dest_path": {"type": "string", "description": "目标目录路径"},
                    },
                    "required": ["source_path", "dest_path"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "copy_folder",
                "description": "复制整个目录到目标位置",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source_path": {"type": "string", "description": "源目录路径"},
                        "dest_path": {"type": "string", "description": "目标目录路径"},
                    },
                    "required": ["source_path", "dest_path"]
                }
            }
        },
        # 📄 文件读写
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "读取文件内容。支持指定行范围、自动截断大文件。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件路径"},
                        "encoding": {"type": "string", "description": "文件编码，默认 utf-8"},
                        "start_line": {"type": "integer", "description": "起始行号（1-based）"},
                        "end_line": {"type": "integer", "description": "结束行号（1-based）"},
                    },
                    "required": ["path"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "创建或覆盖写入文件。会自动创建父目录。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件路径"},
                        "content": {"type": "string", "description": "要写入的内容"},
                        "encoding": {"type": "string", "description": "文件编码，默认 utf-8"},
                    },
                    "required": ["path", "content"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "append_file",
                "description": "追加内容到文件尾部",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件路径"},
                        "content": {"type": "string", "description": "要追加的内容"},
                        "encoding": {"type": "string", "description": "文件编码，默认 utf-8"},
                    },
                    "required": ["path", "content"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "insert_text",
                "description": "在文件指定位置插入内容。position 可以是 'start'（开头）、'end'（末尾），或数字行号。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件路径"},
                        "content": {"type": "string", "description": "要插入的内容"},
                        "position": {"type": "string", "description": "插入位置：'start'/'end'/数字行号"},
                    },
                    "required": ["path", "content", "position"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "replace_text",
                "description": "在文件中查找并替换文本。支持普通文本和正则表达式。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件路径"},
                        "search_text": {"type": "string", "description": "要查找的文本或正则表达式"},
                        "replace_text": {"type": "string", "description": "替换为的文本"},
                        "count": {"type": "integer", "description": "替换次数，0=全部替换"},
                        "regex": {"type": "boolean", "description": "search_text 是否为正则表达式"},
                    },
                    "required": ["path", "search_text", "replace_text"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "delete_lines",
                "description": "删除文件中指定范围的行",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件路径"},
                        "start_line": {"type": "integer", "description": "起始行号（1-based，含）"},
                        "end_line": {"type": "integer", "description": "结束行号（1-based，含）"},
                    },
                    "required": ["path", "start_line", "end_line"]
                }
            }
        },
        # 📄 文件管理
        {
            "type": "function",
            "function": {
                "name": "delete_file",
                "description": "删除文件",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件路径"},
                    },
                    "required": ["path"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "move_file",
                "description": "移动或重命名文件",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source_path": {"type": "string", "description": "源文件路径"},
                        "dest_path": {"type": "string", "description": "目标文件路径"},
                    },
                    "required": ["source_path", "dest_path"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "copy_file",
                "description": "复制文件到目标位置",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source_path": {"type": "string", "description": "源文件路径"},
                        "dest_path": {"type": "string", "description": "目标文件路径"},
                    },
                    "required": ["source_path", "dest_path"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_file_info",
                "description": "获取文件或目录的详细信息（大小、修改时间、行数等）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件或目录路径"},
                    },
                    "required": ["path"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "search_files",
                "description": "按文件名模式搜索文件（支持通配符如 *.txt）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "folder_path": {"type": "string", "description": "搜索的目录路径，默认根目录"},
                        "pattern": {"type": "string", "description": "文件名通配符，如 *.py"},
                        "recursive": {"type": "boolean", "description": "是否递归搜索子目录"},
                    },
                    "required": ["pattern"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "search_content",
                "description": "在文件内容中搜索文本（类似 grep）。支持正则表达式。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "folder_path": {"type": "string", "description": "搜索的目录路径，默认根目录"},
                        "text": {"type": "string", "description": "要搜索的文本或正则表达式"},
                        "file_pattern": {"type": "string", "description": "搜索哪些文件，如 *.py，默认 *"},
                        "recursive": {"type": "boolean", "description": "是否递归搜索子目录"},
                        "case_sensitive": {"type": "boolean", "description": "是否区分大小写"},
                    },
                    "required": ["text"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_file_hash",
                "description": "计算文件的哈希值（MD5/SHA1/SHA256）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件路径"},
                        "algorithm": {"type": "string", "description": "哈希算法: md5/sha1/sha256，默认 md5"},
                    },
                    "required": ["path"]
                }
            }
        },
        # 📄 批量与实用
        {
            "type": "function",
            "function": {
                "name": "batch_read",
                "description": "批量读取多个文件的内容",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "paths": {"type": "array", "items": {"type": "string"}, "description": "文件路径列表，最多20个"},
                    },
                    "required": ["paths"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "zip_files",
                "description": "将多个文件/目录打包压缩为 zip 或 tar.gz",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "paths": {"type": "array", "items": {"type": "string"}, "description": "要压缩的路径列表"},
                        "output_path": {"type": "string", "description": "压缩包输出路径"},
                        "format": {"type": "string", "description": "压缩格式: zip 或 tar.gz，默认 zip"},
                    },
                    "required": ["paths", "output_path"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "unzip_file",
                "description": "解压 zip/tar.gz/tar 文件",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "压缩包路径"},
                        "dest_folder": {"type": "string", "description": "解压目标目录，默认为压缩包同目录"},
                    },
                    "required": ["path"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "count_items",
                "description": "统计目录中的文件数量、子目录数量和总大小",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "目录路径，默认根目录"},
                        "recursive": {"type": "boolean", "description": "是否递归统计"},
                    },
                    "required": []
                }
            }
        },
        # 🗄️ 数据库
        {
            "type": "function",
            "function": {
                "name": "db_list_tables",
                "description": "列出 SQLite 数据库中所有的表",
                "parameters": {"type": "object", "properties": {}, "required": []}
            }
        },
        {
            "type": "function",
            "function": {
                "name": "db_describe_table",
                "description": "查看指定表的结构（列名、类型、约束等）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "table_name": {"type": "string", "description": "表名"},
                    },
                    "required": ["table_name"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "db_query",
                "description": "执行 SELECT 查询（只读），返回查询结果",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "sql": {"type": "string", "description": "SELECT 查询语句"},
                        "params": {"type": "array", "items": {}, "description": "参数化查询参数"},
                        "limit": {"type": "integer", "description": "最大返回行数，默认100"},
                    },
                    "required": ["sql"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "db_execute",
                "description": "执行非查询 SQL 语句（INSERT/UPDATE/DELETE/CREATE/DROP/ALTER）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "sql": {"type": "string", "description": "SQL 语句"},
                        "params": {"type": "array", "items": {}, "description": "参数化查询参数"},
                    },
                    "required": ["sql"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "db_create_table",
                "description": "在数据库中创建新表",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "table_name": {"type": "string", "description": "表名"},
                        "columns": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string", "description": "列名"},
                                    "type": {"type": "string", "description": "数据类型，如 INTEGER/TEXT/REAL/BLOB"},
                                    "constraints": {"type": "string", "description": "约束，如 PRIMARY KEY, NOT NULL"},
                                },
                                "required": ["name", "type"]
                            },
                            "description": "列定义列表"
                        },
                    },
                    "required": ["table_name", "columns"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "db_drop_table",
                "description": "删除数据库中的表",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "table_name": {"type": "string", "description": "要删除的表名"},
                    },
                    "required": ["table_name"]
                }
            }
        },
    ]


# ========== 工具调用路由 ==========

TOOL_MAP = {
    "list_folder": list_folder,
    "create_folder": create_folder,
    "delete_folder": delete_folder,
    "move_folder": move_folder,
    "copy_folder": copy_folder,
    "read_file": read_file,
    "write_file": write_file,
    "append_file": append_file,
    "insert_text": insert_text,
    "replace_text": replace_text,
    "delete_lines": delete_lines,
    "delete_file": delete_file,
    "move_file": move_file,
    "copy_file": copy_file,
    "get_file_info": get_file_info,
    "search_files": search_files,
    "search_content": search_content,
    "get_file_hash": get_file_hash,
    "batch_read": batch_read,
    "zip_files": zip_files,
    "unzip_file": unzip_file,
    "count_items": count_items,
    "db_list_tables": db_list_tables,
    "db_describe_table": db_describe_table,
    "db_query": db_query,
    "db_execute": db_execute,
    "db_create_table": db_create_table,
    "db_drop_table": db_drop_table,
}


def execute_tool(tool_name, arguments, user=None):
    """
    执行工具调用，user 参数用于权限检查
    返回: (success, result_dict)
    """
    if tool_name not in TOOL_MAP:
        return False, {"error": f"未知工具: {tool_name}"}

    if user is not None:
        try:
            import auth
            file_path = arguments.get('path', '') if isinstance(arguments, dict) else ''
            can_exec, err_msg = auth.can_execute_tool(tool_name, user, file_path)
            if not can_exec:
                return False, {"error": err_msg}
        except ImportError:
            pass

    func = TOOL_MAP[tool_name]
    try:
        result = func(**arguments) if arguments else func()
        return True, result
    except TypeError as e:
        return False, {"error": f"工具参数错误: {e}"}
    except Exception as e:
        return False, {"error": f"工具执行异常: {e}"}


def detect_file_type(path):
    """
    检测文件类型（通过扩展名）
    返回 MIME 类型或描述字符串
    """
    mime_type, _ = mimetypes.guess_type(path)
    if mime_type:
        return mime_type
    ext = os.path.splitext(path)[1].lower()
    type_map = {
        '.py': 'text/x-python',
        '.js': 'text/javascript',
        '.html': 'text/html',
        '.css': 'text/css',
        '.json': 'application/json',
        '.xml': 'application/xml',
        '.csv': 'text/csv',
        '.md': 'text/markdown',
        '.txt': 'text/plain',
        '.log': 'text/plain',
        '.ini': 'text/plain',
        '.cfg': 'text/plain',
        '.yml': 'text/yaml',
        '.yaml': 'text/yaml',
        '.pdf': 'application/pdf',
        '.doc': 'application/msword',
        '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        '.xls': 'application/vnd.ms-excel',
        '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    }
    return type_map.get(ext, 'application/octet-stream')


# 初始化
_ensure_dirs()
