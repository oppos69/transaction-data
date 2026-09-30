"""每日市场概况数据获取 — akshare(同花顺行业板块) + baostock指数成交额 + QMT平均股价"""
import akshare as ak
import pandas as pd
import sys
from datetime import date
from pathlib import Path

DATA_DIR = Path("每日分析")
FILE_AVG_PRICE = Path("avg_price_daily.csv")


def load_avg_price(trade_date: str) -> dict:
    """从本地CSV读取全市场平均股价/中位股价（QMT源，由 fetch_avg_price_update.py 维护）

    替代已失效的通达信 880003 指数（mootdx/TDX 通道整体失效）。
    880003_avg_price.csv 为冻结归档，仅供历史参考，不再更新。
    """
    if not FILE_AVG_PRICE.exists():
        return None
    df = pd.read_csv(FILE_AVG_PRICE, encoding="utf-8-sig")
    row = df[df["date"] == trade_date]
    if len(row) == 0:
        return None
    r = row.iloc[0]
    prev = df[df["date"] < trade_date].tail(1)

    def chg(col):
        if len(prev) == 0:
            return 0
        pc = float(prev.iloc[0][col])
        return round((float(r[col]) - pc) / pc * 100, 2) if pc > 0 else 0

    return {
        "avg_price": round(float(r["avg_price"]), 2),
        "avg_price_change_pct": chg("avg_price"),
        "median_price": round(float(r["median_price"]), 2) if pd.notna(r.get("median_price")) else 0,
        "median_price_change_pct": chg("median_price"),
    }


def fetch_industry_stats() -> pd.DataFrame:
    """从akshare获取同花顺行业板块汇总数据（含涨跌家数、成交额）"""
    df = ak.stock_board_industry_summary_ths()
    df.columns = ["排名", "板块", "涨跌幅%", "总成交量(亿手)", "总成交额(亿)", "净流入",
                   "上涨家数", "下跌家数", "均价", "领涨股", "领涨股-最新价", "领涨股-涨跌幅%"]
    df["涨跌幅%"] = pd.to_numeric(df["涨跌幅%"], errors="coerce")
    df["总成交额(亿)"] = pd.to_numeric(df["总成交额(亿)"], errors="coerce")
    df["上涨家数"] = pd.to_numeric(df["上涨家数"], errors="coerce").fillna(0).astype(int)
    df["下跌家数"] = pd.to_numeric(df["下跌家数"], errors="coerce").fillna(0).astype(int)
    df = df.sort_values("涨跌幅%", ascending=False).reset_index(drop=True)
    df["排名"] = range(1, len(df) + 1)
    return df


def fetch_baostock_index_turnover(trade_date: str) -> float:
    """从baostock获取上证+深证总成交额（亿元）"""
    import baostock as bs
    bs.login()
    total = 0
    for idx in ["sh.000001", "sz.399001"]:
        rs = bs.query_history_k_data_plus(idx, "date,amount",
            start_date=trade_date, end_date=trade_date, frequency="d")
        while rs.error_code == "0" and rs.next():
            d = rs.get_row_data()
            if d[1]:
                total += float(d[1]) / 1e8
    bs.logout()
    return round(total, 2)


# 主要指数列表
MAJOR_INDICES = [
    ("sh.000001", "上证指数"),
    ("sz.399001", "深证成指"),
    ("sh.000905", "中证500"),
    ("sh.000852", "中证1000"),
    ("sz.399006", "创业板指"),
    ("sh.000688", "科创50"),
]


def fetch_major_indices(trade_date: str) -> dict:
    """从baostock获取主要指数涨跌幅和成交量数据"""
    import baostock as bs
    from datetime import datetime, timedelta

    bs.login()
    results = {}

    # 计算前一交易日（简单回退3天查找）
    dt = datetime.strptime(trade_date, "%Y-%m-%d")
    prev_dates = [(dt - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(1, 8)]

    for code, name in MAJOR_INDICES:
        # 获取当日数据
        rs = bs.query_history_k_data_plus(code, "date,close,volume,amount",
            start_date=trade_date, end_date=trade_date, frequency="d")
        today_data = None
        while rs.error_code == "0" and rs.next():
            today_data = rs.get_row_data()

        if not today_data or not today_data[1]:
            continue

        today_close = float(today_data[1])
        today_volume = float(today_data[2]) / 1e8 if today_data[2] else 0  # 亿股
        today_amount = float(today_data[3]) / 1e8 if today_data[3] else 0  # 亿元

        # 获取前一交易日数据
        prev_close = None
        prev_volume = None
        for pd in prev_dates:
            rs = bs.query_history_k_data_plus(code, "date,close,volume",
                start_date=pd, end_date=pd, frequency="d")
            while rs.error_code == "0" and rs.next():
                d = rs.get_row_data()
                if d[0] < trade_date and d[1]:
                    prev_close = float(d[1])
                    prev_volume = float(d[2]) / 1e8 if d[2] else 0
            if prev_close is not None:
                break

        change_pct = round((today_close - prev_close) / prev_close * 100, 2) if prev_close else 0
        vol_change_pct = round((today_volume - prev_volume) / prev_volume * 100, 2) if prev_volume and prev_volume > 0 else 0

        results[name] = {
            "close": today_close,
            "change_pct": change_pct,
            "volume_亿股": round(today_volume, 2),
            "amount_亿": round(today_amount, 2),
            "vol_change_pct": vol_change_pct,
        }

    bs.logout()
    return results


def compute_market_stats(sectors: pd.DataFrame, stock_stats: dict = None) -> dict:
    """从板块数据汇总市场统计"""
    advance = int(sectors["上涨家数"].sum())
    decline = int(sectors["下跌家数"].sum())
    total_turnover = round(float(sectors["总成交额(亿)"].sum()), 2)
    mean_pct = round(float(sectors["涨跌幅%"].mean()), 2)
    median_pct = round(float(sectors["涨跌幅%"].median()), 2)
    result = {
        "total_turnover_亿": total_turnover,
        "total_volume_亿股": 0,
        "advance_count": advance,
        "decline_count": decline,
        "flat_count": 0,
        "median_change_pct": median_pct,
        "mean_change_pct": mean_pct,
        "median_change_abs": 0,
        "mean_change_abs": 0,
    }
    if stock_stats:
        result.update(stock_stats)
    return result


def save_market_stats(stats: dict, date_compact: str):
    """保存市场概况到CSV"""
    out_file = DATA_DIR / f"{date_compact}_市场概况.csv"
    df = pd.DataFrame([{
        "date": f"{date_compact[:4]}-{date_compact[4:6]}-{date_compact[6:]}",
        **stats,
    }])
    df.to_csv(out_file, index=False, encoding="utf-8-sig")
    print(f"已保存: {out_file}")


def main():
    today_str = sys.argv[1] if len(sys.argv) > 1 else date.today().strftime("%Y-%m-%d")
    date_compact = today_str.replace("-", "")

    # 非交易日守卫：数据源返回的是「最近收盘快照」且不带日期，非交易日运行会把
    # 前一交易日的数据贴上该日期标签（2026-09-14 已清理 5 个这样的幻影行）
    from trade_calendar import guard
    if not guard(today_str, "fetch_market_daily.py"):
        return

    DATA_DIR.mkdir(exist_ok=True)

    print(f"获取全市场数据 ({today_str})...")

    # 1. akshare同花顺行业板块数据（含涨跌家数、成交额）
    print("  akshare同花顺行业板块API...")
    sectors = fetch_industry_stats()

    # 1.5 全市场平均股价/中位股价（QMT源）
    stock_stats = load_avg_price(today_str)
    if stock_stats:
        print(f"  平均股价(全A等权): {stock_stats['avg_price']:.2f}元  "
              f"涨跌幅: {stock_stats['avg_price_change_pct']:+.2f}%  |  "
              f"中位股价: {stock_stats['median_price']:.2f}元  "
              f"涨跌幅: {stock_stats['median_price_change_pct']:+.2f}%")
    else:
        print(f"  平均股价数据不可用（{today_str} 缺失，请先运行 fetch_avg_price_update.py；"
              f"注意 QMT 库需先采集当日日K）")
        stock_stats = None

    stats = compute_market_stats(sectors, stock_stats)
    print(f"  板块数: {len(sectors)}")
    print(f"  涨: {stats['advance_count']}  跌: {stats['decline_count']}")
    print(f"  中位数涨跌幅: {stats['median_change_pct']:+.2f}%")
    print(f"  平均涨跌幅: {stats['mean_change_pct']:+.2f}%")

    # 2. 用baostock修正总成交额（更准确）
    print("  baostock指数成交额修正...")
    bs_turnover = fetch_baostock_index_turnover(today_str)
    if bs_turnover > 0:
        stats["total_turnover_亿"] = bs_turnover
        print(f"  总成交额: {bs_turnover:.0f}亿 (baostock)")
    else:
        print(f"  总成交额: {stats['total_turnover_亿']:.0f}亿 (同花顺板块汇总)")

    # 3. 获取主要指数数据
    print("  baostock主要指数数据...")
    major_indices = fetch_major_indices(today_str)
    for name, data in major_indices.items():
        stats[f"{name}_收盘"] = data["close"]
        stats[f"{name}_涨跌幅%"] = data["change_pct"]
        stats[f"{name}_成交量_亿股"] = data["volume_亿股"]
        stats[f"{name}_成交额_亿"] = data["amount_亿"]
        stats[f"{name}_成交量涨跌幅%"] = data["vol_change_pct"]
        print(f"    {name}: {data['close']:.2f}  涨跌幅: {data['change_pct']:+.2f}%  成交量涨跌幅: {data['vol_change_pct']:+.2f}%")

    save_market_stats(stats, date_compact)


if __name__ == "__main__":
    main()
