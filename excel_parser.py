#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Excel 解析器 — 使用 zipfile + xml.etree.ElementTree 解析 .xlsx 文件
避免 openpyxl 3.1.5 在 Python 3.13 下的样式表解析 bug
"""

import io
import re
import zipfile
from xml.etree import ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional

NS_MAIN = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
NS_R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
NS_R2 = 'http://schemas.openxmlformats.org/package/2006/relationships'

MAX_ROWS_PER_SHEET = 50000


@dataclass
class ParsedSheet:
    name: str
    headers: list = field(default_factory=list)
    rows: list = field(default_factory=list)  # list[dict[str, str]]
    row_count: int = 0
    column_count: int = 0


@dataclass
class ParsedWorkbook:
    filename: str = ''
    sheets: list = field(default_factory=list)  # list[ParsedSheet]


class ExcelParseError(Exception):
    pass


def parse_xlsx(file_data: bytes, filename: str = '',
               header_row: int = 0, max_rows: int = MAX_ROWS_PER_SHEET) -> ParsedWorkbook:
    """解析 .xlsx 文件

    Args:
        file_data: 文件二进制数据
        filename: 原始文件名（用于记录）
        header_row: 将哪一行作为列标题（0-based，默认第一行）
        max_rows: 每个工作表最大读取行数

    Returns:
        ParsedWorkbook
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(file_data), 'r')
    except (zipfile.BadZipFile, OSError):
        raise ExcelParseError('文件不是有效的 .xlsx 格式（ZIP 损坏或非 Excel 文件）')

    shared_strings = _load_shared_strings(zf)
    sheet_list = _load_sheet_list(zf)

    if not sheet_list:
        raise ExcelParseError('工作簿中没有工作表')

    result = ParsedWorkbook(filename=filename or 'unknown.xlsx')
    for sheet_name, sheet_file in sheet_list:
        try:
            raw_rows = _parse_sheet_xml(zf, sheet_file, shared_strings, max_rows)
        except ExcelParseError:
            raise
        except Exception as e:
            raise ExcelParseError(f'解析工作表 "{sheet_name}" 失败: {e}')

        headers, data_rows = _build_sheet_data(raw_rows, header_row)
        sheet = ParsedSheet(
            name=sheet_name,
            headers=headers,
            rows=data_rows,
            row_count=len(data_rows),
            column_count=len(headers),
        )
        result.sheets.append(sheet)

    return result


def _load_shared_strings(zf: zipfile.ZipFile) -> dict:
    """读取共享字符串表，返回 {index: text}"""
    if 'xl/sharedStrings.xml' not in zf.namelist():
        return {}
    tree = ET.parse(zf.open('xl/sharedStrings.xml'))
    root = tree.getroot()
    strings = {}
    for i, si in enumerate(root.findall(f'{{{NS_MAIN}}}si')):
        t = si.find(f'{{{NS_MAIN}}}t')
        if t is not None:
            strings[i] = t.text or ''
        else:
            # 富文本（多个 <r> 元素）
            texts = [r.find(f'{{{NS_MAIN}}}t') for r in si.findall(f'{{{NS_MAIN}}}r')]
            strings[i] = ''.join((t.text or '') for t in texts if t is not None)
    return strings


def _load_sheet_list(zf: zipfile.ZipFile) -> list:
    """读取 workbook.xml，返回 [(sheet_name, file_path), ...]"""
    if 'xl/workbook.xml' not in zf.namelist():
        return []

    tree = ET.parse(zf.open('xl/workbook.xml'))
    root = tree.getroot()
    sheets = []
    for sheet in root.findall(f'{{{NS_MAIN}}}sheets/{{{NS_MAIN}}}sheet'):
        name = sheet.get('name', '')
        r_id = sheet.get(f'{{{NS_R}}}id', '')
        if name:
            sheets.append((name, r_id))

    # 从关系文件获取实际路径
    rel_map = _load_rels(zf, 'xl/workbook.xml')
    result = []
    for name, r_id in sheets:
        target = rel_map.get(r_id, f'worksheets/sheet{r_id.replace("rId", "")}.xml')
        if not target.startswith('xl/'):
            target = 'xl/' + target
        result.append((name, target))
    return result


def _load_rels(zf: zipfile.ZipFile, parent_path: str) -> dict:
    """加载 .rels 文件，返回 {rId: target_path}"""
    # parent xl/workbook.xml → xl/_rels/workbook.xml.rels
    parts = parent_path.rsplit('/', 1)
    if len(parts) == 2:
        rels_path = f'{parts[0]}/_rels/{parts[1]}.rels'
    else:
        rels_path = f'_rels/{parent_path}.rels'

    if rels_path not in zf.namelist():
        return {}

    tree = ET.parse(zf.open(rels_path))
    root = tree.getroot()
    rel_map = {}
    for rel in root.findall(f'{{{NS_R2}}}Relationship'):
        rel_map[rel.get('Id', '')] = rel.get('Target', '')
    return rel_map


def _parse_sheet_xml(zf: zipfile.ZipFile, sheet_path: str,
                     shared_strings: dict, max_rows: int) -> list:
    """解析工作表 XML，返回 [{col_letter: cell_value}, ...]（按行号排序）"""
    if sheet_path not in zf.namelist():
        raise ExcelParseError(f'工作表文件不存在: {sheet_path}')

    tree = ET.parse(zf.open(sheet_path))
    root = tree.getroot()
    sheet_data = root.find(f'{{{NS_MAIN}}}sheetData')
    if sheet_data is None:
        return []

    rows_data = []
    for row in sheet_data.findall(f'{{{NS_MAIN}}}row'):
        if len(rows_data) >= max_rows:
            break
        row_dict = {}
        for cell in row.findall(f'{{{NS_MAIN}}}c'):
            ref = cell.get('r', '')
            col_letter = _ref_to_col(ref)
            cell_type = cell.get('t', '')
            v = cell.find(f'{{{NS_MAIN}}}v')

            if v is not None and v.text is not None:
                val = v.text
                if cell_type == 's':    # 共享字符串
                    row_dict[col_letter] = shared_strings.get(int(val), '')
                elif cell_type == 'b':  # 布尔
                    row_dict[col_letter] = 'TRUE' if val == '1' else 'FALSE'
                elif cell_type == 'str':  # 公式结果字符串
                    row_dict[col_letter] = val
                else:  # 数字、日期（序列号）
                    row_dict[col_letter] = _try_parse_number(val)
            else:
                row_dict[col_letter] = ''

        rows_data.append(row_dict)

    return rows_data


def _build_sheet_data(raw_rows: list, header_row: int) -> tuple:
    """从原始行数据构建 headers 和 data rows"""
    if not raw_rows:
        return [], []

    # 推断最大列号，补齐所有行到相同列宽
    all_cols = set()
    for r in raw_rows:
        all_cols.update(r.keys())
    sorted_cols = sorted(all_cols, key=_col_to_index)

    # 获取标题行
    if header_row < len(raw_rows):
        header_row_data = raw_rows[header_row]
        headers = []
        for col in sorted_cols:
            val = header_row_data.get(col, '').strip()
            headers.append(val if val else col)
    else:
        headers = sorted_cols

    # 检查是否有重复标题，有则加后缀区分
    seen = {}
    unique_headers = []
    for h in headers:
        if h in seen:
            seen[h] += 1
            unique_headers.append(f'{h}_{seen[h]}')
        else:
            seen[h] = 0
            unique_headers.append(h)
    headers = unique_headers

    # 构建数据行
    data_start = header_row + 1
    rows = []
    for i in range(data_start, len(raw_rows)):
        row_data = {}
        for j, col in enumerate(sorted_cols):
            key = headers[j] if j < len(headers) else col
            row_data[key] = raw_rows[i].get(col, '')
        # 跳过全空行
        if any(v.strip() for v in row_data.values()):
            rows.append(row_data)

    return headers, rows


def _ref_to_col(ref: str) -> str:
    """单元格引用 → 列字母，'AB12' → 'AB'"""
    m = re.match(r'^([A-Z]+)', ref)
    return m.group(1) if m else ref


def _col_to_index(col: str) -> int:
    """列字母 → 索引，'A'→0, 'B'→1, 'AA'→26"""
    n = 0
    for c in col.upper():
        n = n * 26 + (ord(c) - 64)
    return n - 1


def _try_parse_number(val: str) -> str:
    """尝试格式化数字：整数去掉小数点，保留原始值"""
    try:
        f = float(val)
        if f == int(f):
            return str(int(f))
        return val
    except ValueError:
        return val


# ========== 命令行测试入口 ==========
if __name__ == '__main__':
    import os
    import sys

    test_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             'data', '周计划跟进表.xlsx')
    if len(sys.argv) > 1:
        test_file = sys.argv[1]

    if not os.path.exists(test_file):
        print(f'文件不存在: {test_file}')
        sys.exit(1)

    print(f'正在解析: {test_file}')
    with open(test_file, 'rb') as f:
        data = f.read()
    print(f'文件大小: {len(data) / 1024:.1f} KB')

    wb = parse_xlsx(data, os.path.basename(test_file))
    print(f'\n工作表数: {len(wb.sheets)}')
    total_rows = 0
    for s in wb.sheets:
        total_rows += s.row_count
        print(f'  [{s.name}] {s.column_count} 列 × {s.row_count} 行 '
              f'| 标题: {s.headers[:5]}{"..." if len(s.headers) > 5 else ""}')
    print(f'\n总行数: {total_rows}')

    # 打印第一个 sheet 的前几行
    if wb.sheets:
        s = wb.sheets[0]
        print(f'\n--- 预览: [{s.name}] 前 5 行 ---')
        print(' | '.join(s.headers))
        print('-' * 60)
        for row in s.rows[:5]:
            print(' | '.join(str(row.get(h, ''))[:40] for h in s.headers))
