#!/usr/bin/env python3
"""Debug: check users.db state inside Docker"""
import sqlite3, os

path = '/app/data/users.db'
print(f'Path: {path}')
print(f'Exists: {os.path.exists(path)}')
print(f'Size: {os.path.getsize(path) if os.path.exists(path) else "N/A"}')

if os.path.exists(path):
    conn = sqlite3.connect(path)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
    print(f'Tables: {tables}')
    # Check sqlite_master directly
    all_objects = conn.execute("SELECT type, name, sql FROM sqlite_master").fetchall()
    print(f'All objects: {all_objects}')
    conn.close()
