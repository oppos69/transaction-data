#!/usr/bin/env python
"""快照型数据采集执行器 —— GitHub Actions 每日补采入口

背景
----
有一类**快照型 A 股数据**「当天不跑就永久丢失」（数据源只返回最近收盘快照、不带日期、
不支持历史回填）：行业/概念板块、全市场平均股价、涨跌家数/成交、sina 等权 + 涨停跌停池、
强势股题材。本脚本在每天定时（盘后）把 5 个 fetch_* 脚本跑一遍，把当日数据落盘。

用法
----
  python collect_snapshots.py                # 采集今天
  python collect_snapshots.py 2026-10-08     # 采集指定交易日

顺序说明（来自 update_all.py，不能乱）：
  fetch_avg_price_update  先跑，把 avg_price_daily.csv 推进到今天；
  fetch_sector_daily / fetch_market_daily  产出 行业板块.csv / 市场概况.csv；
  fetch_money_effect  消费前两者的 CSV（等权 + 涨停跌停池）。
  一个脚本失败不中断后续（各源独立；guard 已按交易日过滤）。

本脚本只负责「跑脚本、落盘」；是否 commit 由上层 workflow 用 git 判空决定。
"""
import subprocess
import sys
import time
from datetime import datetime

WIN = sys.platform == "win32"
if WIN:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

# (脚本名, 是否接受日期参数, 描述)
STEPS = [
    ("fetch_avg_price_update.py",  False, "全市场平均/中位股价（sina 全A快照）"),
    ("fetch_sector_daily.py",      True,  "行业板块 + 概念板块涨跌"),
    ("fetch_market_daily.py",      True,  "全市场概况（涨跌家数/平均股价/成交）"),
    ("fetch_money_effect.py",      True,  "赚钱效应（sina 等权 + 涨停跌停池）"),
    ("fetch_ths_hot_reason.py",    True,  "强势股题材归因"),
]

SCRIPT_DELAY = 1  # 秒，避免限流


def run_script(script, date_arg, timeout=300):
    cmd = [sys.executable, script]
    if date_arg:
        cmd.append(date_arg)
    print(f"  ▶ {' '.join(cmd[1:])}")
    try:
        r = subprocess.run(cmd, timeout=timeout)
        ok = r.returncode == 0
    except subprocess.TimeoutExpired:
        ok = False
        print(f"    ⏰ 超时({timeout}s)")
    except FileNotFoundError:
        ok = False
        print(f"    ❌ 找不到 {script}")
    except Exception as e:
        ok = False
        print(f"    ❌ {e}")
    print(f"    {'✅ 完成' if ok else '⚠️ 失败（继续）'}")
    return ok


def main():
    date_arg = next((a for a in sys.argv[1:] if a[:4].isdigit() and len(a) >= 8), None)
    label = date_arg or datetime.now().strftime("%Y-%m-%d")
    print("=" * 66)
    print(f"  快照型数据采集  |  {label}")
    print("=" * 66)

    from trade_calendar import guard
    guard_decision = guard(label, "collect_snapshots.py")
    print(f"  guard({label}) → {'通过' if guard_decision else '跳过'}")

    results = []
    for script, takes_date, desc in STEPS:
        print(f"\n  ▶▶ {script} — {desc}")
        ok = run_script(script, date_arg if takes_date else None)
        results.append((script, ok))
        if results[-1][0] != STEPS[-1][0]:
            time.sleep(SCRIPT_DELAY)

    ok_n = sum(1 for _, ok in results if ok)
    print(f"\n{'=' * 66}")
    print(f"  汇总：成功 {ok_n}/{len(results)}")
    for script, ok in results:
        print(f"    {'✅' if ok else '❌'} {script}")
    print("=" * 66)
    # 允许部分失败也返回 0，让数据能提交；严重到无任何产出时 workflow 会判空不提交
    return 0


if __name__ == "__main__":
    sys.exit(main())