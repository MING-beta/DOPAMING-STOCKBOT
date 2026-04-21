import sqlite3
import os
from datetime import datetime, timedelta, date

db_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'trading.db')

if not os.path.exists(db_path):
    print("DB 파일 없음:", db_path)
    exit()

conn = sqlite3.connect(db_path)
cur = conn.cursor()

# 테이블 목록 확인
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cur.fetchall()
print("=== 테이블 목록 ===")
for t in tables:
    print(" -", t[0])

# 각 테이블 컬럼 확인
for (tname,) in tables:
    cur.execute(f"PRAGMA table_info({tname})")
    cols = cur.fetchall()
    print(f"\n=== {tname} 컬럼 ===")
    for c in cols:
        print(f"  {c[1]} ({c[2]})")
    
    # 최근 데이터 샘플
    cur.execute(f"SELECT * FROM {tname} ORDER BY rowid DESC LIMIT 5")
    rows = cur.fetchall()
    print(f"  [최근 5건]")
    for r in rows:
        print(" ", r)

conn.close()
