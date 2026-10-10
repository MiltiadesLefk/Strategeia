import sqlite3

conn = sqlite3.connect('runtime/picker_study.db')
c = conn.cursor()

# Tables
print("=== TABLES & ROW COUNTS ===")
tables = c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
for t in tables:
    n = c.execute(f"SELECT COUNT(*) FROM {t[0]}").fetchone()[0]
    print(f"  {t[0]}: {n} rows")

# knownfact breakdown by kind
print()
print("=== knownfact by kind ===")
rows = c.execute("SELECT kind, COUNT(*) FROM knownfact GROUP BY kind ORDER BY COUNT(*) DESC").fetchall()
for r in rows:
    print(f"  {r[0]}: {r[1]}")

# date range
print()
print("=== Date range (known_at) ===")
r = c.execute("SELECT MIN(known_at), MAX(known_at) FROM knownfact").fetchone()
print(f"  Min: {r[0]}, Max: {r[1]}")

# tickers
print()
print("=== Unique tickers ===")
n = c.execute("SELECT COUNT(DISTINCT symbol) FROM knownfact").fetchone()[0]
print(f"  {n} distinct tickers")

conn.close()
