import sqlite3

for name, path in [('users.db', '/app/data/users.db'), ('app.db', '/app/data/app.db')]:
    print(f'=== {name} ===')
    conn = sqlite3.connect(path)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
    print(f'Tables: {tables}')
    for t in tables:
        count = conn.execute(f'SELECT COUNT(*) FROM [{t}]').fetchone()[0]
        print(f'  {t}: {count} rows')
    conn.close()
    print()
