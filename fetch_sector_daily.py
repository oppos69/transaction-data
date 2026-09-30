"""每日板块数据获取 — 行业板块(同花顺) + 概念板块(东方财富)"""
import akshare as ak
import requests
import pandas as pd
import sys
import time
from datetime import date
from pathlib import Path

DATA_DIR = Path("每日分析")


def fetch_industry() -> pd.DataFrame:
    """同花顺90行业板块涨跌幅排名"""
    df = ak.stock_board_industry_summary_ths()
    df.columns = ["排名", "板块", "涨跌幅%", "总成交量(亿手)", "总成交额(亿)", "净流入",
                   "上涨家数", "下跌家数", "均价", "领涨股", "领涨股-最新价", "领涨股-涨跌幅%"]
    df = df.drop(columns=["总成交量(亿手)"])
    df["涨跌幅%"] = pd.to_numeric(df["涨跌幅%"], errors="coerce")
    df = df.sort_values("涨跌幅%", ascending=False).reset_index(drop=True)
    df["排名"] = range(1, len(df) + 1)
    return df


def fetch_concept() -> pd.DataFrame:
    """东方财富概念板块涨跌幅排名"""
    hosts = ["push2.eastmoney.com", "push2delay.eastmoney.com"]
    items = []
    for attempt in range(4):
        host = hosts[attempt % len(hosts)]
        try:
            resp = requests.get(
                f"https://{host}/api/qt/clist/get",
                params={"pn": 1, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                        "fid": "f3", "fs": "m:90+t:3+f:!50",
                        "fields": "f3,f6,f12,f14,f104,f105"},
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
                timeout=20,
            )
            resp.raise_for_status()
            items = (resp.json().get("data") or {}).get("diff") or []
            if items:
                break
        except Exception as e:
            print(f"  重试 ({attempt+1}/4, {host}): {type(e).__name__}")
        time.sleep(3)

    if not items:
        print("Warning: eastmoney returned no concept data")
        return pd.DataFrame()

    df = pd.DataFrame([{
        "概念板块": it["f14"],
        "代码": it["f12"],
        "涨跌幅%": pd.to_numeric(it["f3"], errors="coerce"),
        "成交额": pd.to_numeric(it["f6"], errors="coerce"),
        "上涨家数": it["f104"],
        "下跌家数": it["f105"],
    } for it in items])
    df = df.sort_values("涨跌幅%", ascending=False).reset_index(drop=True)
    df["排名"] = range(1, len(df) + 1)
    return df[["排名", "概念板块", "代码", "涨跌幅%", "成交额", "上涨家数", "下跌家数"]]


def main():
    today_str = sys.argv[1] if len(sys.argv) > 1 else date.today().strftime("%Y-%m-%d")
    date_compact = today_str.replace("-", "")

    # 非交易日守卫：数据源返回「最近收盘快照」且不带日期，非交易日运行会产出幻影行
    from trade_calendar import guard
    if not guard(today_str, "fetch_sector_daily.py"):
        return

    print(f"Fetching sector data for {today_str}...")

    # 行业板块
    print("  行业板块 (同花顺)...")
    ind = fetch_industry()
    ind_file = DATA_DIR / f"{date_compact}_行业板块.csv"
    ind.to_csv(ind_file, index=False, encoding="utf-8-sig")
    print(f"  -> {ind_file} ({len(ind)} 个行业)")

    # 概念板块
    print("  概念板块 (问财)...")
    con = fetch_concept()
    if not con.empty:
        con_file = DATA_DIR / f"{date_compact}_概念板块.csv"
        con.to_csv(con_file, index=False, encoding="utf-8-sig")
        print(f"  -> {con_file} ({len(con)} 个概念)")
    else:
        print("  -> 概念板块数据获取失败")


if __name__ == "__main__":
    main()
