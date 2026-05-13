#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Excel 导入/查询处理器 — API 处理函数 + Skill 函数
"""

import hashlib
import json
import os
import time

import excel_parser
from tools import (
    _get_db_conn, FILES_DIR, DATA_DIR, save_uploaded_file,
)


def handle_upload(user, file_data, filename, import_notes='', header_row=0):
    """处理 Excel 上传：解析 + 存库 + 保留原文件

    Args:
        user: 当前登录用户字典
        file_data: 文件二进制数据
        filename: 原始文件名
        import_notes: 导入备注
        header_row: 标题行号（0-based）

    Returns:
        dict: {success, import_id, filename, sheet_count, total_rows, sheets, message}
    """
    # 1. 保存原始文件到 data/files/{role}/
    saved_ok, saved_result = save_uploaded_file(file_data, filename)
    if not saved_ok:
        return {'success': False, 'error': saved_result.get('error', '文件保存失败')}

    original_path = saved_result.get('path', '')

    # 2. 解析 Excel
    try:
        wb = excel_parser.parse_xlsx(file_data, filename, header_row=header_row)
    except excel_parser.ExcelParseError as e:
        return {'success': False, 'error': str(e)}
    except Exception as e:
        return {'success': False, 'error': f'解析 Excel 文件失败: {e}'}

    # 3. 计算 MD5
    file_md5 = hashlib.md5(file_data).hexdigest()
    file_size = len(file_data)
    now = time.time()

    # 4. 写入数据库
    conn = _get_db_conn()
    total_rows = sum(s.row_count for s in wb.sheets)
    cur = conn.execute(
        '''INSERT INTO excel_imports
           (filename, original_path, file_size, file_md5, uploaded_by,
            uploader_name, sheet_count, total_rows, status, import_notes, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)''',
        (filename, original_path, file_size, file_md5,
         user.get('id'), user.get('username'),
         len(wb.sheets), total_rows, import_notes or '', now)
    )
    import_id = cur.lastrowid

    sheets_info = []
    for i, sheet in enumerate(wb.sheets):
        cur2 = conn.execute(
            '''INSERT INTO excel_sheets
               (import_id, sheet_name, sheet_index, header_row_index,
                column_count, row_count, column_headers)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (import_id, sheet.name, i, header_row,
             sheet.column_count, sheet.row_count,
             json.dumps(sheet.headers, ensure_ascii=False))
        )
        sheet_id = cur2.lastrowid

        # 批量插入行数据
        for j, row in enumerate(sheet.rows):
            conn.execute(
                '''INSERT INTO excel_rows (sheet_id, row_index, row_data, created_at)
                   VALUES (?, ?, ?, ?)''',
                (sheet_id, j, json.dumps(row, ensure_ascii=False), now)
            )

        sheets_info.append({
            'id': sheet_id,
            'name': sheet.name,
            'row_count': sheet.row_count,
            'column_count': sheet.column_count,
        })

    conn.commit()

    return {
        'success': True,
        'import_id': import_id,
        'filename': filename,
        'sheet_count': len(wb.sheets),
        'total_rows': total_rows,
        'sheets': sheets_info,
        'message': f'已成功导入 {len(wb.sheets)} 个工作表，共 {total_rows} 行数据',
    }


def list_imports(user=None, filters=None):
    """列出导入记录

    Args:
        user: 当前用户
        filters: dict with keys: uploaded_by, status, limit, offset

    Returns:
        dict: {success, imports, total}
    """
    conn = _get_db_conn()
    params = []
    conditions = ['1=1']

    if filters:
        if filters.get('uploaded_by'):
            conditions.append('uploaded_by = ?')
            params.append(int(filters['uploaded_by']))
        if filters.get('status'):
            conditions.append('status = ?')
            params.append(filters['status'])

    where = ' AND '.join(conditions)
    total = conn.execute(f'SELECT COUNT(*) FROM excel_imports WHERE {where}', params).fetchone()[0]

    limit = int(filters.get('limit', 50)) if filters else 50
    offset = int(filters.get('offset', 0)) if filters else 0
    rows = conn.execute(
        f'''SELECT id, filename, original_path, file_size, file_md5,
                   uploaded_by, uploader_name, sheet_count, total_rows,
                   status, import_notes,
                   datetime(created_at, 'unixepoch', 'localtime') as created_at
            FROM excel_imports WHERE {where}
            ORDER BY created_at DESC LIMIT ? OFFSET ?''',
        params + [limit, offset]
    ).fetchall()

    imports = [dict(r) for r in rows]
    return {'success': True, 'imports': imports, 'total': total}


def get_sheets(import_id):
    """获取某个导入的工作表列表"""
    conn = _get_db_conn()
    import_row = conn.execute(
        'SELECT id, filename FROM excel_imports WHERE id=?', (import_id,)
    ).fetchone()
    if not import_row:
        return {'success': False, 'error': '导入记录不存在'}

    sheets = conn.execute(
        '''SELECT id, sheet_name, sheet_index, column_count, row_count, column_headers
           FROM excel_sheets WHERE import_id=? ORDER BY sheet_index''',
        (import_id,)
    ).fetchall()

    sheet_list = []
    for s in sheets:
        d = dict(s)
        try:
            d['column_headers'] = json.loads(d['column_headers'])
        except (json.JSONDecodeError, TypeError):
            d['column_headers'] = []
        sheet_list.append(d)

    return {
        'success': True,
        'import_id': import_id,
        'filename': import_row['filename'],
        'sheets': sheet_list,
    }


def get_rows(sheet_id, params=None):
    """获取工作表行数据（分页/搜索/排序）

    Args:
        sheet_id: 工作表ID
        params: {limit, offset, search, order_by, order_dir}
    """
    conn = _get_db_conn()
    sheet = conn.execute(
        '''SELECT es.id, es.sheet_name, es.column_headers,
                  ei.filename, ei.id as import_id
           FROM excel_sheets es JOIN excel_imports ei ON es.import_id = ei.id
           WHERE es.id=?''', (sheet_id,)
    ).fetchone()
    if not sheet:
        return {'success': False, 'error': '工作表不存在'}

    try:
        headers = json.loads(sheet['column_headers'])
    except (json.JSONDecodeError, TypeError):
        headers = []

    limit = int(params.get('limit', 100)) if params else 100
    offset = int(params.get('offset', 0)) if params else 0
    search = (params.get('search', '') or '').strip() if params else ''

    conditions = ['sheet_id = ?']
    sql_params = [sheet_id]

    if search:
        conditions.append("row_data LIKE ?")
        sql_params.append(f'%{search}%')

    where = ' AND '.join(conditions)
    total = conn.execute(
        f'SELECT COUNT(*) FROM excel_rows WHERE {where}', sql_params
    ).fetchone()[0]

    order_by = 'row_index'
    if params and params.get('order_by') and params['order_by'] in headers:
        order_by = f"json_extract(row_data, '$.{params['order_by']}')"
    order_dir = 'ASC' if (params and params.get('order_dir', 'asc').lower() == 'asc') else 'DESC'

    rows = conn.execute(
        f'''SELECT id, row_index, row_data
            FROM excel_rows WHERE {where}
            ORDER BY {order_by} {order_dir}
            LIMIT ? OFFSET ?''',
        sql_params + [limit, offset]
    ).fetchall()

    row_list = []
    for r in rows:
        try:
            rd = json.loads(r['row_data'])
        except (json.JSONDecodeError, TypeError):
            rd = {}
        row_list.append({'id': r['id'], 'row_index': r['row_index'], 'row_data': rd})

    return {
        'success': True,
        'sheet_id': sheet_id,
        'sheet_name': sheet['sheet_name'],
        'filename': sheet['filename'],
        'column_headers': headers,
        'rows': row_list,
        'total_rows': total,
        'displayed': len(row_list),
    }


def search_excel(query, params=None):
    """全文搜索所有导入数据"""
    if not query or not query.strip():
        return {'success': False, 'error': '搜索内容不能为空'}

    conn = _get_db_conn()
    limit = int(params.get('limit', 50)) if params else 50
    import_id = params.get('import_id') if params else None

    conditions = ["er.row_data LIKE ?"]
    sql_params = [f'%{query.strip()}%']

    if import_id:
        conditions.append('es.import_id = ?')
        sql_params.append(int(import_id))

    where = ' AND '.join(conditions)
    total = conn.execute(
        f'''SELECT COUNT(*) FROM excel_rows er
            JOIN excel_sheets es ON er.sheet_id = es.id
            WHERE {where}''',
        sql_params
    ).fetchone()[0]

    rows = conn.execute(
        f'''SELECT es.import_id, ei.filename, es.id as sheet_id, es.sheet_name,
                   er.id as row_id, er.row_index, er.row_data
            FROM excel_rows er
            JOIN excel_sheets es ON er.sheet_id = es.id
            JOIN excel_imports ei ON es.import_id = ei.id
            WHERE {where}
            ORDER BY er.id DESC LIMIT ?''',
        sql_params + [limit]
    ).fetchall()

    matches = []
    for r in rows:
        try:
            rd = json.loads(r['row_data'])
        except (json.JSONDecodeError, TypeError):
            rd = {}
        matches.append({
            'import_id': r['import_id'],
            'filename': r['filename'],
            'sheet_id': r['sheet_id'],
            'sheet_name': r['sheet_name'],
            'row_id': r['row_id'],
            'row_index': r['row_index'],
            'row_data': rd,
        })

    return {
        'success': True,
        'query': query,
        'total': total,
        'matches': matches,
    }


def delete_import(import_id, user):
    """软删除导入记录（级联删除 sheets 和 rows 由 FK ON DELETE CASCADE 处理）"""
    conn = _get_db_conn()
    imp = conn.execute(
        'SELECT id, filename, status FROM excel_imports WHERE id=?', (import_id,)
    ).fetchone()
    if not imp:
        return {'success': False, 'error': '导入记录不存在'}

    conn.execute(
        "UPDATE excel_imports SET status='deleted' WHERE id=?", (import_id,)
    )
    conn.commit()
    return {'success': True, 'message': f'已删除导入记录: {imp["filename"]}'}


def export_csv(sheet_id):
    """导出工作表为 CSV 字符串"""
    conn = _get_db_conn()
    sheet = conn.execute(
        'SELECT sheet_name, column_headers FROM excel_sheets WHERE id=?', (sheet_id,)
    ).fetchone()
    if not sheet:
        return {'success': False, 'error': '工作表不存在'}

    try:
        headers = json.loads(sheet['column_headers'])
    except (json.JSONDecodeError, TypeError):
        headers = []

    rows = conn.execute(
        'SELECT row_data FROM excel_rows WHERE sheet_id=? ORDER BY row_index',
        (sheet_id,)
    ).fetchall()

    import csv
    import io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    for r in rows:
        try:
            rd = json.loads(r['row_data'])
        except (json.JSONDecodeError, TypeError):
            rd = {}
        writer.writerow([rd.get(h, '') for h in headers])

    return {
        'success': True,
        'sheet_name': sheet['sheet_name'],
        'csv': output.getvalue(),
    }


# ========== Skill 快速通道函数 ==========

def skill_search_excel(*, user, user_text=None):
    """Skill handler: 搜索导入的 Excel 数据"""
    if not user_text:
        return {'success': False, 'reply': '请提供搜索内容'}

    # 从用户输入中提取搜索关键词
    # 移除常见触发词
    import re
    triggers = [
        r'(搜索|查找|找一下|搜索一下)\s*',
        r'.*(导入的|excel|表格|数据|工作表).*',
    ]
    query = user_text.strip()
    for t in triggers:
        query = re.sub(t, '', query, flags=re.IGNORECASE).strip()

    if not query:
        return {'success': False, 'reply': '请提供要搜索的关键词'}

    result = search_excel(query)
    if not result['success']:
        return {'success': False, 'reply': f'搜索失败: {result.get("error", "")}'}

    total = result['total']
    if total == 0:
        return {'success': True, 'reply': f'未找到包含 "{query}" 的数据'}

    lines = [f'在已导入的 Excel 数据中搜索 "{query}"，找到 **{total}** 条匹配：\n']
    for m in result['matches'][:5]:
        rd = m['row_data']
        preview = ' | '.join(
            str(v)[:30] for v in list(rd.values())[:4]
        )
        lines.append(
            f'  • [{m["filename"]}] {m["sheet_name"]} 第{m["row_index"]+1}行: {preview}'
        )

    if total > 5:
        lines.append(f'\n... 还有 {total - 5} 条匹配结果')

    return {'success': True, 'reply': '\n'.join(lines)}


def skill_sheet_detail(*, user, user_text=None):
    """Skill handler: 查看工作表详情"""
    conn = _get_db_conn()

    # 尝试从文本中提取 import_id 或 sheet_id
    import re
    id_match = re.search(r'(?:编号|id|ID|第)\s*(\d+)', user_text or '')
    if not id_match:
        # 默认返回最近导入的列表
        result = list_imports(user, {'limit': 5})
        if not result['success'] or not result['imports']:
            return {'success': True, 'reply': '暂无导入的 Excel 数据'}
        lines = ['最近导入的 Excel 数据：']
        for imp in result['imports']:
            lines.append(
                f'  • #{imp["id"]} {imp["filename"]} | '
                f'{imp["sheet_count"]}个工作表 | {imp["total_rows"]}行 | '
                f'{imp["uploader_name"]} | {imp["created_at"]}'
            )
        return {'success': True, 'reply': '\n'.join(lines)}

    import_id = int(id_match.group(1))
    sheets_result = get_sheets(import_id)
    if not sheets_result['success']:
        return {'success': False, 'reply': sheets_result.get('error', '获取工作表失败')}

    lines = [f'{sheets_result["filename"]} — {len(sheets_result["sheets"])} 个工作表：']
    for s in sheets_result['sheets']:
        headers_preview = ', '.join(s['column_headers'][:5])
        if len(s['column_headers']) > 5:
            headers_preview += '...'
        lines.append(
            f'  • [{s["sheet_name"]}] {s["column_count"]}列 × {s["row_count"]}行'
        )
        lines.append(f'    列标题: {headers_preview}')

    return {'success': True, 'reply': '\n'.join(lines)}
