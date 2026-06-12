#!/Users/zengzhilin/.claude/venv/bin/python3.12
"""
MSC 地中海邮轮 每日价格爬虫
爬取所有航线的各舱型双人价格，保存为 CSV
"""

import requests
import csv
import json
import os
import sys
from datetime import datetime, timezone

BASE_URL = "https://www.msccruises.com.cn/web-api/v2"
HEADERS = {
    "Content-Type": "application/json",
    "Referer": "https://www.msccruises.com.cn/booking/",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}

CABIN_CATEGORIES = {
    0: "游艇会套房",
    1: "内舱房",
    2: "海景房",
    3: "阳台房",
    4: "套房",
}

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))


def fetch_all_cruises():
    """获取所有航次基础信息（含舱型参考价格）"""
    resp = requests.post(
        f"{BASE_URL}/product/getPriceB2c",
        json={"ship": ["BE"], "area": "FAE"},
        headers=HEADERS,
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json().get("body", [])


def fetch_cabin_prices_2adults(cruise_id):
    """获取指定航次双人各舱型价格"""
    resp = requests.post(
        f"{BASE_URL}/dts/bmsSearchCruisesV1",
        json={
            "ageString": "2,0,0,0",
            "childAges": [],
            "cruiseId": cruise_id,
            "discountCodes": [],
            "noofAdults": 2,
            "noofChildren": 0,
        },
        headers=HEADERS,
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json().get("body", [])


def parse_departure_date(ts_str):
    """Unix 时间戳转日期字符串"""
    try:
        ts = int(ts_str)
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return ts_str


def build_cabin_price_map(dts_items):
    """从 bmsSearchCruisesV1 结果中提取每个舱型的最低双人价格（含港务费）"""
    # Category 前缀归类：I=内舱 B=阳台 O=海景 S=套房 YC=游艇会
    PREFIX_MAP = [
        ("YC", "游艇会套房"),
        ("IS", None),   # 内舱保证类，忽略（无具体房号）
        ("I",  "内舱房"),
        ("B",  "阳台房"),
        ("O",  "海景房"),
        ("S",  "套房"),
    ]

    cabin_min = {}  # cabin_name -> min price per person
    for item in dts_items:
        # 只取有实际房间的舱型（CabinsAvailable > 0）
        if int(item.get("CabinsAvailable", 0)) == 0:
            continue
        cat_code = item.get("Category", "")

        # 从 peoplePrice 列表中取第一位成人的含税全价
        pax_list = item.get("peoplePrice", [])
        if not pax_list or not isinstance(pax_list, list):
            continue
        try:
            price = float(pax_list[0].get("AllInclusivePerPax", 0))
        except (ValueError, TypeError, IndexError):
            continue
        if price <= 0:
            continue

        # 确定舱型名
        cabin_name = None
        for prefix, name in PREFIX_MAP:
            if cat_code.startswith(prefix):
                cabin_name = name
                break
        if not cabin_name:
            cabin_name = cat_code  # 未知类型保留原码

        if cabin_name not in cabin_min or price < cabin_min[cabin_name]:
            cabin_min[cabin_name] = price

    return cabin_min


def build_cabin_detail_map(dts_items):
    """返回所有子类型价格明细（含售罄），格式：{代码: price_int | None}"""
    detail = {}
    for item in dts_items:
        code = item.get("Category", "")
        if not code:
            continue
        pax_list = item.get("peoplePrice", [])
        price = None
        if pax_list and isinstance(pax_list, list):
            try:
                p = float(pax_list[0].get("AllInclusivePerPax", 0))
                if p > 0:
                    price = int(p)
            except (ValueError, TypeError, IndexError):
                pass
        # 有票且 CabinsAvailable > 0 才标价格，否则为 None（售罄）
        if int(item.get("CabinsAvailable", 0)) == 0:
            price = None
        detail[code] = price
    return detail


# 优先读环境变量（GitHub Actions Secrets），本地运行退回硬编码默认值
_SUPABASE_BASE = os.environ.get(
    "SUPABASE_URL",
    "https://dbcli-1umy9a5397k3dmeg.database.sankuai.com"
).rstrip("/")
SUPABASE_URL = f"{_SUPABASE_BASE}/rest/v1"
SUPABASE_ANON_KEY = os.environ.get(
    "SUPABASE_ANON_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoiYW5vbiIsImlzcyI6InN1cGFiYXNlIiwiaWF0IjoxNzQ2OTc5MjAwLCJleHAiOjE5MDQ3NDU2MDB9.JCCYE4Bsk0A026X0lmb0cD7ZHm4uvo9dJYW0rHFGSq4"
)
SUPABASE_HEADERS = {
    "apikey": SUPABASE_ANON_KEY,
    "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates",
}


def _sync_supabase(supabase_rows):
    resp = requests.post(
        f"{SUPABASE_URL}/cruise_prices?on_conflict=cruise_id,scrape_date",
        headers=SUPABASE_HEADERS,
        json=supabase_rows,
        timeout=30,
    )
    if resp.status_code in (200, 201):
        print(f"✓ Supabase 同步成功（{len(supabase_rows)} 条，含子类型明细）")
    else:
        print(f"✗ Supabase 同步失败：{resp.status_code} {resp.text[:200]}")


def scrape():
    today = datetime.now().strftime("%Y-%m-%d")
    output_csv = os.path.join(OUTPUT_DIR, f"prices_{today}.csv")
    summary_csv = os.path.join(OUTPUT_DIR, "prices_latest.csv")

    print(f"[{datetime.now().strftime('%H:%M:%S')}] 开始爬取 MSC 航线价格...")
    cruises = fetch_all_cruises()
    print(f"  共找到 {len(cruises)} 个航次")

    rows = []
    supabase_rows = []
    cabin_names_all = ["内舱房", "海景房", "阳台房", "套房", "游艇会套房"]

    for idx, item in enumerate(cruises):
        prod = item.get("product", {})
        dts_summary = item.get("dtsPrice", {})

        cruise_id = prod.get("cruiseId", "")
        title = prod.get("title", "")
        depart_date = parse_departure_date(prod.get("departureDate", ""))

        # 获取双人各舱型价格
        try:
            dts_items = fetch_cabin_prices_2adults(cruise_id)
            cabin_prices = build_cabin_price_map(dts_items)
            cabin_detail = build_cabin_detail_map(dts_items)
        except Exception as e:
            print(f"  [{idx+1}/{len(cruises)}] {cruise_id} 价格获取失败: {e}")
            cabin_prices = {}
            cabin_detail = {}

        # 计算航行天数（从 cruiseId 和产品字段推断，或从 itinerary 中获取）
        # cruiseId 格式: BE20260801SHASHA，出发港和到达港各4字符
        nights = dts_summary.get("nights", "")

        row = {
            "抓取日期": today,
            "航次ID": cruise_id,
            "航线": title,
            "出发日期": depart_date,
        }
        for cabin in cabin_names_all:
            p = cabin_prices.get(cabin)
            row[cabin] = f"¥{int(p)}" if p else "售罄"

        rows.append(row)
        # Supabase 行（包含子类型 detail）
        supabase_rows.append({
            "cruise_id":   cruise_id,
            "scrape_date": today,
            "data": {
                "航线":     title,
                "出发日期": depart_date,
                **{c: row[c] for c in cabin_names_all},
                "detail":   cabin_detail,
            },
        })
        print(f"  [{idx+1}/{len(cruises)}] {depart_date} {title}: " +
              " | ".join(f"{c}:{row[c]}" for c in cabin_names_all))

    # 写 CSV
    fieldnames = ["抓取日期", "航次ID", "航线", "出发日期"] + cabin_names_all
    for path in [output_csv, summary_csv]:
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    print(f"\n完成！已保存到:")
    print(f"  {output_csv}")
    print(f"  {summary_csv}  (最新快照，始终覆盖)")

    # 直接推送完整数据（含 detail）到 Supabase
    print("\n正在同步到 Supabase...")
    _sync_supabase(supabase_rows)
    return rows


if __name__ == "__main__":
    scrape()
