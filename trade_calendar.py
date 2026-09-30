"""交易日判定 —— 供按日期取数的脚本做守卫

背景（2026-09-14 修复）：
  `fetch_market_daily.py` 等脚本按 `today` 取数，但数据源（同花顺板块汇总等）返回的是
  「最近收盘快照」且**不带日期**。非交易日运行时，脚本会把前一交易日的数据贴上非交易日的
  日期标签，静默产出幻影行。已发现 5 个：2026-05-30 / 07-11 / 07-26 / 08-08 / 09-13
  （其中 05-30 归 06-03，07-11 归 07-10，07-26 归 07-24，08-08 归 08-07，09-13 归 09-11）。

判定源优先级：
  1. baostock `query_trade_dates` —— 权威，含节假日调休（按年缓存）
  2. 本地 `hs300_10years.csv` —— 断网兜底（覆盖 2016 年起）
  3. 都不可用 → 返回 None（未知），调用方应告警后放行，不要静默跳过

2026-09-29 修复（兜底误判）：
  本地兜底只能证明「某日在 CSV 里 = 交易日」，**不能**证明「不在 CSV 里 = 非交易日」——
  CSV 的最后一行之后是数据空白，不是非交易日。而 guard 是在数据更新**之前**执行的，
  所以「今天」必然晚于 CSV 末行：baostock 一挂，今天就被判成非交易日，当日按日期取数的
  脚本（market_daily/sector/money_effect）全被跳过，静默丢一整天数据。
  现改为：请求日晚于本地 CSV 末行 → 超出覆盖范围 → 返回 None（未知）而非 False。
"""
import functools
import threading
from pathlib import Path

import pandas as pd

HS300_CSV = Path("hs300_10years.csv")
# 完整交易日历（`tool_trade_date_hist_sina` 生成，1990 起含未来年度排期）。
# 作为**首选**日历源：本地无网络即可用，且 GH runner 上 baostock 常被境外 IP 拦/hang，
# 靠它保证 guard 判交易日不阻塞、不把周末误判成「非交易日」漏采。
TRADE_DATES_CSV = Path("trade_dates.csv")
# baostock 属 TCP 直连、内部不走 requests 的 timeout，被墙时可能无限阻塞，
# 会把整个采集脚本拖死。给网络调用加看门狗，超时即返回 None → 落到本地日历兜底。
BAOSTOCK_TIMEOUT = 40

_trade_days_cache = {}
# 三态：None=未加载；False=文件缺失/无效；否则为交易日 frozenset
_committed_dates_cache = None


def _committed_trade_days():
    """读入库的完整交易日历（`trade_dates.csv`）；文件缺失/解析失败返回 None"""
    global _committed_dates_cache
    if _committed_dates_cache is not None:
        return _committed_dates_cache if _committed_dates_cache is not False else None
    try:
        if not TRADE_DATES_CSV.exists():
            _committed_dates_cache = False
            return None
        df = pd.read_csv(TRADE_DATES_CSV, encoding="utf-8-sig")
        col = "date" if "date" in df.columns else df.columns[0]
        dates = {pd.to_datetime(d).strftime("%Y-%m-%d") for d in df[col].dropna()}
        if not dates:
            _committed_dates_cache = False
            return None
        _committed_dates_cache = frozenset(dates)
    except Exception:
        _committed_dates_cache = False
        return None
    return _committed_dates_cache


def _baostock_trade_days(year: int):
    """返回该年交易日集合（'YYYY-MM-DD'）；失败/超时返回 None"""
    if year in _trade_days_cache:
        return _trade_days_cache[year]
    box = {"days": None}

    def _fetch():
        try:
            import baostock as bs
            lg = bs.login()
            if lg.error_code == "0":
                try:
                    rs = bs.query_trade_dates(
                        start_date=f"{year}-01-01", end_date=f"{year}-12-31")
                    out = set()
                    while rs.error_code == "0" and rs.next():
                        row = dict(zip(rs.fields, rs.get_row_data()))
                        if row.get("is_trading_day") == "1":
                            out.add(row["calendar_date"])
                    box["days"] = frozenset(out) if out else None
                finally:
                    bs.logout()
        except Exception:
            box["days"] = None

    th = threading.Thread(target=_fetch, daemon=True)
    th.start()
    th.join(BAOSTOCK_TIMEOUT)
    if th.is_alive():
        days = None  # 看门狗中断：baostock 被墙/hang，落到本地日历
    else:
        days = box["days"]
    _trade_days_cache[year] = days
    return days


def _local_trade_days() -> set:
    """本地 hs300 日线日期集合（断网兜底）"""
    if not HS300_CSV.exists():
        return set()
    try:
        df = pd.read_csv(HS300_CSV, encoding="utf-8-sig", usecols=["date"])
        return set(pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d"))
    except Exception:
        return set()


def is_trading_day(date_str: str):
    """date_str 形如 'YYYY-MM-DD' 或 'YYYYMMDD'

    返回 True / False / None（None = 无法判定）
    """
    s = date_str if "-" in date_str else f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    # 1) 首选：入库的完整交易日历（无网络、含未来排期；GH runner 上 baostock 常被拦/hang）
    committed = _committed_trade_days()
    if committed:
        if s <= max(committed):
            return s in committed
        # s 超出覆盖范围（如 2027 年、文件尚未刷新）→ 不得判非交易日，落到在线源
    # 2) baostock（带看门狗，不阻塞）
    days = _baostock_trade_days(int(s[:4]))
    if days is not None:
        return s in days
    # 3) hs300 本地兜底
    local = _local_trade_days()
    if local:
        # 本地兜底只在「覆盖范围内」可信：晚于 CSV 末行 = 数据空白，不是非交易日。
        # 这一条专治「baostock 挂掉 → 今天被判非交易日 → 静默丢当天数据」。
        if s > max(local):
            return None
        return s in local
    return None


def _trading_days_between(a: str, b: str):
    """[a, b] 闭区间内的交易日列表；日历不可用返回 None"""
    if a > b:
        return []
    committed = _committed_trade_days()
    if committed and b <= max(committed):
        return sorted(d for d in committed if a <= d <= b)
    out = []
    for y in range(int(a[:4]), int(b[:4]) + 1):
        ds = _baostock_trade_days(y)
        if ds is None:
            return None
        out.extend(d for d in ds if a <= d <= b)
    return sorted(out)


def guard(date_str: str, script: str) -> bool:
    """供脚本开头的统一守卫，两道检查：

    1. 非交易日 → 跳过（会把前一交易日数据贴上该日期）
    2. 快照源已过期 → 跳过。数据源（同花顺板块汇总等）返回的是「最近收盘快照」，
       不支持按日期取数。若请求日之后已存在更新的交易日，写入的必然是那个更新日
       的数据。`20260530` 那个幻影就是这么来的（06-03 运行、写 05-30）。

    未知时告警但放行（返回 True），避免日历源故障导致整条流水线停摆。
    """
    s = date_str if "-" in date_str else f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    td = is_trading_day(s)
    if td is False:
        print(f"  {s} 非交易日，跳过（本脚本按日期取数，非交易日运行会把前一交易日"
              f"的数据贴上该日期标签，产出幻影行）—— {script}")
        return False
    if td is None:
        print(f"  [!] 无法判定 {s} 是否交易日（baostock 与本地日历均不可用），放行")
        return True

    import datetime as _dt
    nxt = (_dt.date.fromisoformat(s) + _dt.timedelta(days=1)).isoformat()
    today = _dt.date.today().isoformat()
    later = _trading_days_between(nxt, today)
    if later:
        print(f"  {s} 的快照已过期：其后已有交易日 {later[-1]}，而数据源只返回「最近收盘"
              f"快照」、不支持按日期取数，写入会变成 {later[-1]} 的数据 —— 跳过。"
              f"（如需该日数据，请在当日运行；历史日期的板块/家数无法回填）—— {script}")
        return False
    return True
