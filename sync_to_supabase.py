#!/Users/zengzhilin/.claude/venv/bin/python3.12
"""
把最新爬取的 MSC 价格 CSV 同步到 Supabase，供看板展示
用法：python3 sync_to_supabase.py [csv_file]
      不传参数则自动读取 prices_latest.csv
"""

import csv
import json
import sys
import os
import requests
from datetime import datetime

SUPABASE_URL = "https://dbcli-1umy9a5397k3dmeg.database.sankuai.com/rest/v1"
ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoiYW5vbiIsImlzcyI6InN1cGFiYXNlIiwiaWF0IjoxNzQ2OTc5MjAwLCJleHAiOjE5MDQ3NDU2MDB9.JCCYE4Bsk0A026X0lmb0cD7ZHm4uvo9dJYW0rHFGSq4"
HEADERS = {
    "apikey": ANON_KEY,
    "Authorization": f"Bearer {ANON_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates",
}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def sync(csv_path: str):
    print(f"读取：{csv_path}")
    rows = []
    with open(csv_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            scrape_date = row.get("抓取日期", "").strip()
            cruise_id   = row.get("航次ID", "").strip()
            if not scrape_date or not cruise_id:
                continue
            rows.append({
                "cruise_id":   cruise_id,
                "scrape_date": scrape_date,
                "data": {
                    "航线":     row.get("航线", ""),
                    "出发日期": row.get("出发日期", ""),
                    "内舱房":   row.get("内舱房", "售罄"),
                    "海景房":   row.get("海景房", "售罄"),
                    "阳台房":   row.get("阳台房", "售罄"),
                    "套房":     row.get("套房", "售罄"),
                    "游艇会套房": row.get("游艇会套房", "售罄"),
                },
            })

    if not rows:
        print("CSV 为空，跳过")
        return

    print(f"上传 {len(rows)} 条记录到 Supabase...")
    resp = requests.post(
        f"{SUPABASE_URL}/cruise_prices",
        headers=HEADERS,
        json=rows,
        timeout=30,
    )
    if resp.status_code in (200, 201):
        print(f"✓ 同步成功（{len(rows)} 条）")
    else:
        print(f"✗ 同步失败：{resp.status_code} {resp.text[:300]}")
        sys.exit(1)


if __name__ == "__main__":
    csv_file = sys.argv[1] if len(sys.argv) > 1 else os.path.join(SCRIPT_DIR, "prices_latest.csv")
    if not os.path.exists(csv_file):
        print(f"文件不存在：{csv_file}")
        sys.exit(1)
    sync(csv_file)
