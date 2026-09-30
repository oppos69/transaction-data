"""赚钱效应数据获取 — 等权平均涨幅 + 胜率 + 涨停跌停家数

数据源：
  1. sina全A快照 (stock_zh_a_spot) — 当日等权平均涨幅、中位数涨幅、涨跌家数（仅当天可用）
  2. 东财涨停/跌停池 (stock_zt_pool_em / stock_zt_pool_dtgc_em) — 涨停/跌停家数（按日期参数，可回填）
  3. 行业板块CSV (每日分析/YYYYMMDD_行业板块.csv) — 家数加权均值 ≈ 等权平均涨幅的历史代理（验证误差<0.03pp）
  4. 市场概况CSV (每日分析/YYYYMMDD_市场概况.csv) — 涨跌家数、成交额
  5. avg_price_daily.csv (sina 全A快照累积，不复权) — 平均股价涨跌幅（主源）
  6. QMT (MySQL trade_stock_daily，前复权) — 平均股价涨跌幅（sina 覆盖不到时回退）

用法：
  python fetch_money_effect.py                # 更新今天
  python fetch_money_effect.py 2026-08-06     # 更新指定日期（历史日期仅回填池数据，无等权真实值）
  python fetch_money_effect.py --backfill     # 回填所有历史日期（一次约2-3分钟）
  python fetch_money_effect.py --backfill-avg-price   # 仅回填平均股价涨跌幅（sina优先/QMT回退，秒级）
"""
import akshare as ak
import pandas as pd
import sys
import time
from pathlib import Path
from datetime import date

# QMT 数据源：MySQL trade_stock_daily（由 qmt/collect_daily_kline.py 采集）
# 注意：MySQL 是**本地专有**资源（依赖本机 QMT 采集器）。在 GitHub Actions 等无本地
# 库的环境下必须**优雅降级**（返回 None/空，走 sina/东财路径），而不是让整条流水线
# 崩掉 —— 云端只需要 sina/东财那部分数据。原先 build_row() 无条件连库，云端必崩。
sys.path.insert(0, str(Path(__file__).parent / "qmt"))
try:
    from db_config import get_connection as _raw_get_connection
except Exception as _e:                      # pymysql 未安装 / db_config 缺失
    _raw_get_connection = None
    _QMT_IMPORT_ERROR = f"{type(_e).__name__}: {_e}"
else:
    _QMT_IMPORT_ERROR = None

_QMT_WARNED = False


def get_connection():
    """获取 MySQL 连接；本地库不可用时返回 None（调用方须判空降级）。

    连接不可跨线程共享（见 qmt/db_config.py）。
    """
    global _QMT_WARNED
    if _raw_get_connection is None:
        if not _QMT_WARNED:
            print(f"  [!] QMT 数据库不可用（{_QMT_IMPORT_ERROR}），"
                  f"跳过所有 QMT 回退路径，仅用 sina/东财数据")
            _QMT_WARNED = True
        return None
    try:
        return _raw_get_connection()
    except Exception as e:
        if not _QMT_WARNED:
            print(f"  [!] QMT 数据库连接失败（{type(e).__name__}），"
                  f"跳过所有 QMT 回退路径，仅用 sina/东财数据")
            _QMT_WARNED = True
        return None

# Windows控制台UTF-8输出（避免GBK编码崩溃）
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DATA_DIR = Path("每日分析")
OUT_FILE = Path("money_effect.csv")

COLS = [
    "date", "up_count", "down_count", "win_rate_pct",
    "eqw_mean_pct", "median_pct", "median_price", "eqw_proxy_pct",
    "limit_up", "limit_down", "avg_price_change_pct", "turnover_亿",
]


def load_market_dates() -> list:
    """返回所有市场概况CSV的日期(YYYYMMDD)列表"""
    return sorted(f.name[:8] for f in DATA_DIR.glob("*_市场概况.csv"))


def load_market_row(date_compact: str) -> dict:
    """读取市场概况CSV单行"""
    f = DATA_DIR / f"{date_compact}_市场概况.csv"
    if not f.exists():
        return None
    df = pd.read_csv(f, encoding="utf-8-sig")
    return df.iloc[0].to_dict()


def sector_weighted_mean(date_compact: str) -> float:
    """行业板块家数加权均值 ≈ 等权平均涨幅（历史代理）"""
    f = DATA_DIR / f"{date_compact}_行业板块.csv"
    if not f.exists():
        return None
    df = pd.read_csv(f, encoding="utf-8-sig")
    s = df[["涨跌幅%", "上涨家数", "下跌家数"]].apply(pd.to_numeric, errors="coerce").dropna()
    if len(s) == 0:
        return None
    tot = s["上涨家数"] + s["下跌家数"]
    if tot.sum() <= 0:
        return None
    return round((s["涨跌幅%"] * tot).sum() / tot.sum(), 3)


def fetch_sina_spot_stats() -> dict:
    """sina全A快照 → 等权平均涨幅、中位数、涨跌家数、涨跌停(阈值近似)

    另返回全市场**不复权**平均股价/中位股价（`最新价` 列）。
    该口径与通达信 880003 平均股价指数一致（两者实测同一量级：2026-09-14
    快照均值 27.58 vs 880003 四日前 28.35），故被 fetch_avg_price_update.py 复用。
    """
    df = ak.stock_zh_a_spot()
    s = pd.to_numeric(df["涨跌幅"], errors="coerce").dropna()
    p = pd.to_numeric(df["最新价"], errors="coerce")
    p = p[p > 0].dropna()  # 剔除停牌/无报价
    up = int((s > 0).sum())
    down = int((s < 0).sum())
    return {
        "eqw_mean_pct": round(float(s.mean()), 3),
        "median_pct": round(float(s.median()), 3),
        "up_count": up,
        "down_count": down,
        "limit_up": int((s >= 9.9).sum()),   # 涨跌停阈值近似（池不可用时兜底）
        "limit_down": int((s <= -9.9).sum()),
        "avg_price": round(float(p.mean()), 4) if len(p) else None,
        "median_price": round(float(p.median()), 4) if len(p) else None,
        "stock_count": int(len(p)),
    }


def fetch_limit_counts(date_compact: str) -> tuple:
    """东财涨停/跌停池家数，调用异常返回None。

    空池语义：池按日期覆盖最近约30个交易日。被覆盖日期的空池 = 真实0家
    （大涨日常常0跌停），须与「未覆盖」区分，否则 0 会被当成缺失写成 nan。
    两池皆空无法区分「未覆盖」与「真双零」，保守视为缺失（真实交易日
    几乎不可能同时0涨停0跌停）。
    """
    zt = dt = None
    try:
        zt = len(ak.stock_zt_pool_em(date=date_compact))
    except Exception as e:
        print(f"  [!] 涨停池失败: {e}")
    time.sleep(0.5)
    try:
        dt = len(ak.stock_zt_pool_dtgc_em(date=date_compact))
    except Exception as e:
        print(f"  [!] 跌停池失败: {e}")
    if zt == 0 and not dt:
        zt = None  # 双空 → 视为未覆盖，非真实0
    if dt == 0 and not zt:
        dt = None
    return zt, dt


# ---------------- 平均股价涨跌幅 ----------------
# 主源: sina 全A快照累积序列 avg_price_daily.csv（不复权原始价，2026-09-14 起，
#       由 fetch_avg_price_update.py 维护）—— 与 880003 平均股价指数同口径。
# 回退: QMT trade_stock_daily.close_price（前复权）—— 涨跌幅可信（与 880003 相关
#       0.9914），但绝对价位不可用（2005 年 37% 个股收盘价 ≤ 0，均值会算成负数），
#       故仅作 sina 序列覆盖不到时的涨跌幅回退。
AVG_PRICE_CSV = Path("avg_price_daily.csv")
_SINA_PRICE_SERIES = None   # {date: 均价}，sina 全A快照序列
_QMT_PRICE_SERIES = None    # {date: 均价}，QMT 前复权序列（回退用）
_QMT_START = "2026-05-20"  # QMT 回填起点（覆盖 money_effect.csv 最早日期 2026-05-28 的前一交易日）


def _fmt(d):
    return d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]


def load_qmt_avg_price_series() -> dict:
    """从 MySQL 一次性加载每日全市场平均股价（前复权收盘价算术平均），返回 {date_str: 均价}。

    仅用于涨跌幅回退 —— 该列是前复权，绝对价位不可用（见文件头说明）。
    """
    global _QMT_PRICE_SERIES
    if _QMT_PRICE_SERIES is not None:
        return _QMT_PRICE_SERIES
    conn = get_connection()
    if conn is None:
        return {}
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT trade_date, AVG(close_price) AS mean_close "
                "FROM trade_stock_daily WHERE trade_date >= %s "
                "GROUP BY trade_date ORDER BY trade_date",
                (_QMT_START,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    series = {_fmt(r["trade_date"]): float(r["mean_close"]) for r in rows}
    _QMT_PRICE_SERIES = series
    return series


def _qmt_recent_trade_dates(date_str: str) -> list:
    """返回 <= date_str 的最近两个交易日（date 对象列表，索引扫描，秒级）。"""
    conn = get_connection()
    if conn is None:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT trade_date FROM trade_stock_daily "
                "WHERE trade_date <= %s ORDER BY trade_date DESC LIMIT 2",
                (date_str,),
            )
            return [r["trade_date"] for r in cur.fetchall()]
    finally:
        conn.close()


def _qmt_daily_mean_close(dates) -> dict:
    """查询若干交易日全市场平均股价（收盘价算术平均），返回 {date_str: 均价}。"""
    if not dates:
        return {}
    keys = [_fmt(d) for d in dates]
    placeholders = ",".join(["%s"] * len(keys))
    conn = get_connection()
    if conn is None:
        return {}
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT trade_date, AVG(close_price) AS mean_close "
                f"FROM trade_stock_daily WHERE trade_date IN ({placeholders}) GROUP BY trade_date",
                tuple(keys),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return {_fmt(r["trade_date"]): float(r["mean_close"]) for r in rows}


def fetch_avg_price_change_qmt(date_str: str) -> float:
    """平均股价涨跌幅(%)：当日平均股价相对前一交易日环比。无数据返回 None。

    注意：`_qmt_recent_trade_dates` 返回的是 <= date_str 的最近两个交易日，
    若 DB 尚无当日数据（QMT 采集器还没跑），它会返回更早的日期 —— 必须校验
    keys[0] == date_str，否则会拿上一交易日的涨跌幅冒充当日（静默错数字）。
    """
    ds = _qmt_recent_trade_dates(date_str)
    if len(ds) < 2:
        return None
    keys = [_fmt(d) for d in ds]
    if keys[0] != date_str:
        return None  # DB 无当日数据，宁缺勿错
    means = _qmt_daily_mean_close(ds)
    cur, prev = means.get(keys[0]), means.get(keys[1])
    if not cur or not prev:
        return None
    return round((cur / prev - 1) * 100, 2)


def load_avg_price_csv() -> dict:
    """读 avg_price_daily.csv → {date_str: avg_price}（sina 全A快照源，不复权）"""
    global _SINA_PRICE_SERIES
    if _SINA_PRICE_SERIES is not None:
        return _SINA_PRICE_SERIES
    if not AVG_PRICE_CSV.exists():
        _SINA_PRICE_SERIES = {}
        return _SINA_PRICE_SERIES
    df = pd.read_csv(AVG_PRICE_CSV, encoding="utf-8-sig")
    _SINA_PRICE_SERIES = {
        str(d)[:10]: float(p)
        for d, p in zip(df["date"], df["avg_price"])
        if pd.notna(p)
    }
    return _SINA_PRICE_SERIES


def fetch_avg_price_change(date_str: str) -> float:
    """平均股价涨跌幅(%)：sina 序列优先，覆盖不到时回退 QMT。

    sina 序列口径为不复权原始价（与 880003 一致），自 2026-09-14 起累积，
    故其起点当天无前一交易日 → 自动回退 QMT，无需特判。
    """
    series = load_avg_price_csv()
    if date_str in series:
        prev = [d for d in sorted(series) if d < date_str]
        if prev and series[prev[-1]]:
            return round((series[date_str] / series[prev[-1]] - 1) * 100, 2)
    return fetch_avg_price_change_qmt(date_str)


def fetch_qmt_market_stats(date_str: str) -> dict:
    """从 QMT(trade_stock_daily) 计算当日全市场统计：涨跌家数、等权/中位数涨幅、成交额。

    涨跌幅 = 前复权收盘价相对前一交易日的日环比（与 sina 口径一致）。
    返回 None 表示无前一交易日或该日无数据。
    """
    conn = get_connection()
    if conn is None:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MAX(trade_date) AS md FROM trade_stock_daily WHERE trade_date < %s",
                (date_str,),
            )
            r = cur.fetchone()
            prev = r["md"] if r else None
            if not prev:
                return None
            cur.execute(
                "SELECT a.close_price AS c, b.close_price AS p, a.amount AS amt "
                "FROM trade_stock_daily a "
                "JOIN trade_stock_daily b ON a.stock_code = b.stock_code AND b.trade_date = %s "
                "WHERE a.trade_date = %s",
                (prev, date_str),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    rets, amt = [], 0.0
    for x in rows:
        if not x["p"]:
            continue
        rets.append(float(x["c"]) / float(x["p"]) - 1)
        amt += float(x["amt"]) if x["amt"] else 0.0
    if not rets:
        return None
    up = sum(1 for r in rets if r > 0)
    down = sum(1 for r in rets if r < 0)
    srt = sorted(rets)
    return {
        "up_count": up,
        "down_count": down,
        "flat_count": len(rets) - up - down,
        "eqw_mean_pct": round(sum(rets) / len(rets) * 100, 3),
        "median_pct": round(srt[len(srt) // 2] * 100, 3),
        "turnover_亿": round(amt / 1e8, 2),
    }


def backfill_avg_price():
    """回填 money_effect.csv 全部日期的平均股价涨跌幅（sina 序列优先，QMT 回退）。"""
    if not OUT_FILE.exists():
        print(f"未找到 {OUT_FILE}，跳过")
        return
    df = pd.read_csv(OUT_FILE, encoding="utf-8-sig")
    if df.empty:
        print("CSV 为空")
        return
    for c in COLS:
        if c not in df.columns:
            df[c] = None  # 新增列的历史行留空，避免 df[COLS] KeyError

    sina = load_avg_price_csv()
    sina_dates = sorted(sina)
    qmt = load_qmt_avg_price_series()
    qmt_dates = sorted(qmt)

    # 两个源都空说明数据源异常，此时清空整列会把好数据抹掉 —— 直接放弃
    if not sina and not qmt:
        print("[!] 两个平均股价源均无数据（sina 序列缺失且 QMT 无返回），放弃回填")
        return

    updated = {"sina": 0, "qmt": 0, "none": 0}
    for i, row in df.iterrows():
        d = str(row["date"])[:10]
        val = None
        if d in sina:
            prev = [x for x in sina_dates if x < d]
            if prev and sina[prev[-1]]:
                val = round((sina[d] / sina[prev[-1]] - 1) * 100, 2)
                updated["sina"] += 1
        if val is None and d in qmt:
            # 必须要求 d 本身在 QMT 序列里；用 <= d 会拿上一交易日的涨跌幅冒充当日
            prev = [x for x in qmt_dates if x < d]
            if prev and qmt[prev[-1]]:
                val = round((qmt[d] / qmt[prev[-1]] - 1) * 100, 2)
                updated["qmt"] += 1
        if val is None:
            updated["none"] += 1
        df.at[i, "avg_price_change_pct"] = val  # 缺失写 None，勿留着过期的旧值

    df = df[COLS].sort_values("date").reset_index(drop=True)
    df.to_csv(OUT_FILE, index=False, encoding="utf-8-sig")
    n = updated["sina"] + updated["qmt"]
    print(f"已回填 {n}/{len(df)} 天平均股价涨跌幅"
          f"（sina {updated['sina']} 天 / QMT回退 {updated['qmt']} 天 / 无源 {updated['none']} 天）→ {OUT_FILE}")


def build_row(date_compact: str, fetch_sina: bool) -> dict:
    """构建单日赚钱效应数据行"""
    date_str = f"{date_compact[:4]}-{date_compact[4:6]}-{date_compact[6:]}"
    mr = load_market_row(date_compact)
    qmt = fetch_qmt_market_stats(date_str)  # QMT 全市场统计（缺失时兜底）

    if mr is None and qmt is None:
        print(f"  [!] 无市场概况且无QMT数据: {date_compact}，跳过")
        return None

    row = {
        "date": date_str,
        "up_count": int(mr.get("advance_count", 0)) if mr else (qmt["up_count"] if qmt else 0),
        "down_count": int(mr.get("decline_count", 0)) if mr else (qmt["down_count"] if qmt else 0),
        "win_rate_pct": None,  # 下方计算
        "eqw_mean_pct": None,   # sina真实（仅当天）
        "median_pct": None,
        "median_price": None,
        "eqw_proxy_pct": sector_weighted_mean(date_compact),
        "limit_up": None,
        "limit_down": None,
        "avg_price_change_pct": fetch_avg_price_change(date_str),
        "turnover_亿": mr.get("total_turnover_亿") if mr else (qmt["turnover_亿"] if qmt else None),
    }
    up, down = row["up_count"], row["down_count"]
    row["win_rate_pct"] = round(up / (up + down) * 100, 1) if up + down > 0 else 0

    # sina真实等权（仅当天可用）
    sina_stats = None
    if fetch_sina:
        try:
            print(f"  sina全A快照获取中（约20秒）...")
            sina_stats = fetch_sina_spot_stats()
            row["eqw_mean_pct"] = sina_stats["eqw_mean_pct"]
            row["median_pct"] = sina_stats["median_pct"]
            row["up_count"] = sina_stats["up_count"]
            row["down_count"] = sina_stats["down_count"]
            row["win_rate_pct"] = round(sina_stats["up_count"] / (sina_stats["up_count"] + sina_stats["down_count"]) * 100, 1)
        except Exception as e:
            print(f"  [!] sina失败，用QMT兜底: {e}")
        # 当天 sina 失败时，用 QMT 真实等权/中位数兜底
        if row["eqw_mean_pct"] is None and qmt:
            row["eqw_mean_pct"] = qmt["eqw_mean_pct"]
            row["median_pct"] = qmt["median_pct"]

    # 涨停/跌停池（空池语义见 fetch_limit_counts：单池空=真实0，双空=未覆盖）
    zt, dt = fetch_limit_counts(date_compact)
    if zt is not None:
        row["limit_up"] = zt
    if dt is not None:
        row["limit_down"] = dt
    # sina阈值兜底：逐列独立补。原先要求「两池都空」才兜底，导致单池失败（如
    # 2026-09-30 涨停池接口报错、跌停池正常）时该列留空 —— 阈值口径虽粗于池计数，
    # 但比空缺好，且与「双池皆空」兜底本就用同一口径。
    if sina_stats:
        if row["limit_up"] is None:
            row["limit_up"] = sina_stats["limit_up"]
        if row["limit_down"] is None:
            row["limit_down"] = sina_stats["limit_down"]

    # 中位数股价：优先市场概况CSV（2026-09-14 起才真实，此前列为0；
    # 2026-09-10 有脏值 484.68，需范围校验），否则用当天 sina 快照
    mp = None
    if mr:
        try:
            mp = float(mr.get("median_price") or 0)
        except (TypeError, ValueError):
            mp = None
    if mp and 0 < mp < 200:
        row["median_price"] = round(mp, 2)
    elif sina_stats and sina_stats.get("median_price"):
        row["median_price"] = sina_stats["median_price"]

    return row


def upsert_row(row: dict):
    """写入/更新 money_effect.csv"""
    df = pd.DataFrame([row])
    if OUT_FILE.exists():
        old = pd.read_csv(OUT_FILE, encoding="utf-8-sig")
        for c in COLS:
            if c not in old.columns:
                old[c] = None  # 新增列的历史行留空，避免 df[COLS] KeyError
        old = old[old["date"] != row["date"]]
        df = pd.concat([old, df], ignore_index=True)
    df = df[COLS].sort_values("date").reset_index(drop=True)
    df.to_csv(OUT_FILE, index=False, encoding="utf-8-sig")
    print(f"  已保存: {OUT_FILE} ({len(df)}行)")


def main():
    today = date.today().strftime("%Y%m%d")

    # 仅回填平均股价涨跌幅（QMT 源，秒级，不请求网络）
    if "--backfill-avg-price" in sys.argv:
        print("回填平均股价涨跌幅(sina 序列优先 / QMT 回退)...")
        backfill_avg_price()
        return

    backfill = "--backfill" in sys.argv
    date_arg = None
    for arg in sys.argv[1:]:
        if arg.startswith("20") and len(arg) == 10 and not arg.startswith("--"):
            date_arg = arg.replace("-", "")

    market_dates = load_market_dates()
    if not market_dates:
        print("提示：未找到市场概况CSV，将用 QMT(trade_stock_daily) 兜底计算涨跌家数/成交额")

    if backfill:
        from trade_calendar import is_trading_day
        targets = [d for d in market_dates if is_trading_day(d) is not False]
        skipped = len(market_dates) - len(targets)
        if skipped:
            print(f"跳过 {skipped} 个非交易日（幻影CSV防护）")
        print(f"回填 {len(targets)} 天历史数据...")
        for i, d in enumerate(targets):
            is_today = (d == today)
            print(f"[{i+1}/{len(targets)}] {d}" + (" (含sina真实等权)" if is_today else ""))
            row = build_row(d, fetch_sina=is_today)
            if row:
                upsert_row(row)
            time.sleep(0.3)
    elif date_arg:
        from trade_calendar import guard
        if not guard(date_arg, "fetch_money_effect.py"):
            return
        is_today = (date_arg == today)
        print(f"更新 {date_arg}" + (" (含sina真实等权)" if is_today else ""))
        row = build_row(date_arg, fetch_sina=is_today)
        if row:
            upsert_row(row)
    else:
        # 默认今天：优先 QMT 兜底（市场概况CSV缺失也能跑）；今日无数据（非交易日）则回退最后交易日
        target = today
        print(f"更新 {target} (含sina真实等权)")
        row = build_row(target, fetch_sina=True)
        if row is None and market_dates:
            target = market_dates[-1]
            print(f"  今日无数据，回退到最后交易日 {target}")
            row = build_row(target, fetch_sina=False)
        if row:
            upsert_row(row)


if __name__ == "__main__":
    main()
