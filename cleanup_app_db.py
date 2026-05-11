#!/usr/bin/env python3
"""Clean up old user tables from app.db after migration to users.db"""
import sqlite3

conn = sqlite3.connect('/app/data/app.db')
cursor = conn.cursor()
for table in ['departments', 'users', 'user_sessions', 'audit_log']:
    try:
        cursor.execute(f"DROP TABLE IF EXISTS [{table}]")
        print(f"Dropped: {table}")
    except Exception as e:
        print(f"Error dropping {table}: {e}")
conn.commit()

tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
print(f"Remaining: {tables}")
conn.close()
