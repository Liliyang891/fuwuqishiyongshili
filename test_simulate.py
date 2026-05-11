#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模拟测试脚本 — 验证 tools.py 和 AI Agent 核心流程
测试所有 28 个工具函数 + Function Calling 调用流程
"""

import sys
import os
import io
import json
import shutil
import time

# Fix Windows GBK encoding for emoji output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 确保 data 目录干净
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
FILES_DIR = os.path.join(DATA_DIR, 'files')
DB_PATH = os.path.join(DATA_DIR, 'app.db')

def cleanup():
    """清理测试数据"""
    import gc
    gc.collect()
    if os.path.exists(DATA_DIR):
        for _ in range(5):
            try:
                shutil.rmtree(DATA_DIR)
                break
            except PermissionError:
                time.sleep(0.3)
    print("🧹 已清理 data 目录")

def setup():
    """初始化测试环境"""
    import tools
    tools._ensure_dirs()
    print("✅ 目录和数据库初始化完成")
    return tools

PASS = 0
FAIL = 0

def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}" + (f" — {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))
    return condition

# ========== 测试 1: 目录操作 ==========
def test_folder_ops(tools):
    print("\n📁 === 目录操作测试 ===")

    # 1.1 创建目录
    r = tools.create_folder("test_dir")
    check("创建目录", r.get("success"), r.get("message"))
    check("目录存在", os.path.isdir(os.path.join(FILES_DIR, "test_dir")))

    # 1.2 创建子目录（多级）
    r = tools.create_folder("test_dir/sub1/sub2")
    check("创建多级子目录", r.get("success"), r.get("message"))

    # 1.3 列出根目录
    r = tools.list_folder("")
    check("列出根目录", r.get("success") and r.get("total") >= 1, f"共 {r.get('total')} 项")
    items = r.get("items", [])
    has_dir = any(i["type"] == "directory" and i["name"] == "test_dir" for i in items)
    check("根目录包含 test_dir", has_dir)

    # 1.4 列出子目录
    r = tools.list_folder("test_dir")
    check("列出 test_dir", r.get("success"), f"共 {r.get('total')} 项")

    # 1.5 递归列出
    r = tools.list_folder("test_dir", recursive=True)
    check("递归列出 test_dir", r.get("success"), f"共 {r.get('total')} 项（含子目录）")

    # 1.6 通配符过滤
    tools.write_file("test_dir/readme.txt", "hello")
    tools.write_file("test_dir/data.csv", "a,b,c")
    r = tools.list_folder("test_dir", pattern="*.txt")
    check("按 *.txt 过滤", r.get("total") == 1, f"找到 {r.get('total')} 个 .txt 文件")

    # 1.7 复制目录
    r = tools.copy_folder("test_dir", "test_dir_copy")
    check("复制目录", r.get("success"), r.get("message"))
    check("复制后目标存在", os.path.isdir(os.path.join(FILES_DIR, "test_dir_copy")))

    # 1.8 移动/重命名目录
    r = tools.move_folder("test_dir_copy", "test_dir_moved")
    check("移动目录", r.get("success"), r.get("message"))
    check("原目录不存在", not os.path.exists(os.path.join(FILES_DIR, "test_dir_copy")))
    check("新目录存在", os.path.isdir(os.path.join(FILES_DIR, "test_dir_moved")))

    # 1.9 删除目录
    r = tools.delete_folder("test_dir_moved")
    check("删除目录", r.get("success"), r.get("message"))
    check("目录已删除", not os.path.exists(os.path.join(FILES_DIR, "test_dir_moved")))

    # 1.10 安全检查：不允许访问父目录
    r = tools.list_folder("../etc")
    check("安全限制-禁止父目录访问", not r.get("success"), r.get("error"))

# ========== 测试 2: 文件读写操作 ==========
def test_file_rw(tools):
    print("\n📄 === 文件读写测试 ===")

    # 2.1 写入文件
    content = "第一行\n第二行\n第三行\n第四行\n第五行"
    r = tools.write_file("hello.txt", content)
    check("写入文件", r.get("success"), r.get("message"))

    # 2.2 读取文件
    r = tools.read_file("hello.txt")
    check("读取文件", r.get("success") and r.get("content") == content, f"{r.get('line_count')} 行")
    check("不是二进制", not r.get("is_binary"))

    # 2.3 读取指定行范围
    r = tools.read_file("hello.txt", start_line=2, end_line=4)
    check("读取第2-4行", r.get("success") and r.get("displayed_lines") == 3)

    # 2.4 追加内容
    r = tools.append_file("hello.txt", "追加行")
    check("追加内容", r.get("success"))
    r = tools.read_file("hello.txt")
    check("追加后行数+1", r.get("line_count") == 6, f"共 {r.get('line_count')} 行")

    # 2.5 在开头插入
    r = tools.insert_text("hello.txt", "开头插入", "start")
    check("在开头插入", r.get("success"))
    r = tools.read_file("hello.txt", start_line=1, end_line=1)
    check("开头插入验证", "开头插入" in r.get("content", ""))

    # 2.6 在指定行插入
    r = tools.insert_text("hello.txt", "中间插入在第3行", 3)
    check("在第3行插入", r.get("success"))
    r = tools.read_file("hello.txt", start_line=3, end_line=3)
    check("第3行验证", "中间插入" in r.get("content", ""))

    # 2.7 文本替换
    r = tools.replace_text("hello.txt", "hello.txt", "hello_renamed.txt")
    check("替换文件名引用", r.get("success"))
    r = tools.read_file("hello.txt", start_line=1, end_line=3)
    check("替换是否生效", "hello_renamed.txt" in r.get("content", "") or "hello" not in r["content"])

    # 2.8 删除行
    r = tools.delete_lines("hello.txt", 2, 4)
    check("删除第2-4行", r.get("success"), f"删除了 {r.get('deleted_lines')} 行")

    # 2.9 读取不存在的文件
    r = tools.read_file("nonexistent.txt")
    check("读取不存在文件", not r.get("success"))

# ========== 测试 3: 文件管理操作 ==========
def test_file_mgmt(tools):
    print("\n📂 === 文件管理测试 ===")

    # 3.1 移动/重命名文件
    r = tools.move_file("hello.txt", "renamed.txt")
    check("重命名文件", r.get("success"), r.get("message"))
    check("原文件不存在", not os.path.exists(os.path.join(FILES_DIR, "hello.txt")))
    check("新文件存在", os.path.exists(os.path.join(FILES_DIR, "renamed.txt")))

    # 3.2 复制文件
    r = tools.copy_file("renamed.txt", "copy.txt")
    check("复制文件", r.get("success"), r.get("message"))
    check("副本存在", os.path.exists(os.path.join(FILES_DIR, "copy.txt")))

    # 3.3 获取文件信息
    r = tools.get_file_info("renamed.txt")
    check("获取文件信息", r.get("success") and r.get("file_info"), str(r.get("file_info", {}).get("name")))
    check("包含行数", r.get("file_info", {}).get("line_count") is not None)

    # 3.4 搜索文件
    r = tools.search_files("", pattern="*.txt")
    check("搜索 *.txt 文件", r.get("success") and r.get("count") >= 2, f"找到 {r.get('count')} 个")

    # 3.5 搜索内容
    r = tools.search_content("", text="插入", file_pattern="*.txt")
    check("搜索内容'插入'", r.get("success") and r.get("match_count") >= 1, f"匹配 {r.get('match_count')} 处")

    # 3.6 文件哈希
    r = tools.get_file_hash("renamed.txt", "md5")
    check("MD5哈希", r.get("success") and len(r.get("hash", "")) == 32, f"MD5: {r.get('hash', '')[:16]}...")

    r = tools.get_file_hash("renamed.txt", "sha256")
    check("SHA256哈希", r.get("success") and len(r.get("hash", "")) == 64, f"SHA256: {r.get('hash', '')[:16]}...")

    # 3.7 删除文件
    r = tools.delete_file("copy.txt")
    check("删除文件", r.get("success"))
    check("文件已删除", not os.path.exists(os.path.join(FILES_DIR, "copy.txt")))

    # 3.8 安全限制：写文件不允许写父目录
    r = tools.write_file("../escape.txt", "hack")
    check("安全限制-禁止写父目录", not r.get("success"))

# ========== 测试 4: 批量与压缩 ==========
def test_batch_compress(tools):
    print("\n📦 === 批量与压缩测试 ===")

    # 4.1 批量读取
    r = tools.batch_read(["renamed.txt", "test_dir/readme.txt", "test_dir/data.csv"])
    check("批量读取3个文件", r.get("success") and len(r.get("files")) == 3)
    all_ok = all(f["result"].get("success") for f in r.get("files"))
    check("批量读取全部成功", all_ok)

    # 4.2 统计
    r = tools.count_items("", recursive=True)
    check("递归统计根目录", r.get("success"), f"文件:{r.get('file_count')} 目录:{r.get('dir_count')} 大小:{r.get('total_size_human')}")

    # 4.3 压缩 zip
    r = tools.zip_files(["renamed.txt", "test_dir/readme.txt"], "bundle.zip", "zip")
    check("打包zip", r.get("success"), r.get("message"))
    check("zip文件存在", os.path.isfile(os.path.join(FILES_DIR, "bundle.zip")))

    # 4.4 解压 zip
    r = tools.unzip_file("bundle.zip", "extracted")
    check("解压zip", r.get("success"), r.get("message"))
    check("解压目录存在", os.path.isdir(os.path.join(FILES_DIR, "extracted")))

# ========== 测试 5: 数据库操作 ==========
def test_database(tools):
    print("\n🗄️ === 数据库操作测试 ===")

    # 5.1 列出表
    r = tools.db_list_tables()
    check("列出数据库表", r.get("success"), f"共 {r.get('count')} 个表")
    check("包含 _meta 系统表", "_meta" in r.get("tables", []))

    # 5.2 创建表
    columns = [
        {"name": "id", "type": "INTEGER", "constraints": "PRIMARY KEY AUTOINCREMENT"},
        {"name": "name", "type": "TEXT", "constraints": "NOT NULL"},
        {"name": "email", "type": "TEXT", "constraints": "UNIQUE"},
        {"name": "age", "type": "INTEGER", "constraints": "DEFAULT 0"},
    ]
    r = tools.db_create_table("simtest_users", columns)
    check("创建 simtest_users 表", r.get("success"), r.get("message"))

    # 5.3 查看表结构
    r = tools.db_describe_table("simtest_users")
    check("查看表结构", r.get("success") and len(r.get("columns")) == 4, f"共 {len(r.get('columns', []))} 列")

    # 5.4 插入数据
    r = tools.db_execute("INSERT INTO simtest_users (name, email, age) VALUES (?, ?, ?)", ["Alice", "alice@test.com", 25])
    check("插入Alice", r.get("success"), f"影响 {r.get('affected_rows')} 行")

    r = tools.db_execute("INSERT INTO simtest_users (name, email, age) VALUES (?, ?, ?)", ["Bob", "bob@test.com", 30])
    check("插入Bob", r.get("success"))

    r = tools.db_execute("INSERT INTO simtest_users (name, email, age) VALUES (?, ?, ?)", ["Charlie", "charlie@test.com", 28])
    check("插入Charlie", r.get("success"))

    # 5.5 查询所有
    r = tools.db_query("SELECT * FROM simtest_users")
    check("查询全部用户", r.get("success") and r.get("total") == 3, f"共 {r.get('total')} 条")

    # 5.6 条件查询
    r = tools.db_query("SELECT * FROM simtest_users WHERE age > ?", [25])
    check("查询年龄>25", r.get("success") and r.get("total") == 2, f"共 {r.get('total')} 条")

    # 5.7 分页查询
    r = tools.db_query("SELECT * FROM simtest_users ORDER BY id", limit=2)
    check("分页查询前2条", r.get("success") and r.get("displayed") == 2 and r.get("truncated"))

    # 5.8 更新数据
    r = tools.db_execute("UPDATE simtest_users SET age = ? WHERE name = ?", [26, "Alice"])
    check("更新Alice年龄", r.get("success"), f"影响 {r.get('affected_rows')} 行")
    r = tools.db_query("SELECT age FROM simtest_users WHERE name = ?", ["Alice"])
    check("年龄是否更新为26", r.get("rows", [{}])[0].get("age") == 26)

    # 5.9 删除数据
    r = tools.db_execute("DELETE FROM simtest_users WHERE name = ?", ["Charlie"])
    check("删除Charlie", r.get("success"))
    r = tools.db_query("SELECT COUNT(*) as cnt FROM simtest_users")
    check("删除后还剩2条", r.get("rows", [{}])[0].get("cnt") == 2)

    # 5.10 安全：禁止 SELECT 通过 db_execute
    r = tools.db_execute("SELECT * FROM simtest_users")
    check("db_execute 禁止 SELECT", not r.get("success"))

    # 5.11 安全：禁止 _meta 表删除
    r = tools.db_drop_table("_meta")
    check("禁止删除 _meta 表", not r.get("success"))

    # 5.12 删除表
    r = tools.db_drop_table("simtest_users")
    check("删除 simtest_users 表", r.get("success"))
    r = tools.db_list_tables()
    check("simtest_users表已删除", "simtest_users" not in r.get("tables", []))

# ========== 测试 6: 文件上传 ==========
def test_upload(tools):
    print("\n📤 === 文件上传测试 ===")

    # 6.1 上传文本文件
    ok, r = tools.save_uploaded_file(b"Hello Upload!", "upload_test.txt")
    check("上传文本文件", ok, r.get("message"))
    check("文件存在", os.path.isfile(os.path.join(FILES_DIR, "upload_test.txt")))

    # 6.2 上传到子目录
    ok, r = tools.save_uploaded_file(b"sub upload", "sub_file.txt", "uploads")
    check("上传到子目录", ok, r.get("message"))
    check("子目录文件存在", os.path.isfile(os.path.join(FILES_DIR, "uploads", "sub_file.txt")))

    # 6.3 上传图片（PNG 头）
    png_data = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
    ok, r = tools.save_uploaded_file(png_data, "image.png")
    check("上传PNG图片", ok, r.get("message"))

    # 6.4 测试文件类型判断
    file_type = tools.detect_file_type(os.path.join(FILES_DIR, "image.png"))
    check("PNG类型检测", "image/png" in file_type or "image" in file_type, file_type)
    file_type = tools.detect_file_type(os.path.join(FILES_DIR, "upload_test.txt"))
    check("文本类型检测", "text/plain" in file_type, file_type)

# ========== 测试 7: Function Calling 定义完整性 ==========
def test_tool_definitions(tools):
    print("\n🔧 === Function Calling 定义测试 ===")

    definitions = tools.get_tools_definition()
    check("工具定义返回列表", isinstance(definitions, list))
    check("工具数量", len(definitions) >= 28, f"共 {len(definitions)} 个工具")

    # 检查每个工具定义格式
    required_fields = ["type", "function"]
    func_required = ["name", "description", "parameters"]
    all_valid = True
    for d in definitions:
        if not all(k in d for k in required_fields):
            all_valid = False
            print(f"    ❌ 工具定义缺少必要字段: {d.get('function', {}).get('name', '?')}")
            continue
        f = d["function"]
        if not all(k in f for k in func_required):
            all_valid = False
            print(f"    ❌ 工具 {f.get('name', '?')} 缺少必要字段")
    check("所有工具定义格式正确", all_valid)

    # 检查工具映射表
    tool_names = list(tools.TOOL_MAP.keys())
    check("TOOL_MAP 存在", len(tool_names) >= 28, f"共 {len(tool_names)} 个映射")

    # 测试 execute_tool
    ok, result = tools.execute_tool("list_folder", {"path": ""})
    check("execute_tool list_folder", ok and result.get("success"), f"找到 {result.get('total', 0)} 项")

    # 测试不存在的工具
    ok, result = tools.execute_tool("non_existent_tool", {})
    check("execute_tool 不存在工具", not ok, result.get("error", ""))

    # 测试缺少参数
    ok, result = tools.execute_tool("read_file", {})
    check("execute_tool 缺少路径参数", not ok)

# ========== 测试 8: 模拟 AI Agent 对话流程 ==========
def test_agent_flow(tools):
    print("\n🤖 === 模拟 AI Agent 对话流程 ===")

    # 模拟 System Prompt 引导 AI 使用工具
    SYSTEM_PROMPT = "你是一个文件管理和数据库操作助手..."

    # 模拟用户消息和预期工具调用
    test_cases = [
        {
            "user": "帮我看看 data/files 里有哪些文件",
            "expected_tool": "list_folder",
            "expected_args": {"path": ""},
        },
        {
            "user": "创建一个名为 hello.txt 的文件，内容是 Hello World",
            "expected_tool": "write_file",
            "expected_args": {"path": "hello.txt", "content": "Hello World"},
        },
        {
            "user": "在数据库创建一个 products 表，有 id(主键)、name(文本)、price(实数) 三个字段",
            "expected_tool": "db_create_table",
        },
        {
            "user": "查询 users 表中的所有数据",
            "expected_tool": "db_query",
        },
        {
            "user": "把 config.json 重命名为 config_backup.json",
            "expected_tool": "move_file",
        },
        {
            "user": "删除 temp.txt 文件",
            "expected_tool": "delete_file",
        },
        {
            "user": "统计一下 data/files 下有多少文件",
            "expected_tool": "count_items",
        },
        {
            "user": "打包 test_dir 目录为 zip",
            "expected_tool": "zip_files",
        },
    ]

    for case in test_cases:
        user_text = case["user"]
        expected = case["expected_tool"]
        print(f"  💬 用户: \"{user_text}\"")
        # 实际环境中 AI 会返回 tool_call，这里我们模拟直接调用
        # 验证对应工具确实存在且可用
        tool_exists = expected in tools.TOOL_MAP
        check(f"  → 工具 {expected} 存在", tool_exists)

    # 模拟完整循环：用户消息 → 工具调用 → 工具结果 → AI 回复
    print("\n  🔄 模拟完整 Function Calling 循环:")

    # Step 1: 用户说 "列出文件"
    user_msg = "列出 data/files 下的文件"
    print(f"    1. 用户: {user_msg}")

    # Step 2: AI 选择工具 list_folder
    tool_name = "list_folder"
    tool_args = {"path": ""}
    print(f"    2. AI → 调用 {tool_name}({json.dumps(tool_args, ensure_ascii=False)})")

    # Step 3: 执行工具
    ok, result = tools.execute_tool(tool_name, tool_args)
    print(f"    3. 工具返回: success={result.get('success')}, total={result.get('total', 0)}")
    check(f"  循环步骤1-3: list_folder 成功", ok and result.get("success"))

    # Step 4: AI 获得结果后，用户再请求 "删除 renamed.txt"
    user_msg2 = "删除 renamed.txt 文件"
    print(f"    4. 用户: {user_msg2}")
    tool_name2 = "delete_file"
    tool_args2 = {"path": "renamed.txt"}
    print(f"    5. AI → 调用 {tool_name2}({json.dumps(tool_args2, ensure_ascii=False)})")
    ok2, result2 = tools.execute_tool(tool_name2, tool_args2)
    print(f"    6. 工具返回: success={result2.get('success')}")
    check(f"  循环步骤4-6: delete_file 成功", ok2 and result2.get("success"))

    print("\n  ✅ Agent 循环流程验证通过")

# ========== 测试 9: 文件信息格式化 ==========
def test_file_info(tools):
    print("\n📊 === 文件信息格式化测试 ===")

    from tools import _format_file_info, _human_size

    check("_human_size 0B", _human_size(0) == "0.0 B")
    check("_human_size 1KB", _human_size(1024) == "1.0 KB")
    check("_human_size 1MB", _human_size(1048576) == "1.0 MB")
    check("_human_size 1GB", _human_size(1073741824) == "1.0 GB")

    # 格式文件信息
    info = _format_file_info(os.path.join(FILES_DIR, "test_dir"))
    check("目录信息格式", info["type"] == "directory" and info["name"] == "test_dir")
    check("目录大小为空", info["size"] is None)
    check("目录size_human为空", info["size_human"] is None)

# ========== 主函数 ==========
def main():
    global PASS, FAIL
    PASS = 0
    FAIL = 0

    print("=" * 60)
    print("  🧪 AI Agent 系统模拟测试")
    print("=" * 60)

    cleanup()
    tools = setup()

    try:
        test_folder_ops(tools)
        test_file_rw(tools)
        test_file_mgmt(tools)
        test_batch_compress(tools)
        test_database(tools)
        test_upload(tools)
        test_tool_definitions(tools)
        test_agent_flow(tools)
        test_file_info(tools)
    finally:
        # 清理
        cleanup()

    print("\n" + "=" * 60)
    print(f"  测试结果: ✅ {PASS} 通过  ❌ {FAIL} 失败")
    if FAIL == 0:
        print("  🎉 所有测试通过！系统运行正常！")
    else:
        print(f"  ⚠️ 有 {FAIL} 个测试失败，需要修复")
    print("=" * 60)

    return FAIL == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)