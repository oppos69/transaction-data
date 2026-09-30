"""全市场平均股价 / 中位股价 增量更新 — sina 全A快照源

替代已失效的通达信 880003 平均股价指数。

为什么不用 mootdx/TDX:
  实测 8 个通达信服务器 TCP 可连，但 index_bars()/bars() 对上证指数(999999)、
  个股(600519)、880003 一律返回 0 行 —— 整条通道失效，非单符号问题。

为什么不用 QMT trade_stock_daily（虽然库里有）:
  其 close_price 是**前复权（减法口径）**，历史价位被折低，越早越失真：
    2005 年 37% 的股票收盘价 ≤ 0（均值算出来是负数）
    2007-2014 年 3~8% 非正，均值被系统性压低约 10%
    与 880003 的比值 2023-02 = 1.19 → 2026-07 = 0.99（约 6%/年漂移）
  且该库是「2026-08-22 一次性回填的前复权历史 + 按日追加的近期行」两种口径拼接。
  → 涨跌幅可用（与 880003 相关 0.9914），**数值不可用**。

口径:
  平均股价 = 全市场个股**最新价（不复权）**的算术平均
  中位股价 = 同上的中位数
  与 880003 同口径，可与 880003_avg_price.csv 归档（止于 2026-09-08）无缝衔接。

局限:
  sina 只提供**当日快照**，无历史。本序列从首次运行之日起逐日累积，
  无法回填 2026-09-09 ~ 首次运行之间的交易日。

用法:
  python fetch_avg_price_update.py           # 增量（已存在当日则重算覆盖）
  python fetch_avg_price_update.py --force   # 强制重取当日
"""
import sys
import time
import akshare as ak
import pandas as pd
from pathlib import Path

from fetch_money_effect import fetch_sina_spot_stats  # 复用同一份 sina 快照实现

CSV_FILE = Path("avg_price_daily.csv")
COLUMNS = ["date", "avg_price", "median_price", "stock_count"]


def _stats_from(prices: pd.Series):
    p = pd.to_numeric(prices, errors="coerce")
    p = p[p > 0].dropna()  # 剔除停牌/无报价
    if not len(p):
        raise RuntimeError("快照无有效价格")
    return round(float(p.mean()), 4), round(float(p.median()), 4), int(len(p))


def _from_sina():
    st = fetch_sina_spot_stats()
    if not st.get("avg_price"):
        raise RuntimeError("sina 快照无有效价格")
    return st["avg_price"], st["median_price"], st["stock_count"], "sina"


def _from_eastmoney():
    df = ak.stock_zh_a_spot_em()
    a, m, n = _stats_from(df["最新价"])
    return a, m, n, "eastmoney"


def fetch_prices():
    """取全市场不复权均价/中位价。sina 为主、东财兜底，各重试 2 轮。

    返回 (avg_price, median_price, stock_count, source)；全失败抛 RuntimeError。
    """
    errs = []
    for attempt in range(2):
        for fn in (_from_sina, _from_eastmoney):
            try:
                return fn()
            except Exception as e:
                errs.append(f"{fn.__name__}({type(e).__name__})")
                time.sleep(3)
    raise RuntimeError("两个快照源均失败: " + ", ".join(errs))


def load_csv() -> pd.DataFrame:
    if not CSV_FILE.exists():
        return pd.DataFrame(columns=COLUMNS)
    df = pd.read_csv(CSV_FILE, encoding="utf-8-sig")
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    return df[COLUMNS]


def main() -> int:
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    force = "--force" in sys.argv

    print(f"获取全市场平均股价 (sina 全A快照) {today}...")
    old = load_csv()
    if len(old) and today in set(old["date"]) and not force:
        print(f"  {today} 已在 CSV 中，跳过（--force 可强制重取）")
        return 0

    try:
        avg, med, n, src = fetch_prices()
    except Exception as e:
        print(f"  获取失败: {e}")
        return 1

    row = {"date": today, "avg_price": avg, "median_price": med, "stock_count": n}
    print(f"  源={src}  样本 {n} 只  平均股价 {avg:.2f}元  中位股价 {med:.2f}元")

    merged = pd.concat([old[old["date"] != today], pd.DataFrame([row])], ignore_index=True)
    merged = merged.sort_values("date").reset_index(drop=True)
    merged.to_csv(CSV_FILE, index=False, encoding="utf-8-sig")
    print(f"  已保存: {CSV_FILE} ({len(merged)} 天)")

    if len(merged) > 1:
        prev = merged.iloc[-2]
        chg = (row["avg_price"] / prev["avg_price"] - 1) * 100
        mchg = (row["median_price"] / prev["median_price"] - 1) * 100
        print(f"  较 {prev['date']}: 平均股价 {chg:+.2f}%   中位股价 {mchg:+.2f}%")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"  异常退出: {type(e).__name__}: {e}")
        sys.exit(1)
