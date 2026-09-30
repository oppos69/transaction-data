#!/usr/bin/env python
"""刷新入库的完整交易日历 `trade_dates.csv`（sina 源，境外可达）。

`trade_calendar.py` 的 guard 首选 `trade_dates.csv`（见其 `_committed_trade_days`）；
本脚本在每个采集日前重生成它，保证覆盖「今天 + 未来年度」的排期 ——
这样 GH runner 上即便 baostock 被境外 IP 拦/hang，guard 也能准确
判出今天是不是交易日，从而正确跳过周末/节假日、不产幻影行。

sina 的 `tool_trade_date_hist_sina` 返回 1990 起含当年/次年排期，每日刷新幂等。
"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass


def main():
    import akshare as ak
    df = ak.tool_trade_date_hist_sina()
    df["date"] = df["trade_date"].astype(str)
    df[["date"]].to_csv("trade_dates.csv", index=False, encoding="utf-8-sig")
    print(f"交易日历已刷新：{len(df)} 行，{df['date'].iloc[0]} → {df['date'].iloc[-1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())