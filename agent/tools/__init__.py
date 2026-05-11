#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent 工具包 — 所有内置工具

工具分类:
- read:   Guest+ (read_file, glob, grep, list_folder, search, etc.)
- write:  Staff+ (write_file, append_file, move, copy, etc.)
- delete: SuperAdmin only (delete_file, delete_folder, db_drop_table)
- db:     Staff+ read, DeptHead+ write, GM+ create, SuperAdmin+ drop
- bash_read:  Guest+
- bash_write: Staff+
- bash_destructive: SuperAdmin only
- web:    Guest+ (fetch, search)
- agent:  Staff+ (sub-agent)
"""
